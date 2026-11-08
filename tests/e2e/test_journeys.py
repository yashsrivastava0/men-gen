"""End to end, mock mode: "hi" to ``spec_ready`` through both gates, per archetype and policy.

For every journey: a valid ``APP_SPEC.md`` (the nine validators clean, the hash verifies, the file
parses back and re-renders to the same bytes), the consumer simulator passes with every slice inside
its budget, the hand-off repo holds the committed file and ``spec.lock.json``, ``spec_ready`` fires
exactly once and the run made no network call (``socket_guard``).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import yaml

from evals.harness.briefs import load_briefs, result_expected
from evals.harness.checks import check_spec, read_handoff_repo
from evals.harness.driver import Driver
from evals.harness.journeys import (
    ENTERPRISE,
    GUIDED,
    LANGUAGES,
    Journey,
    JourneyResult,
    collect,
    play_journey,
    reach_plan_gate,
)
from evals.harness.stack import Stack
from mde_contracts.events import GateEvent, StateChanged
from mde_spec_builder.validators import VALIDATOR_NAMES

pytestmark = pytest.mark.e2e

Opener = Callable[..., tuple[Stack, Driver]]

#: a Guided session asks about seven cards (spec 3.3), never more than ten
GUIDED_CARD_CAP = 10


def assert_valid_handoff(stack: Stack, result: JourneyResult, policy_id: str) -> None:
    record = result.spec
    checks = check_spec(record, stack.policies(policy_id))
    assert checks.ok, checks.problems
    assert checks.validators_run == VALIDATOR_NAMES and len(VALIDATOR_NAMES) == 9
    assert checks.errors == []
    assert checks.embedded_hash_verifies and checks.round_trips
    assert checks.consumer.slices, "the consumer walked no slice"
    assert all(not s.over_budget for s in checks.consumer.slices)
    assert record.status == "handed_off" and record.git_sha
    [ready] = result.spec_ready  # exactly one spec_ready ...
    assert (ready.version, ready.hash, ready.git_sha) == (
        record.version,
        record.hash,
        record.git_sha,
    )
    assert [n.hash for n in stack.frontend.notifications] == [record.hash]  # ... one notification
    repo = read_handoff_repo(stack.repo, result.tenant_id, result.app_id)  # the hand-off commit
    assert repo.spec_text == record.spec_text
    assert repo.lock == {"version": record.version, "hash": record.hash}
    assert repo.commits == 1 and f"spec-v{record.version}" in repo.tags


async def play_checked(driver: Driver, stack: Stack, journey: Journey) -> JourneyResult:
    """The journey, approving Gate 2 role by role and checking that nothing is handed off before
    the last required role has approved the same (version, hash)."""
    await reach_plan_gate(driver, journey)
    gate = next(
        e for e in reversed(await driver.events()) if isinstance(e, GateEvent) and e.gate == "plan"
    )
    roles = gate.required_roles or ["requester"]
    for done, role in enumerate(roles, start=1):
        assert (await driver.snapshot()).state == "PLAN_REVIEW"
        assert not stack.frontend.notifications, "spec_ready fired before every role approved"
        assert not list(stack.projects_dir.rglob("APP_SPEC.md")), "a file was committed too early"
        await driver.decide(
            "plan",
            "approve",
            role=role,
            as_user=None if role == "requester" else f"u-{role}",
        )
        approved = [a for a in await driver.approvals() if a.gate == "plan"]
        assert [a.role for a in approved] == roles[:done]
        assert {(a.version, a.hash) for a in approved} == {(gate.version, gate.hash)}
    assert (await driver.snapshot()).state == "HANDED_OFF"
    return await collect(driver, journey)


@pytest.mark.parametrize("journey", GUIDED, ids=lambda j: j.name)
async def test_guided_journey(journey: Journey, open_session: Opener) -> None:
    stack, driver = open_session()
    result = await play_checked(driver, stack, journey)
    assert_valid_handoff(stack, result, "guided-default")
    # one tap per gate: one approval each, by the requester, through the buttons
    gates = [(a.gate, a.decision, a.via, a.role) for a in result.approvals]
    assert gates == [
        ("brief", "approve", "tap", "requester"),
        ("plan", "approve", "tap", "requester"),
    ]
    assert result.approvals[0].hash != result.approvals[1].hash  # each bound to its own artifact
    assert len(result.cards_asked) <= GUIDED_CARD_CAP, result.cards_asked
    session = await driver.session_record()
    assert session.discovery.slots["idea.archetype"].value == journey.archetype
    # the golden brief of this archetype: the same slots, screens, data and flows, and the same
    # bytes as the golden written by an earlier process (deterministic bytes for the same script)
    brief = next(b for b in load_briefs() if b.archetype == journey.archetype)
    assert brief.opening == journey.opening
    expected = yaml.safe_load(brief.expected_path.read_text(encoding="utf-8"))
    assert result_expected(result, session) == expected
    golden = brief.golden_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert result.spec.spec_text == golden, "the spec differs from its golden (make_goldens)"


@pytest.mark.parametrize("journey", ENTERPRISE, ids=lambda j: j.name)
async def test_enterprise_journey(journey: Journey, open_session: Opener) -> None:
    stack, driver = open_session()
    result = await play_checked(driver, stack, journey)
    assert journey.policy_id is not None
    assert_valid_handoff(stack, result, journey.policy_id)
    policy = stack.policies(journey.policy_id)
    # every required role approved each gate, all on the same (version, hash)
    for gate_kind, rule in (("brief", policy.gates.brief), ("plan", policy.gates.plan)):
        approved = [a for a in result.approvals if a.gate == gate_kind and a.decision == "approve"]
        assert sorted(a.role or "" for a in approved) == sorted(rule.approvers), gate_kind
        assert len({(a.version, a.hash) for a in approved}) == 1, gate_kind
    plan_hash = {a.hash for a in result.approvals if a.gate == "plan"}
    assert plan_hash == {result.spec.hash}
    assert result.spec.spec.header.mode == "enterprise"


@pytest.mark.parametrize("journey", LANGUAGES, ids=lambda j: j.name)
async def test_hindi_and_hinglish_journeys(journey: Journey, open_session: Opener) -> None:
    stack, driver = open_session()
    result = await play_journey(driver, journey, hi=False)
    assert_valid_handoff(stack, result, "guided-default")
    session = await driver.session_record()
    assert session.language == journey.language
    assert session.discovery.slots["locale"].value == journey.language
    assert "SCR-001" in result.spec.spec_text  # spec keywords and ids stay English
    transitions = [e for e in result.events if isinstance(e, StateChanged)]
    assert transitions[-1].to_state == "HANDED_OFF"
