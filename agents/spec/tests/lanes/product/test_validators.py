"""Local validators of the three product calls: the mock's answer passes, and each rule the call promises is enforced
(closed vocabulary, ids and edges that exist, provenance present, no Locked claim without evidence, no layout word, bindings
resolve, no invented number, provider names from a closed list, interrupted flow and pending timeout, emotion assumed)."""

from __future__ import annotations

import pytest

from mde_spec_agent.lanes.product.kit import common
from mde_spec_agent.lanes.product.prod_integ.mock import handle as integ_mock
from mde_spec_agent.lanes.product.prod_integ.validate import validate as integ_validate
from mde_spec_agent.lanes.product.prod_journeys.mock import handle as journeys_mock
from mde_spec_agent.lanes.product.prod_journeys.validate import validate as journeys_validate
from mde_spec_agent.lanes.product.prod_module.mock import handle as module_mock
from mde_spec_agent.lanes.product.prod_module.schema import (
    AcceptRec,
    FieldRec,
    OperationRec,
    OpErrorRec,
    RuleRec,
    StoryRec,
)
from mde_spec_agent.lanes.product.prod_module.validate import validate as module_validate
from mde_spec_agent.lanes.product.steps.inputs import (
    integ_input_factory,
    journey_batches,
    journeys_input_factory,
    module_batches,
    module_input_factory,
)
from mde_spec_agent.lanes.product.steps.plan import ensure_derived, env_of


def module_case(env, key="standard", module="MOD-002"):
    ctx = env.ctx(key)
    ensure_derived(ctx)
    e = env_of(ctx)
    b = next(x for x in module_batches(ctx, e) if x.module_id == module)
    inp = module_input_factory(ctx, e)(module)(b.items)
    return inp, module_mock(inp)


def test_the_mock_answers_pass_their_own_validators(env, key):
    ctx = env.ctx(key)
    ensure_derived(ctx)
    e = env_of(ctx)
    for b in module_batches(ctx, e):
        inp = module_input_factory(ctx, e)(b.module_id)(b.items)
        assert module_validate(inp, module_mock(inp)) == [], b.batch_id
    inp = integ_input_factory(ctx, e)(sorted(e.graph.integrations))
    assert integ_validate(inp, integ_mock(inp)) == []
    for _, ids in journey_batches(ctx, e):
        inp = journeys_input_factory(ctx, e)(ids)
        assert journeys_validate(inp, journeys_mock(inp)) == [], ids


def first_story(out):
    return out.stories[0]


def test_the_module_validator_still_says_no_to_what_no_code_can_choose(env):
    from mde_spec_agent.lanes.product.prod_module.schema import FieldRec

    inp, out = module_case(env)
    out = out.model_copy(deep=True)
    out.fields += [
        FieldRec(entity=out.entities[0].id, key=f"extra_{i}", type="text") for i in range(7)
    ]
    assert any("new fields" in m for m in module_validate(inp, out))
    out.stories[
        0
    ].feature_ids = []  # a draft story that cites no in feature is not a NEW one: code cannot attach it
    assert any("cite an in feature or a policy id" in m for m in module_validate(inp, out))


def new_rule(**kw):
    base = {"id": "NEW-r", "subject": "Fee", "statement": "A fee applies", "prov": ["assumed:x"]}
    return RuleRec(**{**base, **kw})


