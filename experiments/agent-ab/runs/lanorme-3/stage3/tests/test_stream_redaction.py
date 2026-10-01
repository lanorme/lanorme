"""StreamRedactor: redacting a reply piece by piece gives exactly what redact_pii gives whole."""

import itertools
import random

import pytest

from app.guardrails import StreamRedactor, find_safe_cut, redact_pii

SAMPLES = [
    "Contact support@example.org or +44 20 7946 0958.",
    "I am jane@example.com, ring me on (555) 123-4567",
    "call +1 (555) 123-4567 or 555.123.4567 now",
    "Mail Jane.Doe+tag@mail.example.co.uk\nor call +33 1 23 45 67 89",
    "a@b.io or 555-123-4567",
    "The meeting is on 2024-01-15 at 10:30, room 101 ext 4567.",
    "Order 4521 shipped; version 3.12.1 is out. Use the @ sign.",
    "Numbers 555 123 and then 4567 8901 2345 later",
    "x555-123-4567 and 555-123-4567x stay; +1 555 123 4567 goes",
    "",
    "   ",
]


def redact_in_pieces(pieces: list[str]) -> tuple[str, bool]:
    redactor = StreamRedactor()
    released = [redactor.push(piece) for piece in pieces]
    released.append(redactor.flush())
    return "".join(released), redactor.changed


def split_at(text: str, cuts: tuple[int, ...]) -> list[str]:
    bounds = [0, *cuts, len(text)]
    return [text[start:end] for start, end in itertools.pairwise(bounds)]


@pytest.mark.parametrize("text", SAMPLES)
def test_every_two_and_three_way_split_matches_whole_text_redaction(text: str) -> None:
    # Given
    expected = redact_pii(text)
    splits = [
        cuts for size in (1, 2) for cuts in itertools.combinations_with_replacement(range(len(text) + 1), size)
    ]

    # When
    results = {redact_in_pieces(split_at(text, cuts)) for cuts in splits}

    # Then
    assert results == {(expected.text, expected.changed)}


@pytest.mark.parametrize("text", SAMPLES)
@pytest.mark.parametrize("size", [1, 2, 3, 5, 8])
def test_fixed_size_chunks_match_whole_text_redaction(text: str, size: int) -> None:
    pieces = [text[start : start + size] for start in range(0, len(text), size)]

    result = redact_in_pieces(pieces)

    assert result == (redact_pii(text).text, redact_pii(text).changed)


def test_an_unfinished_email_is_held_back() -> None:
    # Given
    redactor = StreamRedactor()

    # When
    first = redactor.push("Write to jane.doe")
    second = redactor.push("@example.com today")
    rest = redactor.flush()

    # Then
    assert first == "Write to "
    assert second == "[REDACTED_EMAIL] "
    assert rest == "today"
    assert redactor.changed


def test_a_phone_number_is_not_released_between_its_digit_groups() -> None:
    # Given
    redactor = StreamRedactor()

    # When
    released = redactor.push("call 555 123 ")

    # Then
    assert released == "call "
    assert redactor.push("4567 please") + redactor.flush() == "[REDACTED_PHONE] please"


def test_a_disabled_redactor_passes_pieces_straight_through() -> None:
    # Given
    redactor = StreamRedactor(enabled=False)

    # When
    released = [redactor.push("mail a@"), redactor.push("b.io"), redactor.flush()]

    # Then
    assert released == ["mail a@", "b.io", ""]
    assert not redactor.changed


@pytest.mark.parametrize(
    ("text", "cut"),
    [("", 0), ("word", 0), ("two words", 4), ("ends with space ", 10), ("call 555 123", 5), ("(555) 123", 0)],
)
def test_safe_cut_is_after_whitespace_outside_phone_numbers(text: str, cut: int) -> None:
    assert find_safe_cut(text) == cut


def test_random_texts_in_random_pieces_match_whole_text_redaction() -> None:
    # Given: PII-shaped text built from digits, separators and address fragments
    rng = random.Random(7)
    atoms = [*"0123456789" * 3, *" ()+-.@_%\n", "a", "io", "com", "  ", "555", "+44", "(20)", "a@b.io"]
    texts = ["".join(rng.choice(atoms) for _ in range(rng.randint(0, 30))) for _ in range(3000)]

    # When
    mismatches = []
    for text in texts:
        cuts = tuple(sorted(rng.sample(range(len(text) + 1), k=min(len(text) + 1, rng.randint(0, 6)))))
        if redact_in_pieces(split_at(text, cuts)) != (redact_pii(text).text, redact_pii(text).changed):
            mismatches.append(text)

    # Then
    assert mismatches == []
