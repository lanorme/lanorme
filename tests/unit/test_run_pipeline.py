"""The run pipeline as an agent sees it: one pass per check, honest labels,
totals, validated selectors, and no traceback on a closed pipe."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import lanorme
from lanorme import CheckResult, Status, Violation, run_all
from lanorme.checks.meta import MetaCheck
from lanorme.cli import _load_builtin_checks, main


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


def _build_sub_region(root: Path, config: str) -> Path:
    """A project with a nested ``sub/`` region carrying *config*; return ``sub``."""
    _project(root)
    sub = root / "sub"
    sub.mkdir()
    (sub / "lanorme.toml").write_text(config, encoding="utf-8")
    return sub


# --------------------------------------------------------------------------- #
# meta audits the collected results instead of re-running the suite
# --------------------------------------------------------------------------- #


def test_run_all_runs_each_check_once_and_meta_audits_them(monkeypatch, tmp_path: Path):
    # Arrange: two counting checks around a real meta, in registry order.
    first, second = _Counting(name="first"), _Counting(name="second")
    monkeypatch.setattr(
        lanorme,
        "_registry",
        lanorme.Registry({"first": first, "meta": MetaCheck(), "second": second}),
    )

    # Act.
    results = run_all(src_root=str(tmp_path))

    # Assert: one run each, meta passed on their results, output in registry order.
    assert (first.runs, second.runs) == (1, 1)
    assert [r.check for r in results] == ["first", "meta", "second"]
    assert results[1].status == Status.PASS


def test_check_meta_under_nested_regions_still_audits_every_check(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    """Selecting the auditor alone must still run the checks it judges."""
    # Arrange: a two-region tree and a check whose result carries the wrong name.
    # The nested file sets a run key: with the registry swapped below, a check
    # table would name an unknown check.
    _build_sub_region(tmp_path, 'select = ["META"]\n')

    ran: list[str] = []  # shared by every copy the runner configures

    @dataclass
    class _Impostor(_Counting):
        def run(self, *, src_root: str) -> CheckResult:
            ran.append(src_root)
            return CheckResult.from_findings(check="someone_else")

    impostor = _Impostor(name="honest")
    # The CLI imports the built-in checks on first use; do it before the
    # registry is swapped so they land in the real one, not the fake.
    _load_builtin_checks()
    monkeypatch.setattr(
        lanorme,
        "_registry",
        lanorme.Registry({"honest": impostor, "meta": MetaCheck()}),
    )

    # Act.
    _run(["check", str(tmp_path), "--check", "meta", "--output-format", "ndjson"])
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    # Assert: META-004 reported, only meta's result shown, the impostor ran.
    assert [(r["check"], r["code"]) for r in records] == [("meta", "META-004")]
    assert ran


def test_meta_audit_flags_a_result_whose_check_name_is_wrong(monkeypatch):
    # Arrange: a well-formed check whose collected result carries another name.
    impostor = _Counting(name="honest")
    monkeypatch.setattr(
        lanorme,
        "_registry",
        lanorme.Registry({"honest": impostor, "meta": MetaCheck()}),
    )
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
    """A block-buffered stdout whose reader has gone away.

    Writes land in the buffer and succeed; the failure only surfaces on flush,
    which is how a real pipe behaves for output under the buffer size.
    """

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        raise BrokenPipeError


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
    (sub / "lanorme.toml").write_text("[file_limits]\nparam_warn = 7\n", encoding="utf-8")
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


# --------------------------------------------------------------------------- #
# a bare rule code is expanded to the declared rule string; spans travel through
# --------------------------------------------------------------------------- #


def test_bare_rule_code_is_expanded_to_the_declared_string(tmp_path: Path):
    # Arrange: a check that emits the code alone and declares the description once.
    @dataclass
    class _Terse(_Counting):
        rules: list[str] = field(default_factory=lambda: ["TERSE-001: Say it once", "TERSE-002"])

        def run(self, *, src_root: str) -> CheckResult:
            finding = Violation(file="f.py", line=3, rule="TERSE-001", message="m", fix="x")
            undeclared = Violation(file="f.py", line=4, rule="TERSE-002", message="m", fix="x")
            return CheckResult.from_findings(check=self.name, violations=[finding, undeclared])

    # Act.
    result = lanorme.run_check(_Terse(name="terse"), src_root=str(tmp_path))

    # Assert: the declared string where one exists, the code untouched otherwise.
    assert [v.rule for v in result.violations] == ["TERSE-001: Say it once", "TERSE-002"]
    assert result.violations[0].code == "TERSE-001"


def test_spans_reach_ndjson_and_github_annotations(tmp_path: Path, capsys):
    # Arrange: a function at the PARAM-001 limit, a finding anchored at its def node.
    _project(tmp_path)
    (tmp_path / "wide.py").write_text(
        "def f(a, b, c, d, e, f, g, h):\n    return a\n",
        encoding="utf-8",
    )

    # Act.
    _run(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "ndjson"])
    (record,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    _run(["check", str(tmp_path), "--check", "PARAM-001", "--output-format", "github"])
    annotation = capsys.readouterr().out.strip()

    # Assert: 0-based column and inclusive end line in ndjson, 1-based col in the annotation.
    assert (record["line"], record["column"], record["end_line"]) == (1, 0, 2)
    assert annotation.startswith("::error file=wide.py,line=1,endLine=2,col=1,endColumn=")


# --------------------------------------------------------------------------- #
# cascading config: the outermost config is the project, a subtree is a region
# --------------------------------------------------------------------------- #


def _build_nested_project(tmp_path: Path) -> Path:
    """A project whose tests/ subtree carries its own config and a TYPE-003 bait."""
    _project(tmp_path, "")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "lanorme.toml").write_text("[file_limits]\nparam_warn = 7\n", encoding="utf-8")
    (tests / "helpers.py").write_text("def f(**kwargs):\n    return kwargs\n", encoding="utf-8")
    return tests


def test_nested_region_keeps_path_based_exemptions(tmp_path: Path, capsys):
    """A region pass sees ``tests/helpers.py``, so the tests/ exemption of TYPE-003 holds."""
    # Arrange.
    _build_nested_project(tmp_path)

    # Act.
    _run(["check", str(tmp_path), "--check", "strong_types", "--output-format", "ndjson"])
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    # Assert: no TYPE-003 on a file under tests/, nested config or not.
    assert [r["code"] for r in records] == []


def test_checking_a_subtree_applies_the_enclosing_project_config(tmp_path: Path, capsys):
    """``lanorme check tests`` under tests/lanorme.toml is still the project's run."""
    # Arrange: the project ignores TYPE at the root; the subtree only tunes a threshold.
    tests = _build_nested_project(tmp_path)
    (tmp_path / "lanorme.toml").write_text('ignore = ["TYPE"]\n', encoding="utf-8")

    # Act.
    _run(["check", str(tests), "--show-config"])
    out = capsys.readouterr().out

    # Assert: the outermost config is the project, the nested one is listed as a region.
    assert f"project root: {tmp_path.resolve()}" in out
    assert "nested:" in out and "tests/lanorme.toml" in out.replace("\\", "/")
    assert "ignore = ['TYPE']" in out


