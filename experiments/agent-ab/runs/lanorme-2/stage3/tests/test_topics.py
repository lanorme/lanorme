import pytest

from app.domain.topics import TopicBlocklist

DEFAULT_TOPICS = ("weapons", "malware")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tell me about WEAPONS", "WEAPONS"),
        ("how is malware made?", "malware"),
        ("Malware, weapons and more", "Malware"),
    ],
)
def test_finds_blocked_topics_case_insensitively(text: str, expected: str) -> None:
    blocklist = TopicBlocklist(topics=DEFAULT_TOPICS)

    assert blocklist.find_topic(text) == expected


@pytest.mark.parametrize(
    "text", ["antimalware tools", "malwares", "a weapon", "weaponsmith", "the weather"]
)
def test_matches_whole_words_only(text: str) -> None:
    blocklist = TopicBlocklist(topics=DEFAULT_TOPICS)

    assert blocklist.find_topic(text) is None


def test_multi_word_topic_matches_any_whitespace() -> None:
    blocklist = TopicBlocklist(topics=["tax  evasion"])

    assert blocklist.find_topic("tips on Tax\nEvasion please") == "Tax\nEvasion"


def test_topic_with_symbols_is_escaped() -> None:
    blocklist = TopicBlocklist(topics=["c++"])

    assert blocklist.find_topic("I like C++ a lot") == "C++"
    assert blocklist.find_topic("ccc") is None


def test_empty_blocklist_blocks_nothing() -> None:
    blocklist = TopicBlocklist(topics=["", "  "])

    assert blocklist.find_topic("weapons") is None
