import asyncio

from app.domain.policy import GuardrailPolicy
from app.infrastructure.repositories.in_memory_policies import InMemoryPolicyRepository

FIRST = GuardrailPolicy(blocked_topics=("a",), redact_pii=True, max_tool_calls=1)
SECOND = GuardrailPolicy(blocked_topics=("b",), redact_pii=False, max_tool_calls=2)


def test_absent_tenant_has_no_policy() -> None:
    assert asyncio.run(InMemoryPolicyRepository().get_policy("acme")) is None


def test_save_replaces_and_delete_forgets() -> None:
    # Given
    repository = InMemoryPolicyRepository()
    asyncio.run(repository.save_policy(tenant_id="acme", policy=FIRST))

    # When
    asyncio.run(repository.save_policy(tenant_id="acme", policy=SECOND))
    replaced = asyncio.run(repository.get_policy("acme"))
    asyncio.run(repository.delete_policy("acme"))
    asyncio.run(repository.delete_policy("acme"))

    # Then
    assert replaced == SECOND
    assert asyncio.run(repository.get_policy("acme")) is None


def test_tenants_are_kept_apart() -> None:
    # Given
    repository = InMemoryPolicyRepository()

    # When
    asyncio.run(repository.save_policy(tenant_id="acme", policy=FIRST))

    # Then
    assert asyncio.run(repository.get_policy("globex")) is None