# What a model gets wrong that code mends mechanically (R-code): nothing here is a validator error, and the repaired answer says so.
REPAIR_CASES = [
    (
        "a layout word in a story",
        lambda o: setattr(
            first_story(o), "want", "see the class, tap the big blue button on the right"
        ),
        lambda o: "button" not in first_story(o).want and first_story(o).want,
    ),
    (
        "no acceptance at all",
        lambda o: o.acceptance.clear(),
        lambda o: len(o.acceptance) >= len(o.stories),
    ),
    (
        "a blank acceptance part",
        lambda o: setattr(o.acceptance[0], "then", " "),
        lambda o: all(a.then.strip() for a in o.acceptance),
    ),
    (
        "a layout word in acceptance",
        lambda o: setattr(
            o.acceptance[0], "when", "the person taps the button on the right side by side with it"
        ),
        lambda o: (
            all("button" not in a.when for a in o.acceptance) and o.acceptance[0].when.strip()
        ),
    ),
    (
        "a draft story left out",
        lambda o: o.stories.pop(0),
        lambda o: len(o.stories) >= 1 and o.acceptance,
    ),
    (
        "a key that is not snake case",
        lambda o: setattr(o.fields[0], "key", "Bad Key"),
        lambda o: o.fields[0].key == "bad_key",
    ),
    (
        "a field listed twice",
        lambda o: o.fields.append(o.fields[0].model_copy()),
        lambda o: (
            [(f.entity, f.key) for f in o.fields].count((o.fields[0].entity, o.fields[0].key)) == 1
        ),
    ),
    (
        "a ref to an entity that does not exist",
        lambda o: (
            setattr(o.fields[0], "ref_entity", "ENT-999"),
            setattr(o.fields[0], "type", "ref"),
        ),
        lambda o: o.fields[0].ref_entity == "" and o.fields[0].type != "ref",
    ),
    (
        "an invented number in a constraint",
        lambda o: setattr(o.fields[0], "constraint", "at most 500 characters"),
        lambda o: "500" not in o.fields[0].constraint,
    ),
    (
        "an enum with no values",
        lambda o: (setattr(o.fields[0], "type", "enum"), setattr(o.fields[0], "enum_values", [])),
        lambda o: o.fields[0].type != "enum" or o.fields[0].enum_values,
    ),
    (
        "a value with no quote behind it",
        lambda o: o.rules.append(new_rule(value="12", unit="percent")),
        lambda o: next(r for r in o.rules if r.id == "NEW-r").value == "",
    ),
    (
        "a value no fact states, even with a user source",
        lambda o: o.rules.append(new_rule(value="12", unit="percent", prov=["user:FTR-001"])),
        lambda o: next(r for r in o.rules if r.id == "NEW-r").value == "",
    ),
    (
        "a number in a rule's text",
        lambda o: o.rules.append(new_rule(statement="A fee of 77 applies")),
        lambda o: all(
            r.id != "NEW-r" for r in o.rules
        ),  # nothing is left of the statement: the rule is dropped
    ),
    (
        "a value with no unit",
        lambda o: o.rules.append(new_rule(statement="a fee", value="4", prov=["user:FTR-001"])),
        lambda o: next(r for r in o.rules if r.id == "NEW-r").value == "",
    ),
]


@pytest.mark.parametrize(
    ("name", "mutate", "check"), REPAIR_CASES, ids=[c[0] for c in REPAIR_CASES]
)
def test_what_code_can_mend_is_mended_not_reasked(env, name, mutate, check):
    from mde_spec_agent.lanes.product.prod_module.repair import repair

    inp, out = module_case(env)
    out = out.model_copy(deep=True)
    mutate(out)
    fixed, rows = repair(inp, out)
    assert check(fixed), name
    assert rows, name  # logged, never silent
    assert module_validate(inp, out) == [], (name, module_validate(inp, out))


OP = OperationRec(
    id="NEW-x",
    name="Confirm it",
    entity="",
    method="POST",
    path="/things/{id}/confirm",
    roles=[],
    prov=["assumed:x"],
)


