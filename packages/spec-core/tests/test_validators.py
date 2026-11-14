import pytest

from mde_spec_core.pack import assemble
from mde_spec_core.planted import STRUCTURAL, catch_rates, default_vocab, run_planted
from mde_spec_core.synth import make_graph
from mde_spec_core.validators import REGISTRY, Context, blockers, run_all


def test_all_45_validators_are_registered_and_v18_is_superseded():
    run_all(Context(make_graph("tiny")))
    assert len(REGISTRY) == 45
    assert REGISTRY["V-18"].superseded_by == "V-26"


@pytest.mark.parametrize("name", ["tiny", "small", "medium", "big", "program"])
def test_good_synthetic_packs_have_no_blockers(name):
    g = make_graph(name)
    f = run_all(Context(g, vocab=default_vocab(), pack=assemble(g)))
    assert blockers(f) == [], [x.as_dict() for x in blockers(f)][:3]


def test_planted_structural_defects_are_caught():
    results = run_planted(make_graph("small"))
    rates = catch_rates(results)
    missed = [r for r in results if not r.caught]
    assert len(STRUCTURAL) == 22
    assert rates["overall"] >= 0.90, [(m.defect, m.expected, m.codes) for m in missed]
    assert all(r.caught for r in results if r.kind == "structural"), [
        (m.defect, m.expected, m.codes) for m in missed
    ]


def test_no_false_positives_on_the_unmodified_pack():
    g = make_graph("small")
    f = run_all(Context(g, vocab=default_vocab(), pack=assemble(g)))
    assert [x for x in f if x.severity == "blocker"] == []


def _act(key: str, what: str, intent: str = "confirm"):
    from mde_spec_core.graph import Action

    return Action(key=key, what=what, intent=intent, importance="primary")  # type: ignore[arg-type]


def test_decisions_counts_a_pair_from_the_data_as_one():
    from mde_spec_core.config import default_config
    from mde_spec_core.validators.screens import decisions

    pairs = default_config().checks.mobile_fit.decision_pairs
    approve, reject = _act("approve", "approves the request"), _act("reject", "rejects it")
    assert decisions([approve, reject], pairs) == 1  # one question, two answers
    assert decisions([reject, approve], pairs) == 1  # either order
    assert decisions([approve, _act("pay", "pays the bill", "pay")], pairs) == 2  # unrelated
    assert decisions([approve, reject, _act("pay", "pays", "pay")], pairs) == 2
    assert decisions([_act("deny_access", "denies it"), _act("allow", "lets them in")], pairs) == 1
    assert decisions([], pairs) == 0


def test_a_web_portal_keeps_every_mobile_fit_rule_except_the_column_limit():
    from mde_spec_core.graph import Region

    g = make_graph("small").model_copy(deep=True)
    s = next(x for x in g.screens.values() if x.surface == "phone" and not x.system)
    s.regions = [Region(key="wide", what="x", shape="collection", attrs=["columns=9"])] + [
        Region(key=f"p{i}", what="x", shape="value", importance="primary")  # type: ignore[arg-type]
        for i in range(4)
    ]
    for surface, wants_columns in (("phone", True), ("web", False)):
        s.surface = surface  # type: ignore[assignment]
        found = run_all(Context(g, vocab=default_vocab()), only={"V-37"})
        why = [f.why for f in found if f.where == s.id]
        assert any("primary regions" in w for w in why), surface  # kept on both surfaces
        assert any("columns" in w for w in why) is wants_columns, surface
