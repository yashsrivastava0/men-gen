"""The structure pipeline (ADR 0017) over the REAL call pipeline in mock mode: tier to band to counts, idempotence,
digests that ignore pins, pinned records, failed calls, a critic that can only report, evidence from the person's words."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.registry import all_specs, build_ports
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.catalog.size_bands import get_band
from mde_compass.domain.brief import Brief, DomainFact, FeatureRecord
from mde_compass.domain.ids import IdLedger
from mde_compass.domain.interview import Fact
from mde_compass.engine import structure_checks as checks
from mde_compass.engine import structure_derive as derive
from mde_compass.engine import structure_draft as sd
from mde_compass.engine import structure_merge as merge
from mde_compass.engine.ports import Ports, TurnContext
from mde_compass.engine.structure_testing import sample_app
from mde_compass.engine.structure_wire import (
    FindingOut,
    ModuleOutput,
    OutlineOutput,
    ReviewOutput,
)
from mde_core.errors import ProviderError

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
CTX = TurnContext(session_id="ses_1", tenant_id="t1")
FAMILIES = ["booking_scheduling", "communication", "health_wellness", "payments_wallet"]


@pytest.fixture(autouse=True)
def _quiet() -> None:
    logging.getLogger().setLevel(logging.ERROR)


def real_ports() -> Ports:
    return build_ports(CallRunner(make_client(all_specs())), load_catalog())


def bare(n: int = 8, *, roles: int = 3) -> Brief:
    """The clinic's roles and apps with ``n`` features and NO structure."""
    b = sample_app()
    b.modules, b.screens, b.flows, b.entities, b.domain_facts = [], [], [], [], []
    b.ids = IdLedger()  # the structure ids start from 001
    for a in b.apps:
        b.ids.note_existing(a.id)
    for r in b.roles:
        b.ids.note_existing(r.id)
    b.data, b.access, b.experience = (
        {},
        {},
        {},
    )  # nothing sensitive, no sign-in, one language: no realism rule applies
    b.roles = b.roles[:roles] if roles <= 3 else b.roles
    b.scope = [
        FeatureRecord(
            id=f"FTR-{i:03d}",
            name=f"Thing{i}",
            intent=f"Do thing {i}",
            priority="must" if i <= 3 else "nice",
            family=FAMILIES[i % 4],
        )
        for i in range(1, n + 1)
    ]
    return b


def make_input(brief: Brief, band: str = "medium", **kw: Any) -> sd.StructureInput:
    return sd.StructureInput(
        brief=brief,
        facts=kw.pop("facts", []),
        language=kw.pop("language", "en"),
        band=get_band(band),
        project_card="Idea",
        org="",
        fact_digest="",
        now=NOW,
        **kw,
    )


async def draft(
    brief: Brief, band: str = "medium", ports: Ports | None = None, **kw: Any
) -> sd.StructureResult:
    return await sd.compute_structure(make_input(brief, band, **kw), ports or real_ports(), CTX)


def settled(res: sd.StructureResult) -> Brief:
    """The working copy with the digests stored, as the controller leaves the Brief after applying a result."""
    out = res.work.model_copy(deep=True)
    out.meta.compile_digests.update(res.digests)
    return out


# ------------------------------------------------------------------------- tier to band to counts (the owner's sizes)


@pytest.mark.parametrize(
    ("band", "features", "screens"),
    [("small", 4, (8, 15)), ("medium", 7, (20, 30)), ("large", 10, (40, 60))],
)
async def test_each_band_gives_a_structure_of_its_size_with_no_error(
    band: str, features: int, screens: tuple[int, int]
) -> None:
    res = await draft(bare(features), band)
    c = derive.counts(res.work)
    assert screens[0] <= c.screens <= screens[1], (band, c)
    assert c.modules >= 2 and c.flows >= 2 and c.entities >= 3
    assert res.fallbacks == 0 and checks.errors(res.issues) == []
    assert get_band(band).in_range("screens", c.screens)


async def test_the_calls_per_band_are_outline_one_wave_and_a_critic_only_where_the_band_asks() -> (
    None
):
    small = await draft(bare(4), "small")
    assert (
        small.calls == 2
    )  # the outline and ONE group call for every module; no critic for a small app
    medium = await draft(bare(7), "medium")
    large = await draft(bare(10), "large")
    # outline + groups of three (medium) or two (large) modules + the critic (+ one repair call per module with a finding)
    assert medium.calls >= 1 + 2 + 1 and large.calls >= 1 + 4 + 1
    assert large.calls > medium.calls > small.calls


