#!/usr/bin/env python3
"""sweep.py - driver for a hyperparameter ablation sweep.

Reads a sweep *spec* (a small JSON document -- JSON is valid YAML, so this
gives a "YAML-ish" config without pulling in a YAML library), expands it into
concrete run configs, launches each run as a subprocess on its own GPU with a
bounded worker pool, streams and parses each run's JSONL metrics, and
aggregates everything into a JSON + Markdown report with per-axis marginals,
best-run-per-axis-value, and a scaling-law fit of loss against compute.

Spec format
-----------

    {
      "base_config": {"model": "small", "steps": 2000},
      "axes": {
        "lr": [0.0001, 0.0003, 0.001],
        "batch_size": [16, 32, 64],
        "seq_len": [256, 512, 1024]
      },
      "constraints": ["batch_size * seq_len <= 32768"],
      "mode": "grid",                 // or "random"
      "budget": 20,                   // required when mode == "random"
      "seed": 0,                      // random-search RNG seed
      "command": ["python", "train.py", "--config", "{config_path}"],
      "gpus": [0, 1, 2, 3],
      "max_workers": 4,               // default: len(gpus)
      "compute_formula": "batch_size * seq_len * steps",
      "timeout_seconds": null
    }

``constraints`` and ``compute_formula`` are arithmetic/boolean expressions
evaluated over the merged run config (base_config + the axis values for that
run) by a small AST-based evaluator -- no ``eval()`` of untrusted code.

Command template placeholders: any key present in the run's config (e.g.
``{lr}``), plus the reserved names ``{config_path}`` (a JSON file holding the
full merged config), ``{metrics_path}`` (where the run is expected to append
JSONL metric lines), ``{run_id}``, ``{run_dir}``, and ``{gpu}``.

Run protocol: the launched command should append one JSON object per line to
``{metrics_path}`` as training progresses, e.g. ``{"step": 100, "loss": 2.1}``.
The final eval loss is taken from the last line containing an "eval_loss" key
(falling back to "loss"). A line may also report "compute" (e.g. FLOPs); if
none do, the sweep falls back to evaluating ``compute_formula`` against the
run's config.

Usage
-----

    python sweep.py run spec.json --output-dir sweep_output
    python sweep.py run spec.json --output-dir sweep_output --dry-run
    python sweep.py aggregate spec.json --output-dir sweep_output
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import logging
import math
import operator
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a stated dependency
    np = None

try:
    from scipy import optimize as scipy_optimize
except ImportError:  # pragma: no cover - scipy is a stated dependency
    scipy_optimize = None

logger = logging.getLogger("sweep")


class SweepConfigError(ValueError):
    """The spec file, or something derived from it, is invalid."""


# --------------------------------------------------------------------------
# Safe expression evaluator (used for constraints and the compute formula).
# --------------------------------------------------------------------------


class ExpressionError(ValueError):
    """A constraint/compute expression failed to parse or evaluate."""


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def safe_eval(expr: str, variables: dict[str, Any]) -> Any:
    """Evaluate a restricted arithmetic/boolean expression.

    Only names, numeric/bool constants, +-*/ // % **, unary +-/not, and
    comparisons/boolean ops are allowed -- no attribute access, calls,
    subscripting, or comprehensions. This is deliberately small: it is meant
    for things like ``"batch_size * seq_len <= 32768"``, not general Python.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression {expr!r}: {exc}") from exc
    return _eval_node(tree.body, variables, expr)


def _eval_node(node: ast.AST, variables: dict[str, Any], expr: str) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)) or node.value is None:
            return node.value
        raise ExpressionError(f"unsupported constant in {expr!r}")
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ExpressionError(f"unknown name {node.id!r} in {expr!r}")
        return variables[node.id]
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"unsupported operator in {expr!r}")
        return op(_eval_node(node.left, variables, expr), _eval_node(node.right, variables, expr))
    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"unsupported unary operator in {expr!r}")
        return op(_eval_node(node.operand, variables, expr))
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, variables, expr) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables, expr)
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _CMPOPS.get(type(op_node))
            if op is None:
                raise ExpressionError(f"unsupported comparison in {expr!r}")
            right = _eval_node(comparator, variables, expr)
            if not op(left, right):
                return False
            left = right
        return True
    raise ExpressionError(f"unsupported syntax in {expr!r}")


