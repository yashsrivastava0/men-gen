"""The structure of the app inside a real session (ADR 0017): the tier sets the band, the draft runs in the seed and scan
wave, the review-time re-run is an exact no-op (same hash, same version, approval still works), a session that was ever
approved is never given a first draft, and with the switch off nothing changes."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG, CompassConfig
from mde_compass.domain.brief import Brief
from mde_compass.domain.events import AnswerEdit, ApproveAll, BatchAnswer, Command
from mde_compass.domain.hashing import brief_hash
from mde_compass.engine.service import Service
from mde_compass.engine.structure_testing import sample_app
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."


def config(*, enabled: bool) -> CompassConfig:
    return replace(
        DEFAULT_CONFIG, interview=replace(DEFAULT_CONFIG.interview, structure_enabled=enabled)
    )


async def review(*, tier: str | None = None, enabled: bool = True) -> tuple[Service, Conversation]:
    svc = build_service(memory=True, clock=FakeClock(), compass_config=config(enabled=enabled))
    conv = await Conversation.start(svc)
    if tier:
        await conv.send(Command(event_id=conv._id(), name=f"budget_{tier}"))  # type: ignore[arg-type]
    await conv.say(IDEA)
    batch = [e.payload for e in conv.events if e.type == "question_batch"][-1]
    await conv.send(
        BatchAnswer(
            event_id=conv._id(), batch_id=str(batch["batch_id"]), answers=[], decide_rest=True
        )
    )
    return svc, conv


async def stored(svc: Service, conv: Conversation) -> Any:
    async with svc.store.begin("t1") as uow:
        return await uow.briefs.get(conv.session_id)


def notices(conv: Conversation) -> list[str]:
    return [e.payload.get("code", "") for e in conv.events if e.type == "notice"]


@pytest.mark.parametrize(
    ("tier", "band", "most"),
    [
        ("quick", "small", 18),
        ("standard", "medium", 37),
        (None, "medium", 37),
        ("thorough", "large", 10_000),
    ],
)
async def test_the_tier_sets_the_band_and_the_draft_is_in_the_brief_at_review(
    tier: str | None, band: str, most: int
) -> None:
    svc, conv = await review(tier=tier)
    rec = await stored(svc, conv)
    b: Brief = rec.brief
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "BRIEF_REVIEW"
    block = b.meta.interview
    assert block is not None and (block.size_band, block.size_source) == (band, "tier")
    assert b.modules and b.screens and b.flows and b.entities
    assert block.structure_screens == len(b.screens) <= most  # never wildly past the band
    assert block.fallback_count == 0 and "structure_ready" in notices(conv)
    assert {s.module for s in b.screens} <= {m.id for m in b.modules}
    assert all(
        r.tier == "Default" for r in [*b.modules, *b.screens, *b.flows, *b.entities]
    )  # the model never locks
    assert not [e for e in conv.events if e.type == "error"]


async def test_the_draft_is_part_of_the_hash_and_the_stored_digests_name_every_module() -> None:
    svc, conv = await review()
    b: Brief = (await stored(svc, conv)).brief
    assert brief_hash(b) != brief_hash(
        b.model_copy(update={"modules": [], "screens": [], "flows": [], "entities": []})
    )
    keys = set(b.meta.compile_digests)
    assert "structure:outline" in keys and {f"structure:module:{m.id}" for m in b.modules} <= keys


async def test_with_the_switch_off_nothing_is_drafted_and_nothing_is_recorded() -> None:
    svc, conv = await review(enabled=False)
    b: Brief = (await stored(svc, conv)).brief
    assert not (b.modules or b.screens or b.flows or b.entities)
    assert b.meta.interview is not None and b.meta.interview.size_band == ""
    assert not [k for k in b.meta.compile_digests if k.startswith("structure:")]
    assert "structure_ready" not in notices(conv)
    dumped = b.model_dump(mode="json")
    assert not {"modules", "screens", "flows", "entities"} & set(
        dumped
    )  # the stored Brief is exactly as before


async def test_a_big_idea_in_the_smallest_tier_is_told_once_and_the_tier_is_never_switched() -> (
    None
):
    svc, conv = await review(tier="quick")
    b: Brief = (await stored(svc, conv)).brief
    sent = [
        e for e in conv.events if e.type == "notice" and e.payload.get("code") == "size_suggestion"
    ]
    assert len(sent) <= 1
    if sent:  # the suggestion carries a one-tap command and never changes the tier by itself
        assert (
            sent[0].payload["command"] in ("budget_standard", "budget_thorough")
            and sent[0].payload["command_label"]
        )
    assert b.meta.plan is not None and b.meta.plan.interview_budget == "quick"
    assert b.meta.interview is not None and b.meta.interview.size_band == "small"


async def test_a_size_suggestion_offers_one_tap_to_the_tier_that_fits_and_is_said_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mde_compass.engine import structure_derive

    monkeypatch.setattr(structure_derive, "suggest_band", lambda brief, tier_band: "large")
    svc = build_service(memory=True, clock=FakeClock(), compass_config=config(enabled=True))
    conv = await Conversation.start(svc)
    await conv.send(Command(event_id=conv._id(), name="budget_quick"))  # type: ignore[arg-type]
    await conv.say(IDEA)

    def sent() -> list[dict[str, Any]]:
        return [
            e.payload
            for e in conv.events
            if e.type == "notice" and e.payload.get("code") == "size_suggestion"
        ]

    assert len(sent()) == 1
    tap = sent()[0]
    assert tap["command"] == "budget_thorough" and tap["command_label"] == "Switch to Enterprise"
    assert "Enterprise" in tap["text"] and "Small" in tap["text"]
    b: Brief = (await stored(svc, conv)).brief
    assert (
        b.meta.plan is not None and b.meta.plan.interview_budget == "quick"
    )  # never switched for the person
    assert b.meta.interview is not None and b.meta.interview.size_suggested == "large"
    await conv.send(Command(event_id=conv._id(), name=tap["command"]))  # the person taps it
    b = (await stored(svc, conv)).brief
    assert b.meta.plan is not None and b.meta.plan.interview_budget == "thorough"
    assert len(sent()) == 1, "said once, however many turns follow"
    assert not [e for e in conv.events if e.type == "error"]


async def test_a_second_open_of_the_review_is_an_exact_no_op_and_approval_still_works() -> None:
    svc, conv = await review()
    first = await stored(svc, conv)
    before_structure = {
        k: first.brief.model_dump(mode="json").get(k)
        for k in ("modules", "screens", "flows", "entities")
    }
    # editing an answer back to what it was reopens the review: the structure step runs again, over the same inputs
    qa = [e for e in conv.events if e.type == "card"]
    card_id = (qa[0].payload.get("card") or {}).get("id") if qa else None
    assert card_id
    await conv.send(AnswerEdit(event_id=conv._id(), card_id=card_id, value=None, custom_text=None))
    again = await stored(svc, conv)
    after_structure = {
        k: again.brief.model_dump(mode="json").get(k)
        for k in ("modules", "screens", "flows", "entities")
    }
    assert after_structure == before_structure  # the same ids, the same records: no redraft
    ready = [e.payload for e in conv.events if e.type == "brief_ready"]
    assert (
        ready[-1]["version"] == again.version and ready[-1]["brief_hash"] == again.hash
    )  # no off-by-one version
    # and the person can approve it
    result = await conv.send(
        ApproveAll(event_id=conv._id(), version=again.version, brief_hash=again.hash)
    )
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "APPROVED", [
        e.type for e in result.events
    ]


async def test_a_session_that_was_ever_approved_is_never_given_a_first_draft() -> None:
    svc, conv = await review(enabled=False)  # approve a Brief that has no structure
    rec = await stored(svc, conv)
    await conv.send(ApproveAll(event_id=conv._id(), version=rec.version, brief_hash=rec.hash))
    assert (await svc.get_session(conv.caller, conv.session_id)).state == "APPROVED"
    svc.config = config(enabled=True)  # the feature is switched on later
    svc.structure.svc = svc
    session = await svc.get_session(conv.caller, conv.session_id)
    assert session.approved_version is not None
    turn_like = type("T", (), {"session": session, "brief": rec.brief})()
    assert (
        svc.structure.active(turn_like) is False
    )  # whatever the Brief holds, an approved session is left alone


def test_the_sample_app_is_what_a_drafted_structure_looks_like_to_the_controller_guards() -> None:
    assert sample_app().modules and sample_app().scope  # the guard needs something in scope to plan


# ------------------------------------------------------------------------------------- the band of a session


def _turn(brief: Brief, facts: list[Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        brief=brief,
        memory=SimpleNamespace(active_facts=lambda: facts),
        session=SimpleNamespace(approved_version=None),
    )


def _doc_facts(n: int, *, verified: bool = True) -> list[Any]:
    from mde_compass.domain.interview import Fact

    return [
        Fact(
            id=f"FCT-{i:03d}",
            type="requirement",
            statement=f"The document says thing {i}",
            quote=f"thing {i}",
            origin="document",
            confidence=0.9,
            unverified_quote=not verified,
        )
        for i in range(1, n + 1)
    ]


def test_a_document_raises_the_band_by_its_verified_claims_and_never_lowers_it() -> None:
    svc = build_service(memory=True, clock=FakeClock(), compass_config=config(enabled=True))
    quick = Brief()
    quick.meta.plan = (
        type(sample_app().meta.plan or object).__mro__ and None
    )  # no plan: the Standard tier
    for facts, expect in (
        (_doc_facts(10), ("medium", "tier")),
        (_doc_facts(30), ("medium", "tier")),
        (_doc_facts(70), ("large", "document")),
    ):
        turn = _turn(Brief(), facts)
        svc.structure.prepare(turn)
        block = turn.brief.meta.interview
        assert block is not None and (block.size_band, block.size_source) == expect, (
            len(facts),
            block,
        )
    # claims whose quote could not be verified do not count: the person has not said them
    shaky = _turn(Brief(), _doc_facts(70, verified=False))
    svc.structure.prepare(shaky)
    assert (
        shaky.brief.meta.interview is not None and shaky.brief.meta.interview.size_band == "medium"
    )


def test_the_persons_own_choice_of_band_is_kept_over_the_tier_and_the_document() -> None:
    from mde_compass.domain.interview import InterviewBlock

    svc = build_service(memory=True, clock=FakeClock(), compass_config=config(enabled=True))
    brief = Brief()
    brief.meta.interview = InterviewBlock(size_band="small", size_source="user")
    turn = _turn(brief, _doc_facts(90))
    svc.structure.prepare(turn)
    assert turn.brief.meta.interview is not None
    assert (turn.brief.meta.interview.size_band, turn.brief.meta.interview.size_source) == (
        "small",
        "user",
    )


# --------------------------------------------------------------------------- what the person does (ADR 0017, S5)


async def act(conv: Conversation, action: str, **fields: Any) -> Any:
    from mde_compass.domain.events import StructureAction

    return await conv.send(StructureAction(event_id=conv._id(), action=action, **fields))  # type: ignore[arg-type]


async def current(svc: Service, conv: Conversation) -> Brief:
    return (await stored(svc, conv)).brief


async def test_accepting_a_module_confirms_it_and_what_is_in_it_and_never_locks_anything() -> None:
    svc, conv = await review()
    b = await current(svc, conv)
    module = b.modules[0]
    await act(conv, "accept", section="modules", target=module.id)
    after = await current(svc, conv)
    got = next(m for m in after.modules if m.id == module.id)
    assert (got.decision, got.status, got.tier) == ("accepted", "confirmed", "Default")
    inside = [s for s in after.screens if s.module == module.id]
    assert inside and all(s.decision == "accepted" and s.tier == "Default" for s in inside)
    assert all(m.decision == "proposed" for m in after.modules if m.id != module.id)
    assert not [e for e in conv.events if e.type == "error"]


async def test_a_typed_edit_is_the_persons_words_and_locks_the_record_where_accept_does_not() -> (
    None
):
    from mde_compass.domain.events import BriefEdit

    svc, conv = await review()
    b = await current(svc, conv)
    screen = b.screens[0]
    await conv.send(
        BriefEdit(event_id=conv._id(), item_id=f"screens.{screen.id}.name", value="My own name")
    )
    after = await current(svc, conv)
    got = next(s for s in after.screens if s.id == screen.id)
    assert (got.name, got.tier) == ("My own name", "Locked") and got.provenance[
        -1
    ].type == "user_text"


async def test_removing_a_module_removes_what_depends_on_it_and_undo_puts_everything_back() -> None:
    svc, conv = await review()
    before = await current(svc, conv)
    victim = next(m for m in before.modules if any(s.module == m.id for s in before.screens))
    await act(conv, "remove", section="modules", target=victim.id)
    mid = await current(svc, conv)
    assert victim.id not in {m.id for m in mid.modules} and not [
        s for s in mid.screens if s.module == victim.id
    ]
    await conv.send(Command(event_id=conv._id(), name="undo"))
    back = await current(svc, conv)
    for key in ("modules", "screens", "flows", "entities"):
        assert back.model_dump(mode="json").get(key) == before.model_dump(mode="json").get(key), key


async def test_the_change_summary_names_the_record_not_its_path() -> None:
    svc, conv = await review()
    b = await current(svc, conv)
    victim = b.modules[-1]
    await act(conv, "accept", section="modules", target=b.modules[0].id)
    await act(conv, "remove", section="modules", target=victim.id)
    summaries = [e.payload["summary"] for e in conv.events if e.type == "brief_patch"]
    assert any(f"Accepted {b.modules[0].name}" in s for s in summaries), summaries
    assert any(f"Removed {victim.name}" in s for s in summaries), summaries
    assert not any("modules.MOD-" in s for s in summaries), "no raw path in what the person reads"
    from mde_compass.domain.events import BriefEdit

    await conv.send(
        BriefEdit(event_id=conv._id(), item_id=f"modules.{b.modules[1].id}.name", value="Orders")
    )
    last = [e.payload["summary"] for e in conv.events if e.type == "brief_patch"][-1]
    assert f"{b.modules[1].name} › name" in last and "MOD-" not in last, last


async def test_adding_a_screen_by_hand_is_locked_with_the_persons_words() -> None:
    svc, conv = await review()
    b = await current(svc, conv)
    await act(
        conv,
        "add",
        section="screens",
        value={"name": "Gift cards", "module": b.modules[0].id, "purpose": "Buy a gift card"},
    )
    after = await current(svc, conv)
    new = next(s for s in after.screens if s.name == "Gift cards")
    assert new.tier == "Locked" and new.id not in {s.id for s in b.screens}


class _AddsWhatWasAsked:
    """The real mock gateway, except that a module draft that carries the person's own words gets one more screen."""

    def __init__(self, clock: FakeClock) -> None:
        from mde_compass.calls.registry import all_specs
        from mde_compass.calls.testing import make_client

        self._real = make_client(
            all_specs(), clock=clock
        )  # the same clock as the service: deadlines agree

    async def run(self, spec: Any, inp: Any, ctx: Any) -> Any:
        result = await self._real.run(spec, inp, ctx)
        if spec.id == "cmp.draft_module" and inp.instruction:
            from mde_compass.engine.structure_wire import ScreenOut

            module = inp.modules[0]
            extra = ScreenOut(
                ref="sx",
                module=module.ref,
                name="Added by request",
                purpose=inp.instruction,
                pattern="form",
                apps=[],
                roles=[],
                serves=[],
            )
            result = result.model_copy(
                update={
                    "output": result.output.model_copy(
                        update={"screens": [*result.output.screens, extra]}
                    )
                }
            )
        return result

    async def budget_status(self) -> Any:  # pragma: no cover
        return None


