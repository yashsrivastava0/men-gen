"""SCRN: the cards. Mock mode, the three real exports through the baseline derivation, one Orchestrator stage per step."""

from __future__ import annotations

import asyncio
import re

from mde_compass_intake import ResearchView
from mde_compass_intake.research_reader import AppliesTo, ResearchItem
from mde_core.errors import GatewayError
from mde_spec_agent.lanes.screens.common import Gate1, facts_text, glossary_block, lane_rules
from mde_spec_agent.lanes.screens.scrn import mock as scrn_mock
from mde_spec_agent.lanes.screens.scrn.build import assemble
from mde_spec_agent.lanes.screens.scrn.fallback import fallback
from mde_spec_agent.lanes.screens.scrn.schema import ScrnIn
from mde_spec_agent.lanes.screens.scrn.slots import build_slot, incoming_edges, vocab_lines
from mde_spec_agent.lanes.screens.scrn.step import release_targets
from mde_spec_core import libraries, vocab
from mde_spec_core.config import default_config
from mde_spec_core.ids import namespaced_copy_key
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context
from mde_spec_core.validators.release import screen_features

CFG = default_config()
CARD_CODES = {"V-06", "V-07", "V-08", "V-13", "V-14", "V-22", "V-24", "V-30", "V-31"}


def carded(run):
    return [run.graph.screens[i] for i in release_targets(run.baseline)]


def test_every_release_screen_has_a_valid_card(finished, key):
    run = finished(key)
    g = run.graph
    assert carded(run)
    for s in carded(run):
        assert any(p.kind == "model" and p.ref == "spec.scrn" for p in s.provenance), s.id
        assert not s.degraded, s.id
        assert s.regions and s.states and s.copy_keys
    ctx = make_context(g)
    blockers = [f for f in run_all(ctx, gate=3) if f.severity == "blocker" and f.code in CARD_CODES]
    assert blockers == []
    # the acceptance minimum of the profile (library) is kept, and extras are within the lane's cap
    mn = CFG.profiles.acceptance_scenarios_min[g.meta.profile]
    cap = lane_rules().acceptance_extra_max[g.meta.profile]
    for s in carded(run):
        assert mn <= len(s.acceptance) <= mn + cap + 4, s.id
        assert all(a.given and a.when and a.then for a in s.acceptance)
    if g.meta.profile == "deep":
        for s in carded(run):
            text = " ".join(a.given.lower() for a in s.acceptance)
            assert "permission" in text or "role" in text or "allowed" in text
            assert "conflict" in text or "changed" in text or "other" in text


def test_system_screens_keep_their_library_cards(finished, key):
    run = finished(key)
    for s in run.graph.screens.values():
        if s.system:
            assert not any(p.ref == "spec.scrn" for p in s.provenance)


def test_no_layout_or_widget_words_and_no_widget_keys(finished, key):
    g = finished(key).graph
    findings = list(run_all(make_context(g), gate=3, only={"V-25"}))
    assert findings == [], [f.where + " " + f.why for f in findings][:5]
    suffixes = tuple(CFG.checks.widget_key_suffixes)
    for s in g.screens.values():
        keys = [r.key for r in s.regions] + [a.key for a in s.actions] + s.copy_keys
        assert not [k for k in keys if k.endswith(suffixes)]


def test_the_card_never_says_where_or_how(finished, key):
    for s in carded(finished(key)):
        d = s.model_dump()
        assert not {"layout", "position", "style", "widget", "order", "color", "animation"} & set(d)
        keys = [r.key for r in s.regions]
        assert keys == sorted(keys)  # alphabetical, and the order carries no meaning
        for a in s.actions:
            assert len(a.effect) <= 3 and a.intent in vocab.intents()
            assert all(
                g.pred in ("signed_in", "role_has_access", "field_equals", "field_in")
                for g in a.guard
            )
        for r in s.regions:
            assert r.importance in ("primary", "supporting", "optional")
            assert r.shape in vocab.shapes() or r.shape.startswith("other:")
            if r.shape == "collection":
                assert r.cardinality or r.maximum


