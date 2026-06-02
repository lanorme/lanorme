"""Tests for the CLI config-wiring the unit checks cannot exercise directly.

These lock the parts that live in ``cli.py``: that ``source_root`` is injected
into the two layout-aware checks and *only* those, and that a configured
``exclude`` reaches the discovery layer through ``main``.
"""

from __future__ import annotations

from pathlib import Path

from lanorme import _registry, discovery, get_check, register
from lanorme.cli import _apply_check_config, main


class _Spy:
    """A throwaway configurable check that records the settings it is handed."""

    name = "spy_check"
    description = "records settings"
    rules: list[str] = []

    def __init__(self) -> None:
        self.received: dict[str, object] | None = None

    def configure(self, *, settings: dict[str, object]) -> None:
        self.received = dict(settings)


def test_source_root_injected_only_into_layout_checks():
    # Arrange: a spy stands in for a generic configurable check.
    spy = _Spy()
    register(spy)
    try:
        # Act.
        _apply_check_config(
            config={
                "source_root": "src/pkg",
                "layer_deps": {"composition_root": ["api/dependencies.py"]},
                "spy_check": {"some_key": 1},
            }
        )

        # Assert: the two layout-aware checks receive it; the spy does not.
        assert get_check("layer_deps").source_root == "src/pkg"
        assert get_check("port_coverage").source_root == "src/pkg"
        assert spy.received == {"some_key": 1}
        assert "source_root" not in spy.received
    finally:
        _registry.pop("spy_check", None)


def test_main_publishes_configured_excludes_to_discovery(tmp_path: Path, capsys):
    # Arrange: a project that configures an exclude glob.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lanorme]\nexclude = ["vendor/*"]\n', encoding="utf-8"
    )
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

    # Act: run the real CLI entry point (it may exit nonzero on findings).
    try:
        main(["check", str(tmp_path), "--json"])
    except SystemExit:
        pass

    # Assert: the configured glob reached the discovery layer, not just output.
    assert "vendor/*" in discovery.active_excludes()
