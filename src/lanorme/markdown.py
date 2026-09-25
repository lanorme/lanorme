"""The prose surface of a Markdown file, and what prose rules match against.

The prose and docs checks both read Markdown and must agree on which lines
are prose: a fenced code block is not, YAML front matter is not, and an
inline code span is not. Reading that surface here, once, keeps the two from
drifting apart on it, and keeps a fence subtlety in one place: a fence closes
only on a run of the same character at least as long as the one that opened
it, so a four-backtick fence can hold a three-backtick line without ending
early. The emoji pattern lives here too, because the comments check applies
the same PROSE rules to Python comments and must flag the same characters.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

# A code span is a run of backticks, then content, then a matching run of the
# same length (CommonMark). The backreference is what makes double-backtick
# spans like ``color`` strip correctly; a single-backtick pattern would only
# blank the delimiter pairs and leave the content exposed to the scanner.
_INLINE_CODE = re.compile(r"(`+).*?\1")

# A fence opener: three or more backticks or tildes, then an optional info
# string. A closer is a run of the same character at least as long, alone.
_FENCE = re.compile(r"^(`{3,}|~{3,})")

# A URL with a scheme, up to the next whitespace. Not prose in any language.
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)

# The code points Unicode gives the Emoji property, plus the emoji presentation
# selector that turns a text symbol into one. Whole blocks would be simpler,
# but the symbol blocks are mostly typography: a check mark (U+2713), a ballot
# box, a musical note or a die is not an emoji, and flagging one cries wolf.
# Left out on purpose: the black arrows and the gender signs, which carry the
# property but read as plain symbols in prose and diagrams.
EMOJI_RE = re.compile(
    "["
    "⌚⌛⏩-⏬⏰⏳◽◾"
    "☀-☄☎☑☔☕☘☝☠☢☣☦"
    "☪☮☯☸-☺♈-♓♟♠♣♥♦"
    "♨♻♾♿⚒-⚗⚙⚛⚜⚠⚡⚧"
    "⚪⚫⚰⚱⚽⚾⛄⛅⛈⛎⛏⛑"
    "⛓⛔⛩⛪⛰-⛵⛷-⛺⛽"
    "✂✅✈-✍✏✒✔✖✝✡✨✳"
    "✴❄❇❌❎❓-❕❗❣❤➕-➗"
    "➰➿⬛⬜⭐⭕"
    "\U0001f004\U0001f0cf\U0001f18e\U0001f191-\U0001f19a\U0001f1e6-\U0001f1ff"
    "\U0001f201\U0001f21a\U0001f22f\U0001f232-\U0001f23a\U0001f250\U0001f251"
    "\U0001f300-\U0001faff"
    "️"
    "]",
)

# The delimiters that open and close a YAML front matter block.
_FRONT_MATTER_OPEN = "---"
_FRONT_MATTER_CLOSE = ("---", "...")


@dataclass(frozen=True)
class _Fence:
    """An open fenced code block: the character and run length that opened it."""

    char: str
    length: int


def strip_inline_code(line: str) -> str:
    """Blank out inline `code` spans, preserving length for column fidelity."""
    return _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)


def _match_fence_opener(stripped: str) -> _Fence | None:
    match = _FENCE.match(stripped)
    if match is None:
        return None
    run = match.group(1)
    return _Fence(char=run[0], length=len(run))


def _is_fence_closer(*, fence: _Fence, stripped: str) -> bool:
    body = stripped.rstrip()
    return len(body) >= fence.length and body == fence.char * len(body)


def _measure_front_matter(lines: list[str]) -> int:
    """Number of leading lines taken by a YAML front matter block, or 0."""
    if not lines or lines[0].strip() != _FRONT_MATTER_OPEN:
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() in _FRONT_MATTER_CLOSE:
            return index + 1
    return 0


def iter_prose_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, raw_line)`` for every line outside fences and front matter.

    Line numbers are 1-based. The fence delimiter lines themselves are not
    prose and are not yielded either.
    """
    fence: _Fence | None = None
    for index in range(_measure_front_matter(lines), len(lines)):
        raw = lines[index]
        stripped = raw.lstrip()
        if fence is None:
            fence = _match_fence_opener(stripped)
            if fence is None:
                yield index + 1, raw
        elif _is_fence_closer(fence=fence, stripped=stripped):
            fence = None
