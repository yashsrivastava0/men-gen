"""BEHAV: only the app-specific and mixed categories are written by the model; the library supplies the rest."""

from __future__ import annotations

import asyncio

from mde_spec_agent.lanes.screens.behav import mock as behav_mock
from mde_spec_agent.lanes.screens.behav.step import behav_step, wanted_categories
from mde_spec_core import vocab
from mde_spec_core.libraries import form_word_findings
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context


def required(run):
    g = run.graph
    return vocab.behavior_categories_for(g.meta.profile, set(g.meta.facts) | set(run.ctx.facts))


def test_every_required_category_is_present(finished, key):
    run = finished(key)
    have = {b.category for b in run.graph.behaviors.values()}
    assert {c.name for c in required(run)} <= have
    assert not [
        f
        for f in run_all(make_context(run.graph), gate=3, only={"V-35"})
        if f.severity == "blocker"
    ]


def test_only_app_specific_and_mixed_categories_are_model_written(finished, key):
    run = finished(key)
    cats = {c.name: c for c in required(run)}
    for b in run.graph.behaviors.values():
        cat = cats.get(b.category)
        if cat is None:
            continue
        written = any(p.kind == "model" for p in b.provenance)
        if cat.supplied_by == "library":
            assert not written, b.id
        else:
            assert written, b.id
        assert b.text.strip() and not form_word_findings(b.text)


def test_provider_side_flows_have_interruption_and_pending_rules(finished, key):
    run = finished(key)
    g = run.graph
    side = [i for i in g.integrations.values() if i.provider_side_flow]
    if not side:
        return
    texts = {b.category: b.text for b in g.behaviors.values()}
    for cat in ("interrupted_external_flow", "pending_timeouts"):
        assert any(i.name.lower() in texts[cat].lower() for i in side), cat


def test_baseline_ids_are_reused_and_new_ones_are_stable(finished, key):
    run = finished(key)
    base = {b.category: b.id for _, b in sorted(run.baseline.behaviors.items())}
    for b in run.graph.behaviors.values():
        if b.category in base and any(p.kind == "model" for p in b.provenance):
            assert b.id == base[b.category]


def test_a_behaviour_with_no_basis_is_assumed(fresh, monkeypatch):
    run = fresh("quick")
    real = behav_mock.handle

    def handle(inp):
        out = real(inp)
        b = out.behaviors[0]
        out.behaviors[0] = b.model_copy(update={"basis": [], "assumed": True})
        return out

    monkeypatch.setattr(behav_mock, "handle", handle)
    asyncio.run(behav_step(run.ctx))
    cat = wanted_categories(run.graph, set(run.graph.meta.facts) | set(run.ctx.facts))[0].name
    assert next(b for b in run.graph.behaviors.values() if b.category == cat).assumed


def test_bad_output_is_reasked_once_then_the_baseline_text_stays_flagged(fresh, monkeypatch):
    run = fresh("quick")
    real = behav_mock.handle

    def handle(inp):
        out = real(inp)
        out.behaviors.pop()  # a category is missing, every time
        return out

    monkeypatch.setattr(behav_mock, "handle", handle)
    asyncio.run(behav_step(run.ctx))
    flags = [f for f in run.ctx.flags if f["kind"] == "degraded" and f["step"] == "behav"]
    assert flags
    nodes = [b for b in run.graph.behaviors.values() if b.degraded]
    assert nodes and all(b.assumed for b in nodes)
    have = {b.category for b in run.graph.behaviors.values()}
    assert {c.name for c in required(run)} <= have  # the pack never has an unmarked hole


def test_step_is_idempotent(fresh):
    run = fresh("quick")
    asyncio.run(behav_step(run.ctx))
    a = run.graph.model_dump_json()
    asyncio.run(behav_step(run.ctx))
    assert run.graph.model_dump_json() == a


