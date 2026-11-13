from mde_spec_core.bn import builder_needs, missing_needs, plain_lines, summary
from mde_spec_core.graph import Prov
from mde_spec_core.pack import assemble
from mde_spec_core.synth import make_graph


def by_id(needs):
    return {n.id: n for n in needs}


def test_all_seventeen_needs_on_a_clean_graph_and_none_missing():
    g = make_graph("small")
    needs = builder_needs(g, assemble(g))
    assert [n.id for n in needs] == [f"BN-{i:02d}" for i in range(1, 18)]
    assert missing_needs(needs) == []
    assert all(n.validators and n.expected_files for n in needs)
    assert sum(summary(needs).values()) == 17


def test_statuses_present_default_assumed_missing():
    g = make_graph("small")
    n = by_id(builder_needs(g))
    assert n["BN-04"].status == "default"  # entities are library nodes, flagged
    assert n["BN-02"].status == "assumed"  # no feature list
    assert n["BN-16"].status in ("default", "present")
    for e in g.entities.values():
        e.provenance = [Prov(kind="user", ref="BR-1")]
    assert by_id(builder_needs(g))["BN-04"].status == "present"
    g.behaviors.clear()
    n = by_id(builder_needs(g))
    assert n["BN-16"].status == "missing"
    assert [x.id for x in missing_needs(builder_needs(g))] == ["BN-16"]


def test_a_graph_without_some_behaviour_category_is_missing_that_need():
    g = make_graph("small")
    drop = next(iter(g.behaviors))
    del g.behaviors[drop]
    n = by_id(builder_needs(g))["BN-16"]
    assert n.status == "missing" and "behaviour categories missing" in n.why


def test_missing_needs_by_cause():
    g = make_graph("small")
    g.integrations.clear()  # facts say payments
    assert by_id(builder_needs(g))["BN-10"].status == "missing"
    g = make_graph("small")
    g.entities.clear()
    assert by_id(builder_needs(g))["BN-04"].status == "missing"
    g = make_graph("small")
    g.operations.clear()
    assert by_id(builder_needs(g))["BN-06"].status == "missing"
    g = make_graph("small")
    for s in g.screens.values():
        s.acceptance = []
    assert by_id(builder_needs(g))["BN-13"].status == "missing"
    g = make_graph("small")
    g.apps["APP-001"].scope_cap = 3
    assert by_id(builder_needs(g))["BN-17"].status == "missing"


def test_files_listed_come_from_the_pack_and_plain_lines():
    g = make_graph("tiny")
    p = assemble(g)
    n = by_id(builder_needs(g, p))
    assert "screens/SCR-001.md" in n["BN-09"].files and "build/build-plan.json" in n["BN-12"].files
    assert all(line.split()[0].startswith("BN-") for line in plain_lines(builder_needs(g, p)))
