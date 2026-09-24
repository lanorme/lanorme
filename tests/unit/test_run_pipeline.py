"""The run pipeline as an agent sees it: one pass per check, honest labels,
totals, validated selectors, and no traceback on a closed pipe."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import lanorme
from lanorme import CheckResult, Status, Violation, run_all
from lanorme.checks.meta import MetaCheck
from lanorme.cli import main


@dataclass
class _Counting:
    """A check that records how often it ran."""

    name: str
    description: str = "counts its runs"
    rules: list[str] = field(default_factory=lambda: ["CNT-001: counted"])
    runs: int = 0

    def run(self, *, src_root: str) -> CheckResult:
        self.runs += 1
        return CheckResult.from_findings(check=self.name)


def _run(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return 0


def _project(root: Path, config: str = "") -> Path:
    (root / "lanorme.toml").write_text(config, encoding="utf-8")
    (root / "sample.py").write_text("x = 1\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# meta audits the collected results instead of re-running the suite
# --------------------------------------------------------------------------- #


def test_run_all_runs_each_check_once_and_meta_audits_them(monkeypatch, tmp_path: Path):
    # Arrange: two counting checks around a real meta, in registry order.
    first, second = _Counting(name="first"), _Counting(name="second")
    monkeypatch.setattr(lanorme, "_registry", {"first": first, "meta": MetaCheck(), "second": second})

    # Act.
    results = run_all(src_root=str(tmp_path))

    # Assert: one run each, meta passed on their results, output in registry order.
    assert (first.runs, second.runs) == (1, 1)
    assert [r.check for r in results] == ["first", "meta", "second"]
    assert results[1].status == Status.PASS


def test_meta_audit_flags_a_result_whose_check_name_is_wrong(monkeypatch):
    # Arrange: a well-formed check whose collected result carries another name.
    impostor = _Counting(name="honest")
    monkeypatch.setattr(lanorme, "_registry", {"honest": impostor, "meta": MetaCheck()})
    results = {"honest": CheckResult.from_findings(check="someone_else")}

    # Act.
    audit = MetaCheck().audit(results=results)

    # Assert: META-004, without running the check.
    assert [v.code for v in audit.violations] == ["META-004"]
    assert impostor.runs == 0


# --------------------------------------------------------------------------- #
# human output: warnings are labelled as such and totals are printed
# --------------------------------------------------------------------------- #


def test_human_output_labels_warnings_and_counts_findings(tmp_path: Path, capsys):
    # Arrange: a function at the PARAM-001 warn threshold (a warning, not an error).
    _project(tmp_path)
    (tmp_path / "wide.py").write_text("def f(a, b, c, d, e):\n    return a\n", encoding="utf-8")

    # Act.
    code = _run(["check", str(tmp_path), "--check", "file_limits"])
    out = capsys.readouterr().out

    # Assert: labelled WARNING, never VIOLATION, and the totals line.
    assert code == 0
    assert "  WARNING: wide.py:1" in out
    assert "VIOLATION:" not in out
    assert "Findings: 0 errors to fix, 1 advisory warning." in out


def test_full_format_keeps_the_verbose_listing_without_totals(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path)

    # Act.
    _run(["check", str(tmp_path), "--output-format", "full"])
    out = capsys.readouterr().out

    # Assert.
    assert "[PASS]" in out and "Findings:" not in out


# --------------------------------------------------------------------------- #
# unknown selectors are usage errors
# --------------------------------------------------------------------------- #


def test_unknown_select_code_exits_2_and_names_it(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path)

    # Act.
    code = _run(["check", str(tmp_path), "--select", "NOPE-999,SIZE-001"])
    err = capsys.readouterr().err

    # Assert.
    assert code == 2
    assert "'NOPE-999'" in err and "SIZE-001" not in err.split("names no known")[1].split(".")[0]


def test_unknown_ignore_in_config_exits_2(tmp_path: Path, capsys):
    # Arrange: a category that no check declares.
    _project(tmp_path, 'ignore = ["LAYRE"]\n')

    # Act.
    code = _run(["check", str(tmp_path)])

    # Assert.
    assert code == 2
    assert "'ignore' names no known rule code or category: 'LAYRE'" in capsys.readouterr().err


def test_known_selector_forms_are_accepted(tmp_path: Path, capsys):
    # Arrange: a code, a category, ALL, a -000 notice code and a placeholder-family code.
    _project(tmp_path, 'ignore = ["SIZE-001", "DRY", "SIZE-000", "TERM-042"]\npromote = ["ALL"]\n')

    # Act.
    code = _run(["check", str(tmp_path), "--select", "size"])

    # Assert.
    assert code == 0
    assert "ERROR" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# a closed pipe ends the output quietly and keeps the exit code
# --------------------------------------------------------------------------- #


class _ClosedPipe:
    """A stdout whose reader has gone away."""

    def write(self, _text: str) -> int:
        raise BrokenPipeError

    def flush(self) -> None:
        return None


def test_rules_listing_into_a_closed_pipe_exits_cleanly(monkeypatch):
    # Arrange.
    monkeypatch.setattr("sys.stdout", _ClosedPipe())

    # Act / Assert: no traceback, normal return.
    assert _run(["rules"]) == 0


def test_check_into_a_closed_pipe_keeps_the_failure_exit_code(tmp_path: Path, monkeypatch):
    # Arrange: a tree with a hard violation, printed to a reader that has gone.
    _project(tmp_path)
    (tmp_path / "bad.py").write_text("import os\nos.system('rm -rf /')\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdout", _ClosedPipe())

    # Act / Assert: the verdict survives the lost output.
    assert _run(["check", str(tmp_path), "--check", "security_calls"]) == 1


# --------------------------------------------------------------------------- #
# --show-config shows where promotions come from and whether a baseline is on
# --------------------------------------------------------------------------- #


def test_show_config_prints_extends_and_baseline(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path, 'extends = ["strict"]\nbaseline = "debt.json"\n')

    # Act.
    _run(["check", str(tmp_path), "--show-config"])
    out = capsys.readouterr().out

    # Assert.
    assert "extends = ['strict']" in out and "baseline = 'debt.json'" in out


# --------------------------------------------------------------------------- #
# an explicitly requested path that the excludes cover says so
# --------------------------------------------------------------------------- #


def test_targets_entirely_excluded_are_noted_on_stderr(tmp_path: Path, capsys):
    # Arrange: a fixture tree the config excludes, then requested by name.
    _project(tmp_path, 'exclude = ["fixtures/*"]\n')
    fixture = tmp_path / "fixtures"
    fixture.mkdir()
    (fixture / "junk.py").write_text("eval(input())\n", encoding="utf-8")

    # Act.
    code = _run(["check", str(fixture / "junk.py")])
    captured = capsys.readouterr()

    # Assert: clean exit, but the note explains why nothing was checked.
    assert code == 0
    assert "every requested path matches an exclude glob" in captured.err


# --------------------------------------------------------------------------- #
# walks that used to bypass discovery now honour regions and excludes
# --------------------------------------------------------------------------- #


def test_stray_artifact_in_a_nested_region_is_reported_once(tmp_path: Path, capsys):
    # Arrange: a root region and a nested one holding a stray image.
    (tmp_path / "pyproject.toml").write_text("[tool.lanorme]\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "lanorme.toml").write_text("", encoding="utf-8")
    (sub / "pic.png").write_bytes(b"\x89PNG")

    # Act.
    _run(["check", str(tmp_path), "--check", "stray_artifacts", "--output-format", "ndjson"])
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    # Assert: one JUNK-002, not one per region pass.
    assert [(r["code"], r["file"]) for r in records] == [("JUNK-002", "sub/pic.png")]


def test_forbidden_dir_under_an_excluded_tree_is_not_reported(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path, 'exclude = ["vendored/*"]\n[forbidden_paths]\ndirs = ["legacy"]\n')
    (tmp_path / "vendored" / "legacy").mkdir(parents=True)
    (tmp_path / "legacy").mkdir()

    # Act.
    _run(["check", str(tmp_path), "--check", "forbidden_paths", "--output-format", "ndjson"])
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    # Assert: only the one outside the excluded tree.
    assert [r["file"] for r in records] == ["legacy"]


# --------------------------------------------------------------------------- #
# mistyped settings are refused at configure time, not at run time
# --------------------------------------------------------------------------- #


def test_string_where_a_list_is_expected_exits_2_and_names_the_key(tmp_path: Path, capsys):
    # Arrange: a bare string, which used to be iterated character by character.
    _project(tmp_path, '[forbidden_paths]\ndirs = "build"\n')

    # Act.
    code = _run(["check", str(tmp_path)])

    # Assert.
    assert code == 2
    assert "[tool.lanorme.forbidden_paths] dirs" in capsys.readouterr().err


def test_domain_term_rule_missing_canonical_exits_2(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path, '[[domain_terms.rules]]\nid = "TERM-001"\nforbidden = ["client"]\n')

    # Act.
    code = _run(["check", str(tmp_path)])

    # Assert: a config error, not a RUN-000 crash notice.
    assert code == 2
    assert "needs a string 'canonical'" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# a custom layer violation carries a real rule code and a complete fix
# --------------------------------------------------------------------------- #


def test_custom_layer_violation_is_layer_007_with_allowed_list_in_fix(tmp_path: Path, capsys):
    # Arrange: two custom layers, web may import nothing, and it imports core.
    _project(
        tmp_path,
        '[layer_deps]\nlayers = ["core", "web"]\n[layer_deps.allowed]\ncore = []\nweb = []\n',
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "views.py").write_text("from core import thing\n", encoding="utf-8")

    # Act.
    _run(["check", str(tmp_path), "--check", "layer_deps", "--output-format", "ndjson"])
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    # Assert.
    assert [r["code"] for r in records] == ["LAYER-007"]
    assert records[0]["fix"] == "Remove the import from core/; web/ may import no other layer"


def test_violation_code_survives_an_empty_rule_string():
    # Arrange / Act / Assert: no IndexError from a plugin that left the rule blank.
    assert Violation(file="f", line=1, rule="", message="m", fix="x").code == ""
