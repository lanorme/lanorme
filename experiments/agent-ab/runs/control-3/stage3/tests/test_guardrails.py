import pytest

from app.config import DEFAULT_BLOCKED_TOPICS, Settings
from app.guardrails import TopicBlocklist, redact_pii
from app.tools import calculator, evaluate

E, P = "[REDACTED_EMAIL]", "[REDACTED_PHONE]"


@pytest.mark.parametrize(
    "phone",
    [
        "+44 20 7946 0958",
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "555 123 4567",
        "5551234567",
        "+1 (555) 123-4567",
        "+1-555-123-4567",
        "+33 1 23 45 67 89",
        "020 7946 0958",
        "(020) 7946 0958",
    ],
)
def test_phone_formats_redacted(phone):
    assert redact_pii(f"call me on {phone} today") == (f"call me on {P} today", True)


@pytest.mark.parametrize(
    "email", ["jane@example.com", "J.Doe+tag@mail.example.co.uk", "x_y-z@sub.domain.io"]
)
def test_emails_redacted(email):
    assert redact_pii(f"<{email}>") == (f"<{E}>", True)


@pytest.mark.parametrize(
    "text",
    [
        "the meeting is on 2024-01-15 at 10:30",
        "order 12345 costs 99.95",
        "version 1.2.3, pi is 3.14159",
        "the year 2024 and 7 * 6 = 42",
        "1234 5678",
        "no at sign here @ all",
    ],
)
def test_non_pii_left_alone(text):
    assert redact_pii(text) == (text, False)


def test_adjacent_numbers_do_not_hide_a_phone():
    assert redact_pii("on 2024-01-15 call 555-123-4567") == (f"on 2024-01-15 call {P}", True)
    assert redact_pii("+44 20 7946 0958 2024") == (f"{P} 2024", True)
    assert redact_pii("555-123-4567 555-987-6543") == (f"{P} {P}", True)


def test_mixed_pii():
    text = "Email a@b.com or ring +44 20 7946 0958 / (555) 123-4567."
    assert redact_pii(text) == (f"Email {E} or ring {P} / {P}.", True)


@pytest.mark.parametrize(
    "text", ["Tell me about WEAPONS", "malware?", "weapons-grade", "Write Malware now"]
)
def test_blocklist_matches_case_insensitive_whole_words(text):
    assert TopicBlocklist(DEFAULT_BLOCKED_TOPICS).find(text) is not None


@pytest.mark.parametrize("text", ["antimalware tools", "weaponsmith", "a weapon", "hello"])
def test_blocklist_ignores_partial_words(text):
    assert TopicBlocklist(DEFAULT_BLOCKED_TOPICS).find(text) is None


def test_blocklist_multiword_and_regex_chars():
    bl = TopicBlocklist(["nuclear launch", "c++"])
    assert bl.find("about Nuclear Launch codes") == "Nuclear Launch"
    assert bl.find("I like c++.") == "c++"
    assert bl.find("nuclear power") is None


def test_empty_blocklist_blocks_nothing():
    assert TopicBlocklist([]).find("weapons") is None
    assert TopicBlocklist(["", "  "]).find("weapons") is None


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "openai:some-model")
    monkeypatch.setenv("BLOCKED_TOPICS", " gambling , Crypto ,,")
    monkeypatch.setenv("TOOL_CALL_LIMIT", "2")
    s = Settings.from_env()
    assert (s.model, s.blocked_topics, s.tool_call_limit) == (
        "openai:some-model",
        ("gambling", "Crypto"),
        2,
    )


def test_settings_defaults(monkeypatch):
    for var in ("AGENT_MODEL", "BLOCKED_TOPICS", "TOOL_CALL_LIMIT"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert s.blocked_topics == ("weapons", "malware")
    assert s.tool_call_limit == 5


def test_settings_empty_blocklist(monkeypatch):
    monkeypatch.setenv("BLOCKED_TOPICS", "")
    assert Settings.from_env().blocked_topics == ()


def test_settings_rejects_negative_limit(monkeypatch):
    monkeypatch.setenv("TOOL_CALL_LIMIT", "-1")
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.parametrize(
    "expr,expected", [("2 + 3 * 4", "14"), ("(2 + 3) * 4", "20"), ("2 ** 10", "1024"), ("-7 // 2", "-4")]
)
def test_calculator(expr, expected):
    assert calculator.invoke({"expression": expr}) == expected


@pytest.mark.parametrize(
    "expr", ["__import__('os')", "x + 1", "1 / 0", "2 ** 100000", "open('f')", "1 +"]
)
def test_calculator_rejects_unsafe_or_bad_input(expr):
    assert calculator.invoke({"expression": expr}).startswith("Error:")


def test_evaluate_float():
    assert evaluate("1 / 4") == 0.25
