"""Vocabulary evidence for a package: do its members talk about the same things?

Semantic clustering groups source files by the vocabulary of their identifiers
and comments [@kuhn2007semantic], and the conceptual cohesion of a class is the
mean textual similarity of its methods [@marcus2005conceptual]. Developers'
own judgement of which classes are coupled agrees with such a semantic measure
better than with structural, dynamic or logical coupling
[@bavota2013perception]. This module computes the same kind of measure for the
members of a package, with the standard library only:

- **Terms.** Every identifier a module defines or uses, split on underscores
  and capital letters and lower-cased; language keywords, builtins and
  one-letter words are dropped. The prose variant adds the words of
  docstrings and comments.
- **Weights.** Term frequency times inverse document frequency over the
  production modules of the tree [@salton1988term], so a word every module of
  a project uses counts for little.
- **Cohesion.** The mean pairwise cosine similarity of the members, as the
  conceptual cohesion of classes does over methods. The lift subtracts the
  mean similarity between members and a seeded sample of modules outside the
  package, so a project whose every file shares boilerplate is not read as
  cohesive.

Latent semantic indexing, which the cited works apply before the cosine, needs
a singular value decomposition the standard library does not have; the plain
weighted vector space is what a standard-library-only check could ship. It
is no handicap: against the package structure of 16 Java projects the indexed
variant fell consistently below the plain weighted one [@beck2011congruence].
"""

from __future__ import annotations

import ast
import builtins
import keyword
import math
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from lanorme.paths import is_test_file
from lanorme.sources import Module

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
_PROSE_WORD = re.compile(r"[A-Za-z]{3,}")
_STOP_WORDS = frozenset(
    {word.lower() for word in keyword.kwlist + keyword.softkwlist}
    | {word.lower() for name in dir(builtins) for word in _WORD.findall(name)}
    | {"self", "cls", "args", "kwargs"},
)
_PROSE_STOP_WORDS = frozenset(
    "the and for that this with from not are was but its has have will can may one all any "
    "when where which what into than then them they their there here been being does each "
    "only also such see use used uses using return returns given".split(),
)
BACKGROUND_SAMPLE = 200
_SEED = 20261112


def split_identifier(name: str) -> list[str]:
    """The lower-cased words of an identifier: ``HTTPServer_id2`` gives http, server, id."""
    return [word.lower() for word in _WORD.findall(name) if len(word) > 1]


def _list_identifiers(module: Module) -> list[str]:
    """Every identifier *module* defines or uses, one entry per occurrence."""
    found: list[str] = []
    nodes = module.index.collect(
        ast.Name,
        ast.Attribute,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.arg,
        ast.keyword,
    )
    for node in nodes:
        match node:
            case ast.Name(id=name) | ast.Attribute(attr=name) | ast.arg(arg=name):
                found.append(name)
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                found.append(name)
            case ast.ClassDef(name=name):
                found.append(name)
            case ast.keyword(arg=str() as name):
                found.append(name)
    return found


def count_terms(module: Module, *, with_prose: bool) -> Counter[str]:
    """How often each vocabulary term occurs in *module*."""
    terms: Counter[str] = Counter()
    for name in _list_identifiers(module):
        terms.update(word for word in split_identifier(name) if word not in _STOP_WORDS)
    if with_prose:
        prose = [docstring.text for docstring in module.docstrings]
        prose.extend(comment.text for comment in module.comments)
        for text in prose:
            terms.update(
                word.lower()
                for word in _PROSE_WORD.findall(text)
                if word.lower() not in _PROSE_STOP_WORDS and word.lower() not in _STOP_WORDS
            )
    return terms


@dataclass
class Vocabulary:
    """The weighted term vectors of a tree's production modules."""

    vectors: dict[str, dict[str, float]]
    terms: dict[str, frozenset[str]]
    norms: dict[str, float] = field(default_factory=dict)

    def measure_cosine(self, *, first: str, second: str) -> float:
        """The cosine similarity of two modules' weighted term vectors (0 when one is empty)."""
        left, right = self.vectors[first], self.vectors[second]
        if len(left) > len(right):
            left, right = right, left
        product = sum(weight * right.get(term, 0.0) for term, weight in left.items())
        scale = self._read_norm(first) * self._read_norm(second)
        return product / scale if scale else 0.0

    def measure_overlap(self, *, first: str, second: str) -> float:
        """The Jaccard similarity of two modules' unweighted term sets."""
        union = self.terms[first] | self.terms[second]
        return len(self.terms[first] & self.terms[second]) / len(union) if union else 0.0

    def _read_norm(self, relative: str) -> float:
        if relative not in self.norms:
            total = sum(weight * weight for weight in self.vectors[relative].values())
            self.norms[relative] = math.sqrt(total)
        return self.norms[relative]


def build_vocabulary(*, modules: list[Module], with_prose: bool) -> Vocabulary:
    """Weight every production module's terms by frequency and rarity across the tree."""
    counts = {
        module.relative: count_terms(module, with_prose=with_prose)
        for module in modules
        if not is_test_file(module.relative)
    }
    document_frequency: Counter[str] = Counter()
    for terms in counts.values():
        document_frequency.update(terms.keys())
    total = len(counts)
    vectors = {
        relative: {
            term: (1 + math.log(count)) * math.log(total / document_frequency[term])
            for term, count in terms.items()
        }
        for relative, terms in counts.items()
    }
    return Vocabulary(
        vectors=vectors,
        terms={relative: frozenset(terms) for relative, terms in counts.items()},
    )


def _compute_mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def measure_vocabulary(
    *,
    vocabulary: Vocabulary,
    members: list[str],
    package: str,
) -> dict[str, float | None]:
    """The vocabulary cohesion of *members*, its lift over the tree, and the raw term overlap."""
    present = [member for member in members if member in vocabulary.vectors]
    pairs = list(combinations(present, 2))
    cohesion = _compute_mean(
        [vocabulary.measure_cosine(first=first, second=second) for first, second in pairs],
    )
    outside = sorted(
        relative for relative in vocabulary.vectors if not relative.startswith(f"{package}/")
    )
    sample = random.Random(_SEED).sample(outside, min(BACKGROUND_SAMPLE, len(outside)))
    background = _compute_mean(
        [
            vocabulary.measure_cosine(first=member, second=other)
            for member in present
            for other in sample
        ],
    )
    lift = None if cohesion is None or background is None else cohesion - background
    return {
        "cohesion": cohesion,
        "lift": lift,
        "overlap": _compute_mean(
            [vocabulary.measure_overlap(first=first, second=second) for first, second in pairs],
        ),
    }
