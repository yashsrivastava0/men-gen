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
    EntityRec,
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


MODULE_CASES = [
    (
        lambda o: setattr(first_story(o), "feature_ids", ["FTR-999"]),
        "feature FTR-999 does not exist",
    ),
    (lambda o: setattr(first_story(o), "feature_ids", []), "cite an in feature or a policy id"),
    (lambda o: setattr(first_story(o), "screen_ids", ["SCR-999"]), "screen SCR-999 does not exist"),
    (lambda o: setattr(first_story(o), "role", "ROL-999"), "is not a role of this app"),
    (lambda o: setattr(first_story(o), "prov", ["assumed:x"]), "assumed story is never voice must"),
    (lambda o: setattr(first_story(o), "prov", []), "provenance is empty"),
    (
        lambda o: setattr(first_story(o), "prov", ["user:FTR-999"]),
        "which is not an id of this call",
    ),
    (lambda o: setattr(first_story(o), "prov", ["gossip:somebody"]), "must be kind:ref"),
    (
        lambda o: setattr(first_story(o), "want", "tap the big blue button on the right"),
        "form word",
    ),
    (lambda o: setattr(first_story(o), "id", "US-S999"), "use an id of the draft or NEW-"),
    (lambda o: o.acceptance.clear(), "write at least one acceptance"),
    (lambda o: setattr(o.acceptance[0], "then", " "), "given, when and then must all be written"),
    (lambda o: o.stories.pop(0), "no story serves it"),
    (lambda o: setattr(o.fields[0], "key", "Bad Key"), "lower snake case"),
    (lambda o: o.fields.append(o.fields[0].model_copy()), "listed twice"),
    (lambda o: setattr(o.fields[0], "ref_entity", "ENT-999"), "does not exist"),
    (
        lambda o: setattr(o.fields[0], "constraint", "at most 500 characters"),
        "never invent a number",
    ),
    (
        lambda o: setattr(o.fields[0], "type", "enum") or setattr(o.fields[0], "enum_values", []),
        "needs its values",
    ),
    (
        lambda o: o.entities.append(
            EntityRec(id="NEW-pet", name="Pet", klass="primary", prov=["assumed:x"])
        ),
        "must be a join or log entity",
    ),
    (lambda o: setattr(o.entities[0], "prov", []), "provenance is empty"),
    (
        lambda o: o.rules.append(
            RuleRec(
                id="NEW-r",
                subject="Fee",
                statement="A fee applies",
                value="12",
                unit="percent",
                prov=["assumed:x"],
            )
        ),
        "needs a user or Compass quote",
    ),
    (
        lambda o: o.rules.append(
            RuleRec(
                id="NEW-r",
                subject="Fee",
                statement="A fee applies",
                value="12",
                unit="percent",
                prov=["user:FTR-001"],
            )
        ),
        "never invent a number",
    ),
    (
        lambda o: o.rules.append(
            RuleRec(id="NEW-r", subject="Fee", statement="A fee of 77 applies", prov=["assumed:x"])
        ),
        "never invent a number",
    ),
    (
        lambda o: o.rules.append(
            RuleRec(id="NEW-r", subject="Fee", statement="a fee", value="4", prov=["user:FTR-001"])
        ),
        "needs its unit",
    ),
]


@pytest.mark.parametrize(("mutate", "message"), MODULE_CASES)
def test_the_module_validator_says_no(env, mutate, message):
    inp, out = module_case(env)
    # the story-based cases need a story of this module that is voice must and user-evidenced
    out = out.model_copy(deep=True)
    mutate(out)
    errors = module_validate(inp, out)
    assert any(message in m for m in errors), (message, errors)


OP = OperationRec(
    id="NEW-x",
    name="Confirm it",
    entity="",
    method="POST",
    path="/things/{id}/confirm",
    roles=[],
    prov=["assumed:x"],
)


def test_custom_operation_rules(env):
    inp, out = module_case(env)
    ent = inp.entity_ids[0]
    role = inp.role_ids[0]

    def with_op(**kw):
        o = out.model_copy(deep=True)
        o.operations.append(OP.model_copy(update={"entity": ent, "roles": [role], **kw}))
        return o

    ok = with_op()
    ok.op_errors.append(OpErrorRec(op="NEW-x", code="forbidden", recovery="stay"))
    assert module_validate(inp, ok) == []
    bad = [
        (with_op(roles=[]), "name the roles"),
        (with_op(roles=["ROL-999"]), "is not a role of this app"),
        (with_op(path="things/confirm"), "path must start with /"),
        (with_op(entity="ENT-999"), "does not exist"),
        (with_op(prov=[]), "provenance is empty"),
        (with_op(), "declare its errors"),
        (with_op(id="OP-S001"), "written as NEW-<key>"),
        (with_op(name="Tap the red button"), "form word"),
    ]
    for o, msg in bad:
        assert any(msg in m for m in module_validate(inp, o)), (msg, module_validate(inp, o))
    # the closed vocabularies are enforced by the schema itself
    with pytest.raises(ValueError):
        OpErrorRec(op="NEW-x", code="made_up_code", recovery="stay")
    with pytest.raises(ValueError):
        FieldRec(entity="e", key="k", type="gadget")
    with pytest.raises(ValueError):
        StoryRec(id="NEW-s", role="r", want="w", so_that="s", voice="shouting")
    assert AcceptRec(story="s", given="g", when="w", then="t")