async def test_every_in_scope_feature_gets_screens_and_a_later_one_gets_none() -> None:
    brief = bare(5)
    brief.scope[4] = brief.scope[4].model_copy(update={"scope_status": "later"})
    res = await draft(brief, "medium")
    served = {f for s in res.work.screens for f in s.serves}
    assert {
        f.id for f in res.work.scope if f.scope_status == "in"
    } <= served and "FTR-005" not in served


async def test_a_must_have_feature_has_a_flow_with_acceptance_and_every_role_has_somewhere_to_work() -> (
    None
):
    res = await draft(bare(6), "medium")
    for f in res.work.scope:
        if f.priority == "must":
            assert any(f.id in fl.features and fl.acceptance for fl in res.work.flows), f.id
    assert "role_without_access" not in {i.code for i in res.issues}


async def test_the_ids_are_stable_spec_graph_ids_allocated_from_the_ledger() -> None:
    res = await draft(bare(4), "small")
    w = res.work
    assert [m.id for m in w.modules] == [f"MOD-{n:03d}" for n in range(1, len(w.modules) + 1)]
    assert w.ids.counters["SCR"] == len(w.screens) and w.ids.counters["ENT"] == len(w.entities)
    assert checks.errors(checks.run_checks(w, get_band("small"))) == []


async def test_drafted_records_are_default_assumed_and_proposed_never_locked() -> None:
    res = await draft(bare(4), "small")
    records = [*res.work.modules, *res.work.screens, *res.work.flows, *res.work.entities]
    assert records and all(
        (r.tier, r.status, r.decision) == ("Default", "assumed", "proposed") for r in records
    )
    assert all(r.provenance[0].type == "model_knowledge" for r in records)


# ----------------------------------------------------------------------------------------------- idempotence


async def test_a_rerun_with_the_same_inputs_makes_no_call_and_changes_nothing() -> None:
    first = await draft(bare(7), "medium")
    again = await draft(settled(first).model_copy(deep=True), "medium")
    assert again.calls == 0 and again.fallbacks == 0
    assert again.changed is False and sd.structure_dump(again.work) == sd.structure_dump(first.work)
    assert again.digests == first.digests


async def test_accepting_a_module_or_moving_a_pin_never_changes_a_digest() -> None:
    first = await draft(bare(6), "medium")
    brief = settled(first)
    brief.modules[0] = brief.modules[0].model_copy(
        update={"decision": "accepted", "status": "confirmed"}
    )
    brief.screens[0] = brief.screens[0].model_copy(update={"decision": "accepted"})
    again = await draft(brief.model_copy(deep=True), "medium")
    assert again.calls == 0 and again.digests == first.digests


async def test_changing_one_feature_redrafts_only_the_module_it_belongs_to() -> None:
    first = await draft(bare(7), "medium")
    brief = settled(first)
    victim = next(m for m in brief.modules if m.features)
    changed_feature = victim.features[0]
    brief.scope = [
        f.model_copy(update={"intent": "A different job"}) if f.id == changed_feature else f
        for f in brief.scope
    ]
    again = await draft(brief.model_copy(deep=True), "medium")
    assert set(again.drafted) >= {victim.id}
    untouched = [
        m.id for m in brief.modules if m.id != victim.id and changed_feature not in m.features
    ]
    assert untouched and not set(untouched) & set(again.drafted)  # the others were not redrafted
    # ids are kept: a regeneration matches by id and name
    assert {m.id for m in again.work.modules} == {m.id for m in brief.modules}
    assert {s.id for s in again.work.screens} >= {
        s.id for s in brief.screens if s.module not in again.drafted
    }


async def test_a_different_band_or_language_is_a_different_digest() -> None:
    a = await draft(bare(5), "medium")
    b = await draft(bare(5), "large")
    c = await draft(bare(5), "medium", language="hi")
    assert a.digests[sd.OUTLINE_KEY] != b.digests[sd.OUTLINE_KEY] != c.digests[sd.OUTLINE_KEY]


