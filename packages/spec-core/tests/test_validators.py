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