async def test_regenerating_a_module_with_the_persons_words_redrafts_only_that_module_and_undo_restores() -> (
    None
):
    clock = FakeClock()
    svc = build_service(
        memory=True,
        clock=clock,
        compass_config=config(enabled=True),
        gateway=_AddsWhatWasAsked(clock),  # type: ignore[arg-type]
    )
    conv = await Conversation.start(svc)
    await conv.say(IDEA)
    batch = [e.payload for e in conv.events if e.type == "question_batch"][-1]
    await conv.send(
        BatchAnswer(
            event_id=conv._id(), batch_id=str(batch["batch_id"]), answers=[], decide_rest=True
        )
    )
    before = await current(svc, conv)
    target = next(m for m in before.modules if m.features)
    others = {s.id: s.model_dump(mode="json") for s in before.screens if s.module != target.id}
    await act(conv, "regenerate", target=target.id, text="add a gift card screen")
    after = await current(svc, conv)
    assert "Added by request" in {s.name for s in after.screens if s.module == target.id}
    assert {
        s.id: s.model_dump(mode="json") for s in after.screens if s.module != target.id
    } == others  # nothing else moved
    assert [e.payload for e in conv.events if e.type == "brief_patch"][-1]["voided_sections"]
    await conv.send(Command(event_id=conv._id(), name="undo"))
    back = await current(svc, conv)
    assert "Added by request" not in {s.name for s in back.screens}
    assert back.model_dump(mode="json").get("screens") == before.model_dump(mode="json").get(
        "screens"
    )