def test_lifecycle_rules(env):
    inp, out = module_case(env)
    ent = next(
        s.entity for s in out.states if sum(1 for x in out.states if x.entity == s.entity) >= 3
    )
    a = out.model_copy(deep=True)
    a.transitions = [t for t in a.transitions if t.entity != ent][:0] or [
        t for t in a.transitions if t.entity != ent
    ]
    assert any("unreachable" in m for m in module_validate(inp, a))
    b = out.model_copy(deep=True)
    for s in b.states:
        if s.entity == ent:
            s.initial = False
    assert any("exactly one status must be initial" in m for m in module_validate(inp, b))
    c = out.model_copy(deep=True)
    next(t for t in c.transitions if t.entity == ent).roles = []
    assert any("names no role" in m for m in module_validate(inp, c))


def test_integration_rules(env):
    ctx = env.ctx("standard")
    ensure_derived(ctx)
    e = env_of(ctx)
    inp = integ_input_factory(ctx, e)(sorted(e.graph.integrations))
    out = integ_mock(inp)
    assert integ_validate(inp, out) == []
    cases = [
        (
            lambda o: setattr(o.integrations[0], "interruption", ""),
            "needs an interrupted-flow rule",
        ),
        (lambda o: setattr(o.integrations[0], "pending_timeout", ""), "needs a pending timeout"),
        (
            lambda o: setattr(
                o.integrations[0], "pending_timeout", "Wait 45 seconds, then ask again"
            ),
            "never invent a number",
        ),
        (
            lambda o: setattr(o.integrations[0], "provider_ids", ["Acme Pay"]),
            "not in the closed list",
        ),
        (lambda o: setattr(o.integrations[0], "id", "INT-999"), "not a need of the plan"),
        (lambda o: setattr(o.integrations[0], "prov", []), "provenance is empty"),
        (
            lambda o: setattr(o.integrations[0], "claims", ["RI-1"]),
            "not a research item of this call",
        ),
        (
            lambda o: setattr(o.integrations[0], "flow", "A blue button opens the provider"),
            "form word",
        ),
        (lambda o: o.integrations.clear(), "no record"),
    ]
    for mutate, msg in cases:
        o = out.model_copy(deep=True)
        mutate(o)
        assert any(msg in m for m in integ_validate(inp, o)), (msg, integ_validate(inp, o))


def test_journey_rules(env):
    ctx = env.ctx("standard")
    ensure_derived(ctx)
    e = env_of(ctx)
    _, ids = journey_batches(ctx, e)[0]
    inp = journeys_input_factory(ctx, e)(ids)
    out = journeys_mock(inp)
    assert journeys_validate(inp, out) == []
    cases = [
        (lambda o: setattr(o.stages[0], "screen_ids", ["SCR-999"]), "does not exist"),
        (
            lambda o: setattr(o.stages[0], "action_keys", ["no_such_action"]),
            "is not an action of its screens",
        ),
        (lambda o: setattr(o.stages[0], "emotion_ref", "FCT-9999"), "is not a fact of this call"),
        (lambda o: setattr(o.flows[0], "moments", [99]), "is not a stage number"),
        (lambda o: setattr(o.flows[0], "unhappy", []), "keep the flow's own alternates"),
        (lambda o: setattr(o.flows[0], "role_ids", ["ROL-999"]), "does not exist"),
        (lambda o: setattr(o.flows[0], "prov", []), "provenance is empty"),
        (lambda o: setattr(o.stages[0], "goal", "wait 12 minutes"), "never invent a number"),
        (
            lambda o: setattr(o.stages[0], "pain", "a hover tooltip on the left hides it"),
            "form word",
        ),
        (lambda o: o.stages.pop(0), "numbered 1, 2, 3"),
        (lambda o: o.flows.pop(), "no record"),
    ]
    for mutate, msg in cases:
        o = out.model_copy(deep=True)
        mutate(o)
        assert any(msg in m for m in journeys_validate(inp, o)), (msg, journeys_validate(inp, o))
    with pytest.raises(ValueError):
        type(out.stages[0])(flow="f", n=1, goal="g", emotion="ecstatic")  # the closed emotion set


def test_deep_variants_are_wanted_and_checked(env):
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
    assert any("variant" in m for m in journeys_validate(inp, o))


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
