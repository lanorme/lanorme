"""Every Pandoc-style citation in the code and docs resolves in references.bib.

AGENTS.md asks for literature to be cited as ``[@key]`` (Pandoc citation
syntax) with a BibTeX entry in ``docs/references.bib``; this test is the
mechanical check that keeps the two in step.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BIBLIOGRAPHY = REPOSITORY_ROOT / "docs" / "references.bib"

# Where citations may appear; verbatim corpora, fixtures and agent-written
# experiment output are not ours to cite from.
CITING_GLOBS = (
    "*.md",
    "src/**/*.py",
    "docs/**/*.md",
    "evals/*.md",
    "evals/*.py",
    "experiments/*/*.md",
    "experiments/*/harness/*.py",
    ".claude/skills/*/SKILL.md",
)

# A bracketed group holding at least one "@", such as [@a; see @b, p. 3].
CITATION_GROUP = re.compile(r"\[[^\[\]]*@[^\[\]]*\]")
# Inside a group, a key follows "[", whitespace, ";" or the "-" that suppresses
# the author; the lookbehind keeps an e-mail address from reading as a key.
CITATION_KEY = re.compile(r"(?:(?<=\[)|(?<=[\s;-]))@([A-Za-z0-9_][\w:.\-]*\w)")
BIBTEX_ENTRY_KEY = re.compile(r"^@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def find_citation_keys(text: str) -> set[str]:
    """Return every key cited in Pandoc citation syntax in *text*."""
    keys: set[str] = set()
    for group in CITATION_GROUP.findall(text):
        keys.update(CITATION_KEY.findall(group))
    return keys


def read_bibliography_keys() -> set[str]:
    """Return every entry key defined in ``docs/references.bib``."""
    return set(BIBTEX_ENTRY_KEY.findall(BIBLIOGRAPHY.read_text(encoding="utf-8")))


def collect_cited_keys() -> dict[str, set[str]]:
    """Map each cited key to the repository files that cite it."""
    cited: dict[str, set[str]] = {}
    for pattern in CITING_GLOBS:
        for path in REPOSITORY_ROOT.glob(pattern):
            relative = str(path.relative_to(REPOSITORY_ROOT))
            for key in find_citation_keys(path.read_text(encoding="utf-8")):
                cited.setdefault(key, set()).add(relative)
    return cited


def test_find_citation_keys_reads_pandoc_syntax():
    text = "As argued [@ousterhout2018philosophy, ch. 4; see -@parnas1972criteria]."

    keys = find_citation_keys(text)

    assert keys == {"ousterhout2018philosophy", "parnas1972criteria"}


def test_find_citation_keys_ignores_email_addresses_and_plain_brackets():
    text = "Mail [someone@example.com] or read [the docs](https://a.b/@c)."

    keys = find_citation_keys(text)

    assert keys == set()


def test_every_cited_key_has_a_bibliography_entry():
    # Arrange
    defined = read_bibliography_keys()
    cited = collect_cited_keys()

    # Act
    missing = {key: sorted(files) for key, files in cited.items() if key not in defined}

    # Assert
    assert missing == {}, f"cited keys with no entry in docs/references.bib: {missing}"
