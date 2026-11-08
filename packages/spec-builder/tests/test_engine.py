"""The engine end to end (mock gateway): every archetype, fallbacks, budget, cancel, determinism."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from helpers import (
    DirectMockGateway,
    ScriptedGateway,
    fake_c11_spec,
    make_brief,
    make_context,
    make_note,
    make_policy,
    make_request,
    no_c11,
)

from mde_content.packs import load_pack, pack_ids
from mde_content.stack import load_stack_profile
from mde_contracts.calls import C12Output, CriticFinding, SpecProposals
from mde_contracts.engines import BuildRequest, SpecBuilderEngine
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CircuitOpen,
    ProviderError,
    StructuredOutputError,
)
from mde_core.ids import SequentialIds
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.engine import BuilderSettings, SpecBuilderImpl
from mde_spec_builder.render import render_spec, with_rendered_hash
from mde_spec_builder.validators import errors, run_validators
from mde_spec_loader import SpecIndex, parse_spec, simulate_consumer

STACK = load_stack_profile()


def engine(gateway: Any | None = None, **kw: Any) -> SpecBuilderImpl:
    kw.setdefault("summary_loader", no_c11)  # independent of which prompt specs are merged
    return SpecBuilderImpl(gateway or DirectMockGateway(), **kw)


def validators_ok(req: BuildRequest, spec: Any) -> None:
    found = run_validators(
        spec,
        stack_profile=STACK,
        limits=req.policy.limits,
        max_tokens=req.policy.limits.max_spec_tokens,
    )
    assert errors(found) == [], errors(found)


def test_the_engine_implements_the_protocol() -> None:
    impl: SpecBuilderEngine = engine()
    assert hasattr(impl, "build")


# --------------------------------------------------------------------------- all five archetypes


@pytest.mark.parametrize("archetype", pack_ids())
async def test_mock_end_to_end_for_every_archetype(archetype: str) -> None:
    gateway = DirectMockGateway()
    req = make_request(archetype, research=make_note(), brief=make_brief(features=("search",)))
    res = await engine(gateway).build(req, make_context())
    assert res.status == "ok" and res.spec is not None and res.spec_text is not None
    assert res.question is None and res.question_slot is None
    validators_ok(req, res.spec)
    # the hash is the embedded header hash and verifies against the text
    index = SpecIndex.from_text(res.spec_text)
    assert res.spec_hash == res.spec.header.hash == index.header.hash and index.verify()
    # the file reads back as exactly the spec the engine returned
    assert parse_spec(res.spec_text) == res.spec
    assert render_spec(res.spec) == res.spec_text
    # the frontend consumer simulator is happy, within budget
    report = simulate_consumer(index)
    assert report.problems == [] and report.hash_ok and report.unscheduled == []
    # counts and the plan
    assert res.proposals_applied > 0 and res.proposals_dropped == 0 and res.repair_rounds == 0
    assert res.plan is not None and res.plan.spec_hash == res.spec_hash
    assert res.plan.screen_count == len(res.spec.screens)
    assert [a.id for a in res.plan.research_added] == ["RA-001", "RA-002", "RA-003", "RA-004"]
    assert not any(f.severity == "error" for f in res.findings)
    assert res.spec.header.spec_version == 1 == res.spec.revision.version


async def test_the_file_is_more_than_the_baseline() -> None:
    req = make_request("commerce", research=make_note())
    gateway = DirectMockGateway()
    res = await engine(gateway).build(req, make_context())
    assert res.spec is not None
    # the integration suggestion has no pack feature: C9b adds it as a mock, tagged as research
    assert any(
        i.source == "research" and i.research_ref == "RA-003" for i in res.spec.integrations.items
    )
    assert all("intro" in s.texts for s in res.spec.screens)  # C10 finished every screen
    assert len(res.spec.screens) == len(build_baseline(req).screens)  # favorites came from the pack


async def test_a_feature_suggestion_naming_a_pack_feature_is_applied_by_the_baseline() -> None:
    plain = build_baseline(make_request("commerce"))
    req = make_request("commerce", research=make_note())
    base = build_baseline(req)
    assert len(base.screens) > len(plain.screens)
    tagged = [s for s in base.screens if s.research_ref == "RA-001"]
    assert tagged and all(s.source == "research" for s in tagged)
    addition = {a.id: a for a in base.assumptions.research_additions}["RA-001"]
    assert {s.id for s in tagged} <= set(addition.affects)
    assert any(i.startswith("ENT-") for i in addition.affects)  # the data of the feature too
    assert any(e.source == "research" for e in base.data.entities)
    gateway = DirectMockGateway()
    await engine(gateway).build(req, make_context())
    asked = next(i for c, i, _ in gateway.calls if c == "C9a.screens_nav")
    assert [r.id for r in asked.research] == ["RA-002"]  # type: ignore[attr-defined]  # RA-001 is done


async def test_a_suggestion_for_a_feature_the_pack_lacks_is_left_to_the_writers() -> None:
    req = make_request("food_delivery", research=make_note())  # no `favorites` in this pack
    base = build_baseline(req)
    assert not any(s.research_ref == "RA-001" for s in base.screens)
    res = await engine().build(req, make_context())
    assert res.spec is not None
    assert any(s.research_ref == "RA-001" and s.source == "research" for s in res.spec.screens)
    other = make_request(
        "commerce", research=make_note(feature_applies_to="scope.features:teleport")
    )
    assert not any(s.research_ref == "RA-001" for s in build_baseline(other).screens)  # unknown id
    bare = make_request("commerce", research=make_note(feature_applies_to="scope.features"))
    assert not any(s.research_ref == "RA-001" for s in build_baseline(bare).screens)


async def test_the_same_request_gives_the_same_bytes() -> None:
    req = make_request("food_delivery", research=make_note())
    first = await engine().build(req, make_context())
    second = await engine().build(req, make_context())
    assert first.spec_text == second.spec_text and first.spec_hash == second.spec_hash
    assert first.plan == second.plan


async def test_concurrency_never_changes_the_result() -> None:
    req = make_request("commerce", research=make_note())
    sequential = await engine().build(req, make_context())
    parallel = await engine(settings=BuilderSettings(concurrency=4)).build(req, make_context())
    assert sequential.spec_text == parallel.spec_text


# --------------------------------------------------------------------------- calls, scope and progress


async def test_calls_run_in_order_with_the_session_scope_and_a_purpose() -> None:
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    req = make_request("commerce", research=make_note())
    res = await engine(gateway).build(req, make_context())
    assert res.status == "ok"
    ids = gateway.ids
    assert ids[:3] == ["C9a.screens_nav", "C9b.data_api_roles", "C9c.design_tokens"]
    assert set(ids[3:-1]) == {"C10.screen_briefs"} and ids[-1] == "C12.critic"
    assert res.spec is not None
    batches = ids.count("C10.screen_briefs")
    assert (
        batches == -(-len(res.spec.screens) // 4)
        or batches == -(-len(build_baseline(req).screens) // 4) + 1
    )
    for _call_id, _input, ctx in gateway.calls:
        assert ctx.budget_scope == "session:ses_TEST00000000000000000001"
        assert ctx.purpose.startswith("build:") and ctx.session_id == "ses_TEST00000000000000000001"
    purposes = {c[0]: c[2].purpose for c in gateway.calls}
    assert purposes["C9a.screens_nav"] == "build:screens"
    assert (
        purposes["C10.screen_briefs"] == "build:briefs" and purposes["C12.critic"] == "build:critic"
    )


async def test_screen_briefs_come_in_batches_of_three_to_four_with_only_their_context() -> None:
    gateway = DirectMockGateway()
    req = make_request("commerce")
    await engine(gateway).build(req, make_context())
    inputs = [i for call_id, i, _ in gateway.calls if call_id == "C10.screen_briefs"]
    assert inputs
    sizes = [len(i.screens) for i in inputs]  # type: ignore[attr-defined]
    assert max(sizes) == 4 and all(1 <= s <= 4 for s in sizes)
    assert all(3 <= s <= 4 for s in sizes[:-1])
    for inp in inputs:
        batch_recipes = {s.recipe for s in inp.screens}  # type: ignore[attr-defined]
        assert {r.id for r in inp.recipes} == batch_recipes  # type: ignore[attr-defined]
        assert len(inp.recipes) <= 4  # type: ignore[attr-defined]


async def test_progress_events_use_plain_labels() -> None:
    events: list[tuple[str, str, str, int, int]] = []
    await engine().build(make_request("custom"), make_context(progress=events))
    assert {e[0] for e in events} == {"build"}
    steps = [e[1] for e in events]
    assert steps[0] == "freeze" and steps[-1] == "finish"
    for step in ("baseline", "screens", "briefs", "validate", "critic"):
        assert step in steps
    labels = {e[2] for e in events}
    assert {"Drafting the screens, data and look", "Checking everything fits together"} <= labels
    briefs = [e for e in events if e[1] == "briefs" and e[4] > 1]
    assert briefs and briefs[-1][3] == briefs[-1][4]  # done == total at the end


async def test_summary_comes_from_c11_when_available_and_falls_back_to_the_template() -> None:
    req = make_request("commerce", brand__name="Mithai Mart")
    res = await engine(summary_loader=fake_c11_spec).build(req, make_context())
    assert res.spec is not None and res.plan is not None
    assert res.plan.summary.startswith("Mithai Mart is a mobile app built from")
    assert res.spec.summary.startswith(res.plan.summary) and "\n\n- " in res.spec.summary
    fallback = await engine().build(req, make_context())  # C11 unavailable: template summary
    assert fallback.plan is not None and fallback.plan.summary.startswith("Mithai Mart has ")
    broken = ScriptedGateway(fallback=DirectMockGateway()).script(
        "C11.write_summary", BudgetExceeded("no money")
    )
    res3 = await engine(broken, summary_loader=fake_c11_spec).build(req, make_context())
    assert res3.plan is not None and res3.plan.summary == fallback.plan.summary


# --------------------------------------------------------------------------- fallbacks


@pytest.mark.parametrize(
    "error",
    [
        BudgetExceeded("over the cap"),
        CircuitOpen("open"),
        StructuredOutputError("invalid twice"),
        ProviderError("500", status=500, retryable=True),
    ],
)
async def test_a_failing_gateway_never_fails_the_build(error: Exception) -> None:
    gateway = ScriptedGateway()
    for call_id in (
        "C9a.screens_nav",
        "C9b.data_api_roles",
        "C9c.design_tokens",
        "C10.screen_briefs",
        "C12.critic",
        "C13.repair",
        "C11.write_summary",
    ):
        gateway.script(call_id, error, repeat=True)
    req = make_request("commerce", research=make_note())
    res = await engine(gateway, summary_loader=fake_c11_spec).build(req, make_context())
    assert res.status == "ok" and res.spec is not None
    assert res.proposals_applied == 0 and res.proposals_dropped == 0
    validators_ok(req, res.spec)
    # the baseline stands on its own (only the summary and the hash/revision differ)
    base = build_baseline(req)
    assert [s.model_dump() for s in res.spec.screens] == [s.model_dump() for s in base.screens]
    assert (
        res.spec.design == base.design and res.spec.data == base.data and res.spec.api == base.api
    )
    if isinstance(error, BudgetExceeded | CircuitOpen):
        assert len(gateway.calls) == 1  # once the model is unavailable the build stops asking


async def test_budget_running_out_mid_build_keeps_what_was_applied() -> None:
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    gateway.script("C9b.data_api_roles", BudgetExceeded("spent"))
    req = make_request("food_delivery", research=make_note())
    res = await engine(gateway).build(req, make_context())
    assert res.status == "ok" and res.spec is not None
    assert gateway.ids == ["C9a.screens_nav", "C9b.data_api_roles"]  # nothing after the refusal
    assert any(s.source == "research" for s in res.spec.screens)  # C9a was applied
    assert not any(i.source == "research" for i in res.spec.integrations.items)
    assert res.proposals_applied > 0


async def test_an_unusable_answer_is_dropped_proposal_by_proposal() -> None:
    junk = SpecProposals.model_validate(
        {
            "proposals": [
                {
                    "kind": "add_screen",
                    "rationale": "x",
                    "route": "/home",
                    "recipe": "list",
                    "name": "Dup",
                    "purpose": "p",
                },
                {"kind": "remove_item", "rationale": "x", "target": "FLW-001"},
                {
                    "kind": "set_design_token",
                    "rationale": "x",
                    "pairs": [{"key": "colors_light.primary", "value": "blue"}],
                },
            ]
        }
    )
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    gateway.script("C9a.screens_nav", junk)
    req = make_request("commerce")
    res = await engine(gateway).build(req, make_context())
    assert res.status == "ok" and res.proposals_dropped == 3
    assert res.spec is not None
    validators_ok(req, res.spec)


async def test_a_missing_call_spec_is_just_a_fallback() -> None:
    from mde_prompts.registry import load_spec

    specs = {"C9a.screens_nav": load_spec("C9a.screens_nav")}
    impl = engine(specs=specs)
    impl._call_spec = lambda key: specs.get(  # type: ignore[method-assign]
        {"c9a": "C9a.screens_nav"}.get(key, "missing")
    )
    res = await impl.build(make_request("custom"), make_context())
    assert res.status == "ok"


async def test_per_batch_retry_then_fallback() -> None:
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    gateway.script("C10.screen_briefs", StructuredOutputError("bad"))  # first batch: one failure
    res = await engine(gateway).build(make_request("custom"), make_context())
    assert res.status == "ok" and res.spec is not None
    assert all("intro" in s.texts for s in res.spec.screens)  # the retry succeeded
    always = ScriptedGateway(fallback=DirectMockGateway())
    always.script("C10.screen_briefs", StructuredOutputError("bad"), repeat=True)
    res2 = await engine(always).build(make_request("custom"), make_context())
    assert res2.status == "ok" and res2.spec is not None
    assert not any("intro" in s.texts for s in res2.spec.screens)
    batches = -(-len(res2.spec.screens) // 4)
    assert always.ids.count("C10.screen_briefs") == 2 * batches  # one retry per batch


async def test_a_cap_on_batches_limits_the_c10_calls() -> None:
    gateway = DirectMockGateway()
    await engine(gateway, settings=BuilderSettings(c10_batch_limit=2)).build(
        make_request("commerce"), make_context()
    )
    assert gateway.ids.count("C10.screen_briefs") == 2


async def test_the_critic_can_be_turned_off() -> None:
    gateway = DirectMockGateway()
    await engine(gateway, settings=BuilderSettings(critic=False)).build(
        make_request("custom"), make_context()
    )
    assert "C12.critic" not in gateway.ids


# --------------------------------------------------------------------------- cancel


async def test_a_cancelled_run_stops_before_any_call() -> None:
    token = CancellationToken()
    token.cancel("stop")
    gateway = DirectMockGateway()
    with pytest.raises(CallCancelled):
        await engine(gateway).build(make_request("commerce"), make_context(cancel=token))
    assert gateway.calls == []


async def test_cancelling_mid_build_stops_the_next_call() -> None:
    token = CancellationToken()

    class Cancelling(DirectMockGateway):
        async def run(self, spec: Any, input: Any, ctx: Any) -> Any:
            out = await super().run(spec, input, ctx)
            if len(self.calls) == 2:
                token.cancel("user pressed stop")
            return out

    gateway = Cancelling()
    with pytest.raises(CallCancelled):
        await engine(gateway).build(make_request("commerce"), make_context(cancel=token))
    assert gateway.ids == ["C9a.screens_nav", "C9b.data_api_roles"]
    assert all(ctx.cancel is token for _, _, ctx in gateway.calls)


# --------------------------------------------------------------------------- critic and repair


def dangling_fix_gateway() -> ScriptedGateway:
    """C12 reports one error; C13 fixes the screen with a valid proposal."""
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    gateway.script(
        "C12.critic",
        C12Output(
            verdict="fix",
            findings=[
                CriticFinding(
                    kind="mismatch",
                    severity="error",
                    message="The home screen purpose says nothing about browsing",
                    target_ids=["SCR-004", "SCR-999"],
                    suggested_fix="Rewrite the purpose",
                )
            ],
        ),
        C12Output(verdict="pass", findings=[]),
    )
    gateway.script(
        "C13.repair",
        SpecProposals.model_validate(
            {
                "proposals": [
                    {
                        "kind": "set_screen_purpose",
                        "target": "SCR-004",
                        "source": "repair",
                        "rationale": "Say what the screen is for",
                        "purpose": "Browse categories and featured products",
                    }
                ]
            }
        ),
    )
    return gateway


async def test_a_critic_error_is_repaired_in_one_round() -> None:
    gateway = dangling_fix_gateway()
    res = await engine(gateway).build(make_request("commerce"), make_context())
    assert res.status == "ok" and res.repair_rounds == 1 and res.spec is not None
    assert (
        next(s for s in res.spec.screens if s.id == "SCR-004").purpose
        == "Browse categories and featured products"
    )
    assert gateway.ids.count("C12.critic") == 2 and gateway.ids.count("C13.repair") == 1
    critic_input = next(i for c, i, _ in gateway.calls if c == "C13.repair")
    assert [f.severity for f in critic_input.findings] == ["error"]  # type: ignore[attr-defined]
    assert critic_input.findings[0].target_ids == ["SCR-004"]  # type: ignore[attr-defined]  # unknown ids are dropped
    assert critic_input.round == 1 and critic_input.blocks[0].id == "SCR-004"  # type: ignore[attr-defined]


async def test_repair_stops_after_two_rounds_and_asks_one_question() -> None:
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    error = C12Output(
        verdict="fix",
        findings=[
            CriticFinding(kind="missing_flow", severity="error", message="No checkout journey")
        ],
    )
    gateway.script("C12.critic", error, repeat=True)
    gateway.script("C13.repair", SpecProposals(proposals=[]), repeat=True)
    ids = SequentialIds()
    clock = FakeClock()
    res = await engine(gateway, new_id_fn=ids, clock=clock).build(
        make_request("commerce"), make_context()
    )
    assert res.status == "needs_answer" and res.repair_rounds == 2
    assert gateway.ids.count("C13.repair") == 2  # never a third round
    assert res.spec_text is None and res.spec_hash is None and res.plan is None
    assert res.spec is not None  # the draft, for the flow to pass back as previous_spec
    card = res.question
    assert card is not None and res.question_slot == card.card.slot == "scope.features"
    assert card.card.type == "choice3" and len(card.card.options) == 3
    assert [o.recommended for o in card.card.options] == [True, False, False]
    assert card.instance_id == "crd_TEST0000000000000000000001" and card.shown_at == clock.now()
    assert card.card.options[0].sets["scope.features"] == load_default_features("commerce")
    assert any(f.validator == "critic" and f.severity == "error" for f in res.findings)


def load_default_features(archetype: str) -> list[str]:
    from mde_content.packs import default_features

    return default_features(load_pack(archetype))


async def test_a_validator_error_that_repair_cannot_fix_asks_the_matching_question() -> None:
    policy = make_policy(
        max_spec_tokens=500
    )  # far too small for any app: the limits validator fails
    req = make_request("commerce", policy=policy)
    res = await engine().build(req, make_context())
    assert res.status == "needs_answer" and res.question is not None
    assert res.question.card.id == "Q-build-limits" and res.question_slot == "scope.features"
    assert "bigger than one app" in res.question.card.text
    assert any(f.validator == "limits" and f.severity == "error" for f in res.findings)
    core = res.question.card.options[1].sets["scope.features"]
    from mde_content.packs import core_features

    assert core == core_features(load_pack("commerce"))
    assert res.repair_rounds == 2


async def test_answering_the_question_makes_a_valid_build() -> None:
    from helpers import slot

    policy = make_policy(max_spec_tokens=8000)
    first = await engine().build(make_request("commerce", policy=policy), make_context())
    assert first.status == "needs_answer" and first.question is not None
    chosen = first.question.card.options[1].sets  # "start with the essentials only"
    answers = {k: slot(k, v) for k, v in chosen.items()}
    req = make_request("commerce", policy=policy).model_copy(update={"answer_slots": answers})
    second = await engine().build(req, make_context())
    assert second.status == "ok" and second.spec is not None
    assert len(second.spec.screens) < len(build_baseline(make_request("commerce")).screens)


async def test_without_repair_budget_errors_still_end_in_a_question() -> None:
    gateway = ScriptedGateway()
    gateway.script("C9a.screens_nav", BudgetExceeded("spent"))
    req = make_request("commerce", policy=make_policy(max_spec_tokens=500))
    res = await engine(gateway).build(req, make_context())
    assert res.status == "needs_answer" and res.repair_rounds == 0  # the model is gone: ask at once


async def test_the_secret_in_a_name_is_asked_about() -> None:
    from helpers import slot  # noqa: F401

    key = "sk-" + "Ab1" * 10
    req = make_request("commerce", brand__name=key)
    res = await engine().build(req, make_context())
    # the baseline cleans the name to one line, but a key-looking name must never reach the file
    assert res.status == "needs_answer" and res.question is not None
    assert res.question.card.id == "Q-build-no_secrets" and res.question_slot == "brand.name"
    assert all(key not in f.message for f in res.findings)


# --------------------------------------------------------------------------- the Gate 1 diff


async def test_the_plan_diffs_against_the_first_draft() -> None:
    first = await engine().build(make_request("commerce"), make_context())
    assert first.spec is not None and first.plan is not None
    assert first.plan.diff_since_gate1 is None  # no first draft yet
    second_req = make_request("commerce", spec_version=2, research=make_note()).model_copy(
        update={"gate1_spec": first.spec, "gate1_spec_ids": []}
    )
    second = await engine().build(second_req, make_context())
    assert second.plan is not None and second.plan.diff_since_gate1 is not None
    diff = second.plan.diff_since_gate1
    assert any(i.startswith("SCR-") for i in diff.added_ids)
    # what a person can name: items of the first draft, and the singleton blocks (look, app, behavior)
    nameable = (
        {s.id for s in first.spec.screens}
        | {e.id for e in first.spec.data.entities}
        | {i.id for i in first.spec.integrations.items}
        | {o.id for o in first.spec.api.operations}
        | {r.id for r in first.spec.roles.roles}
        | {f.id for f in first.spec.flows}
        | {"APP", "DESIGN", "BEHAVIOR"}
    )
    assert diff.changed_ids and all(i in nameable for i in diff.changed_ids)
    assert "DESIGN" in diff.changed_ids and "Changed the look" in diff.lines  # the brand look
    assert diff.removed_ids == [] and not any("Removed item" in line for line in diff.lines)
    assert any(line.startswith("Added screen") for line in diff.lines)
    only_ids = make_request("commerce", spec_version=2).model_copy(
        update={"gate1_spec_ids": [s.id for s in first.spec.screens]}
    )
    third = await engine().build(only_ids, make_context())
    assert third.plan is not None and third.plan.diff_since_gate1 is not None
    assert third.plan.diff_since_gate1.changed_ids == []  # ids alone cannot say what changed


# --------------------------------------------------------------------------- misc


async def test_results_are_valid_for_a_regulated_policy() -> None:
    policy = make_policy("regulated-bank")
    req = make_request(
        "internal_workforce",
        policy=policy,
        data__classification="confidential",
        regions=["eu-west"],
        brief=make_brief(mode="enterprise"),
    )
    res = await engine().build(req, make_context())
    assert res.status == "ok" and res.spec is not None and res.plan is not None
    assert res.spec.header.mode == "enterprise" and res.plan.enterprise is not None
    assert res.plan.enterprise.full_file_view is True
    validators_ok(req, res.spec)


async def test_the_result_is_a_stamped_spec() -> None:
    res = await engine().build(make_request("custom"), make_context())
    assert res.spec is not None
    assert with_rendered_hash(res.spec) == res.spec  # header.hash already holds the rendered hash


def test_runs_under_asyncio_run() -> None:
    res = asyncio.run(engine().build(make_request("custom"), make_context()))
    assert res.status == "ok"
