from mde_spec_core.coverage import coverage_matrix, plain_words
from mde_spec_core.graph import Feature, Story
from mde_spec_core.synth import make_graph


def test_clean_graph_has_no_gaps_and_rows_for_everything():
    g = make_graph("small")
    m = coverage_matrix(g)
    assert m.gaps == []
    assert set(m.entities) == set(g.entities) and set(m.roles) == set(g.roles)
    assert m.entities["ENT-001"]["shown_on"] and m.flows["FLW-001"] == ["SCR-001", "SCR-002"]


def test_an_entity_that_loses_its_screens_is_a_gap():
    g = make_graph("small")
    eid = "ENT-001"
    for s in g.screens.values():
        s.regions = [r for r in s.regions if not any(b.startswith(f"{eid}.") for b in r.bind)]
        for a in s.actions:
            for step in a.effect:
                if step.verb == "call" and g.operations[step.target].entity == eid:
                    step.target = next(o.id for o in g.operations.values() if o.entity == "ENT-002")
    m = coverage_matrix(g)
    assert [x.id for x in m.gaps if x.kind == "entity"] == [eid]
    assert any(eid in line for line in plain_words(m))


def test_role_flow_and_feature_gaps():
    g = make_graph("small")
    g.roles["ROL-003"].home_screen = None
    for s in g.screens.values():
        s.role_ids = [r for r in s.role_ids if r != "ROL-003"]
    g.flows["FLW-001"].stages[1].screen_ids = ["SCR-404"]
    g.add("features", Feature(id="FTR-001", name="Loyalty", status="in"))
    g.stories["US-001"].feature_ids = []
    m = coverage_matrix(g)
    kinds = {(x.kind, x.id) for x in m.gaps}
    assert ("role", "ROL-003") in kinds and ("flow", "FLW-001") in kinds
    assert ("feature", "FTR-001") in kinds
    assert len([x for x in m.gaps if x.id == "FTR-001"]) == 2  # no story and no screen
    g.stories["US-001"] = Story(
        id="US-001",
        role_id="ROL-001",
        want="w",
        so_that="s",
        feature_ids=["FTR-001"],
        screen_ids=["SCR-001"],
    )
    assert [x for x in coverage_matrix(g).gaps if x.id == "FTR-001"] == []


def test_later_screens_do_not_count():
    g = make_graph("small")
    g.screens["SCR-004"].status = "later"
    assert "SCR-004" not in {s for v in coverage_matrix(g).roles.values() for s in v}