# ------------------------------------------------------------------------------------------------- pinned


async def test_a_pinned_module_and_its_screens_survive_a_full_redraft_untouched() -> None:
    first = await draft(bare(6), "medium")
    brief = settled(first)
    keep = brief.modules[1]
    brief.modules[1] = keep.model_copy(update={"decision": "accepted", "name": "My own name"})
    for i, s in enumerate(brief.screens):
        if s.module == keep.id:
            brief.screens[i] = s.model_copy(update={"decision": "accepted", "name": f"Mine {i}"})
    mine_before = [s.model_dump() for s in brief.screens if s.module == keep.id]
    again = await draft(brief.model_copy(deep=True), "medium", force=True)
    assert next(m for m in again.work.modules if m.id == keep.id).name == "My own name"
    assert [s.model_dump() for s in again.work.screens if s.module == keep.id] == mine_before
    assert keep.id not in again.drafted  # a pinned module is never asked for


async def test_a_locked_record_is_pinned_even_without_an_accept() -> None:
    assert merge.is_pinned(sample_app().modules[0].model_copy(update={"tier": "Locked"}))
    assert merge.is_pinned(sample_app().modules[0].model_copy(update={"decision": "accepted"}))
    assert not merge.is_pinned(sample_app().modules[0])


async def test_regenerating_one_module_with_an_instruction_leaves_the_rest_alone() -> None:
    first = await draft(bare(6), "medium")
    brief = settled(first)
    target = brief.modules[1]
    others = {s.id: s.model_dump() for s in brief.screens if s.module != target.id}
    again = await draft(
        brief.model_copy(deep=True),
        "medium",
        only_modules=(target.id,),
        instruction="make it simpler",
    )
    assert again.drafted == [target.id] and again.calls == 1  # one call, no outline, no critic
    assert {s.id: s.model_dump() for s in again.work.screens if s.module != target.id} == others


# --------------------------------------------------------------------------------------------------- failure


async def test_a_failed_outline_and_a_failed_group_answer_with_flagged_floors() -> None:
    gateway = ScriptedGateway(
        by_spec={
            "cmp.draft_outline": [ProviderError("boom")] * 2,
            "cmp.draft_module": [ProviderError("boom")] * 12,
            "cmp.review_structure": [ProviderError("boom")],
        }
    )
    ports = build_ports(CallRunner(gateway), load_catalog())
    res = await draft(bare(5), "medium", ports=ports)
    assert res.fallbacks >= 2 and res.reasons  # counted: a floor is a failure
    c = derive.counts(res.work)
    assert c.screens > 0 and checks.errors(res.issues) == []  # the floor is still a valid structure


class _FailsOnGroups:
    """A module port that fails every call for more than one module (the live case: the answer hit the output cap)
    and answers a single module with the real mock handler."""

    def __init__(self, inner: Any, *, fail_alone: bool = False) -> None:
        self.inner, self.fail_alone = inner, fail_alone
        self.sizes: list[int] = []

    async def draft(self, ctx: TurnContext, inp: Any) -> Any:
        self.sizes.append(len(inp.modules))
        res = await self.inner.draft(ctx, inp)
        if len(inp.modules) > 1 or self.fail_alone:
            return res.model_copy(update={"used_fallback": True, "reason": "timed out"})
        return res


def _ports_with(drafter: Any) -> Ports:
    import dataclasses

    return dataclasses.replace(real_ports(), structure_drafter=drafter)


def test_a_wave_is_grouped_by_the_bands_group_size_and_by_the_screens_a_call_can_write() -> None:
    from types import SimpleNamespace

    mods = [SimpleNamespace(id=f"MOD-{i:03d}") for i in range(1, 8)]
    big = SimpleNamespace(targets={m.id: 6 for m in mods})
    inp = make_input(bare(5), "medium")  # group_size 3
    groups = sd.module_groups(
        inp, big, mods
    )  # 6 + 6 = 12: two modules fit one call, a third does not
    assert [len(g) for g in groups] == [2, 2, 2, 1]
    small = SimpleNamespace(targets={m.id: 3 for m in mods})
    assert [len(g) for g in sd.module_groups(inp, small, mods)] == [3, 3, 1]  # the band's own limit
    assert [len(g) for g in sd.module_groups(make_input(bare(5), "small"), small, mods)] == [
        4,
        3,
    ]  # 12 screens
    alone = SimpleNamespace(targets={mods[0].id: 40})
    assert [len(g) for g in sd.module_groups(inp, alone, mods[:1])] == [1]
    assert sd.module_groups(inp, small, []) == []


