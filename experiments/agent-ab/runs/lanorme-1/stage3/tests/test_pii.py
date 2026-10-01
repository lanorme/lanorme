import random

import pytest

from app.guardrails.pii import StreamRedactor, redact_pii


@pytest.mark.parametrize(
    "text",
    [
        "+44 20 7946 0958",
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "555 123 4567",
        "1-555-123-4567",
        "+1 (555) 123-4567",
        "+1-555-123-4567",
        "+33 1 23 45 67 89",
        "+447946095800",
        "020 7946 0958",
        "07700 900123",
    ],
)
def test_phone_formats_are_redacted(text: str) -> None:
    result = redact_pii(f"call {text} today")

    assert result.text == "call [REDACTED_PHONE] today"
    assert result.changed


@pytest.mark.parametrize(
    "text",
    [
        "a@b.co",
        "jane.doe@example.com",
        "first+tag@mail.example.co.uk",
        "UPPER_case-99@Sub.Domain.ORG",
    ],
)
def test_email_formats_are_redacted(text: str) -> None:
    assert redact_pii(f"<{text}>").text == "<[REDACTED_EMAIL]>"


@pytest.mark.parametrize(
    "text",
    [
        "2024-01-15",
        "192.168.100.200",
        "12 * 34 + 56",
        "order 12345",
        "3.14159",
        "version 1.2.3",
        "user@localhost",
        "no pii here",
    ],
)
def test_non_pii_is_left_alone(text: str) -> None:
    result = redact_pii(text)

    assert result.text == text
    assert not result.changed


def test_multiple_items_in_one_message() -> None:
    text = "a@x.com, b@y.org, 555-123-4567 and (555) 987-6543"

    assert redact_pii(text).text == (
        "[REDACTED_EMAIL], [REDACTED_EMAIL], [REDACTED_PHONE] and [REDACTED_PHONE]"
    )


def test_a_phone_number_opening_a_line_is_redacted_after_a_digit() -> None:
    assert redact_pii("Order 123\n+44 20 7946 0958").text == "Order 123\n[REDACTED_PHONE]"


def redact_in_pieces(text: str, sizes: list[int]) -> tuple[str, bool]:
    redactor = StreamRedactor()
    pieces, start = [], 0
    for size in sizes:
        pieces.append(redactor.feed(text[start : start + size]))
        start += size
    pieces.append(redactor.feed(text[start:]))
    pieces.append(redactor.flush())
    return "".join(pieces), redactor.changed


STREAMED = (
    "Mail jane.doe@example.com or ring +44 20 7946 0958, (555) 123-4567 and "
    "555 123 4567 today.\nOn 2024-01-15 call 020 7946 0958 or 1-555-123-4567; "
    "version 1.2.3, IP 192.168.100.200, order 12345.\tFinally a@b.co."
)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, 40])
def test_streamed_redaction_matches_whole_redaction_for_even_chunks(size: int) -> None:
    # Given
    sizes = [size] * (len(STREAMED) // size)

    # When
    text, changed = redact_in_pieces(STREAMED, sizes)

    # Then
    assert text == redact_pii(STREAMED).text
    assert changed


def test_streamed_redaction_matches_whole_redaction_for_random_text_and_chunks() -> None:
    # Given texts built from fragments that sit on or near PII boundaries
    rng = random.Random(1234)
    fragments = [
        "a@b.co", "jane.doe@example.com", "555", "123", "4567", "+44", "20", "(555)", "1",
        "0958", "020", "07700", "900123", " ", " ", " ", "  ", "\n", "-", ".", "x", "call",
        "2024-01-15", "+1", "(", ")", ",",
    ]

    for _ in range(500):
        text = "".join(rng.choice(fragments) for _ in range(rng.randint(1, 30)))
        sizes = [rng.randint(1, 6) for _ in range(len(text))]

        # When
        streamed, changed = redact_in_pieces(text, sizes)

        # Then
        whole = redact_pii(text)
        assert streamed == whole.text, (text, sizes)
        assert changed == whole.changed


def test_text_is_released_before_the_stream_ends() -> None:
    # Given
    redactor = StreamRedactor()

    # When
    first = redactor.feed("Hello there, ")
    second = redactor.feed("write to jane")

    # Then the words before the possibly unfinished email are already out
    assert first == "Hello"
    assert second == " there, write to"
    assert redactor.feed("@example.com now") == " [REDACTED_EMAIL]"
    assert redactor.flush() == " now"
