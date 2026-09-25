"""Label-preserving and label-breaking edits of a function, for generated evals.

Each transform copies a seed function and changes the copy in one controlled
way. The transform, not any rule, decides the label of the resulting pair:

- label-preserving (the pair is still a duplicate): rename every local
  identifier, swap two adjacent independent statements, change the string
  literals;
- label-breaking (the pair is no longer a duplicate): flip one operator, wrap
  an assignment in a new branch, change one called name.

Every choice comes from the ``random.Random`` passed in, so a fixed seed gives
the same edit on every run.
"""

from __future__ import annotations

import ast
import copy
import random
from collections.abc import Callable, Iterator

PRESERVING = ("rename_identifiers", "reorder_statements", "change_string_literals")
BREAKING = ("flip_operator", "add_branch", "change_called_name")

_WORDS = (
    "alder", "amber", "basil", "birch", "cedar", "clover", "dune", "elm", "ember",
    "fennel", "fjord", "garnet", "grove", "hazel", "heron", "indigo", "iris", "jade",
    "juniper", "kelp", "kestrel", "larch", "lumen", "maple", "moss", "nectar", "nettle",
    "olive", "onyx", "pebble", "poppy", "quartz", "quill", "reed", "rowan", "sable",
    "sorrel", "tansy", "thistle", "umber", "upland", "vale", "violet", "willow", "wren",
    "yarrow", "zephyr",
)  # fmt: skip
_SHORT_STRINGS = ("|", ";", "~", "^", "!", "&", "$")
_COMPARE_FLIPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.Gt, ast.Gt: ast.Lt, ast.LtE: ast.GtE, ast.GtE: ast.LtE,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}  # fmt: skip
_BINARY_FLIPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
}  # fmt: skip
_NAME_SWAPS = {
    "min": "max", "max": "min", "any": "all", "all": "any", "sorted": "reversed",
    "sum": "len", "len": "sum", "int": "float", "float": "int", "str": "repr",
    "list": "tuple", "tuple": "list", "isinstance": "issubclass",
}  # fmt: skip
_METHOD_SWAPS = {
    "append": "remove", "add": "discard", "get": "pop", "pop": "get",
    "startswith": "endswith", "endswith": "startswith", "lower": "upper",
    "upper": "lower", "lstrip": "rstrip", "rstrip": "lstrip", "strip": "lstrip",
    "split": "rsplit", "find": "rfind", "read": "readline", "write": "writelines",
    "items": "values", "keys": "values", "encode": "decode", "isascii": "isalpha",
}  # fmt: skip
_ASSIGNMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign)

_Function = ast.FunctionDef


def strip_docstring(*, function: _Function) -> _Function:
    """Return a copy of *function* without its docstring."""
    stripped = copy.deepcopy(function)
    first = stripped.body[0] if stripped.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if isinstance(first.value.value, str):
            stripped.body = stripped.body[1:]
    return stripped


def apply_transform(*, function: _Function, transform: str, rng: random.Random) -> _Function | None:
    """Return the edited copy of *function*, or None if the transform has no site."""
    variant = copy.deepcopy(function)
    variant.name = f"{function.name}_variant"
    edited = _TRANSFORMS[transform](function=variant, rng=rng)
    return None if edited is None else ast.fix_missing_locations(edited)


def collect_local_names(*, function: _Function) -> list[str]:
    """Return the parameters and every name the function binds, sorted."""
    arguments = function.args
    params = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    params.extend(arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None)
    names = {arg.arg for arg in params}
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return sorted(names)


def rename_identifiers(*, function: _Function, rng: random.Random) -> _Function | None:
    """Rename every parameter and local variable consistently."""
    names = collect_local_names(function=function)
    if not names or len(names) > len(_WORDS):
        return None
    mapping = dict(zip(names, rng.sample(_WORDS, len(names)), strict=True))
    for node in ast.walk(function):
        rename_node(node=node, mapping=mapping)
    return function


def rename_node(*, node: ast.AST, mapping: dict[str, str]) -> None:
    """Rename the identifier *node* binds or reads, if *mapping* covers it."""
    if isinstance(node, ast.Name):
        node.id = mapping.get(node.id, node.id)
    elif isinstance(node, ast.arg):
        node.arg = mapping.get(node.arg, node.arg)
    elif isinstance(node, ast.ExceptHandler) and node.name:
        node.name = mapping.get(node.name, node.name)


def iter_bodies(*, function: _Function) -> Iterator[list[ast.stmt]]:
    """Yield every statement list inside *function*, its own body first."""
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                yield block


