"""Tests for the naming_canon check (NAMING-006..008, default-on warnings).

Each rule gets a positive, the exemptions the design lists as deliberate (a
CQRS suffix, a noun head, a framework-named function, a nested closure, a
query named for its value, a protocol method), and its config knob. Findings
are warnings, never violations. Assertions key on the rule code, except where
the fix suggestion is the behaviour under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lanorme import Status
from lanorme.checks.naming_canon import NamingCanonCheck, agent_noun


def _run(*, root: Path, body: str, check: NamingCanonCheck | None = None, name: str = "sample.py"):
    """Write *body* as *name* under *root* and run the check over it."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return (check or NamingCanonCheck()).run(src_root=str(root))


def _codes(result) -> list[str]:
    """The rule codes of all warnings on *result*."""
    return [w.code for w in result.warnings]


def _configured(**settings) -> NamingCanonCheck:
    """A check with *settings* applied."""
    check = NamingCanonCheck()
    check.configure(settings=settings)
    return check


# --------------------------------------------------------------------------- #
# NAMING-006: a class is a thing
# --------------------------------------------------------------------------- #


def test_verb_first_class_is_a_warning(tmp_path: Path) -> None:
    # Arrange / Act
    result = _run(root=tmp_path, body="class FetchUsers:\n    pass\n")

    # Assert: a warning, not a violation, and the check reports WARN.
    assert _codes(result) == ["NAMING-006"]
    assert result.violations == [] and result.status is Status.WARN


def test_noun_phrase_classes_pass(tmp_path: Path) -> None:
    body = "".join(
        f"class {name}:\n    pass\n"
        for name in ("UserFetcher", "FetchOptions", "ConnectTimeout", "CompileError", "DeleteView",
                     "SaveTest2", "Configurable", "BuildResult", "CONSOLE_INFO", "Fetch")
    )
    result = _run(root=tmp_path, body=body)
    assert _codes(result) == []


def test_command_object_suffixes_are_exempt_and_extensible(tmp_path: Path) -> None:
    # Arrange: a bundled suffix that only the suffix path exempts, and one added through config.
    body = "class CreateUserResponse:\n    pass\nclass CreateUserInteractor:\n    pass\n"

    # Act
    default = _run(root=tmp_path, body=body)
    extended = _run(root=tmp_path, body=body, check=_configured(command_suffixes=["Interactor"]))

    # Assert: the bundled suffix survives the extension.
    assert [w.line for w in default.warnings] == [3]
    assert _codes(extended) == []


@pytest.mark.parametrize(
    ("name", "suggested"),
    [("ValidateOrder", "OrderValidator"), ("_Send2Users", "_UsersSender2"), ("EmitMetrics", "MetricsEmitter")],
)
def test_class_fix_names_the_thing(tmp_path: Path, name: str, suggested: str) -> None:
    result = _run(root=tmp_path, body=f"class {name}:\n    pass\n")
    assert f"'{suggested}'" in result.warnings[0].fix


@pytest.mark.parametrize(
    ("verb", "noun"),
    [("validate", "validator"), ("parse", "parser"), ("get", "getter"), ("notify", "notifier"),
     ("execute", "executor"), ("collect", "collector"), ("send", "sender")],
)
def test_agent_noun(verb: str, noun: str) -> None:
    assert agent_noun(verb=verb) == noun


# --------------------------------------------------------------------------- #
# NAMING-007: a function that acts is named verb-first
# --------------------------------------------------------------------------- #


def test_noun_named_command_is_flagged(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="def layout(root):\n    root.write_text('x')\n")
    assert _codes(result) == ["NAMING-007"]


def test_verb_first_commands_pass(tmp_path: Path) -> None:
    body = (
        "def write_layout(root):\n    root.clear()\n"
        "def bulk_insert_rows(rows):\n    rows.clear()\n"
        "def re_apply(x):\n    x.clear()\n"
        "def unquote_all(x):\n    x.clear()\n"
        "def getheaders(x):\n    x.clear()\n"
        "def setUp(self):\n    self.x.clear()\n"
    )
    result = _run(root=tmp_path, body=body)
    assert _codes(result) == []


def test_queries_named_for_their_value_pass(tmp_path: Path) -> None:
    body = (
        "def thresholds():\n    return 1\n"
        "def result_processor():\n    return None\n"
        "def rows():\n    yield 1\n"
    )
    result = _run(root=tmp_path, body=body)
    assert _codes(result) == []


@pytest.mark.parametrize(
    ("name", "suggested"),
    [("_cert_verify", "_verify_cert"), ("user_count_update", "update_user_count"), ("bulk_cert_verify", "bulk_verify_cert")],
)
def test_command_fix_puts_the_trailing_verb_first(tmp_path: Path, name: str, suggested: str) -> None:
    result = _run(root=tmp_path, body=f"def {name}(conn):\n    conn.clear()\n")
    assert f"'{suggested}'" in result.warnings[0].fix


def test_command_message_names_the_judged_word_not_the_modifier(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="def bulk_cert_verify(conn):\n    conn.clear()\n")
    assert "'cert' does not read as a verb" in result.warnings[0].message


def test_non_ascii_names_are_not_judged(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="def résumé_thing(x):\n    x.clear()\nclass Envoyé:\n    pass\n")
    assert _codes(result) == []


