import asyncio

import pytest

from app.application.services.policies import PolicyService
from app.domain.policy import GuardrailPolicy
from app.infrastructure.repositories.in_memory_policies import InMemoryPolicyRepository

DEFAULT = GuardrailPolicy(blocked_topics=("weapons",), redact_pii=True, max_tool_calls=5)
CUSTOM = GuardrailPolicy(
    blocked_topics=("gambling",), redact_pii=False, max_tool_calls=1, system_prompt="Be brief."
)


def build_service() -> PolicyService:
    return PolicyService(repository=InMemoryPolicyRepository(), default=DEFAULT)


def test_tenant_without_policy_gets_the_default() -> None:
    assert asyncio.run(build_service().get_policy("acme")) == DEFAULT


def test_saved_policy_is_returned_and_applies_only_to_its_tenant() -> None:
    # Given
    service = build_service()

    # When
    saved = asyncio.run(service.save_policy(tenant_id="acme", policy=CUSTOM))

    # Then
    assert saved == CUSTOM
    assert asyncio.run(service.get_policy("acme")) == CUSTOM
    assert asyncio.run(service.get_policy("globex")) == DEFAULT


def test_reset_falls_back_to_the_default() -> None:
    # Given
    service = build_service()
    asyncio.run(service.save_policy(tenant_id="acme", policy=CUSTOM))

    # When
    asyncio.run(service.reset_policy("acme"))

    # Then
    assert asyncio.run(service.get_policy("acme")) == DEFAULT


def test_reset_without_a_stored_policy_is_harmless() -> None:
    service = build_service()

    asyncio.run(service.reset_policy("nobody"))

    assert asyncio.run(service.get_policy("nobody")) == DEFAULT


def test_policy_rejects_negative_tool_limit() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        GuardrailPolicy(blocked_topics=(), redact_pii=True, max_tool_calls=-1)