def test_a_behaviour_about_a_provider_flow_may_name_it_loosely_or_cite_its_id():
    """A real model wrote "the payment provider" for an integration called `payments`: that is about it. The text naming the
    integration (with or without the plural s) or the basis citing its id is accepted; a text about nothing is not."""
    from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut, BehOut, CatIn, IntegIn
    from mde_spec_agent.lanes.screens.behav.validate import validate

    inp = BehavIn(
        language="en", profile="standard", facts="", glossary="",
        categories=[CatIn(name="pending_timeouts", what="w", supplied_by="app_specific", library_text="", existing_id=None, existing_text="")],
        integrations=[IntegIn(id="INT-S001", name="payments", provider_side=True, flow="f", interruption="i", pending_timeout="p", events=[])],
        roles=[], entities=[], rules=[], languages=["en"],
    )  # fmt: skip

    def out(text, basis):
        return BehavOut(
            behaviors=[
                BehOut(category="pending_timeouts", text=text, basis=basis, assumed=not basis)
            ]
        )

    assert (
        validate(
            inp,
            out(
                "While the payment provider has not answered the person sees that the result is pending.",
                [],
            ),
        )
        == []
    )
    assert validate(inp, out("payments: the result is waited for.", [])) == []
    assert (
        validate(inp, out("The result is waited for, then shown as unknown.", ["INT-S001"])) == []
    )
    errors = validate(inp, out("The result is waited for, then shown as unknown.", []))
    assert errors and "INT-S001" in errors[0] and "payments" in errors[0]


def test_an_unknown_basis_id_is_dropped_by_code_not_reasked(fresh, monkeypatch):
    run = fresh("quick")
    real = behav_mock.handle

    def handle(inp):
        out = real(inp)
        b = out.behaviors[0]
        out.behaviors[0] = b.model_copy(update={"basis": ["MOD-001"], "assumed": False})
        return out

    monkeypatch.setattr(behav_mock, "handle", handle)
    asyncio.run(behav_step(run.ctx))
    assert not [f for f in run.ctx.flags if f["kind"] == "degraded"]  # no fallback
    calls = [c for c in run.ctx.runner.calls if c[0] == "spec.behav"]
    assert calls and not any(res.reasked for _, res in calls)
    assert all(
        "MOD-001" not in " ".join(p.ref for p in b.provenance) + " ".join(b.applies_to)
        for b in run.graph.behaviors.values()
    )


def test_repair_keeps_a_behaviour_only_if_something_still_grounds_it():
    from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut, BehOut, CatIn, IntegIn
    from mde_spec_agent.lanes.screens.behav.validate import repair_basis, validate

    def cat(n):
        return CatIn(
            name=n,
            what="x",
            supplied_by="mixed",
            library_text="",
            existing_id=None,
            existing_text="",
        )

    inp = BehavIn(
        language="en",
        profile="standard",
        facts="",
        glossary="",
        categories=[cat("session"), cat("state"), cat("offline")],
        integrations=[
            IntegIn(
                id="INT-001",
                name="payments",
                provider_side=True,
                flow="",
                interruption="",
                pending_timeout="",
                events=[],
            )
        ],
        roles=["ROL-001 Buyer"],
        entities=["ENT-006 Order (primary)"],
        rules=[],
        languages=["en"],
    )
    out = BehavOut(
        behaviors=[
            BehOut(
                category="session", text="A session lasts.", basis=["MOD-001"], assumed=False
            ),  # nothing left: goes
            BehOut(
                category="state",
                text="The Order is shared between screens.",
                basis=["MOD-001"],
                assumed=False,
            ),  # names an entity: stays
            BehOut(
                category="offline",
                text="Offline works.",
                basis=["MOD-001", "ENT-006"],
                assumed=False,
            ),  # another id remains: stays
        ]
    )
    assert validate(inp, out) == []  # nothing here is a re-ask
    fixed, log = repair_basis(inp, out)
    assert [b.category for b in fixed.behaviors] == ["state", "offline"]
    assert fixed.behaviors[0].basis == [] and fixed.behaviors[0].assumed
    assert fixed.behaviors[1].basis == ["ENT-006"] and not fixed.behaviors[1].assumed
    assert any("removed" in x for x in log)
