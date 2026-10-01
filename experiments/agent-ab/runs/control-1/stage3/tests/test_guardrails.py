import random

import pytest

from app.guardrails import StreamingRedactor, TopicBlocklist, redact_pii


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


# --- Streaming redaction --------------------------------------------------


def stream_redact(chunks):
    redactor = StreamingRedactor()
    out = [redactor.feed(c) for c in chunks] + [redactor.finish()]
    return out, redactor.redacted


@pytest.mark.parametrize(
    "chunks",
    [
        ["Mail jane.d", "oe@exam", "ple.com now"],
        ["Call +44 20 79", "46 0958."],
        ["Call (555) ", "123-", "4567", " today"],
        ["a@b.c", "om"],
        ["a@b.co", "m!"],
        ["555 123", "4"],
        ["year 2024-", "01-15 ok"],
        ["x", "@", "y", ".", "i", "o"],
        ["no pii ", "here at all"],
    ],
)
def test_streaming_redactor_matches_whole_text(chunks):
    out, redacted = stream_redact(chunks)
    expected, changed = redact_pii("".join(chunks))
    assert "".join(out) == expected
    assert redacted == changed


def test_streaming_redactor_emits_early_and_holds_back_possible_pii():
    redactor = StreamingRedactor()
    assert redactor.feed("Hello there, write to jane") == "Hello there, write to "
    assert redactor.feed("@example.com") == ""
    assert redactor.feed(" or call 555 123") == "[REDACTED_EMAIL] or call "
    assert redactor.feed("4567. Bye") == "[REDACTED_PHONE]. "
    assert redactor.finish() == "Bye"


def test_streaming_redactor_random_splits():
    # Text dense in PII-ish characters, cut at random points, must always redact
    # exactly as the whole text would be.
    rng = random.Random(1234)
    pieces = [
        "a", "Z", "_", "1", "2", "9", "٣", " ", "  ", "\n", " ", ".", "-", "+", "(", ")",
        "@", "%", ",", "!", "[", "jane@ex.com", "+44 20 7946 0958", "(555) 123-4567",
        "2024-01-15", "x.y@a-b.co.uk", "555.123.4567", "é",
    ]
    for _ in range(3000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
        cuts = sorted(rng.sample(range(len(text) + 1), k=min(len(text) + 1, rng.randint(0, 6))))
        chunks = [text[i:j] for i, j in zip([0, *cuts], [*cuts, len(text)])]
        out, redacted = stream_redact(chunks)
        assert "".join(out) == redact_pii(text)[0], chunks
        assert redacted == redact_pii(text)[1], chunks
