"""Pairs of top-level definitions in one package, and what joins them.

Whether two modules should be one is a question raters do not agree on. What
an author did is not in dispute: every module is a set of top-level
definitions (functions, classes, constants) the author kept together, and
every boundary between sibling modules is one the author drew. So for any two
definitions of one package, "same module or not" is ground truth by
construction. A module split mechanically, one definition per file, is a
package whose every cross-module pair was a same-module pair: a detector that
recognises same-module pairs recognises that over-split.

The features of a pair do not depend on where the two definitions sit, only
on what they are and how they are used, so the file layout cannot leak the
answer:

- **Reference.** One definition names the other in its body or signature.
- **Shared referrers.** The Jaccard similarity of the package's other
  definitions that name each.
- **Client co-use.** The Jaccard similarity of the client scopes (functions
  and module bodies outside the package) that name each: the Common Reuse
  Principle at the granularity of a name and a function
  [@martin2002agile, ch. 20].
- **Vocabulary.** The cosine similarity of the two definitions' identifier
  terms, weighted over the tree's definitions [@salton1988term].
- **Fan-out.** The Jaccard similarity of the imported names each uses.
- **Kind.** Whether both are classes, functions or constants, whether two
  classes share a base, and how many words the two names share.

Moving a method or a class to where its collaborators are is the reasoning of
Move Method and Extract Class recommenders
[@bavota2013modularization]; here the placement authors chose is the label.
"""

from __future__ import annotations

import ast
import math
from collections import Counter
from collections.abc import Hashable, Set
from dataclasses import dataclass, field

from boundary_signals import Boundaries, ClientUse
from member_surfaces import is_dunder, list_defined_names
from vocabulary_signals import split_identifier

from lanorme.astnames import build_attr_chain
from lanorme.sources import Module

Key = tuple[str, str]
FEATURES = (
    "reference",
    "shared_referrers",
    "client_co_use",
    "has_clients",
    "vocabulary",
    "fan_out",
    "same_kind",
    "shared_base",
    "name_overlap",
)
_KINDS = {ast.ClassDef: "class", ast.FunctionDef: "function", ast.AsyncFunctionDef: "function"}


@dataclass(frozen=True)
class Definition:
    """One top-level definition: what it is, what it names and the words it uses."""

    module: str
    name: str
    kind: str
    bases: frozenset[str]
    names_used: frozenset[str]
    attributes_used: frozenset[tuple[str, str]]
    terms: Counter[str]
    lines: int

    @property
    def key(self) -> Key:
        """The definition's identity: its module and its name."""
        return (self.module, self.name)


def _read_statement(*, module: Module, statement: ast.stmt, name: str) -> Definition:
    names: set[str] = set()
    attributes: set[tuple[str, str]] = set()
    terms: Counter[str] = Counter()
    for node in ast.walk(statement):
        match node:
            case ast.Name(id=found):
                names.add(found)
                terms.update(split_identifier(found))
            case ast.Attribute(value=ast.Name(id=base), attr=attribute):
                attributes.add((base, attribute))
                terms.update(split_identifier(attribute))
            case ast.Attribute(attr=attribute) | ast.arg(arg=attribute):
                terms.update(split_identifier(attribute))
            case ast.FunctionDef(name=found) | ast.AsyncFunctionDef(name=found):
                terms.update(split_identifier(found))
            case ast.ClassDef(name=found):
                terms.update(split_identifier(found))
    bases = getattr(statement, "bases", [])
    return Definition(
        module=module.relative,
        name=name,
        kind=_KINDS.get(type(statement), "constant"),
        bases=frozenset(".".join(build_attr_chain(base)[-1:]) for base in bases),
        names_used=frozenset(names - {name}),
        attributes_used=frozenset(attributes),
        terms=terms,
        lines=(statement.end_lineno or statement.lineno) - statement.lineno + 1,
    )


def read_definitions(module: Module) -> list[Definition]:
    """Every top-level definition of *module*, dunder names left out."""
    return [
        _read_statement(module=module, statement=statement, name=name)
        for statement in module.tree.body
        for name in list_defined_names(statement)
        if not is_dunder(name)
    ]


def _map_external_imports(*, module: Module, own: ClientUse | None) -> dict[str, str]:
    """Each local name *module* binds by an import that is not of a sibling member."""
    siblings = own.local_names if own is not None else {}
    found: dict[str, str] = {}
    for imported in module.imports:
        for alias in imported.aliases:
            local = alias.asname or alias.name.split(".", 1)[0]
            if local in siblings:
                continue
            prefix = "." * imported.level + imported.module
            found[local] = f"{prefix}.{alias.name}" if imported.is_from else alias.name
    return found


def _list_targets(*, names: set[str], attributes: set[tuple[str, str]], use: ClientUse) -> set[Key]:
    """The members' definitions a piece of client code names, by name or through a namespace."""
    targets = {(use.local_names[name], use.origins[name]) for name in names if name in use.origins}
    targets |= {
        (use.local_names[base], attribute)
        for base, attribute in attributes
        if use.local_names.get(base) in use.namespaces
    }
    return targets