def test_could_is_open_capped_based_and_has_a_non_motion_alternative(finished, key):
    g = finished(key).graph
    per_app: dict[str, int] = {}
    total = 0
    for s in g.screens.values():
        assert len(s.could) <= CFG.profiles.could_cap_per_screen
        per_app[s.app_id] = per_app.get(s.app_id, 0) + len(s.could)
        for c in s.could:
            total += 1
            assert c.basis.strip() and c.basis_kind
            assert c.tier != "C" and c.kind != "alternative"
            if c.motion and vocab.is_essential_motion(c.motion):
                assert c.reduced_motion_alt
            if c.basis_kind == "journey_moment":
                m = re.search(r"(FLW-\w+) step (\d+)", c.basis)
                assert m and m.group(1) in g.flows
                stage = next(x for x in g.flows[m.group(1)].stages if x.n == int(m.group(2)))
                assert c.assumed or not stage.emotion_assumed
    assert total > 0
    for n in per_app.values():
        assert n <= CFG.profiles.could_cap_per_app[g.meta.profile]
    # COULD lives outside the G2 hash
    h = g.content_hash()
    for s in g.screens.values():
        s.could = []
    assert g.content_hash() == h


def test_copy_keys_are_minted_by_code_and_namespaced(finished, key):
    g = finished(key).graph
    src = g.meta.source_language
    for s in carded(finished(key)):
        for k in s.copy_keys:
            assert k.startswith(namespaced_copy_key(s.id, "")) or k.startswith("lib.")
            assert k in g.locale_copy[src]
        for a in s.actions:
            assert namespaced_copy_key(s.id, a.key) in s.copy_keys
        assert namespaced_copy_key(s.id, "title") in s.copy_keys


def test_states_come_from_the_library_never_from_the_model(finished, key):
    g = finished(key).graph
    for s in carded(finished(key)):
        lib = libraries.state_library_for(s.screen_type, g.meta.profile)
        kinds = {st.kind for st in s.states}
        assert set(lib.required_state_kinds()) <= kinds
        for st in s.states:
            assert st.origin == "library"


# ---------------------------------------------------------------- the paths that go wrong


def _scrn_step(run):
    from mde_spec_agent.lanes.screens.scrn.step import screens_step

    return asyncio.run(screens_step(run.ctx))


def test_an_action_that_needs_an_operation_product_never_wrote_is_unspecified(fresh, monkeypatch):
    run = fresh("quick")
    real = scrn_mock.handle

    def handle(inp):
        out = real(inp)
        if out.actions:
            a = out.actions[0]
            out.actions[0] = a.model_copy(update={"unspecified": True, "effect": []})
        return out

    monkeypatch.setattr(scrn_mock, "handle", handle)
    _scrn_step(run)
    flagged = [f for f in run.ctx.flags if f["kind"] == "needs_decision"]
    assert flagged and all(f["owner"] == "product" for f in flagged)
    marked = [a for s in run.graph.screens.values() for a in s.actions if a.unspecified]
    assert marked and all(a.effect == [] for a in marked)
    assert not [
        f
        for f in run_all(make_context(run.graph), gate=1)
        if f.code == "V-07" and f.severity == "blocker"
    ]


