"""Process and configuration records: the stage transcript, its timing, the LaNorme config.

The transcript is Claude Code ``stream-json`` output: one JSON object per
line, assistant turns carrying ``tool_use`` blocks, and a final ``result``
line with the duration, turn count, cost and token usage. LaNorme is never
run here; its config is only read, for the orchestrator's analysis.
"""

import json
import re
import tomllib
from collections import Counter
from pathlib import Path

RESULT_FIELDS = (
    "subtype",
    "is_error",
    "duration_ms",
    "duration_api_ms",
    "num_turns",
    "total_cost_usd",
    "usage",
)
LANORME_CHECK = re.compile(r"lanorme(?:@[\w.]+)?\s+check\b")


def measure_transcript(transcript: Path, *, timing: Path) -> dict[str, object]:
    """Wall time, turns, cost and tool use for one stage, plus LaNorme compliance."""
    record: dict[str, object] = {"timing": _read_json(timing)}
    if not transcript.is_file():
        record["transcript"] = None
        return record
    result: dict[str, object] | None = None
    tools: Counter[str] = Counter()
    commands: list[str] = []
    bad_lines = 0
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if event.get("type") == "result":
            result = {field: event.get(field) for field in RESULT_FIELDS}
        for block in _iter_tool_uses(event):
            tools[block.get("name", "?")] += 1
            if block.get("name") == "Bash":
                commands.append(str(block.get("input", {}).get("command", "")))
    lanorme = [command for command in commands if "lanorme" in command.lower()]
    record["transcript"] = {
        "result": result,
        "finished": result is not None,
        "tool_uses": dict(tools.most_common()),
        "bash_calls": len(commands),
        "lanorme_bash_calls": len(lanorme),
        "lanorme_check_calls": sum(bool(LANORME_CHECK.search(c)) for c in lanorme),
        "unparseable_lines": bad_lines,
    }
    return record


def _iter_tool_uses(event: dict[str, object]) -> list[dict]:
    if event.get("type") != "assistant":
        return []
    content = (event.get("message") or {}).get("content") or []
    return [
        block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _read_json(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"unparseable": path.read_text(encoding="utf-8", errors="replace")}


def read_lanorme_config(root: Path) -> dict[str, object]:
    """The LaNorme config the agent wrote: ``lanorme.toml`` and ``[tool.lanorme]``, raw and parsed."""
    config: dict[str, object] = {
        "lanorme_toml": _read_toml_file(root / "lanorme.toml"),
        "pyproject_tool_lanorme": _read_pyproject_table(root / "pyproject.toml"),
    }
    config["present"] = any(
        config[key] is not None for key in ("lanorme_toml", "pyproject_tool_lanorme")
    )
    return config


def _read_toml_file(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    return {"raw": raw, "parsed": _parse_toml(raw)}


def _read_pyproject_table(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    parsed = _parse_toml(raw)
    table = parsed.get("tool", {}).get("lanorme") if isinstance(parsed, dict) else None
    section = _extract_tool_lanorme_text(raw)
    if table is None and not section:
        return None
    return {"raw": section, "parsed": table}


def _extract_tool_lanorme_text(raw: str) -> str:
    """The verbatim ``[tool.lanorme]`` table text (and its sub-tables), comments included."""
    kept: list[str] = []
    inside = False
    for line in raw.splitlines():
        header = re.match(r"\s*\[\[?\s*([^\]]+?)\s*\]", line)
        if header:
            inside = header.group(1).startswith("tool.lanorme")
        if inside:
            kept.append(line)
    return "\n".join(kept).strip()


def _parse_toml(raw: str) -> object:
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as error:
        return {"toml_error": str(error)}
