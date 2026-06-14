"""The generated docs must never drift from the tool.

``scripts/gen_docs.py`` renders the configuration reference, the rule index, the
JSON schema, and the ``llms.txt`` / ``llms-full.txt`` files from LaNorme itself.
This test runs the generator's ``--check`` mode, so a committed file that no
longer matches a fresh render fails CI rather than misleading a reader. Run
``python3 scripts/gen_docs.py`` to regenerate.

Tests follow AAA structure with inline ``# Arrange / # Act / # Assert`` markers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GEN = Path(__file__).resolve().parents[2] / "scripts" / "gen_docs.py"


def _load_generator() -> object:
    """Import scripts/gen_docs.py as a module (it is not on the package path)."""
    spec = importlib.util.spec_from_file_location("gen_docs", _GEN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass needs the module in sys.modules
    spec.loader.exec_module(module)
    return module


def test_generated_docs_are_in_sync():
    # Arrange.
    generator = _load_generator()

    # Act: --check returns 1 when any committed generated file is stale.
    exit_code = generator.main(argv=["--check"])

    # Assert.
    assert exit_code == 0, "generated docs are stale; run 'python3 scripts/gen_docs.py'"


def test_every_top_level_config_key_is_documented():
    # Arrange: the keys the CLI actually reads from [tool.lanorme].
    generator = _load_generator()
    documented = {key.name for key in generator.CONFIG_KEYS}
    cli_keys = {
        "select", "ignore", "exclude", "per-file-ignores",
        "promote", "extends", "baseline", "source_root", "plugins",
    }

    # Act / Assert: the generated reference covers every real top-level key.
    assert cli_keys <= documented, f"undocumented config keys: {cli_keys - documented}"