def test_an_invented_operation_is_refused_unless_marked_unspecified(finished):
    run = finished("quick")
    g = run.baseline
    sid = release_targets(g)[0]
    slot = build_slot(
        g.screens[sid], g, CFG, lane_rules(), copy=g.locale_copy["en"], incoming=incoming_edges(g)
    )
    inp = ScrnIn(
        language="en", profile=g.meta.profile, vocab=[], facts="", glossary="", slots=[slot]
    )
    out = fallback(inp)
    a0 = out.actions[0]
    bad = out.model_copy(
        update={"actions": [a0.model_copy(update={"effect": ["call OP-999"]}), *out.actions[1:]]}
    )
    errs = assemble(g, inp, bad, CFG, lane_rules()).errors
    assert any("OP-999 is not an operation" in e for e in errs)
    ok = out.model_copy(
        update={
            "actions": [
                a0.model_copy(update={"effect": ["call OP-999"], "unspecified": True}),
                *out.actions[1:],
            ]
        }
    )
    built = assemble(g, inp, ok, CFG, lane_rules())
    assert not [e for e in built.errors if "OP-999" in e]
    assert any(f["kind"] == "needs_decision" for f in built.flags)


def test_one_reask_per_node_then_the_card_is_written_and_not_flagged(fresh, monkeypatch):
    run = fresh("quick")
    real = scrn_mock.handle

    def handle(inp):
        out = real(inp)
        if not inp.reask:  # the first answer breaks the freedom rule: a key that names a widget
            r = out.regions[0]
            out.regions[0] = r.model_copy(update={"key": "open_button"})
        return out

    monkeypatch.setattr(scrn_mock, "handle", handle)
    _scrn_step(run)
    calls = [c for c in run.ctx.runner.calls if c[0] == "spec.scrn"]
    assert calls and all(
        res.reasked and not res.flagged for _, res in calls
    )  # asked once more, then fine
    assert not any(s.degraded for s in run.graph.screens.values())
    for sid in release_targets(run.baseline):
        assert run.ctx.runner.ledger.has_used(sid)
    assert len(calls) == (len(release_targets(run.baseline)) + 3) // 4  # one run per batch
    assert not [f for f in run.ctx.flags if f["kind"] == "degraded"]


def test_after_the_one_reask_the_fallback_is_used_and_flagged(fresh, monkeypatch):
    run = fresh("quick")
    real = scrn_mock.handle

    def handle(inp):
        out = real(inp)
        r = out.regions[0]
        out.regions[0] = r.model_copy(update={"key": "open_button"})  # always wrong
        return out

    monkeypatch.setattr(scrn_mock, "handle", handle)
    _scrn_step(run)
    ids = release_targets(run.baseline)
    assert all(run.graph.screens[i].degraded for i in ids)
    flags = [f for f in run.ctx.flags if f["kind"] == "degraded"]
    assert {f["node"] for f in flags} == set(ids)
    # the fallback is the baseline card: still valid, never an unmarked hole
    assert not [
        f
        for f in run_all(make_context(run.graph), gate=1)
        if f.severity == "blocker" and f.where.split(".")[0] in ids
    ]
    for sid in ids:
        assert run.graph.screens[sid].regions


def test_truncation_splits_the_batch_and_redoes_only_what_is_unfinished(fresh):
    run = fresh("quick")
    inner = run.ctx.runner._gateway
    seen: list[int] = []
    orig = inner.run

    async def run_(spec, inp, ctx):
        if spec.id == "spec.scrn":
            seen.append(len(inp.slots))
            if len(inp.slots) > 1:
                raise GatewayError("the output was truncated at its limit")
        return await orig(spec, inp, ctx)

    inner.run = run_  # type: ignore[method-assign]
    _scrn_step(run)
    ids = release_targets(run.baseline)
    assert max(seen) > 1 and seen.count(1) >= len(ids)  # it split down to single screens
    assert not any(run.graph.screens[i].degraded for i in ids)
    assert all(any(p.ref == "spec.scrn" for p in run.graph.screens[i].provenance) for i in ids)


def test_a_screen_that_still_truncates_alone_falls_back_flagged(fresh):
    run = fresh("quick")
    inner = run.ctx.runner._gateway
    orig = inner.run

    async def run_(spec, inp, ctx):
        if spec.id == "spec.scrn":
            raise GatewayError("truncated")
        return await orig(spec, inp, ctx)

    inner.run = run_  # type: ignore[method-assign]
    _scrn_step(run)
    ids = release_targets(run.baseline)
    assert all(run.graph.screens[i].degraded for i in ids)


