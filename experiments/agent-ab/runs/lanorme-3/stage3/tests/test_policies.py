"""Policy resolution and the in-memory store, without HTTP."""

import asyncio

from app.config import Settings
from app.policies import GuardrailPolicy, InMemoryPolicyStore, TenantPolicies, build_default_policy

STRICT = GuardrailPolicy(blocked_topics=("gambling",), redact_pii=False, max_tool_calls=1, system_prompt="Be brief.")
DEFAULT = GuardrailPolicy(blocked_topics=("weapons", "malware"), redact_pii=True, max_tool_calls=5, system_prompt=None)


def test_default_policy_follows_the_settings() -> None:
    policy = build_default_policy(Settings(blocked_topics=("x",), max_tool_calls=3))

    assert policy == GuardrailPolicy(blocked_topics=("x",), redact_pii=True, max_tool_calls=3, system_prompt=None)


def test_unset_settings_give_the_contract_default() -> None:
    assert build_default_policy(Settings()) == DEFAULT


def test_store_saves_replaces_and_deletes() -> None:
    async def scenario() -> list[GuardrailPolicy | None]:
        store = InMemoryPolicyStore()
        seen = [await store.get_policy("acme")]
        await store.save_policy(tenant_id="acme", policy=DEFAULT)
        await store.save_policy(tenant_id="acme", policy=STRICT)
        seen.append(await store.get_policy("acme"))
        await store.delete_policy("acme")
        await store.delete_policy("acme")
        seen.append(await store.get_policy("acme"))
        return seen

    assert asyncio.run(scenario()) == [None, STRICT, None]


def test_tenants_fall_back_to_the_default_independently() -> None:
    async def scenario() -> tuple[GuardrailPolicy, GuardrailPolicy]:
        policies = TenantPolicies(store=InMemoryPolicyStore(), default=DEFAULT)
        await policies.save_policy(tenant_id="acme", policy=STRICT)
        return await policies.get_effective_policy("acme"), await policies.get_effective_policy("globex")

    assert asyncio.run(scenario()) == (STRICT, DEFAULT)
