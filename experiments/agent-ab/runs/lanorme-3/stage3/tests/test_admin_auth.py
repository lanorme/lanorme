"""The admin-key comparison and how tenant system prompts join the agent's own."""

import pytest

from app.agent import SYSTEM_PROMPT, compose_system_prompt
from app.api.admin_auth import is_admin_key_valid


@pytest.mark.parametrize(
    ("presented", "expected", "valid"),
    [
        ("k3y", "k3y", True),
        ("k3y", "k3y ", False),
        ("K3Y", "k3y", False),
        (None, "k3y", False),
        ("k3y", None, False),
        (None, None, False),
        ("clé", "clé", True),
    ],
)
def test_admin_key_must_match_exactly(presented: str | None, expected: str | None, valid: bool) -> None:
    assert is_admin_key_valid(presented=presented, expected=expected) is valid


@pytest.mark.parametrize("tenant_prompt", [None, "", "   \n"])
def test_blank_tenant_prompt_leaves_our_instructions_alone(tenant_prompt: str | None) -> None:
    assert compose_system_prompt(tenant_prompt) == SYSTEM_PROMPT


def test_tenant_prompt_follows_our_instructions() -> None:
    composed = compose_system_prompt("Answer in French.")

    assert composed.startswith(SYSTEM_PROMPT)
    assert composed.endswith("Answer in French.")
