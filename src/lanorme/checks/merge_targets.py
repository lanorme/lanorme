"""Where SHALLOW-001 suggests merging a package, and the words of its finding.

A helper of the ``shallow_modules`` check; it registers nothing. The merge it
suggests must stay legal under every other LaNorme rule, so the target depends
on how the package's directory is used:

1. **Rule-located directory: fold inside it.** Other rules find files by these
   directory names (the ``[layer_deps]`` layers, the ``[port_coverage]`` ports
   directory and adapter roots, the ``api/`` AUTHN-001 scans, the directories
   NAMING-001..003 and TESTFILE-001 read). Collapsing one into a module would
   take its code out of those rules, so the target is its largest member and
   the directory stays.
2. **Junk name** (``utils/``, ``common/``): one module in the parent, named for
   what it holds. The junk name is never carried into a file, which NAMING-010
   would flag.
3. **Top-level package, or a sibling module of the same name: fold inside.** A
   top-level name is what installers and entry points use.
4. **Otherwise** the package becomes the module ``<package>.py``, which keeps
   its import path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from lanorme.checks.naming_consistency import ENDPOINT_DIRS, REPO_DIRS, SERVICE_DIRS
from lanorme.checks.naming_words import JUNK_MODULES
from lanorme.checks.port_coverage import PORT_FILES_WITHOUT_SERVICE_IMPL
from lanorme.checks.test_coverage import TESTABLE_DIRS

# A member below this many lines of code (docstrings left out) is tiny.
TINY_CODE_LINES = 20

# How many members a finding names before it says "and N more".
MAX_LISTED_MEMBERS = 6

PACKAGE_FILE = "__init__.py"

# The directory AUTHN-001 reads endpoints from.
_AUTHENTICATED_DIRECTORY = "api"


@dataclass(frozen=True)
class PackageMember:
    """One module that counts towards a package's split: its path and its sizes."""

    relative: str
    lines: int
    code_lines: int

    @property
    def file_name(self) -> str:
        """The member's file name, such as ``errors.py``."""
        return self.relative.rsplit("/", 1)[-1]

    @property
    def is_package_file(self) -> bool:
        """True for the package's own ``__init__.py``."""
        return self.file_name == PACKAGE_FILE


@dataclass(frozen=True)
class RuleLocatedDirectory:
    """A directory another rule finds files by, and why, as the fix words it."""

    path: str
    reason: str
    is_ports_directory: bool = False


@dataclass(frozen=True)
class MergeTarget:
    """Where to merge a package: inside it, into a module named for it, or a new name."""

    kind: str
    path: str = ""
    reason: str = ""


FOLD_INSIDE = "fold inside"
NAMED_FOR_CONTENTS = "named for contents"
PACKAGE_MODULE = "package module"


def list_rule_located_directories(
    *,
    layers: Iterable[str],
    ports_directory: str,
    adapter_roots: Iterable[str],
) -> tuple[RuleLocatedDirectory, ...]:
    """Every directory a rule locates files by, in the order the reason is chosen."""
    found = [
        RuleLocatedDirectory(path=layer, reason="[layer_deps] layers names it") for layer in layers
    ]
    found.append(
        RuleLocatedDirectory(
            path=ports_directory,
            reason="[port_coverage] ports_dir names it",
            is_ports_directory=True,
        ),
    )
    found.extend(
        RuleLocatedDirectory(path=root, reason="[port_coverage] adapter_roots names it")
        for root in adapter_roots
    )
    found.append(
        RuleLocatedDirectory(
            path=_AUTHENTICATED_DIRECTORY,
            reason="AUTHN-001 checks the endpoints under it",
        ),
    )
    for directories, reason in (
        (REPO_DIRS, "NAMING-001 matches repository modules under it"),
        (SERVICE_DIRS, "NAMING-002 matches service modules under it"),
        (ENDPOINT_DIRS, "NAMING-003 matches endpoint modules under it"),
        (
            tuple(path for path, _prefix in TESTABLE_DIRS),
            "TESTFILE-001 pairs its modules with tests",
        ),
    ):
        found.extend(RuleLocatedDirectory(path=path, reason=reason) for path in directories)
    return tuple(found)


def find_rule_located_directory(
    *,
    package: str,
    directories: tuple[RuleLocatedDirectory, ...],
) -> RuleLocatedDirectory | None:
    """The first rule-located directory *package* is, at any depth, or ``None``."""
    for directory in directories:
        if package == directory.path or package.endswith(f"/{directory.path}"):
            return directory
    return None


def _choose_largest_member(
    *,
    members: list[PackageMember],
    avoid_ports_without_adapter: bool,
) -> str:
    """The member with the most lines (ties broken by path), never ``__init__.py``."""
    pool = [member for member in members if not member.is_package_file]
    if avoid_ports_without_adapter:
        preferred = [
            member for member in pool if member.file_name not in PORT_FILES_WITHOUT_SERVICE_IMPL
        ]
        pool = preferred or pool
    return max(pool, key=lambda member: (member.lines, member.relative)).relative


