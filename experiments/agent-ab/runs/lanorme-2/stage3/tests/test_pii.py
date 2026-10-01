import pytest

from app.domain.pii import EMAIL_PLACEHOLDER, PHONE_PLACEHOLDER, StreamingRedactor, redact_pii


@pytest.mark.parametrize(
    "phone",
    [
        "+44 20 7946 0958",
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "5551234567",
        "+1 (555) 123-4567",
        "+1-555-123-4567",
        "020 7946 0958",
        "+33 1 23 45 67 89",
    ],
)
def test_redacts_common_phone_formats(phone: str) -> None:
    result = redact_pii(f"Call me on {phone} tomorrow.")

    assert result.text == f"Call me on {PHONE_PLACEHOLDER} tomorrow."
    assert result.changed


@pytest.mark.parametrize(
    "email", ["jane.doe@example.com", "a+tag@mail.co.uk", "X_Y-1@sub.domain.io"]
)
def test_redacts_email_addresses(email: str) -> None:
    result = redact_pii(f"Write to <{email}>.")

    assert result.text == f"Write to <{EMAIL_PLACEHOLDER}>."
    assert result.changed


def test_redacts_several_items_in_one_text() -> None:
    result = redact_pii("me@x.org, 555-123-4567 or bob@y.com / (555) 987-6543")

    assert result.text == (
        f"{EMAIL_PLACEHOLDER}, {PHONE_PLACEHOLDER} or {EMAIL_PLACEHOLDER} / {PHONE_PLACEHOLDER}"
    )


def test_digits_inside_an_email_are_not_read_as_a_phone() -> None:
    result = redact_pii("5551234567@example.com")

    assert result.text == EMAIL_PLACEHOLDER


@pytest.mark.parametrize(
    "text",
    [
        "The meeting is on 2026-10-01 at 10:30.",
        "Ticket 4821 was closed in 2024.",
        "Server 192.168.100.200 is down.",
        "I owe 1,250.75 pounds.",
        "See https://example.com/items/5551234567/details",
        "Order 12345678901234567890 shipped.",
        "",
    ],
)
def test_leaves_text_without_pii_unchanged(text: str) -> None:
    result = redact_pii(text)

    assert result.text == text
    assert not result.changed


STREAMED_TEXTS = [
    "Call me on +44 20 7946 0958 tomorrow.",
    "Ring (555) 123-4567 or +1 (555) 123-4567, or mail a.b+c@sub.example.co.uk now",
    "Ticket 4821 and 555 123 4567 and 2026-10-01 at 10:30 via 192.168.100.200",
    "me@x.org, 555-123-4567 or bob@y.com / (555) 987-6543",
    "5551234567@example.com then 020 7946 0958\n\nand https://example.com/items/5551234567/x",
    "Ends with a number 555 123 4567",
    "  leading spaces  and   gaps 1 2 3 4 5 6 7 8 9 0",
]


def stream_through(redactor: StreamingRedactor, chunks: list[str]) -> str:
    released = [redactor.redact_chunk(chunk) for chunk in chunks]
    return "".join(released) + redactor.flush()


@pytest.mark.parametrize("text", STREAMED_TEXTS)
def test_every_two_way_split_redacts_like_the_whole_text(text: str) -> None:
    expected = redact_pii(text)

    for cut in range(len(text) + 1):
        redactor = StreamingRedactor(enabled=True)
        assert stream_through(redactor, [text[:cut], text[cut:]]) == expected.text, cut
        assert redactor.changed == expected.changed


@pytest.mark.parametrize("text", STREAMED_TEXTS)
def test_character_by_character_stream_redacts_like_the_whole_text(text: str) -> None:
    redactor = StreamingRedactor(enabled=True)

    assert stream_through(redactor, list(text)) == redact_pii(text).text


def test_partial_pii_is_held_back_until_it_is_complete() -> None:
    # Given
    redactor = StreamingRedactor(enabled=True)

    # When
    released = [redactor.redact_chunk("Write to jane"), redactor.redact_chunk("@example.com "), redactor.redact_chunk("today")]

    # Then
    assert released == ["Write to ", f"{EMAIL_PLACEHOLDER} ", ""]
    assert redactor.flush() == "today"


def test_spaced_digits_are_held_until_the_number_ends() -> None:
    # Given
    redactor = StreamingRedactor(enabled=True)

    # When
    released = [redactor.redact_chunk("Ring 555 "), redactor.redact_chunk("123 "), redactor.redact_chunk("4567 now ")]

    # Then
    assert released == ["Ring ", "", f"{PHONE_PLACEHOLDER} now "]


def test_disabled_redactor_passes_chunks_through() -> None:
    # Given
    redactor = StreamingRedactor(enabled=False)

    # When
    released = [redactor.redact_chunk("a@b.com "), redactor.redact_chunk("555-123-4567")]

    # Then
    assert released == ["a@b.com ", "555-123-4567"]
    assert redactor.flush() == ""
    assert not redactor.changed
