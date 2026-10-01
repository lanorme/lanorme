"""Run the pinned third-party measurement tools in a subprocess.

Every analyser runs through ``uvx`` at a pinned version on Python 3.13, so
nothing is installed into the snapshot and every arm is scored by the same
tool builds. The project-environment tools (mypy, pytest) run through
``uv run`` inside the synced temporary copy instead.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TOOL_PYTHON = "3.13"
PINNED = {
    "radon": "6.0.1",
    "ruff": "0.15.8",
    "bandit": "1.9.4",
    "pylint": "4.1.1",
    "vulture": "2.16",
    "mypy": "1.19.1",
}
DEFAULT_TIMEOUT = 300
SECRET_MARKERS = ("API_KEY", "_TOKEN", "SECRET")
UV_CHATTER = re.compile(
    r"\s*(Installed|Uninstalled|Resolved|Prepared|Audited|Downloading|Downloaded|Building|Built) ",
)


@dataclass(frozen=True)
class ToolRun:
    """The outcome of one tool invocation."""

    returncode: int | None
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    def build_error_tail(self, lines: int = 15) -> str:
        """The last few lines of stdout and stderr, uv's install chatter dropped."""
        kept = [
            line
            for line in (self.stdout + "\n" + self.stderr).splitlines()
            if line.strip() and not UV_CHATTER.match(line)
        ]
        return "\n".join(kept[-lines:])


def build_tool_env() -> dict[str, str]:
    """The environment for a tool: no API keys, no inherited virtualenv."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SECRET_MARKERS)
    }
    env.pop("VIRTUAL_ENV", None)
    env["UV_NO_PROGRESS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_command(args: list[str], *, cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> ToolRun:
    """Run ``args`` in ``cwd``, capturing output; a timeout is recorded, not raised."""
    start = time.monotonic()
    try:
        done = subprocess.run(
            args,
            cwd=cwd,
            env=build_tool_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        return ToolRun(
            returncode=None,
            stdout=_decode(expired.stdout),
            stderr=_decode(expired.stderr),
            seconds=time.monotonic() - start,
            timed_out=True,
        )
    return ToolRun(done.returncode, done.stdout, done.stderr, time.monotonic() - start)


def run_uvx(tool: str, *, args: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> ToolRun:
    """Run a pinned analyser through ``uvx``."""
    command = ["uvx", "--python", TOOL_PYTHON, "--from", f"{tool}=={PINNED[tool]}", tool]
    return run_command([*command, *args], cwd=cwd, timeout=timeout)


def build_versions() -> dict[str, str]:
    """The pinned versions, for the output record."""
    return {**PINNED, "tool_python": TOOL_PYTHON}


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return data