def collect_reads(*, statement: ast.stmt) -> set[str]:
    """Return the names a statement reads, including the bases it mutates."""
    return {
        node.id
        for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def collect_writes(*, statement: ast.stmt) -> set[str]:
    """Return the names a statement binds or mutates through a target."""
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    written: set[str] = set()
    for target in targets:
        written.update(node.id for node in ast.walk(target) if isinstance(node, ast.Name))
    return written


def has_call(*, statement: ast.stmt) -> bool:
    """True when the statement contains a call, which may have side effects."""
    return any(isinstance(node, ast.Call) for node in ast.walk(statement))


def is_independent(*, first: ast.stmt, second: ast.stmt) -> bool:
    """True when two assignments can swap without changing what either computes.

    Neither may read or write what the other writes, and at most one may call
    anything; the other then must not touch any name the calling one touches.
    """
    if not isinstance(first, _ASSIGNMENTS) or not isinstance(second, _ASSIGNMENTS):
        return False
    reads_a, writes_a = collect_reads(statement=first), collect_writes(statement=first)
    reads_b, writes_b = collect_reads(statement=second), collect_writes(statement=second)
    if writes_a & (reads_b | writes_b) or writes_b & reads_a:
        return False
    calls_a, calls_b = has_call(statement=first), has_call(statement=second)
    if calls_a and calls_b:
        return False
    if calls_a or calls_b:
        return not (reads_a | writes_a) & (reads_b | writes_b)
    return True


def reorder_statements(*, function: _Function, rng: random.Random) -> _Function | None:
    """Swap one pair of adjacent independent assignments."""
    sites = [
        (block, index)
        for block in iter_bodies(function=function)
        for index in range(len(block) - 1)
        if is_independent(first=block[index], second=block[index + 1])
    ]
    if not sites:
        return None
    block, index = rng.choice(sites)
    block[index], block[index + 1] = block[index + 1], block[index]
    return function


def change_string_literals(*, function: _Function, rng: random.Random) -> _Function | None:
    """Replace the content of every non-empty string literal."""
    literals = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value
    ]
    if not literals:
        return None
    mapping: dict[str, str] = {}
    for value in sorted({node.value for node in literals}):
        pool = _SHORT_STRINGS if len(value) <= 2 else _WORDS
        mapping[value] = rng.choice([word for word in pool if word != value])
    for node in literals:
        node.value = mapping[node.value]
    return function


def flip_operator(*, function: _Function, rng: random.Random) -> _Function | None:
    """Flip one comparison, arithmetic or boolean operator to its opposite."""
    sites: list[Callable[[], None]] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Compare):
            sites.extend(
                build_compare_flip(node=node, index=index)
                for index, op in enumerate(node.ops)
                if type(op) in _COMPARE_FLIPS
            )
        elif isinstance(node, (ast.BinOp, ast.AugAssign)) and type(node.op) in _BINARY_FLIPS:
            sites.append(build_binary_flip(node=node))
        elif isinstance(node, ast.BoolOp):
            sites.append(build_boolean_flip(node=node))
    if not sites:
        return None
    rng.choice(sites)()
    return function


def build_compare_flip(*, node: ast.Compare, index: int) -> Callable[[], None]:
    """Return the edit that flips one comparison operator."""

    def flip() -> None:
        node.ops[index] = _COMPARE_FLIPS[type(node.ops[index])]()

    return flip


def build_binary_flip(*, node: ast.BinOp | ast.AugAssign) -> Callable[[], None]:
    """Return the edit that flips an arithmetic operator."""

    def flip() -> None:
        node.op = _BINARY_FLIPS[type(node.op)]()

    return flip


def build_boolean_flip(*, node: ast.BoolOp) -> Callable[[], None]:
    """Return the edit that turns ``and`` into ``or`` or back."""

    def flip() -> None:
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()

    return flip


def add_branch(*, function: _Function, rng: random.Random) -> _Function | None:
    """Wrap one top-level assignment in a branch on the first parameter.

    ``x = f()`` becomes ``if first: x = f()`` with ``else: x = None``, so the
    value now depends on a condition the seed never tested.
    """
    arguments = [*function.args.posonlyargs, *function.args.args]
    sites = [
        index
        for index, statement in enumerate(function.body[:-1])
        if isinstance(statement, ast.Assign)
        and all(isinstance(target, ast.Name) for target in statement.targets)
    ]
    if not arguments or not sites:
        return None
    index = rng.choice(sites)
    original = function.body[index]
    fallback = ast.Assign(targets=copy.deepcopy(original.targets), value=ast.Constant(value=None))
    guard = ast.Name(id=arguments[0].arg, ctx=ast.Load())
    function.body[index] = ast.If(test=guard, body=[original], orelse=[fallback])
    return function


def change_called_name(*, function: _Function, rng: random.Random) -> _Function | None:
    """Call a different function or method at one call site."""
    calls = collect_external_calls(function=function)
    if not calls:
        return None
    swappable = [call for call in calls if read_swap(call=call)]
    call = rng.choice(swappable or calls)
    swap = read_swap(call=call) or f"{read_called_name(call=call)}_other"
    if isinstance(call.func, ast.Attribute):
        call.func.attr = swap
    else:
        call.func.id = swap
    return function


def collect_external_calls(*, function: _Function) -> list[ast.Call]:
    """Return the calls to a method or to a name the function does not bind."""
    local = set(collect_local_names(function=function))
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Attribute)
            or (isinstance(node.func, ast.Name) and node.func.id not in local)
        )
    ]


def read_called_name(*, call: ast.Call) -> str:
    """Return the bare name a call targets (the attribute for a method)."""
    return call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id


def read_swap(*, call: ast.Call) -> str | None:
    """Return the conventional opposite of a called name, if it has one."""
    swaps = _METHOD_SWAPS if isinstance(call.func, ast.Attribute) else _NAME_SWAPS
    return swaps.get(read_called_name(call=call))


_TRANSFORMS: dict[str, Callable[..., _Function | None]] = {
    "rename_identifiers": rename_identifiers,
    "reorder_statements": reorder_statements,
    "change_string_literals": change_string_literals,
    "flip_operator": flip_operator,
    "add_branch": add_branch,
    "change_called_name": change_called_name,
}