async def test_a_regeneration_that_changes_nothing_says_so_and_records_no_change() -> None:
    svc, conv = await review()
    before = await current(svc, conv)
    target = next(m for m in before.modules if m.features)
    version = (await stored(svc, conv)).version
    await act(conv, "regenerate", target=target.id, text="keep it")
    assert "structure_nothing_to_do" in notices(conv)
    assert (await stored(svc, conv)).version == version


async def test_planning_for_another_band_redrafts_from_the_same_answers_and_records_the_persons_choice() -> (
    None
):
    svc, conv = await review(tier="quick")
    small = await current(svc, conv)
    assert small.meta.interview is not None and small.meta.interview.size_band == "small"
    await act(conv, "set_band", band="large")
    big = await current(svc, conv)
    assert big.meta.interview is not None and (
        big.meta.interview.size_band,
        big.meta.interview.size_source,
    ) == ("large", "user")
    assert len(big.screens) > len(small.screens)  # the same three features, planned as a bigger app
    assert (
        big.meta.plan is not None and big.meta.plan.interview_budget == "quick"
    )  # the tier itself never moved


async def test_a_large_app_needs_every_module_accepted_and_approve_all_does_it_in_one_change() -> (
    None
):
    svc, conv = await review(tier="thorough")
    rec = await stored(svc, conv)
    ready = [e.payload for e in conv.events if e.type == "brief_ready"][-1]
    assert ready["approvable"] is False and any(
        "plan" in line.lower() for line in ready["blockers"]
    )
    # accepting every module clears the blocker...
    for m in rec.brief.modules:
        await act(conv, "accept", section="modules", target=m.id)
    rec2 = await stored(svc, conv)
    assert svc.structure.unreviewed(_turn(rec2.brief, [])) == 0
    # ...and Approve All does it too, never creating Locked
    svc2, conv2 = await review(tier="thorough")
    rec3 = await stored(svc2, conv2)
    await conv2.send(ApproveAll(event_id=conv2._id(), version=rec3.version, brief_hash=rec3.hash))
    assert (await svc2.get_session(conv2.caller, conv2.session_id)).state == "APPROVED"
    approved: Brief = (await stored(svc2, conv2)).brief
    records = [*approved.modules, *approved.screens, *approved.flows, *approved.entities]
    assert records and all(r.decision == "accepted" and r.tier == "Default" for r in records)


async def test_a_small_app_is_approvable_without_reviewing_the_structure() -> None:
    _svc, conv = await review(tier="quick")
    ready = [e.payload for e in conv.events if e.type == "brief_ready"][-1]
    assert not [
        line for line in ready["blockers"] if "app plan" in line.lower()
    ]  # small: shown, never a blocker


async def test_an_unknown_record_is_not_found_and_a_stale_approve_all_is_a_conflict() -> None:
    from mde_core.errors import Conflict, NotFound

    svc, conv = await review()
    with pytest.raises(NotFound):
        await act(conv, "accept", section="modules", target="MOD-999")
    rec = await stored(svc, conv)
    with pytest.raises(Conflict):
        await conv.send(
            ApproveAll(event_id=conv._id(), version=rec.version + 5, brief_hash="0" * 64)
        )