# --------------------------------------------------------------------------- #
# strict settings, summary format, notes
# --------------------------------------------------------------------------- #


def test_unknown_setting_key_exits_2_and_lists_the_keys(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path, "[file_limits]\nfile_warn = 10\n")

    # Act.
    code = _run(["check", str(tmp_path)])
    err = capsys.readouterr().err

    # Assert.
    assert code == 2
    assert "unknown key in [tool.lanorme.file_limits]: 'file_warn'" in err
    assert "file_warn_lines" in err


def test_summary_format_counts_by_code_and_directory(tmp_path: Path, capsys):
    # Arrange: two eval calls in two directories.
    _project(tmp_path)
    for directory in ("app", "lib"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "m.py").write_text("eval(input())\n", encoding="utf-8")

    # Act.
    code = _run(["check", str(tmp_path), "--output-format", "summary"])
    out = capsys.readouterr().out

    # Assert.
    assert code == 1
    assert "EVAL-001         error    2" in out
    assert "app/" in out and "lib/" in out and "By directory:" in out


def test_summary_notes_report_suppressions_and_disabled_opt_ins(tmp_path: Path, capsys):
    # Arrange: one eval call silenced inline, another by per-file-ignores.
    _project(tmp_path, '[per-file-ignores]\n"quiet.py" = ["EVAL-001"]\n')
    (tmp_path / "loud.py").write_text(
        "eval(input())  # lanorme: ignore[EVAL-001]\n",
        encoding="utf-8",
    )
    (tmp_path / "quiet.py").write_text("eval(input())\n", encoding="utf-8")

    # Act.
    code = _run(["check", str(tmp_path)])
    out = capsys.readouterr().out

    # Assert: clean exit, but the summary says what was silenced and what is off.
    assert code == 0
    assert "Suppressed: 1 by inline ignores, 1 by per-file-ignores, 0 by the baseline." in out
    assert "Opt-in checks not enabled:" in out


def test_selecting_a_disabled_opt_in_check_is_noted(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path)

    # Act.
    code = _run(["check", str(tmp_path), "--check", "similarity"])
    err = capsys.readouterr().err

    # Assert.
    assert code == 0
    assert "similarity is opt-in and not enabled" in err


