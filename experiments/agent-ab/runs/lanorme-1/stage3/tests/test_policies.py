import asyncio

import pytest
from pydantic import ValidationError

from app.policies import InMemoryPolicyStore, TenantPolicies, TenantPolicy

DEFAULT = TenantPolicy(
    blocked_topics=("weapons",), redact_pii=True, max_tool_calls=5, system_prompt=None
)
CUSTOM = TenantPolicy(
    blocked_topics=("crypto",), redact_pii=False, max_tool_calls=1, system_prompt="Be terse."
)


def test_json_policy_round_trips() -> None:
    policy = TenantPolicy.model_validate_json(CUSTOM.model_dump_json())

    assert policy == CUSTOM


def test_topics_are_trimmed() -> None:
    policy = TenantPolicy.model_validate_json(
        '{"blocked_topics": [" crypto "], "redact_pii": true, "max_tool_calls": 0,'
        ' "system_prompt": null}'
    )

    assert policy.blocked_topics == ("crypto",)


def test_policies_fall_back_to_the_default_until_one_is_saved() -> None:
    # Given
    policies = TenantPolicies(store=InMemoryPolicyStore(), default=DEFAULT)

    # When
    before = asyncio.run(policies.get_effective("acme"))
    asyncio.run(policies.save(tenant_id="acme", policy=CUSTOM))
    after = asyncio.run(policies.get_effective("acme"))
    other = asyncio.run(policies.get_effective("globex"))

    # Then
    assert (before, after, other) == (DEFAULT, CUSTOM, DEFAULT)


def test_reset_restores_the_default_and_tolerates_a_missing_policy() -> None:
    # Given
    policies = TenantPolicies(store=InMemoryPolicyStore(), default=DEFAULT)
    asyncio.run(policies.save(tenant_id="acme", policy=CUSTOM))

    # When
    asyncio.run(policies.reset("acme"))
    asyncio.run(policies.reset("acme"))

    # Then
    assert asyncio.run(policies.get_effective("acme")) == DEFAULT


@pytest.mark.parametrize(
    "payload",
    [
        '{"blocked_topics": [], "redact_pii": "yes", "max_tool_calls": 1, "system_prompt": null}',
        '{"blocked_topics": [], "redact_pii": true, "max_tool_calls": 1.5, "system_prompt": null}',
        '{"blocked_topics": [""], "redact_pii": true, "max_tool_calls": 1, "system_prompt": null}',
        '{"blocked_topics": [], "redact_pii": true, "max_tool_calls": 1}',
    ],
)
def test_invalid_policies_are_rejected(payload: str) -> None:
    with pytest.raises(ValidationError):
        TenantPolicy.model_validate_json(payload)