async def test_a_failed_call_for_several_modules_is_asked_again_one_module_at_a_time() -> None:
    port = _FailsOnGroups(real_ports().structure_drafter)
    res = await draft(bare(7), "medium", ports=_ports_with(port))
    assert max(port.sizes) > 1 and port.sizes.count(1) >= max(
        port.sizes
    )  # the group, then each module alone
    assert res.fallbacks == 0, res.reasons  # the retry succeeded: no module got its floor
    assert any("asked again one at a time" in n for n in res.notes)
    assert checks.errors(res.issues) == []
    assert derive.counts(res.work).screens >= 12


class _RepairWritesJourneys:
    """A module port whose repair call (``issues`` given) answers like a full draft, journey segments included: what
    a live model does when asked to fix a module."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.repairs = inner, 0

    async def draft(self, ctx: TurnContext, inp: Any) -> Any:
        if inp.issues:
            self.repairs += 1
            inp = inp.model_copy(update={"issues": []})
        return await self.inner.draft(ctx, inp)


async def test_the_repair_pass_continues_the_flows_it_found_and_never_makes_a_second_copy() -> None:
    import dataclasses

    finding = ReviewOutput(
        findings=[
            FindingOut(
                code="missing", ids=["FTR-001"], fact_id="", note="There is no way to cancel."
            )
        ]
    )
    ports = build_ports(CallRunner(_MockWithCritic(finding)), load_catalog())
    repairing = _RepairWritesJourneys(ports.structure_drafter)
    res = await draft(
        bare(8), "medium", ports=dataclasses.replace(ports, structure_drafter=repairing)
    )
    assert repairing.repairs >= 1, "the repair pass ran (a finding asked for it)"
    names = [f.name for f in res.work.flows]
    assert names and len(names) == len(set(names)), f"the repair pass made copies of flows: {names}"
    ids = [f.id for f in res.work.flows]
    assert len(ids) == len(set(ids))


class _OutlineHangsOnce:
    def __init__(self, inner: Any, *, always: bool = False) -> None:
        self.inner, self.always, self.n = inner, always, 0

    async def outline(self, ctx: TurnContext, inp: Any) -> Any:
        self.n += 1
        res = await self.inner.outline(ctx, inp)
        if self.always or self.n == 1:
            return res.model_copy(update={"used_fallback": True, "reason": "timed out"})
        return res


async def test_a_failed_outline_is_asked_once_more_and_only_a_second_failure_gets_the_floor() -> (
    None
):
    import dataclasses

    once = _OutlineHangsOnce(real_ports().structure_outliner)
    res = await draft(
        bare(6), "medium", ports=dataclasses.replace(real_ports(), structure_outliner=once)
    )
    assert once.n == 2 and res.fallbacks == 0, res.reasons
    assert any("outline call failed" in n for n in res.notes)
    twice = _OutlineHangsOnce(real_ports().structure_outliner, always=True)
    res = await draft(
        bare(6), "medium", ports=dataclasses.replace(real_ports(), structure_outliner=twice)
    )
    assert twice.n == 2 and res.fallbacks >= 1 and checks.errors(res.issues) == []


async def test_a_module_that_fails_alone_too_gets_its_floor_flagged_and_counted() -> None:
    port = _FailsOnGroups(real_ports().structure_drafter, fail_alone=True)
    res = await draft(bare(7), "medium", ports=_ports_with(port))
    assert res.fallbacks >= 1 and res.reasons
    assert derive.counts(res.work).screens > 0 and checks.errors(res.issues) == []


async def test_a_model_that_returns_garbage_pointers_never_gets_them_into_the_brief() -> None:
    brief = bare(3)
    mock = real_ports()
    outline = (
        await mock.structure_outliner.outline(CTX, sd.build_outline_input(make_input(brief)))
    ).output
    wild = outline.model_copy(
        update={
            "modules": [
                outline.modules[0].model_copy(
                    update={
                        "features": ["FTR-001", "FTR-999"],
                        "roles": ["ROL-999", "Patient"],
                        "apps": ["APP-999"],
                    }
                ),
                *outline.modules[1:],
            ]
        }
    )
    plan, refmap = merge.merge_outline(brief, wild, NOW)
    first = brief.modules[0]
    assert "FTR-999" not in first.features and "ROL-999" not in first.roles
    assert first.roles == ["ROL-001"]  # the NAME "Patient" resolved to its id
    assert first.apps  # an app that does not exist falls back to every app
    assert plan.journeys and set(refmap.values()) >= {m.id for m in brief.modules}


# -------------------------------------------------------------------------------------------------- critic


def test_a_finding_is_kept_only_when_it_cites_a_real_id_and_a_real_fact() -> None:
    brief = sample_app()
    fact = Fact(
        id="FCT-001", type="rule", statement="Patients see only their own notes", quote="own notes"
    )
    out = ReviewOutput(
        findings=[
            FindingOut(
                code="inconsistent_permission",
                ids=["ENT-002", "ROL-001"],
                fact_id="FCT-001",
                note="Patients can open every note.",
            ),
            FindingOut(
                code="inconsistent_permission",
                ids=["ENT-002"],
                fact_id="FCT-099",
                note="rests on a fact nobody said",
            ),
            FindingOut(code="unrealistic", ids=["SCR-777"], fact_id="", note="points at nothing"),
            FindingOut(code="opinion", ids=["SCR-001"], fact_id="", note="not a known code"),
            FindingOut(
                code="missing",
                ids=["MOD-002"],
                fact_id="",
                note="There is no way to cancel a booking.",
            ),
            FindingOut(code="missing", ids=["MOD-002"], fact_id="", note=" "),
        ]
    )
    issues = sd.findings_to_issues(out, brief, [fact])
    assert [(i.code, i.ids) for i in issues] == [
        ("critic_finding", ("ENT-002", "ROL-001")),
        ("critic_finding", ("MOD-002",)),
    ]
    assert (
        issues[0].module == "MOD-003" and issues[0].fix == "repair"
    )  # the module that owns the entity
    assert "(fact FCT-001)" in issues[0].detail and issues[1].module == "MOD-002"


async def test_a_valid_finding_makes_the_repair_pass_run_for_its_module_and_a_clean_critic_does_not() -> (
    None
):
    brief = bare(6)
    clean = await draft(brief.model_copy(deep=True), "medium")
    base_calls = clean.calls
    finding = ReviewOutput(
        findings=[
            FindingOut(
                code="missing", ids=["FTR-001"], fact_id="", note="There is no way to cancel."
            )
        ]
    )
    gateway = _MockWithCritic(finding)
    res = await draft(
        brief.model_copy(deep=True),
        "medium",
        ports=build_ports(CallRunner(gateway), load_catalog()),
    )
    assert (
        res.calls == base_calls + 1
    )  # one more call: the repair of the module that delivers FTR-001
    assert any("There is no way to cancel" in " ".join(r) for r in gateway.module_issues)


class _MockWithCritic:
    """The real mock gateway for every call except the critic, which answers with a scripted review."""

    def __init__(self, review: ReviewOutput) -> None:
        self._real = make_client(all_specs())
        self._review = review
        self.module_issues: list[list[str]] = []

    async def run(self, spec: Any, inp: Any, ctx: Any) -> Any:
        if spec.id == "cmp.review_structure":
            from mde_contracts.gateway import CallResult, Usage

            return CallResult(
                call_id=ctx.request_id,
                spec_id=spec.id,
                spec_version=int(spec.version),
                model="scripted",
                mode="mock",
                output=self._review,
                usage=Usage(),
            )
        if spec.id == "cmp.draft_module" and inp.issues:
            self.module_issues.append(list(inp.issues))
        return await self._real.run(spec, inp, ctx)


# ---------------------------------------------------------------------------------------- evidence from words


async def test_a_screen_the_person_described_in_their_own_words_becomes_locked_with_the_verified_quote() -> (
    None
):
    brief = bare(4)
    brief.domain_facts = [
        DomainFact(
            id="FCT-050",
            type="requirement",
            statement="A screen to see Thing1 details",
            kind="screen",
        )
    ]
    fact = Fact(
        id="FCT-050",
        type="requirement",
        statement="A screen to see Thing1 details",
        quote="a screen to see Thing1 details",
        origin="stated",
    )
    res = await draft(brief, "small", facts=[fact])
    locked = [s for s in res.work.screens if s.tier == "Locked"]
    assert locked and locked[0].name.startswith("Thing1") and "details" in locked[0].name
    ev = locked[0].provenance[-1]
    assert (ev.type, ev.ref, ev.quote) == ("user_text", "FCT-050", "a screen to see Thing1 details")
    assert all(
        s.tier == "Default" for s in res.work.screens if s.id != locked[0].id
    )  # nothing else claims the person said it


async def test_an_unverified_or_unsupported_statement_never_makes_a_record_locked() -> None:
    brief = bare(4)
    brief.domain_facts = [
        DomainFact(
            id="FCT-050",
            type="requirement",
            statement="A screen to see Thing1 details",
            kind="screen",
        )
    ]
    shaky = Fact(
        id="FCT-050",
        type="requirement",
        statement="A screen to see Thing1 details",
        quote="x",
        unverified_quote=True,
    )
    res = await draft(brief, "small", facts=[shaky])
    assert all(s.tier == "Default" for s in res.work.screens)


async def test_a_locked_record_whose_fact_was_superseded_is_downgraded_on_the_next_run() -> None:
    brief = bare(4)
    brief.domain_facts = [
        DomainFact(
            id="FCT-050",
            type="requirement",
            statement="A screen to see Thing1 details",
            kind="screen",
        )
    ]
    fact = Fact(
        id="FCT-050",
        type="requirement",
        statement="A screen to see Thing1 details",
        quote="a screen to see Thing1 details",
    )
    first = await draft(brief, "small", facts=[fact])
    stale = settled(first)
    again = await draft(stale, "small", facts=[fact.model_copy(update={"status": "superseded"})])
    assert all(s.tier != "Locked" for s in again.work.screens)


# -------------------------------------------------------------------------------------------- merge details


def test_merge_matches_modules_by_id_then_name_so_a_regeneration_keeps_ids() -> None:
    brief = sample_app()
    outline = OutlineOutput(
        modules=[
            merge_module("MOD-002", "Appointments"),
            merge_module(
                "m9", "consultations"
            ),  # a new ref, the name of an existing module: its id is kept
            merge_module("m10", "Brand new"),
        ],
        entities=[],
        journeys=[],
    )
    before = {m.id: m.name for m in brief.modules}
    plan, refmap = merge.merge_outline(brief, outline, NOW)
    assert refmap["m9"] == "MOD-003" and refmap["MOD-002"] == "MOD-002"
    assert refmap["m10"] == "MOD-004"  # the ledger continues
    assert "MOD-001" in brief.ids.tombstones and "MOD-001" not in {
        m.id for m in brief.modules
    }  # unmentioned: removed
    assert {s.module for s in brief.screens} <= {"MOD-002", "MOD-003"}  # its screens went with it
    assert before["MOD-001"] == "Account and privacy" and plan.journeys == []


def merge_module(ref: str, name: str) -> Any:
    from mde_compass.engine.structure_wire import ModuleOut

    return ModuleOut(
        ref=ref, kind="", name=name, purpose="p", apps=[], features=[], roles=[], screen_target=3
    )


def test_assembling_flows_replaces_only_the_legs_that_were_rewritten() -> None:
    brief = sample_app()
    plan = merge.plan_from_existing(brief)
    run = next(j for j in plan.journeys if j.flow_id == "FLW-002")
    assert run.legs == [
        "MOD-002",
        "MOD-003",
    ]  # the consultation flow leaves the appointments module for consultations
    segments = {("FLW-002", 1): [("SCR-011", "join"), ("SCR-012", "notes")]}
    merge.assemble_flows(brief, plan, segments, {}, NOW, keep_unplanned=True)
    flow = next(f for f in brief.flows if f.id == "FLW-002")
    assert [(s.screen, s.action) for s in flow.steps] == [
        ("SCR-010", "open today's visit"),
        ("SCR-011", "join"),
        ("SCR-012", "notes"),
    ]


def test_a_journey_with_a_single_usable_step_is_left_out_not_invented() -> None:
    brief = sample_app()
    plan = merge.plan_from_existing(brief)
    plan.journeys[0].legs = ["MOD-002"]
    notes = merge.assemble_flows(
        brief, plan, {("FLW-001", 0): [("SCR-007", "x")]}, {}, NOW, keep_unplanned=False
    )
    assert (
        notes and "FLW-001" not in {f.id for f in brief.flows} and "FLW-001" in brief.ids.tombstones
    )


def test_a_screen_pointing_at_names_and_made_up_ids_is_resolved_or_dropped() -> None:
    brief = sample_app()
    from mde_compass.engine.structure_wire import EntryOut, ScreenOut, UseOut

    out = ModuleOutput(
        screens=[
            ScreenOut(
                ref="s1",
                module="MOD-002",
                name="Cancel a visit",
                purpose="p",
                pattern="carousel",
                apps=["Patient app", "APP-099"],
                roles=["Doctor", "ROL-099"],
                serves=["Book appointments", "FTR-099"],
            )
        ],
        uses=[
            UseOut(screen="s1", entity="ENT-001", use="edit"),
            UseOut(screen="s1", entity="ENT-099", use="view"),
            UseOut(screen="s1", entity="ENT-001", use="delete"),
        ],
        entries=[EntryOut(module="MOD-002", screen="s1")],
        segments=[],
        entities=[],
        fields=[],
        access=[],
        journeys=[],
    )
    merge.merge_modules(brief, out, ["MOD-002"], patch=True, now=NOW)
    new = next(s for s in brief.screens if s.name == "Cancel a visit")
    assert (
        new.pattern == ""
        and new.apps == ["APP-001"]
        and new.roles == ["ROL-002"]
        and new.serves == ["FTR-001"]
    )
    assert [(u.entity, u.use) for u in new.entities] == [("ENT-001", "edit")]
    assert new.id == "SCR-014"


def test_patch_keeps_what_the_call_did_not_return_and_replace_removes_it() -> None:
    from mde_compass.engine.structure_wire import ScreenOut

    def only_one() -> ModuleOutput:
        return ModuleOutput(
            screens=[
                ScreenOut(
                    ref="SCR-006",
                    module="MOD-002",
                    name="Appointments home",
                    purpose="p",
                    pattern="list",
                    apps=[],
                    roles=["ROL-001"],
                    serves=["FTR-001"],
                )
            ],
            uses=[],
            entries=[],
            segments=[],
            entities=[],
            fields=[],
            access=[],
            journeys=[],
        )

    patched = sample_app()
    merge.merge_modules(patched, only_one(), ["MOD-002"], patch=True, now=NOW)
    assert next(s for s in patched.screens if s.id == "SCR-006").name == "Appointments home"
    assert {"SCR-007", "SCR-008", "SCR-009", "SCR-010"} <= {s.id for s in patched.screens}
    replaced = sample_app()
    merge.merge_modules(replaced, only_one(), ["MOD-002"], patch=False, now=NOW)
    assert {s.id for s in replaced.screens if s.module == "MOD-002"} == {"SCR-006"}
    assert {"SCR-007", "SCR-008", "SCR-009", "SCR-010"} <= set(replaced.ids.tombstones)


def test_a_floor_journey_is_for_the_role_the_feature_is_about_not_the_first_role() -> None:
    from mde_compass.calls.structure_floor import journey_role
    from mde_compass.engine.structure_wire import FeatureWire, RoleWire

    roles = [
        RoleWire(id="ROL-001", name="admin", goal="run the clinic chain"),
        RoleWire(id="ROL-002", name="Patients", goal="book appointments"),
        RoleWire(id="ROL-003", name="Doctors", goal="see patients and write notes"),
    ]

    def feature(name: str, intent: str = "") -> FeatureWire:
        return FeatureWire(
            id="FTR-001",
            name=name,
            intent=intent,
            priority="must",
            scope_status="in",
            family="",
            apps=[],
        )

    assert journey_role(feature("Doctors write notes"), roles) == "ROL-003"
    assert journey_role(feature("Appointments", "Patient can book a slot"), roles) == "ROL-002"
    assert (
        journey_role(feature("Reports"), roles) == "ROL-002"
    )  # no match: the first role that is not an admin
    assert (
        journey_role(feature("Reports"), roles[:1]) == "ROL-001"
    )  # only an admin exists: the first role
    assert journey_role(feature("Reports"), []) == ""