def test_usage_errors_surface_as_exit_2_from_one_place(tmp_path: Path, capsys):
    # Arrange: a path that does not exist.
    missing = tmp_path / "nope"

    # Act.
    code = _run(["check", str(missing)])
    err = capsys.readouterr().err

    # Assert: the library's UsageError became the CLI's ERROR line and exit 2.
    assert code == 2
    assert err.startswith("ERROR: path '") and "does not exist" in err


# --------------------------------------------------------------------------- #
# a subtree scan runs from the project root, confined to the subtree
# --------------------------------------------------------------------------- #

_DUP_FUNCTION = "def compute():\n    a = 1\n    b = 2\n    c = a + b\n    d = c * 2\n    return d\n"


def _build_subtree_project(tmp_path: Path) -> Path:
    """A project whose tests/ subtree has a TYPE-003 bait and an EVAL-001 hit.

    The root holds an EVAL-001 hit too, so a subtree scan that leaked outside
    the subtree would show it.
    """
    _project(tmp_path)
    (tmp_path / "loud.py").write_text("eval(input())\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "helpers.py").write_text(
        "def f(**kwargs):\n    return eval(kwargs)\n",
        encoding="utf-8",
    )
    return tests


def _run_records(argv: list[str], capsys) -> list[dict]:
    """Run ``check`` with ndjson output and return the finding records."""
    _run(["check", *argv, "--output-format", "ndjson"])
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_subtree_scan_keeps_path_exemptions_and_project_relative_paths(tmp_path: Path, capsys):
    """``lanorme check tests`` hands the checks ``tests/helpers.py``, not ``helpers.py``."""
    # Arrange.
    tests = _build_subtree_project(tmp_path)

    # Act.
    records = _run_records([str(tests)], capsys)

    # Assert: no TYPE finding on a tests/ file, the path is project-relative,
    # and nothing outside the subtree is reported.
    assert [(r["code"], r["file"]) for r in records] == [("EVAL-001", "tests/helpers.py")]


def test_file_target_in_a_subtree_keeps_path_exemptions(tmp_path: Path, capsys):
    # Arrange.
    tests = _build_subtree_project(tmp_path)

    # Act.
    records = _run_records([str(tests / "helpers.py"), "--check", "strong_types"], capsys)

    # Assert: the tests/ exemption of TYPE-002 / TYPE-003 holds for a file target too.
    assert records == []


def test_file_target_still_finds_a_duplicate_elsewhere_in_the_project(tmp_path: Path, capsys):
    """Tree-scoped checks see the whole project; the report is narrowed to the target."""
    # Arrange: the duplicate pair straddles two top-level packages.
    _project(tmp_path)
    for directory, name in (("pkg", "a.py"), ("other", "b.py")):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / name).write_text(_DUP_FUNCTION, encoding="utf-8")

    # Act.
    records = _run_records([str(tmp_path / "pkg" / "a.py"), "--check", "DRY-001"], capsys)

    # Assert: the pair is found, reported on the target alone, naming its partner.
    assert [(r["code"], r["file"]) for r in records] == [("DRY-001", "pkg/a.py")]
    assert "other/b.py:1" in records[0]["message"]