def test_custom_operations_with_unknown_references_are_mended_not_reasked(env):
    """Live Thorough run 2: operations on an entity id of another module. Relinked when exactly one entity of this module fits
    by name, else dropped; unknown roles removed; paths made well formed; every case logged, none re-asked."""
    from mde_spec_agent.lanes.product.prod_module.repair import repair

    inp, out = module_case(env)
    ent = inp.entity_ids[0]
    ent_name = next(e.name for e in out.entities if e.id == ent)
    role = inp.role_ids[0]
    other = next(i for i in inp.ref_ids if i.startswith("ENT-") and i not in inp.entity_ids)

    def with_op(key="x", **kw):
        o = out.model_copy(deep=True)
        o.operations.append(
            OP.model_copy(update={"id": f"NEW-{key}", "entity": ent, "roles": [role], **kw})
        )
        o.op_errors.append(OpErrorRec(op=f"NEW-{key}", code="forbidden", recovery="stay"))
        return o

    assert module_validate(inp, with_op()) == []
    # an entity of another module: relinked to the one of this module whose name fits, else the operation is dropped
    fit = with_op("list-" + ent_name.lower().replace(" ", "-"), entity=other)
    fixed, rows = repair(inp, fit)
    op = next(o for o in fixed.operations if o.id.startswith("NEW-list-"))
    assert op.entity == ent and any("relinked" in r["what"] for r in rows)
    nofit = with_op(
        "zzz-unrelated", entity=other, name="Something unrelated", path="/zzz/unrelated"
    )
    fixed, rows = repair(inp, nofit)
    assert all(o.id != "NEW-zzz-unrelated" for o in fixed.operations)
    assert all(
        f.op != "NEW-zzz-unrelated" for f in fixed.op_fields + fixed.op_errors
    )  # what hung on it went too
    assert any("operation dropped" in r["what"] for r in rows)
    # an entity that exists nowhere
    gone, _ = repair(inp, with_op("ghost", entity="ENT-999", name="Ghost thing", path="/ghost"))
    assert all(o.id != "NEW-ghost" for o in gone.operations)
    # roles: unknown ones are removed; none left and none that move the entity: dropped
    assert repair(inp, with_op(roles=[role, "ROL-999"]))[0].operations[-1].roles == [role]
    assert all(
        o.id != "NEW-x" for o in repair(inp, with_op(roles=["ROL-999"], entity=""))[0].operations
    )
    # path, errors, repeats
    assert repair(inp, with_op(path="things confirm"))[0].operations[-1].path == "/things-confirm"
    no_err = out.model_copy(deep=True)
    no_err.operations.append(OP.model_copy(update={"entity": ent, "roles": [role]}))
    assert any(e.op == "NEW-x" for e in repair(inp, no_err)[0].op_errors)
    twice = with_op("a")
    twice.operations.append(twice.operations[-1].model_copy(update={"id": "NEW-b"}))
    assert [o.id for o in repair(inp, twice)[0].operations if o.id in ("NEW-a", "NEW-b")] == [
        "NEW-a"
    ]
    for o in (fit, nofit, with_op(roles=["ROL-999"]), with_op(id="OP-S001")):
        assert module_validate(inp, o) == []
    # the closed vocabularies are enforced by the schema itself
    with pytest.raises(ValueError):
        OpErrorRec(op="NEW-x", code="made_up_code", recovery="stay")
    with pytest.raises(ValueError):
        FieldRec(entity="e", key="k", type="gadget")
    with pytest.raises(ValueError):
        StoryRec(id="NEW-s", role="r", want="w", so_that="s", voice="shouting")
    assert AcceptRec(story="s", given="g", when="w", then="t")