def test_gate_one_reports_a_broken_binding_on_a_touched_screen(finished):
    run = finished("quick")
    g = run.baseline
    sid = release_targets(g)[0]
    s = g.screens[sid].model_copy(deep=True)
    s.regions[0].bind = ["ENT-001.no_such_field"]
    from mde_spec_agent.run.patch import node_patch

    gate = Gate1(g, CFG)
    errs = gate.errors(node_patch("screens", [s], replace=True), [sid])
    assert any(e.startswith("V-06") for e in errs)
    assert (
        gate.errors(node_patch("screens", [s], replace=True), ["SCR-999"]) == []
    )  # only touched ids count


def _view_for(run):
    g = run.baseline
    sid = release_targets(g)[0]
    ftr = sorted(screen_features(g, g.screens[sid]))[0]
    items = (
        ResearchItem(
            id="RI-9001",
            kind="screen_pattern",
            claim="Comparable apps list items with their price first.",
            evidence="corroborated",
            applies_to=(AppliesTo(kind="FTR", id=ftr, relation="supports"),),
            rank=1,
        ),
        ResearchItem(
            id="RI-9002",
            kind="competitor",
            claim="A rival offers a free trial.",
            evidence="official",
            applies_to=(AppliesTo(kind="FTR", id=ftr, relation="supports"),),
            rank=2,
        ),
        ResearchItem(
            id="RI-9003",
            kind="workflow",
            claim="Peers usually confirm before the final step.",
            evidence="single_source",
            applies_to=(AppliesTo(kind="FTR", id=ftr, relation="supports"),),
            rank=3,
        ),
    )
    return sid, ResearchView(thin=False, status="complete", items=items)


def test_usually_lines_cite_offered_research_with_unchanged_evidence(fresh):
    run = fresh("quick")
    sid, view = _view_for(run)
    run.ctx.research = view
    _scrn_step(run)
    u = run.graph.screens[sid].usually
    assert {x.item for x in u} <= {"RI-9001", "RI-9003"}  # never a competitor item
    ev = {x.item: x.evidence for x in u}
    assert (
        ev.get("RI-9001") == "corroborated"
        and ev.get("RI-9003", "single_source") == "single_source"
    )
    assert not [x for x in u if re.search(r"\b(should|must)\b", x.note)]


def test_a_usually_line_with_an_unoffered_item_is_repaired_by_code_not_reasked(finished):
    run = finished("quick")
    g = run.baseline
    sid = release_targets(g)[0]
    slot = build_slot(
        g.screens[sid], g, CFG, lane_rules(), copy=g.locale_copy["en"], incoming=incoming_edges(g)
    )
    inp = ScrnIn(
        language="en", profile=g.meta.profile, vocab=[], facts="", glossary="", slots=[slot]
    )
    from mde_spec_agent.lanes.screens.scrn.schema import UsuallyOut

    base = fallback(inp)
    for u in (
        UsuallyOut(screen=sid, note="Peers do this.", evidence="official", item="RI-043"),
        UsuallyOut(screen=sid, note="Peers do this.", evidence="official", item=None),
    ):
        built = assemble(g, inp, base.model_copy(update={"usually": [u]}), CFG, lane_rules())
        assert not [e for e in built.errors if "usually" in e]  # no re-ask for this
        if u.item:
            assert any("RI-043" in n for n in built.notes)
        line = built.screens[sid].usually[0]
        assert (
            line.item is None and line.evidence == "unverified"
        )  # never evidence it does not have