# --------------------------------------------------------------------------
# Spec.
# --------------------------------------------------------------------------


@dataclass
class SweepSpec:
    base_config: dict[str, Any]
    axes: dict[str, list[Any]]
    constraints: list[str]
    mode: str
    budget: int | None
    seed: int
    command: list[str]
    gpus: list[int]
    max_workers: int
    compute_formula: str | None
    timeout_seconds: float | None

    @classmethod
    def from_file(cls, path: Path) -> "SweepSpec":
        try:
            raw = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SweepConfigError(f"could not read spec {path}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SweepSpec":
        base_config = raw.get("base_config", {})
        axes = raw.get("axes")
        command = raw.get("command")
        gpus = raw.get("gpus", [0])
        mode = raw.get("mode", "grid")

        if not isinstance(base_config, dict):
            raise SweepConfigError("base_config must be an object")
        if not axes or not isinstance(axes, dict):
            raise SweepConfigError("axes must be a non-empty object")
        for name, values in axes.items():
            if not isinstance(values, list) or not values:
                raise SweepConfigError(f"axis {name!r} must be a non-empty list")
        if not command or not isinstance(command, list):
            raise SweepConfigError("command must be a non-empty list of strings")
        if not gpus or not isinstance(gpus, list):
            raise SweepConfigError("gpus must be a non-empty list")
        if mode not in ("grid", "random"):
            raise SweepConfigError(f"mode must be 'grid' or 'random', got {mode!r}")
        budget = raw.get("budget")
        if mode == "random" and not budget:
            raise SweepConfigError("mode 'random' requires a positive 'budget'")

        overlap = set(base_config) & set(axes)
        if overlap:
            raise SweepConfigError(f"keys appear in both base_config and axes: {sorted(overlap)}")

        return cls(
            base_config=base_config,
            axes=axes,
            constraints=list(raw.get("constraints", [])),
            mode=mode,
            budget=budget,
            seed=int(raw.get("seed", 0)),
            command=list(command),
            gpus=list(gpus),
            max_workers=int(raw.get("max_workers", len(gpus))),
            compute_formula=raw.get("compute_formula"),
            timeout_seconds=raw.get("timeout_seconds"),
        )


# --------------------------------------------------------------------------
# Run config expansion.
# --------------------------------------------------------------------------


@dataclass
class RunConfig:
    run_id: str
    axis_values: dict[str, Any]
    config: dict[str, Any]


def _run_id(config: dict[str, Any]) -> str:
    import hashlib

    payload = json.dumps(config, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"run-{digest[:12]}"


def _constraints_satisfied(config: dict[str, Any], constraints: list[str]) -> bool:
    for expr in constraints:
        try:
            ok = safe_eval(expr, config)
        except ExpressionError as exc:
            raise SweepConfigError(f"constraint {expr!r} failed on config {config}: {exc}") from exc
        if not ok:
            return False
    return True


def _expand_grid(axes: dict[str, list[Any]]) -> list[dict[str, Any]]:
    names = list(axes.keys())
    value_lists = [axes[n] for n in names]
    return [dict(zip(names, values)) for values in itertools.product(*value_lists)]


def _expand_random(
    axes: dict[str, list[Any]],
    budget: int,
    seed: int,
    constraints: list[str],
    base_config: dict[str, Any],
) -> list[dict[str, Any]]:
    if np is None:
        raise SweepConfigError("numpy is required for random-search mode")
    rng = np.random.default_rng(seed)
    names = list(axes.keys())
    total_space = math.prod(len(axes[n]) for n in names)
    target = min(budget, total_space)

    seen: set[tuple[Any, ...]] = set()
    combos: list[dict[str, Any]] = []
    max_attempts = max(target * 200, 2000)
    attempts = 0
    while len(combos) < target and attempts < max_attempts:
        attempts += 1
        choice = {n: axes[n][int(rng.integers(0, len(axes[n])))] for n in names}
        key = tuple(choice[n] for n in names)
        if key in seen:
            continue
        seen.add(key)
        if _constraints_satisfied({**base_config, **choice}, constraints):
            combos.append(choice)
    if len(combos) < target:
        logger.warning(
            "random search only found %d/%d constraint-satisfying combos after %d attempts",
            len(combos),
            target,
            attempts,
        )
    return combos


def build_run_configs(spec: SweepSpec) -> list[RunConfig]:
    if spec.mode == "grid":
        combos = _expand_grid(spec.axes)
        combos = [c for c in combos if _constraints_satisfied({**spec.base_config, **c}, spec.constraints)]
    else:
        assert spec.budget is not None
        combos = _expand_random(spec.axes, spec.budget, spec.seed, spec.constraints, spec.base_config)

    run_configs = []
    seen_ids: set[str] = set()
    for combo in combos:
        merged = {**spec.base_config, **combo}
        run_id = _run_id(merged)
        if run_id in seen_ids:
            continue
        seen_ids.add(run_id)
        run_configs.append(RunConfig(run_id=run_id, axis_values=combo, config=merged))
    return run_configs


# --------------------------------------------------------------------------
# Run execution.
# --------------------------------------------------------------------------

_OOM_PATTERN = re.compile(r"out of memory|\boom\b|cuda error: out of memory", re.IGNORECASE)


@dataclass
class RunResult:
    run_id: str
    config: dict[str, Any]
    status: str  # "completed" or "failed"
    eval_loss: float | None
    compute: float | None
    error: str | None
    duration_s: float
    gpu: int | None

    def to_state_entry(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "config": self.config,
            "eval_loss": self.eval_loss,
            "compute": self.compute,
            "error": self.error,
            "duration_s": self.duration_s,
            "gpu": self.gpu,
        }

    @classmethod
    def from_state(cls, run_id: str, entry: dict[str, Any]) -> "RunResult":
        return cls(
            run_id=run_id,
            config=entry.get("config", {}),
            status=entry.get("status", "failed"),
            eval_loss=entry.get("eval_loss"),
            compute=entry.get("compute"),
            error=entry.get("error"),
            duration_s=entry.get("duration_s", 0.0),
            gpu=entry.get("gpu"),
        )


class SweepState:
    """A JSON-backed, thread-safe run_id -> result-entry map, for resume."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                logger.warning("could not read state file %s, starting fresh", path)
                self.data = {}

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self.data.get(run_id)

    def update(self, run_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self.data[run_id] = entry
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True, default=str))
        tmp.replace(self.path)


def _read_metrics(metrics_path: Path) -> list[dict[str, Any]]:
    if not metrics_path.exists():
        return []
    records = []
    with open(metrics_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def _extract_eval_loss(metrics: list[dict[str, Any]]) -> float | None:
    for record in reversed(metrics):
        if "eval_loss" in record:
            try:
                return float(record["eval_loss"])
            except (TypeError, ValueError):
                continue
    for record in reversed(metrics):
        if "loss" in record:
            try:
                return float(record["loss"])
            except (TypeError, ValueError):
                continue
    return None


def _extract_compute(
    metrics: list[dict[str, Any]], config: dict[str, Any], compute_formula: str | None
) -> float | None:
    for record in reversed(metrics):
        if "compute" in record:
            try:
                return float(record["compute"])
            except (TypeError, ValueError):
                continue
    if compute_formula:
        try:
            value = safe_eval(compute_formula, config)
            return float(value)
        except (ExpressionError, TypeError, ValueError) as exc:
            logger.warning("compute_formula failed on config %s: %s", config, exc)
    return None


def run_one(run_cfg: RunConfig, spec: SweepSpec, gpu_id: int, output_dir: Path) -> RunResult:
    run_dir = output_dir / "runs" / run_cfg.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    metrics_path = run_dir / "metrics.jsonl"
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    config_path.write_text(json.dumps(run_cfg.config, indent=2, sort_keys=True, default=str))
    if metrics_path.exists():
        metrics_path.unlink()  # drop stale metrics from a previous failed attempt

    fmt_vars = {
        **run_cfg.config,
        "config_path": str(config_path),
        "metrics_path": str(metrics_path),
        "run_id": run_cfg.run_id,
        "run_dir": str(run_dir),
        "gpu": gpu_id,
    }
    try:
        cmd = [str(arg).format(**fmt_vars) for arg in spec.command]
    except KeyError as exc:
        raise SweepConfigError(f"command template references unknown placeholder {exc}") from exc

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    oom_flag = threading.Event()
    start = time.monotonic()

    with open(stdout_path, "w", encoding="utf-8") as stdout_f, open(
        stderr_path, "w", encoding="utf-8"
    ) as stderr_f:
        try:
            proc = subprocess.Popen(cmd, env=env, stdout=stdout_f, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            return RunResult(
                run_id=run_cfg.run_id,
                config=run_cfg.config,
                status="failed",
                eval_loss=None,
                compute=None,
                error=f"launch_failed: {exc}",
                duration_s=time.monotonic() - start,
                gpu=gpu_id,
            )

        def _watch_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_f.write(line)
                if _OOM_PATTERN.search(line):
                    oom_flag.set()
                    proc.terminate()

        watcher = threading.Thread(target=_watch_stderr, daemon=True)
        watcher.start()

        timed_out = False
        try:
            proc.wait(timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        watcher.join(timeout=5)

    duration = time.monotonic() - start
    metrics = _read_metrics(metrics_path)
    eval_loss = _extract_eval_loss(metrics)
    compute = _extract_compute(metrics, run_cfg.config, spec.compute_formula)

    status = "completed"
    error = None
    if oom_flag.is_set():
        status, error = "failed", "oom"
    elif timed_out:
        status, error = "failed", "timeout"
    elif proc.returncode != 0:
        status, error = "failed", f"exit_code={proc.returncode}"
    elif eval_loss is None:
        status, error = "failed", "no_metrics"

    return RunResult(
        run_id=run_cfg.run_id,
        config=run_cfg.config,
        status=status,
        eval_loss=eval_loss,
        compute=compute,
        error=error,
        duration_s=duration,
        gpu=gpu_id,
    )


# --------------------------------------------------------------------------
# Sweep driver: bounded worker pool, one GPU per run, resume.
# --------------------------------------------------------------------------


def launch_sweep(spec: SweepSpec, output_dir: Path, resume: bool = True) -> list[RunResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state = SweepState(output_dir / "state.json")

    run_configs = build_run_configs(spec)
    pending: list[RunConfig] = []
    results: list[RunResult] = []
    for rc in run_configs:
        entry = state.get(rc.run_id) if resume else None
        if entry and entry.get("status") == "completed":
            results.append(RunResult.from_state(rc.run_id, entry))
            continue
        pending.append(rc)

    logger.info(
        "%d run(s) total, %d already completed, %d to launch",
        len(run_configs),
        len(results),
        len(pending),
    )
    if not pending:
        return results

    gpu_queue: "queue.Queue[int]" = queue.Queue()
    for gpu in spec.gpus:
        gpu_queue.put(gpu)

    def _task(rc: RunConfig) -> RunResult:
        gpu_id = gpu_queue.get()
        try:
            state.update(rc.run_id, {"status": "running", "config": rc.config})
            logger.info("starting %s on gpu %s: %s", rc.run_id, gpu_id, rc.axis_values)
            try:
                result = run_one(rc, spec, gpu_id, output_dir)
            except Exception as exc:  # noqa: BLE001 - a driver bug must not sink the sweep
                logger.exception("driver error running %s", rc.run_id)
                result = RunResult(
                    run_id=rc.run_id,
                    config=rc.config,
                    status="failed",
                    eval_loss=None,
                    compute=None,
                    error=f"driver_error: {exc}",
                    duration_s=0.0,
                    gpu=gpu_id,
                )
            state.update(rc.run_id, result.to_state_entry())
            logger.info(
                "finished %s: status=%s eval_loss=%s error=%s",
                rc.run_id,
                result.status,
                result.eval_loss,
                result.error,
            )
            return result
        finally:
            gpu_queue.put(gpu_id)

    with ThreadPoolExecutor(max_workers=spec.max_workers) as pool:
        futures = {pool.submit(_task, rc): rc for rc in pending}
        for fut in as_completed(futures):
            results.append(fut.result())

    return results


# --------------------------------------------------------------------------
# Aggregation.
# --------------------------------------------------------------------------


def _sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value)
    return (1, str(value))


def _axis_marginals(spec: SweepSpec, completed: list[RunResult]) -> dict[str, list[dict[str, Any]]]:
    axis_marginals: dict[str, list[dict[str, Any]]] = {}
    for axis in spec.axes:
        by_value: dict[Any, list[RunResult]] = defaultdict(list)
        for r in completed:
            if axis in r.config:
                by_value[r.config[axis]].append(r)
        rows = []
        for value, runs in sorted(by_value.items(), key=lambda kv: _sort_key(kv[0])):
            losses = [r.eval_loss for r in runs if r.eval_loss is not None]
            if not losses:
                continue
            rows.append({"value": value, "mean_eval_loss": statistics.fmean(losses), "n": len(losses)})
        axis_marginals[axis] = rows
    return axis_marginals


def _best_per_axis(spec: SweepSpec, completed: list[RunResult]) -> dict[str, list[dict[str, Any]]]:
    best_per_axis: dict[str, list[dict[str, Any]]] = {}
    for axis in spec.axes:
        by_value: dict[Any, list[RunResult]] = defaultdict(list)
        for r in completed:
            if axis in r.config and r.eval_loss is not None:
                by_value[r.config[axis]].append(r)
        rows = []
        for value, runs in sorted(by_value.items(), key=lambda kv: _sort_key(kv[0])):
            best = min(runs, key=lambda r: r.eval_loss)  # type: ignore[arg-type]
            rows.append(
                {
                    "value": value,
                    "run_id": best.run_id,
                    "eval_loss": best.eval_loss,
                    "config": best.config,
                }
            )
        best_per_axis[axis] = rows
    return best_per_axis


def fit_scaling_law(completed: list[RunResult]) -> dict[str, Any]:
    """Fit L = a * C ** -b + c over (compute, eval_loss) pairs."""
    points = [(r.compute, r.eval_loss) for r in completed if r.compute and r.compute > 0 and r.eval_loss is not None]
    if len(points) < 4:
        return {
            "fit_ok": False,
            "reason": "need at least 4 completed runs with compute and eval_loss",
            "n_points": len(points),
        }
    if np is None or scipy_optimize is None:
        return {"fit_ok": False, "reason": "numpy/scipy not available", "n_points": len(points)}

    computes = np.array([p[0] for p in points], dtype=float)
    losses = np.array([p[1] for p in points], dtype=float)

    def model(c: Any, a: float, b: float, const: float) -> Any:
        return a * np.power(c, -b) + const

    a0 = max(float(losses.max() - losses.min()), 1e-6)
    c0 = max(float(losses.min()), 0.0)
    p0 = (a0, 0.1, c0)
    bounds = ([1e-8, 1e-6, 0.0], [np.inf, 5.0, float(losses.max()) + 1e-6])
    try:
        popt, _pcov = scipy_optimize.curve_fit(model, computes, losses, p0=p0, bounds=bounds, maxfev=20000)
    except (RuntimeError, ValueError) as exc:
        return {"fit_ok": False, "reason": str(exc), "n_points": len(points)}

    a, b, c = (float(x) for x in popt)
    predicted = model(computes, a, b, c)
    residuals = losses - predicted
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((losses - losses.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "fit_ok": True,
        "a": a,
        "b": b,
        "c": c,
        "r_squared": r_squared,
        "n_points": len(points),
        "formula": "L = a * C ** -b + c",
    }


def aggregate_results(spec: SweepSpec, results: list[RunResult]) -> dict[str, Any]:
    completed = [r for r in results if r.status == "completed"]
    failed = [r for r in results if r.status != "completed"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "num_runs_total": len(results),
        "num_completed": len(completed),
        "num_failed": len(failed),
        "failed_runs": [
            {"run_id": r.run_id, "config": r.config, "error": r.error} for r in failed
        ],
        "axis_marginals": _axis_marginals(spec, completed),
        "best_per_axis": _best_per_axis(spec, completed),
        "scaling_law": fit_scaling_law(completed),
    }


# --------------------------------------------------------------------------
# Report writers.
# --------------------------------------------------------------------------


def write_json_report(aggregate: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(aggregate, indent=2, sort_keys=True, default=str))


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def write_markdown_report(aggregate: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Ablation sweep report")
    lines.append("")
    lines.append(f"Generated: {aggregate['generated_at']}")
    lines.append("")
    lines.append(
        f"- Total runs: {aggregate['num_runs_total']}\n"
        f"- Completed: {aggregate['num_completed']}\n"
        f"- Failed: {aggregate['num_failed']}"
    )
    lines.append("")

    if aggregate["failed_runs"]:
        lines.append("## Failed runs")
        lines.append("")
        lines.append("| run_id | error | config |")
        lines.append("|---|---|---|")
        for r in aggregate["failed_runs"]:
            lines.append(f"| {r['run_id']} | {r['error']} | `{json.dumps(r['config'], default=str)}` |")
        lines.append("")

    lines.append("## Per-axis marginal means")
    lines.append("")
    for axis, rows in aggregate["axis_marginals"].items():
        lines.append(f"### {axis}")
        lines.append("")
        lines.append("| value | mean eval_loss | n |")
        lines.append("|---|---|---|")
        for row in rows:
            lines.append(f"| {_fmt(row['value'])} | {_fmt(row['mean_eval_loss'])} | {row['n']} |")
        lines.append("")

    lines.append("## Best run per axis value")
    lines.append("")
    for axis, rows in aggregate["best_per_axis"].items():
        lines.append(f"### {axis}")
        lines.append("")
        lines.append("| value | best run_id | eval_loss |")
        lines.append("|---|---|---|")
        for row in rows:
            lines.append(f"| {_fmt(row['value'])} | {row['run_id']} | {_fmt(row['eval_loss'])} |")
        lines.append("")

    lines.append("## Scaling law fit: L = a * C ** -b + c")
    lines.append("")
    law = aggregate["scaling_law"]
    if law.get("fit_ok"):
        lines.append(f"- a = {_fmt(law['a'])}")
        lines.append(f"- b = {_fmt(law['b'])}")
        lines.append(f"- c = {_fmt(law['c'])}")
        lines.append(f"- R^2 = {_fmt(law['r_squared'])}")
        lines.append(f"- fitted on {law['n_points']} runs")
    else:
        lines.append(f"- fit not available: {law.get('reason')} ({law.get('n_points', 0)} usable points)")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    spec = SweepSpec.from_file(args.spec)
    run_configs = build_run_configs(spec)

    if args.dry_run:
        for rc in run_configs:
            print(json.dumps({"run_id": rc.run_id, "config": rc.config}, default=str))
        print(f"{len(run_configs)} run(s) after constraints.", file=sys.stderr)
        return 0

    results = launch_sweep(spec, args.output_dir, resume=not args.no_resume)
    aggregate = aggregate_results(spec, results)
    write_json_report(aggregate, args.output_dir / "aggregate.json")
    write_markdown_report(aggregate, args.output_dir / "aggregate.md")
    logger.info(
        "sweep complete: %d completed, %d failed",
        aggregate["num_completed"],
        aggregate["num_failed"],
    )
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    spec = SweepSpec.from_file(args.spec)
    state = SweepState(args.output_dir / "state.json")
    results = [RunResult.from_state(run_id, entry) for run_id, entry in state.data.items()]
    aggregate = aggregate_results(spec, results)
    write_json_report(aggregate, args.output_dir / "aggregate.json")
    write_markdown_report(aggregate, args.output_dir / "aggregate.md")
    logger.info(
        "aggregate written: %d completed, %d failed",
        aggregate["num_completed"],
        aggregate["num_failed"],
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sweep.py", description="Hyperparameter ablation sweep driver.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run (or resume) the sweep and write the aggregate report.")
    run_p.add_argument("spec", type=Path, help="path to the sweep spec JSON file")
    run_p.add_argument("--output-dir", type=Path, default=Path("sweep_output"))
    run_p.add_argument("--no-resume", action="store_true", help="ignore existing state and rerun everything")
    run_p.add_argument("--dry-run", action="store_true", help="print expanded run configs and exit")
    run_p.set_defaults(func=_cmd_run)

    agg_p = sub.add_parser("aggregate", help="Re-aggregate an existing sweep's state without launching runs.")
    agg_p.add_argument("spec", type=Path, help="path to the sweep spec JSON file")
    agg_p.add_argument("--output-dir", type=Path, required=True)
    agg_p.set_defaults(func=_cmd_aggregate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    try:
        return args.func(args)
    except SweepConfigError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