def test_subtree_scan_honours_the_nested_region_config(tmp_path: Path, capsys):
    """The region at the scanned subtree still governs its files.

    The subtree is ``sub/``, not ``tests/``: SIZE-001 exempts test files, so a
    tests/ region would show nothing whatever threshold it set.
    """
    # Arrange: sub/ lowers the file warn threshold to catch a three-line file.
    sub = _build_sub_region(tmp_path, "[file_limits]\nfile_warn_lines = 1\n")
    (sub / "long.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (sub / "helpers.py").write_text("def f(**kwargs):\n    return kwargs\n", encoding="utf-8")

    # Act.
    records = _run_records([str(sub), "--check", "SIZE-001"], capsys)

    # Assert: the nested threshold applied, to the subtree's files only.
    assert {r["file"] for r in records} == {"sub/helpers.py", "sub/long.py"}


# --------------------------------------------------------------------------- #
# the opt-in note counts the selected checks only
# --------------------------------------------------------------------------- #


def test_opt_in_note_counts_only_the_selected_checks(tmp_path: Path, capsys):
    # Arrange.
    _project(tmp_path)

    # Act: a default-on selection, an opt-in selection, and the full run.
    _run(["check", str(tmp_path), "--check", "file_limits"])
    default_on = capsys.readouterr().out
    _run(["check", str(tmp_path), "--check", "named_args"])
    opt_in = capsys.readouterr().out
    _run(["check", str(tmp_path)])
    full = capsys.readouterr().out

    # Assert: nothing to count, the one selected, and every registered one.
    assert "Opt-in checks not enabled" not in default_on
    assert "Opt-in checks not enabled: 1 (" in opt_in
    registered = sum(
        1 for c in lanorme.get_all_checks().values() if not getattr(c, "enabled", True)
    )
    assert f"Opt-in checks not enabled: {registered} (" in full


def test_opt_in_count_covers_only_the_selected_checks():
    # Arrange.
    from lanorme.runner import count_opt_in_disabled

    _load_builtin_checks()
    checks = lanorme.get_all_checks()
    registered = sum(1 for c in checks.values() if not getattr(c, "enabled", True))

    # Act / Assert.
    assert count_opt_in_disabled(checks=checks, selected=checks) == registered
    assert count_opt_in_disabled(checks=checks, selected=("file_limits",)) == 0
    assert count_opt_in_disabled(checks=checks, selected=("similarity",)) == 1
    assert count_opt_in_disabled(checks=checks, selected=("no_such_check",)) == 0


# --------------------------------------------------------------------------- #
# unknown top-level keys and a prefixed table in a dedicated file are refused
# --------------------------------------------------------------------------- #


def test_unknown_top_level_key_exits_2_and_lists_the_accepted_ones(tmp_path: Path, capsys):
    # Arrange: a misspelt run key and a misspelt check table.
    _project(tmp_path, 'selct = ["SIZE"]\n[file_limit]\nparam_warn = 3\n')

    # Act.
    code = _run(["check", str(tmp_path)])
    err = capsys.readouterr().err

    # Assert.
    assert code == 2
    assert "unknown key in [tool.lanorme]: 'file_limit', 'selct'." in err
    assert "Run keys: baseline, exclude, extends, ignore, per-file-ignores" in err
    assert "Check tables: attribute_access, comments" in err and "file_limits" in err


def test_unknown_top_level_key_in_a_nested_region_names_its_file(tmp_path: Path, capsys):
    # Arrange.
    _build_sub_region(tmp_path, "[file_limitz]\nparam_warn = 3\n")

    # Act.
    code = _run(["check", str(tmp_path)])
    err = capsys.readouterr().err

    # Assert.
    assert code == 2
    assert "unknown key in " in err and "sub/lanorme.toml: 'file_limitz'" in err.replace("\\", "/")


def test_every_run_key_and_check_table_is_accepted(tmp_path: Path, capsys):
    # Arrange: each documented run key plus a check table, in one file.
    _project(
        tmp_path,
        'select = ["SIZE"]\nignore = []\nexclude = []\npromote = []\nextends = []\n'
        'source_root = "."\nplugins = []\nroot = true\n[per-file-ignores]\n'
        '"x.py" = ["SIZE-001"]\n[file_limits]\nparam_warn = 3\n',
    )
    (tmp_path / "debt.json").write_text("{}", encoding="utf-8")

    # Act.
    code = _run(["check", str(tmp_path)])

    # Assert.
    assert code == 0
    assert "ERROR" not in capsys.readouterr().err


def test_plugin_check_table_counts_as_a_known_key(tmp_path: Path, capsys, monkeypatch):
    # Arrange: a plugin module on sys.path whose check is configured at the top level.
    plugin = tmp_path / "house_plugin.py"
    plugin.write_text(
        "from dataclasses import dataclass, field\n"
        "from lanorme import CheckResult, register\n\n"
        "@dataclass\nclass House:\n"
        "    name: str = 'house'\n    description: str = 'house rules'\n"
        "    rules: list[str] = field(default_factory=lambda: ['HOUSE-001: rule'])\n"
        "    def run(self, *, src_root):\n"
        "        return CheckResult.from_findings(check=self.name)\n\n"
        "register(House())\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _project(tmp_path, 'plugins = ["house_plugin"]\n[house]\nenabled = true\n')

    # Act.
    code = _run(["check", str(tmp_path)])

    # Assert: the plugin registered before the keys were checked, so its table is known.
    assert code == 0
    assert "unknown key" not in capsys.readouterr().err


def test_prefixed_table_in_a_dedicated_file_is_refused(tmp_path: Path, capsys):
    # Arrange: the pyproject form written into lanorme.toml.
    _project(tmp_path, '[tool.lanorme]\nselect = ["SIZE"]\n')

    # Act.
    code = _run(["check", str(tmp_path)])
    err = capsys.readouterr().err

    # Assert: exit 2 and a message that says where the prefix belongs.
    assert code == 2
    assert "lanorme.toml holds a [tool.lanorme] table" in err
    assert "keys are top level" in err and "pyproject.toml only" in err
