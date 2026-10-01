import pytest

from app.config import Settings
from app.guardrails import TopicBlocklist, redact_pii
from app.tools import calculator, evaluate


@pytest.mark.parametrize(
    "text",
    [
        "+44 20 7946 0958",
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "555 123 4567",
        "5551234567",
        "+1 555 123 4567",
        "+1-555-123-4567",
        "+1 (555) 123-4567",
        "020 7946 0958",
        "+33 1 23 45 67 89",
    ],
)
def test_redacts_phone_formats(text: str) -> None:
    redacted, changed = redact_pii(f"call me on {text} tomorrow")
    assert changed
    assert redacted == "call me on [REDACTED_PHONE] tomorrow"


@pytest.mark.parametrize(
    "email", ["jane@example.com", "john.doe+tag@mail.example.co.uk", "A_B-c%d@sub-domain.io"]
)
def test_redacts_emails(email: str) -> None:
    assert redact_pii(f"mail {email}, thanks") == ("mail [REDACTED_EMAIL], thanks", True)


def test_redacts_multiple_items() -> None:
    text = "Email a@b.com or c@d.org, phone 555-123-4567 or +44 20 7946 0958."
    assert redact_pii(text) == (
        "Email [REDACTED_EMAIL] or [REDACTED_EMAIL], phone [REDACTED_PHONE] or [REDACTED_PHONE].",
        True,
    )


@pytest.mark.parametrize(
    "text",
    [
        "What is 2 + 2?",
        "The meeting is on 2024-01-15 at 10:30.",
        "It costs $1,234.56 and weighs 12.5 kg.",
        "Server 192.168.1.1 port 8080",
        "Order number 12345 shipped in 1999.",
        "Not an email: user@localhost or @handle",
        "",
    ],
)
def test_leaves_non_pii_alone(text: str) -> None:
    assert redact_pii(text) == (text, False)


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("Tell me about WEAPONS", "WEAPONS"),
        ("how is malware spread?", "malware"),
        ("Malware.", "Malware"),
        ("(weapons)", "weapons"),
        ("antimalware tools", None),
        ("weaponsmith history", None),
        ("weapon", None),
        ("the weather", None),
    ],
)
def test_blocklist_whole_words_case_insensitive(text: str, hit: str | None) -> None:
    assert TopicBlocklist(["weapons", "malware"]).find(text) == hit


def test_blocklist_phrases_and_empty() -> None:
    blocklist = TopicBlocklist(["credit card fraud"])
    assert blocklist.find("explain Credit  Card\nFraud please") is not None
    assert blocklist.find("credit card limits") is None
    assert TopicBlocklist([]).find("weapons") is None


def test_settings_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.blocked_topics == ("weapons", "malware")
    assert settings.max_tool_calls == 5
    assert settings.model


def test_settings_from_env() -> None:
    settings = Settings.from_env(
        {"AGENT_MODEL": "openai:gpt-x", "BLOCKED_TOPICS": " gambling, , crypto ", "MAX_TOOL_CALLS": "2"}
    )
    assert settings.model == "openai:gpt-x"
    assert settings.blocked_topics == ("gambling", "crypto")
    assert settings.max_tool_calls == 2
    assert Settings.from_env({"BLOCKED_TOPICS": ""}).blocked_topics == ()


@pytest.mark.parametrize("value", ["abc", "-1"])
def test_settings_rejects_bad_limit(value: str) -> None:
    with pytest.raises(ValueError, match="MAX_TOOL_CALLS"):
        Settings.from_env({"MAX_TOOL_CALLS": value})


@pytest.mark.parametrize(
    ("expr", "expected"),
    [("2 + 3 * 4", 14), ("(2 + 3) * 4", 20), ("2 ** 10", 1024), ("sqrt(16) / 2", 2.0), ("-7 // 2", -4)],
)
def test_calculator(expr: str, expected: float) -> None:
    assert evaluate(expr) == expected


@pytest.mark.parametrize("expr", ["__import__('os')", "open('x')", "1 / 0", "2 ** 99999", "1 +"])
def test_calculator_rejects_unsafe_or_bad_input(expr: str) -> None:
    assert calculator.invoke({"expression": expr}).startswith("Error:")