def test_lifecycles_are_mended_by_code(env):
    """A lifecycle V-09 would refuse (unreachable status, no status with no way out, no initial, a transition with no role)
    is mended: roles from the draft, one initial status, else the draft's own lifecycle; the status values follow."""
    from mde_spec_agent.lanes.product.prod_module.repair import repair

    inp, out = module_case(env)
    ent = next(
        s.entity for s in out.states if sum(1 for x in out.states if x.entity == s.entity) >= 3
    )
    draft = [s.state for s in out.states if s.entity == ent]

    def states(o):
        return [s.state for s in o.states if s.entity == ent]

    a = out.model_copy(deep=True)
    a.transitions = [
        t for t in a.transitions if t.entity != ent
    ]  # every status after the first is unreachable
    fixed, rows = repair(inp, a)
    assert states(fixed) == draft and any(r["node_id"] == ent for r in rows)
    assert [
        t for t in fixed.transitions if t.entity == ent
    ]  # the draft's transitions came back with it
    b = out.model_copy(deep=True)
    for s in b.states:
        if s.entity == ent:
            s.initial = False
    fixed, _ = repair(inp, b)
    assert [s.state for s in fixed.states if s.entity == ent and s.initial] == [draft[0]]
    c = out.model_copy(deep=True)
    for t in c.transitions:
        if t.entity == ent:
            t.roles = []
    fixed, _ = repair(inp, c)
    assert all(t.roles for t in fixed.transitions if t.entity == ent)
    d = out.model_copy(deep=True)  # a cycle with no way out: no terminal status
    d.transitions = [t for t in d.transitions if t.entity != ent] + [
        t.model_copy(update={"entity": ent, "from_state": draft[-1], "to_state": draft[0]})
        for t in out.transitions
        if t.entity == ent
    ][:1]
    d.transitions += [t.model_copy() for t in out.transitions if t.entity == ent]
    d.transitions += [
        type(out.transitions[0])(
            entity=ent, from_state=draft[-1], to_state=draft[0], roles=["ROL-001"]
        )
    ]
    fixed, _ = repair(inp, d)
    for x in (a, b, c, d):
        assert module_validate(inp, x) == []
    status = [f for f in fixed.fields if f.entity == ent and f.key == "status" and f.type == "enum"]
    assert all(set(f.enum_values) == set(states(fixed)) for f in status)


def test_integrations_are_mended_by_code_not_reasked(env):
    from mde_spec_agent.lanes.product.kit import vocab
    from mde_spec_agent.lanes.product.prod_integ.repair import repair

    ctx = env.ctx("standard")
    ensure_derived(ctx)
    e = env_of(ctx)
    inp = integ_input_factory(ctx, e)(sorted(e.graph.integrations))
    out = integ_mock(inp)
    assert integ_validate(inp, out) == []
    lib = vocab.hints()["integration_defaults"]
    cases = [
        (
            "no interrupted-flow rule",
            lambda o: setattr(o.integrations[0], "interruption", ""),
            lambda o: o.integrations[0].interruption == lib["interruption"],
        ),
        (
            "no pending timeout",
            lambda o: setattr(o.integrations[0], "pending_timeout", ""),
            lambda o: o.integrations[0].pending_timeout == lib["pending_timeout"],
        ),
        (
            "an invented number",
            lambda o: setattr(
                o.integrations[0], "pending_timeout", "Wait 45 seconds, then ask again"
            ),
            lambda o: (
                "45" not in o.integrations[0].pending_timeout
                and o.integrations[0].pending_timeout.strip()
            ),
        ),
        (
            "a provider outside the list",
            lambda o: setattr(o.integrations[0], "provider_ids", ["Acme Pay"]),
            lambda o: "Acme Pay" not in o.integrations[0].provider_ids,
        ),
        (
            "a record that is no need",
            lambda o: setattr(o.integrations[0], "id", "INT-999"),
            lambda o: (
                all(r.id != "INT-999" for r in o.integrations)
                and len(o.integrations) == len(inp.integration_ids)
            ),
        ),
        (
            "a layout word",
            lambda o: setattr(
                o.integrations[0],
                "flow",
                "A blue button opens the provider. The person pays there.",
            ),
            lambda o: "button" not in o.integrations[0].flow,
        ),
        (
            "no record at all",
            lambda o: o.integrations.clear(),
            lambda o: len(o.integrations) == len(inp.integration_ids),
        ),
        (
            "no events",
            lambda o: o.events.clear(),
            lambda o: (
                {x.integration for x in o.events}
                >= {r.id for r in o.integrations if r.provider_side_flow}
            ),
        ),
    ]
    for name, mutate, check in cases:
        o = out.model_copy(deep=True)
        mutate(o)
        fixed, rows = repair(inp, o)
        assert check(fixed), name
        assert rows, name
        assert integ_validate(inp, o) == [], (name, integ_validate(inp, o))