def test_costs_nothing_to_build_slots_and_the_prompt_input_is_deterministic(finished):
    g = finished("standard").baseline
    ids = release_targets(g)
    inc = incoming_edges(g)
    a = [
        build_slot(
            g.screens[i], g, CFG, lane_rules(), copy=g.locale_copy["en"], incoming=inc
        ).model_dump_json()
        for i in ids
    ]
    b = [
        build_slot(
            g.screens[i], g, CFG, lane_rules(), copy=g.locale_copy["en"], incoming=inc
        ).model_dump_json()
        for i in ids
    ]
    assert a == b
    assert facts_text(g) == facts_text(g) and glossary_block(g) == glossary_block(g)
    assert vocab_lines() == vocab_lines()


def _built_with(run, region_attrs=None, guards=None, card_guards=None):
    from mde_spec_agent.lanes.screens.scrn.schema import RegionOut

    g = run.baseline
    sid = next(i for i in release_targets(g) if g.screens[i].actions)
    slot = build_slot(
        g.screens[sid], g, CFG, lane_rules(), copy=g.locale_copy["en"], incoming=incoming_edges(g)
    )
    inp = ScrnIn(
        language="en", profile=g.meta.profile, vocab=[], facts="", glossary="", slots=[slot]
    )
    out = fallback(inp)
    upd = {}
    if region_attrs is not None:
        reg = RegionOut(
            screen=sid, key="progress_booking", what="where the booking stands", shape="sequence",
            shape_kind="stages", attrs=region_attrs, bind=[], importance="supporting",
            freshness=None, cardinality="few", typical=None, maximum=None, live="none",
        )  # fmt: skip
        upd["regions"] = [*out.regions, reg]
    if guards is not None:
        a0 = out.actions[0].model_copy(
            update={"guard": guards, "precondition": "the booking is open"}
        )
        upd["actions"] = [a0, *out.actions[1:]]
    if card_guards is not None:
        upd["cards"] = [out.cards[0].model_copy(update={"guards": card_guards})]
    return sid, assemble(g, inp, out.model_copy(update=upd), CFG, lane_rules())


def test_a_boolean_attribute_is_coerced_or_dropped_by_code(finished):
    run = finished("quick")
    for given, want in (
        ("exits=Yes", "exits=true"),
        ("exits=FALSE", "exits=false"),
        ("exits=no", "exits=false"),
    ):
        sid, built = _built_with(run, region_attrs=[given])
        assert not built.errors, built.errors
        reg = next(r for r in built.screens[sid].regions if r.key == "progress_booking")
        assert reg.attrs == [want]
    sid, built = _built_with(run, region_attrs=["exits=SCR-009"])
    assert not built.errors, built.errors  # not a re-ask
    assert next(r for r in built.screens[sid].regions if r.key == "progress_booking").attrs == []
    assert any("dropped attribute exits" in n for n in built.notes)  # logged as R-code


def test_a_guard_outside_the_grammar_is_dropped_and_kept_as_plain_words(finished):
    run = finished("quick")
    sid, built = _built_with(
        run,
        guards=["method_chosen", "signed_in", "amount_due_present"],
        card_guards=["role_has_access", "is_open"],
    )
    assert not built.errors, built.errors
    s = built.screens[sid]
    a0 = sorted(s.actions, key=lambda x: x.key)[0]
    assert [g.pred for g in a0.guard] == ["signed_in"]
    assert "method chosen" in a0.precondition and "amount due present" in a0.precondition
    assert a0.precondition.startswith("the booking is open")
    assert [g.pred for g in s.route.guards] == [
        "role_has_access"
    ] and "is open" in s.role_conditions
    assert sum("outside the grammar" in n for n in built.notes) == 3
    # a structured guard still has to be well formed when it is written in the grammar
    sid, built = _built_with(run, guards=["field_equals ENT-001.status open"])
    assert not built.errors


def test_the_prompt_shows_the_guard_grammar_and_the_boolean_rule():
    text = "\n".join(vocab_lines())
    assert "signed_in | role_has_access | field_equals" in text and "true or false" in text
    from mde_spec_agent.lanes.screens.scrn.spec import HERE

    system = (HERE / "system.jinja").read_text()
    assert "plain words in precondition" in system and "bool take exactly true or false" in system
