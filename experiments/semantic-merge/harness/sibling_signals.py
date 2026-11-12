"""Sibling evidence for a package: are its members parallel instances of one role?

A contested convention (one router per resource, one use case per module, one
value object per module) is a set of parallel siblings: each member is another
instance of the same role, built from the same parts. An over-split is the
opposite, the complementary pieces of one idea (its types, its errors, its one
function). Two measures tell them apart without reading names:

- **Fan-out similarity.** The mean pairwise Jaccard similarity of what the
  members import from outside the package, the coupling concept Beck and Diehl
  found most congruent with the package structure of Java projects after
  direct dependencies [@beck2011congruence]. Parallel siblings import the same
  framework and the same base classes.
- **Role similarity.** The mean pairwise Jaccard similarity of the kinds of
  top-level definition each member holds: a class with given bases, a function
  with given decorators, a constant. It is this spike's own exploratory
  measure, defined on the dev split; only its holdout numbers are evidence.
"""

from __future__ import annotations

import ast
from itertools import combinations

from lanorme.astnames import build_attr_chain, list_decorator_leaves
from lanorme.module_graph import ModuleGraph
from lanorme.sources import Module


def _list_fan_out(*, module: Module, package: str, graph: ModuleGraph) -> frozenset[str]:
    """What *module* imports from outside its package: in-tree files and third-party names."""
    inside = f"{package}/"
    in_tree = {
        target for target in graph.imports.get(module.relative, ()) if not target.startswith(inside)
    }
    roots = {node.dotted.split(".", 1)[0] for node in graph.nodes.values()}
    third_party = set()
    for imported in module.imports:
        if imported.level:
            continue
        names = (
            [imported.module] if imported.is_from else [alias.name for alias in imported.aliases]
        )
        third_party |= {name for name in names if name.split(".", 1)[0] not in roots}
    return frozenset(in_tree | third_party)


def _describe_definition(statement: ast.stmt) -> str | None:
    """The kind of one top-level statement: a class by its bases, a function by its decorators."""
    match statement:
        case ast.ClassDef(bases=bases):
            names = sorted(".".join(build_attr_chain(base)[-1:]) for base in bases)
            return f"class({','.join(names)})"
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return f"function({','.join(sorted(list_decorator_leaves(statement)))})"
        case ast.Assign() | ast.AnnAssign():
            return "constant"
    return None


def _list_roles(module: Module) -> frozenset[str]:
    found = (_describe_definition(statement) for statement in module.tree.body)
    return frozenset(role for role in found if role is not None)


def _compute_mean_similarity(sets: list[frozenset[str]]) -> float | None:
    scores = [
        len(first & second) / len(first | second)
        for first, second in combinations(sets, 2)
        if first | second
    ]
    return sum(scores) / len(scores) if scores else None


def measure_siblings(
    *,
    members: list[Module],
    package: str,
    graph: ModuleGraph,
) -> dict[str, float | None]:
    """The fan-out similarity and the role similarity of one package's members."""
    fan_out = [_list_fan_out(module=member, package=package, graph=graph) for member in members]
    return {
        "fan_out_similarity": _compute_mean_similarity(fan_out),
        "role_similarity": _compute_mean_similarity([_list_roles(member) for member in members]),
    }