def journeys_case(env, key="standard"):
    ctx = env.ctx(key)
    ensure_derived(ctx)
    e = env_of(ctx)
    batches = journey_batches(ctx, e)
    _, ids = next(
        b
        for b in batches
        if any(i in {f.id for f in e.graph.flows.values() if f.unhappy_paths} for i in b[1])
    )
    inp = journeys_input_factory(ctx, e)(ids)
    return inp, journeys_mock(inp)


def test_journey_rules_code_cannot_mend(env):
    inp, out = journeys_case(env)
    assert journeys_validate(inp, out) == []
    with pytest.raises(ValueError):
        type(out.stages[0])(flow="f", n=1, goal="g", emotion="ecstatic")  # the closed emotion set


def test_journeys_are_mended_by_code_not_reasked(env):
    """First live Thorough run: 4 of 5 journey calls fell back because the draft flow's own alternates were not kept first,
    and flows came back with no stages. Everything below is mended mechanically and logged."""
    from mde_spec_agent.lanes.product.prod_journeys.repair import repair

    inp, out = journeys_case(env)
    flow = next(
        f
        for f in out.flows
        if inp.baseline.flows and f.id in {b.id for b in inp.baseline.flows if b.unhappy}
    )
    own = next(b.unhappy for b in inp.baseline.flows if b.id == flow.id)

    def stages_of(o, fid):
        return sorted((s for s in o.stages if s.flow == fid), key=lambda s: s.n)

    cases = {
        "alternates dropped": (
            lambda o: setattr(next(f for f in o.flows if f.id == flow.id), "unhappy", []),
            lambda o: next(f for f in o.flows if f.id == flow.id).unhappy[: len(own)] == own,
        ),
        "alternates reordered behind an addition": (
            lambda o: setattr(
                next(f for f in o.flows if f.id == flow.id),
                "unhappy",
                ["If the person gives up, nothing is lost.", *own],
            ),
            lambda o: (
                next(f for f in o.flows if f.id == flow.id).unhappy
                == [*own, "If the person gives up, nothing is lost."]
            ),
        ),
        "a flow with no stages": (
            lambda o: setattr(o, "stages", [s for s in o.stages if s.flow != flow.id]),
            lambda o: len(stages_of(o, flow.id)) >= 1,
        ),
        "a flow left out": (
            lambda o: setattr(o, "flows", [f for f in o.flows if f.id != flow.id]),
            lambda o: any(f.id == flow.id for f in o.flows) and stages_of(o, flow.id),
        ),
        "stages with gaps": (
            lambda o: setattr(stages_of(o, flow.id)[0], "n", 7),
            lambda o: (
                [s.n for s in stages_of(o, flow.id)]
                == list(range(1, len(stages_of(o, flow.id)) + 1))
            ),
        ),
        "an unknown screen": (
            lambda o: setattr(stages_of(o, flow.id)[0], "screen_ids", ["SCR-999"]),
            lambda o: stages_of(o, flow.id)[0].screen_ids,
        ),
        "an action its screens do not have": (
            lambda o: setattr(stages_of(o, flow.id)[0], "action_keys", ["no_such_action"]),
            lambda o: "no_such_action" not in stages_of(o, flow.id)[0].action_keys,
        ),
        "an unknown fact for a feeling": (
            lambda o: setattr(stages_of(o, flow.id)[0], "emotion_ref", "FCT-9999"),
            lambda o: stages_of(o, flow.id)[0].emotion_ref == "",
        ),
        "a moment that is no stage": (
            lambda o: setattr(next(f for f in o.flows if f.id == flow.id), "moments", [99]),
            lambda o: 99 not in next(f for f in o.flows if f.id == flow.id).moments,
        ),
        "an unknown role": (
            lambda o: setattr(next(f for f in o.flows if f.id == flow.id), "role_ids", ["ROL-999"]),
            lambda o: (
                next(f for f in o.flows if f.id == flow.id).role_ids
                and "ROL-999" not in next(f for f in o.flows if f.id == flow.id).role_ids
            ),
        ),
        "an invented number in a goal": (
            lambda o: setattr(stages_of(o, flow.id)[0], "goal", "wait 12 minutes"),
            lambda o: (
                "12" not in stages_of(o, flow.id)[0].goal and stages_of(o, flow.id)[0].goal.strip()
            ),
        ),
        "a layout word in pain": (
            lambda o: setattr(
                stages_of(o, flow.id)[0], "pain", "a hover tooltip on the left hides it"
            ),
            lambda o: "tooltip" not in stages_of(o, flow.id)[0].pain,
        ),
        "a layout word in an unhappy path": (
            lambda o: setattr(
                next(f for f in o.flows if f.id == flow.id),
                "unhappy",
                [*own, "A red banner shows. The person can retry."],
            ),
            lambda o: (
                all("banner" not in u for u in next(f for f in o.flows if f.id == flow.id).unhappy)
                and "The person can retry." in next(f for f in o.flows if f.id == flow.id).unhappy
            ),
        ),
    }
    for name, (mutate, check) in cases.items():
        o = out.model_copy(deep=True)
        mutate(o)
        fixed, rows = repair(inp, o)
        assert check(fixed), name
        assert rows, name  # logged, never silent
        assert journeys_validate(inp, o) == [], (name, journeys_validate(inp, o))


