"""A deliberately messy module used to prove LaNorme finds violations."""

from __future__ import annotations

from typing import Any


def process(data: dict[str, Any], count):  # TYPE-001 + KWARG-001
    import json  # IMPORT-001: inline import

    api_key = "sk-live-0123456789abcdef"  # SECRETPY-001: hardcoded secret
    return json.dumps({"data": data, "count": count, "key": api_key})


def ready() -> bool:  # NAMING-004 (warning): bool without is_/has_ prefix
    return True
