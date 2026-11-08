"""The Org Profile reaches Discover through the flow (spec 10, D-077).

``FlowService.create_session`` sends the tenant's profile to the engine with the FIRST call (``start``)
of an Enterprise session and never again: after that the profile lives in the slots. A Guided session
carries nothing, even when a profile is passed. Run over both stores (the in-memory fake and
Postgres); the real engine into the real service is tested in ``apps/context-api/tests``.
"""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from mde_contracts.common import Mode
from mde_flow.testing.fakes import FakeDiscoveryEngine
from mde_flow.testing.kit import Driver, make_flow_kit, principal

PROFILE: dict[str, JsonValue] = {
    "brand": {"name": "Acme", "direction": "bold-dark"},
    "identity": {"sso": "required"},
    "compliance": {"regions": ["in"]},
    "integrations": ["crm"],
}


@pytest.fixture(autouse=True)
def _every_store(store_matrix: None) -> None:
    """Run every test over each store of the matrix (conftest: memory, Postgres)."""


async def start(
    mode: Mode, profile: dict[str, JsonValue] | None
) -> tuple[Driver, FakeDiscoveryEngine]:
    kit = make_flow_kit()
    who = principal()
    rec, events = await kit.service.create_session(who, mode, None, org_profile=profile)
    driver = Driver(kit, who, rec.id)
    driver.events.extend(events)
    return driver, kit.discovery


async def test_an_enterprise_session_sends_the_profile_with_start_once() -> None:
    driver, engine = await start("enterprise", PROFILE)
    assert engine.profile_seen_by("start") == [PROFILE]
    assert engine.calls[0] == ("start", "")
    assert driver.events and driver.events[0].type == "assistant_message"


async def test_later_turns_do_not_carry_the_profile() -> None:
    driver, engine = await start("enterprise", PROFILE)
    await driver.to_gate1()  # answers, the Brief compile and the gate
    await driver.say("set design.direction=bold-dark")  # a change after the gate
    names = [name for name, _ in engine.org_profiles]
    assert names[0] == "start" and len(names) > 3
    assert engine.profile_seen_by("start") == [PROFILE]
    for name, seen in engine.org_profiles[1:]:
        assert seen is None, name


async def test_a_guided_session_never_carries_a_profile_even_when_one_is_passed() -> None:
    _, engine = await start("guided", PROFILE)
    assert engine.profile_seen_by("start") == [None]


async def test_an_enterprise_session_without_a_profile_sends_none() -> None:
    _, engine = await start("enterprise", None)
    assert engine.profile_seen_by("start") == [None]


async def test_the_profile_itself_is_not_stored_in_the_session_record() -> None:
    driver, _ = await start("enterprise", PROFILE)
    async with driver.kit.store.begin(driver.who.tenant_id) as uow:
        rec = await uow.sessions.get(driver.session_id)
    assert rec is not None
    assert "Acme" not in rec.model_dump_json()  # the engine decides what to keep: the slots
