import pytest

from app.domain.pii import EMAIL_PLACEHOLDER, PHONE_PLACEHOLDER, redact_pii


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