def test_framework_named_functions_pass(tmp_path: Path) -> None:
    # Arrange: every shape whose name a framework, protocol or convention chose.
    body = (
        "@app.route('/')\ndef index():\n    app.clear()\n"
        "@pytest.fixture\ndef db():\n    store.clear()\n"
        "def main():\n    app.clear()\n"
        "def on_click(event):\n    event.clear()\n"
        "def pytest_configure(config):\n    config.clear()\n"
        "def and_(a):\n    a.clear()\n"
        "def to_dict(self):\n    self.x.clear()\n"
        "def _repr_mimebundle_(self):\n    self.x.clear()\n"
        "def _env_file_callback(ctx):\n    ctx.clear()\n"
        "class Store:\n    def keys(self):\n        self.x.clear()\n"
    )

    # Act
    result = _run(root=tmp_path, body=body)

    # Assert
    assert _codes(result) == []


def test_closures_stubs_and_raisers_pass(tmp_path: Path) -> None:
    body = (
        "def outer():\n    def wrapper():\n        x.clear()\n    return wrapper\n"
        "class Repo(Protocol):\n    def thresholds(self): ...\n"
        "def key_not_found(key):\n    raise KeyError(key)\n"
        "def placeholder():\n    pass\n"
    )
    result = _run(root=tmp_path, body=body)
    assert _codes(result) == []


def test_generated_migration_trees_are_skipped(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="def schema_step(op):\n    op.clear()\n", name="migrations/0001.py")
    assert _codes(result) == []


def test_a_migrations_directory_above_the_root_does_not_silence_the_check(tmp_path: Path) -> None:
    # Arrange: the project itself lives under a directory named migrations.
    root = tmp_path / "migrations" / "project"

    # Act
    result = _run(root=root, body="def schema_step(op):\n    op.clear()\n")

    # Assert
    assert _codes(result) == ["NAMING-007"]


def test_unparseable_and_bom_files(tmp_path: Path) -> None:
    # Arrange: a syntax error to skip, and a BOM-prefixed file that must still be read.
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbfdef layout(root):\n    root.clear()\n")

    # Act
    result = NamingCanonCheck().run(src_root=str(tmp_path))

    # Assert
    assert [(w.code, w.file) for w in result.warnings] == [("NAMING-007", "bom.py")]


def test_findings_come_in_source_order(tmp_path: Path) -> None:
    body = "try:\n    def layout(r):\n        r.clear()\nexcept ImportError:\n    def layout2(r):\n        r.clear()\n\ndef layout3(r):\n    r.clear()\n"
    result = _run(root=tmp_path, body=body)
    assert [w.line for w in result.warnings] == [2, 5, 8]


def test_verbs_config_extends_the_vocabulary(tmp_path: Path) -> None:
    # Arrange
    body = "def frob_all(x):\n    x.clear()\n"

    # Act
    default = _run(root=tmp_path, body=body)
    extended = _run(root=tmp_path, body=body, check=_configured(verbs=["frob"]))

    # Assert
    assert _codes(default) == ["NAMING-007"]
    assert _codes(extended) == []


def test_exempt_config_silences_a_name_with_or_without_underscores(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="def _layout(root):\n    root.clear()\n", check=_configured(exempt=["layout"]))
    assert _codes(result) == []


def test_findings_carry_a_root_relative_posix_path(tmp_path: Path) -> None:
    result = _run(root=tmp_path, body="def layout(root):\n    root.clear()\n", name="pkg/sub/mod.py")
    assert result.warnings[0].file == "pkg/sub/mod.py" and result.warnings[0].line == 1


# --------------------------------------------------------------------------- #
# NAMING-008: the verb says what happens
# --------------------------------------------------------------------------- #


def test_weak_verbs_are_flagged(tmp_path: Path) -> None:
    # Arrange: a command and a query, one with the two-word weak verb.
    body = "def handle_data(d):\n    d.clear()\ndef deal_with_error(e):\n    return e\n"

    # Act
    result = _run(root=tmp_path, body=body)

    # Assert: both fire, and the fix names the object after 'deal with'.
    assert _codes(result) == ["NAMING-008", "NAMING-008"]
    assert "parse_error" in result.warnings[1].fix


def test_weak_verb_exemptions(tmp_path: Path) -> None:
    # Arrange: a bare verb, a likely override, a framework hook, a registered handler.
    body = (
        "def handle(e):\n    e.clear()\n"
        "class Handler(Base):\n    def do_GET(self):\n        self.x.clear()\n"
        "class Middleware:\n    def process_request(self, r):\n        r.clear()\n"
        "@app.errorhandler(404)\ndef handle_404(e):\n    e.clear()\n"
    )

    # Act
    result = _run(root=tmp_path, body=body)

    # Assert
    assert _codes(result) == []


def test_weak_verbs_config_replaces_the_default(tmp_path: Path) -> None:
    # Arrange
    body = "def handle_data(d):\n    d.clear()\ndef frob_data(d):\n    d.clear()\n"

    # Act
    result = _run(root=tmp_path, body=body, check=_configured(weak_verbs=["frob"]))

    # Assert: only the configured verb fires, and as a weak verb rather than a missing one.
    assert [(w.code, w.line) for w in result.warnings] == [("NAMING-008", 3)]


def test_malformed_config_is_rejected() -> None:
    with pytest.raises(TypeError):
        NamingCanonCheck().configure(settings={"verbs": "frobnicate"})
    with pytest.raises(ValueError):
        NamingCanonCheck().configure(settings={"exempt": ["two words"]})