@dataclass(frozen=True)
class PackagePlacement:
    """What the target choice needs to know about where a package sits."""

    package: str
    is_top_level: bool
    has_sibling_module: bool


def choose_merge_target(
    *,
    placement: PackagePlacement,
    members: list[PackageMember],
    directories: tuple[RuleLocatedDirectory, ...],
) -> MergeTarget:
    """The target the fix names for *placement*'s package; the first matching case wins."""
    package = placement.package
    located = find_rule_located_directory(package=package, directories=directories)
    if located is not None:
        path = _choose_largest_member(
            members=members,
            avoid_ports_without_adapter=located.is_ports_directory,
        )
        return MergeTarget(kind=FOLD_INSIDE, path=path, reason=located.reason)
    if package.rsplit("/", 1)[-1] in JUNK_MODULES:
        return MergeTarget(kind=NAMED_FOR_CONTENTS)
    if placement.is_top_level or placement.has_sibling_module:
        reason = (
            "it is a top-level package, the name installers and entry points use"
            if placement.is_top_level
            else f"'{package}.py' already exists beside it"
        )
        path = _choose_largest_member(members=members, avoid_ports_without_adapter=False)
        return MergeTarget(kind=FOLD_INSIDE, path=path, reason=reason)
    return MergeTarget(kind=PACKAGE_MODULE, path=f"{package}.py")


def order_members(*, members: list[PackageMember]) -> list[PackageMember]:
    """``__init__.py`` first when it is a member, then by file name."""
    return sorted(members, key=lambda member: (not member.is_package_file, member.file_name))


def join_in_english(*, names: list[str]) -> str:
    """``a.py, b.py and c.py``; past MAX_LISTED_MEMBERS, ``a.py, ..., f.py and N more``."""
    if len(names) > MAX_LISTED_MEMBERS:
        shown = ", ".join(names[:MAX_LISTED_MEMBERS])
        return f"{shown} and {len(names) - MAX_LISTED_MEMBERS} more"
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def build_message(*, package: str, members: list[PackageMember], tiny_count: int) -> str:
    """The question a finding asks, with each member's size, the total and the tiny count."""
    ordered = order_members(members=members)
    sizes = [f"{member.file_name} {member.lines}" for member in ordered[:MAX_LISTED_MEMBERS]]
    listing = ", ".join(sizes)
    if len(ordered) > MAX_LISTED_MEMBERS:
        listing += f", and {len(ordered) - MAX_LISTED_MEMBERS} more"
    total = sum(member.lines for member in members)
    return (
        f"Package '{package}/' holds {len(members)} modules in {total} lines, "
        f"{tiny_count} of them under {TINY_CODE_LINES} lines of code ({listing}). "
        "Is each of these really its own thing?"
    )


@dataclass(frozen=True)
class FixContext:
    """The package-level facts every fix template reads."""

    package: str
    dotted: str
    members: list[PackageMember]
    warning_lines: int


def _build_silence_advice(*, package: str) -> str:
    return (
        "If the split is deliberate, keep it and silence this with a per-file-ignores entry "
        f"for '{package}/__init__.py', or '# noqa: SHALLOW-001' on its line 1 (which counts "
        "against a SUPPRESS-001 budget)."
    )


def _build_fold_fix(*, context: FixContext, target: MergeTarget, size: str) -> str:
    ordered = order_members(members=context.members)
    moved = [
        member.file_name
        for member in ordered
        if member.relative != target.path and not member.is_package_file
    ]
    if any(member.is_package_file for member in ordered):
        moved.append(f"the code in '{PACKAGE_FILE}'")
    return (
        f"If they belong together, consider folding {join_in_english(names=moved)} into "
        f"'{target.path}' ({size}) and keeping the '{context.package}/' directory, "
        f"because {target.reason}."
    )


def _build_merge_fix(*, context: FixContext, target: MergeTarget, size: str) -> str:
    names = join_in_english(
        names=[member.file_name for member in order_members(members=context.members)],
    )
    package = context.package
    if target.kind == NAMED_FOR_CONTENTS:
        parent, _separator, name = package.rpartition("/")
        where = f"'{parent}/'" if parent else "the project root"
        return (
            f"If they belong together, consider merging {names} into one module in {where} "
            f"named for what it holds rather than '{name}.py' ({size}) and deleting '{package}/'."
        )
    return (
        f"If they belong together, consider merging {names} into one module, "
        f"'{target.path}' ({size}), deleting '{package}/' and importing from "
        f"'{context.dotted}' instead of '{context.dotted}.<module>'."
    )


def build_fix(*, context: FixContext, target: MergeTarget) -> str:
    """The suggested merge, its size against SIZE-001, and how to keep a deliberate split."""
    total = sum(member.lines for member in context.members)
    size = f"{total} lines, under SIZE-001's {context.warning_lines}-line warning"
    if target.kind == FOLD_INSIDE:
        merge = _build_fold_fix(context=context, target=target, size=size)
    else:
        merge = _build_merge_fix(context=context, target=target, size=size)
    return f"{merge} {_build_silence_advice(package=context.package)}"