@dataclass
class PackageDefinitions:
    """A package's definitions, the references among them and the client scopes naming them."""

    definitions: list[Definition]
    references: set[tuple[Key, Key]] = field(default_factory=set)
    referrers: dict[Key, set[Key]] = field(default_factory=dict)
    scopes: dict[Key, set[str]] = field(default_factory=dict)
    fan_out: dict[Key, frozenset[str]] = field(default_factory=dict)

    def add_reference(self, *, source: Key, target: Key) -> None:
        """Note that the definition *source* names the definition *target*."""
        if source != target:
            self.references.add((source, target))
            self.referrers.setdefault(target, set()).add(source)


def _collect_references(*, found: PackageDefinitions, boundaries: Boundaries) -> None:
    """Fill the references among the package's definitions and each one's imported names."""
    known = {definition.key for definition in found.definitions}
    for definition in found.definitions:
        module = boundaries.view.modules[definition.module]
        own = boundaries.siblings.get(definition.module)
        targets = {(definition.module, name) for name in definition.names_used}
        if own is not None:
            targets |= _list_targets(
                names=set(definition.names_used),
                attributes=set(definition.attributes_used),
                use=own,
            )
        for target in targets & known:
            found.add_reference(source=definition.key, target=target)
        external = _map_external_imports(module=module, own=own)
        found.fan_out[definition.key] = frozenset(
            external[name] for name in definition.names_used if name in external
        )


def _list_scope_nodes(client: Module) -> list[tuple[str, ast.AST]]:
    """The scopes of a client: each function, and the module body as one more."""
    scopes: list[tuple[str, ast.AST]] = [
        (f"{client.relative}:{function.lineno}", function) for function in client.index.functions
    ]
    scopes.append((client.relative, client.tree))
    return scopes


def _collect_scopes(*, found: PackageDefinitions, boundaries: Boundaries) -> None:
    """Fill, for each definition, the client scopes outside the package that name it."""
    known = {definition.key for definition in found.definitions}
    for client, use in boundaries.outside.items():
        for scope, node in _list_scope_nodes(boundaries.view.modules[client]):
            names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
            attributes = {
                (child.value.id, child.attr)
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
            }
            for target in _list_targets(names=names, attributes=attributes, use=use) & known:
                found.scopes.setdefault(target, set()).add(scope)


def read_package_definitions(
    boundaries: Boundaries,
    *,
    by_module: dict[str, list[Definition]],
) -> PackageDefinitions:
    """The definitions of a package's members with their references and client scopes.

    *by_module* holds every module's definitions, read once for the tree.
    """
    definitions = [
        definition for surface in boundaries.surfaces for definition in by_module[surface.relative]
    ]
    found = PackageDefinitions(definitions=definitions)
    _collect_references(found=found, boundaries=boundaries)
    _collect_scopes(found=found, boundaries=boundaries)
    return found


def _measure_jaccard(*, first: Set[Hashable], second: Set[Hashable]) -> float:
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def measure_cosine(*, first: Counter[str], second: Counter[str], rarity: dict[str, float]) -> float:
    """The cosine similarity of two term counts, each term weighted by its rarity."""
    left = {term: (1 + math.log(count)) * rarity.get(term, 0.0) for term, count in first.items()}
    right = {term: (1 + math.log(count)) * rarity.get(term, 0.0) for term, count in second.items()}
    product = sum(weight * right.get(term, 0.0) for term, weight in left.items())
    scale = math.sqrt(sum(weight * weight for weight in left.values())) * math.sqrt(
        sum(weight * weight for weight in right.values()),
    )
    return product / scale if scale else 0.0


def measure_pair(
    *,
    found: PackageDefinitions,
    first: Definition,
    second: Definition,
    rarity: dict[str, float],
) -> dict[str, float]:
    """The layout-invariant features of two definitions of one package."""
    keys = (first.key, second.key)
    scopes = [found.scopes.get(key, set()) for key in keys]
    referrers = [found.referrers.get(key, set()) - set(keys) for key in keys]
    words = [set(split_identifier(definition.name)) for definition in (first, second)]
    is_referenced = (keys[0], keys[1]) in found.references or (keys[1], keys[0]) in found.references
    return {
        "reference": float(is_referenced),
        "shared_referrers": _measure_jaccard(first=referrers[0], second=referrers[1]),
        "client_co_use": _measure_jaccard(first=scopes[0], second=scopes[1]),
        "has_clients": float(bool(scopes[0] or scopes[1])),
        "vocabulary": measure_cosine(first=first.terms, second=second.terms, rarity=rarity),
        "fan_out": _measure_jaccard(first=found.fan_out[keys[0]], second=found.fan_out[keys[1]]),
        "same_kind": float(first.kind == second.kind),
        "shared_base": float(bool(first.bases & second.bases)),
        "name_overlap": _measure_jaccard(first=words[0], second=words[1]),
    }