def test_deep_variants_are_wanted_and_made_by_code_when_missing(env):
    from mde_spec_agent.lanes.product.prod_journeys.repair import repair

    ctx = env.ctx("thorough")
    ensure_derived(ctx)
    e = env_of(ctx)
    batch = next(b for b in journey_batches(ctx, e) if any(e.graph.flows[i].primary for i in b[1]))
    inp = journeys_input_factory(ctx, e)(batch[1])
    assert inp.variants_wanted and all(
        r.split(":")[0] in {"first_run", "returning", "recovery", "cross_app"}
        for r in inp.variants_wanted
    )
    out = journeys_mock(inp)
    assert journeys_validate(inp, out) == []
    o = out.model_copy(deep=True)
    victim = next(f for f in o.flows if f.id.startswith("NEW-"))
    o.flows.remove(victim)
    o.stages = [s for s in o.stages if s.flow != victim.id]
    fixed, rows = repair(inp, o)
    assert any(f.variant == victim.variant and f.of == victim.of for f in fixed.flows)
    assert any("variant was asked for" in r["what"] for r in rows)
    assert journeys_validate(inp, o) == []


def test_compact_apps_only_carry_emotion_on_the_primary_journey(env):
    ctx = env.ctx("quick")
    ensure_derived(ctx)
    env.run(ctx)
    g = ctx.graph
    for f in g.flows.values():
        if not f.primary:
            assert all(s.emotion == "calm" for s in f.stages), f.id
    assert all(
        s.emotion_assumed for f in g.flows.values() for s in f.stages
    )  # no fact states a feeling


def test_common_helpers():
    assert common.parse_prov("user:FTR-001") == ("user", "FTR-001")
    assert common.parse_prov("policy:store.x@2026-11-14") == ("policy", "policy:store.x@2026-11-14")
    assert common.parse_prov("assumed:no source") == ("assumed", "no source")
    assert common.parse_prov("whatever") is None and common.parse_prov("gossip:x") is None
    labels = {"RI-1": "official", "RI-2": "single_source", "RI-3": "unverified"}
    P = common.prov_nodes
    assert common.tier_and_assumed(P(["user:FTR-001"]), labels) == (
        "default",
        False,
    )  # never locked from a model
    assert common.tier_and_assumed(P(["research:RI-1"]), labels) == ("default", False)
    assert common.tier_and_assumed(P(["research:RI-2"]), labels) == ("open", True)
    assert common.tier_and_assumed(P(["assumed:x"]), labels) == ("default", True)
    assert common.invented_numbers("pay 40 within 2 days", {"2"}) == ["40"]
    assert common.invented_numbers("pay 4,5 now", {"4.5"}) == []
    assert common.new_form_words("a red button", "the person pays") == [
        "button",
        "red",
    ] or common.new_form_words("a red button", "the person pays")
    assert common.new_form_words("a red button", "the owner wants a red button") == []
