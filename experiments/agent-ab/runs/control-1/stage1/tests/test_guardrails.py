import pytest

from app.guardrails import TopicBlocklist, redact_pii


@pytest.mark.parametrize(
    "text",
    [
        "+44 20 7946 0958",
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "+1-800-555-0199",
        "+1 (555) 123-4567",
        "07700 900123",
        "5551234567",
    ],
)
def test_phone_formats_are_redacted(text):
    redacted, changed = redact_pii(f"call me on {text} please")
    assert changed
    assert redacted == "call me on [REDACTED_PHONE] please"


@pytest.mark.parametrize(
    "text",
    ["jane.doe@example.com", "a.b+tag@mail.example.co.uk", "X_Y@sub-domain.io"],
)
def test_emails_are_redacted(text):
    redacted, changed = redact_pii(f"write to {text}.")
    assert changed
    assert redacted == "write to [REDACTED_EMAIL]."


@pytest.mark.parametrize(
    "text",
    [
        "What is 12 * 34?",
        "It costs $1,250.00",
        "Meet on 2024-01-15 at 10:30",
        "version 1.2.3",
        "the year 1999",
        "ip 192.168.1.1",
        "12345 * 6789",
    ],
)
def test_non_pii_is_left_alone(text):
    assert redact_pii(text) == (text, False)


def test_mixed_pii():
    redacted, changed = redact_pii("Email bob@x.org or ring (555) 123-4567.")
    assert changed
    assert redacted == "Email [REDACTED_EMAIL] or ring [REDACTED_PHONE]."


@pytest.mark.parametrize(
    "text, blocked",
    [
        ("Tell me about WEAPONS", True),
        ("is this malware?", True),
        ("Malware.", True),
        ("antimalware tools", False),
        ("weaponsmith history", False),
        ("weapon", False),
        ("hello", False),
    ],
)
def test_blocklist_whole_word_case_insensitive(text, blocked):
    assert TopicBlocklist(["weapons", "malware"]).matches(text) is blocked


def test_blocklist_multiword_and_empty():
    assert TopicBlocklist(["dark web"]).matches("browsing the Dark Web today")
    assert not TopicBlocklist([]).matches("weapons")
    assert not TopicBlocklist(["  ", ""]).matches("anything")
