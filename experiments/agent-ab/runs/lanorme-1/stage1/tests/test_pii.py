import pytest

from app.guardrails.pii import redact_pii


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
