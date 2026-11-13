"""What is derived from the structure in code (ADR 0017): matrices, navigation, phase, the band of a session, the research
guidance, the routing of every detail fact and the module slice (values only, and small enough for one call)."""

from __future__ import annotations

import json

import pytest

from mde_compass.catalog.size_bands import get_band
from mde_compass.domain.brief import Brief, DomainFact, IntegrationRecord
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.domain.situation import DiscoveryPlan, DomainHypothesis, SituationProfile
from mde_compass.domain.structure import (
    EntityRecord,
    FlowRecord,
    FlowStep,
    ModuleRecord,
    ScreenEntityUse,
    ScreenRecord,
)
from mde_compass.engine import structure_derive as derive
from mde_compass.engine.structure_testing import DOCTOR, FRONT_DESK, PATIENT, sample_app

SECRET = "the owner said this word for word ZQX"


def test_counts_and_whether_there_is_a_structure() -> None:
    assert derive.has_structure(Brief()) is False
    brief = sample_app()
    assert derive.has_structure(brief)
    assert derive.counts(brief) == derive.Counts(modules=3, screens=13, flows=2, entities=3)


def test_the_role_matrix_lists_the_screens_each_role_can_open_per_module() -> None:
    matrix = derive.role_matrix(sample_app())
    assert matrix[PATIENT]["MOD-002"] == ["SCR-006", "SCR-007", "SCR-008", "SCR-009"]
    assert matrix[DOCTOR]["MOD-003"] == ["SCR-011", "SCR-012", "SCR-013"]
    assert "MOD-003" not in matrix[FRONT_DESK]  # the receptionist has nothing in consultations


def test_the_entity_matrix_comes_from_each_entitys_access() -> None:
    assert derive.entity_matrix(sample_app())[PATIENT] == {
        "ENT-001": "create",
        "ENT-002": "view",
        "ENT-003": "view",
    }


def test_navigation_edges_are_the_consecutive_steps_of_the_flows_without_repeats() -> None:
    brief = sample_app()
    assert derive.nav_edges(brief) == [
        ("SCR-007", "SCR-008"),
        ("SCR-008", "SCR-009"),
        ("SCR-010", "SCR-011"),
        ("SCR-011", "SCR-012"),
    ]
    brief.flows.append(
        FlowRecord(
            id="FLW-003",
            name="again",
            steps=[
                FlowStep(screen="SCR-007"),
                FlowStep(screen="SCR-008"),
                FlowStep(screen="SCR-007"),
            ],
        )
    )
    edges = derive.nav_edges(brief)
    assert edges.count(("SCR-007", "SCR-008")) == 1 and ("SCR-008", "SCR-007") in edges


def test_the_phase_of_a_screen_follows_the_priority_the_person_gave_the_features() -> None:
    phases = derive.phase_of(sample_app())
    assert phases["SCR-008"] == "core"  # serves a must feature
    assert phases["SCR-012"] == "core"
    assert (
        phases["SCR-010"] == "core"
    )  # serves only a nice feature, but is a step of the consultation flow (a must)
    assert phases["SCR-001"] == "extended"  # serves nothing: neither core nor deferred


def test_a_screen_for_a_feature_set_for_later_is_later() -> None:
    brief = sample_app()
    brief.screens.append(
        ScreenRecord(
            id="SCR-014", name="Points", module="MOD-002", serves=["FTR-004"], roles=[PATIENT]
        )
    )
    assert derive.phase_of(brief)["SCR-014"] == "later"


# ------------------------------------------------------------------------------------------------ band


def test_the_band_is_the_tiers_raised_by_a_document_or_set_by_the_person() -> None:
    assert derive.effective_band("quick") == ("small", "tier")
    assert derive.effective_band("standard") == ("medium", "tier")
    assert derive.effective_band("thorough") == ("large", "tier")
    assert derive.effective_band("quick", claims=30) == ("medium", "document")
    assert derive.effective_band("quick", claims=80) == ("large", "document")
    assert derive.effective_band("thorough", claims=30) == (
        "large",
        "tier",
    )  # a document never lowers it
    assert derive.effective_band("thorough", override="small") == (
        "small",
        "user",
    )  # the person's word wins
    assert derive.effective_band("quick", override="nonsense") == ("small", "tier")


def _idea(**changes: object) -> Brief:
    brief = Brief()
    for key, value in changes.items():
        setattr(brief.meta, key, value)
    return brief


def test_a_big_idea_in_a_small_tier_is_suggested_a_larger_band_and_a_fitting_one_is_not() -> None:
    enterprise = _idea(situation_profile=SituationProfile(size_stakes="enterprise"))
    assert derive.suggest_band(enterprise, "small") == "large"
    assert derive.suggest_band(enterprise, "large") is None
    assert derive.suggest_band(_idea(), "small") is None  # no signal: no suggestion
    assert derive.suggest_band(_idea(plan=DiscoveryPlan(depth="large")), "medium") == "large"


def test_roles_apps_and_features_count_toward_the_suggestion() -> None:
    brief = sample_app()  # 3 roles and 2 apps: a medium app
    assert derive.suggest_band(brief, "small") == "medium"
    assert derive.suggest_band(brief, "medium") is None
    hypothesis = DomainHypothesis(
        likely_roles=["customer", "Customer", "courier", "owner", "admin", "support", "driver"]
    )
    assert (
        derive.suggest_band(_idea(hypothesis=hypothesis), "small") == "large"
    )  # six distinct roles named by the idea


def test_the_drafted_size_is_over_under_or_fits_its_band() -> None:
    brief = sample_app()  # 13 screens
    assert derive.size_verdict(brief, get_band("small")) == "fits"
    assert derive.size_verdict(brief, get_band("medium")) == "under"
    assert derive.size_verdict(brief, get_band("large")) == "under"
    brief.screens = brief.screens * 2  # 26: over a small band (15 x 1.25)
    assert derive.size_verdict(brief, get_band("small")) == "over"


# -------------------------------------------------------------------------------------- research guidance


def test_the_research_guidance_follows_the_band_and_names_what_to_look_up() -> None:
    brief = sample_app()
    brief.idea = {
        "category": BriefItem(value="healthcare practice management"),
        "subcategory": BriefItem(value="clinic chain"),
    }
    brief.meta.hypothesis = DomainHypothesis(
        likely_families=["health_wellness", "booking_scheduling", "health_wellness"]
    )
    brief.integrations = [
        IntegrationRecord(name="SMS reminders"),
        IntegrationRecord(name="Payments"),
    ]
    brief.domain_facts.append(
        DomainFact(
            id="FCT-020", type="rule", statement="Patient data must stay private", kind="compliance"
        )
    )
    g = derive.research_guidance(brief, "large")
    assert (g["size_band"], g["depth"], g["search_budget"], g["gap_fill"], g["max_requests"]) == (
        "large",
        "deep",
        20,
        True,
        20,
    )
    assert g["category"] == "healthcare practice management" and g["families"] == [
        "health_wellness",
        "booking_scheduling",
    ]
    focus = {f["topic"]: f["about"] for f in g["focus"]}  # type: ignore[union-attr, index]
    assert focus["landscape"] == ["Account and privacy", "Appointments", "Consultations"]
    assert "Appointment" in focus["domain"] and focus["integrations"] == [
        "SMS reminders",
        "Payments",
    ]
    assert (
        "health" in focus["compliance"] and "Patient data must stay private" in focus["compliance"]
    )
    assert focus["locales"] == ["en", "hi"]


def test_a_small_app_gets_light_research_and_a_brief_without_structure_still_has_guidance() -> None:
    g = derive.research_guidance(Brief(), "small")
    assert (g["depth"], g["search_budget"], g["gap_fill"]) == ("light", 6, False)
    assert "focus" not in g and "category" not in g  # nothing to say yet: no empty rows


def test_without_modules_the_landscape_focus_is_the_in_scope_features() -> None:
    brief = sample_app()
    brief.modules = []
    brief.screens = []
    brief.flows = []
    brief.entities = []
    focus = {f["topic"]: f["about"] for f in derive.research_guidance(brief, "medium")["focus"]}  # type: ignore[union-attr, index]
    assert focus["landscape"] == [
        "Book appointments",
        "Video consultation",
        "Schedule view",
    ]  # the later feature is left out


# ---------------------------------------------------------------------------------------- fact routing


def facts(*rows: tuple[str, str, str, list[str]]) -> list[DomainFact]:
    return [
        DomainFact(id=i, type="rule", statement=s, kind=k, refs=r, quote=SECRET)
        for i, k, s, r in rows
    ]


def test_a_fact_goes_to_the_module_its_refs_name() -> None:
    brief = sample_app()
    routing = derive.route_facts(
        brief,
        facts(
            ("FCT-1", "rule", "Visit notes are private", ["ENT-002"]),
            ("FCT-2", "rule", "Doctors start at nine", ["ROL-002"]),
        ),
    )
    assert [f.id for f in routing.by_module["MOD-003"]] == ["FCT-1", "FCT-2"]
    assert [f.id for f in routing.by_module["MOD-001"]] == [
        "FCT-2"
    ]  # a role belongs to every module it works in
    assert routing.orphans == []


def test_a_fact_with_no_refs_goes_to_the_module_it_shares_the_most_real_words_with() -> None:
    brief = sample_app()
    routing = derive.route_facts(
        brief,
        facts(("FCT-3", "rule", "An appointment can be cancelled until two hours before", [])),
    )
    assert list(routing.by_module) == ["MOD-002"]


def test_the_same_statement_read_again_from_each_batch_is_one_fact_for_the_routing() -> None:
    brief = sample_app()
    routing = derive.route_facts(
        brief,
        facts(
            ("FCT-1", "rule", "Six clinics in Pune", []),
            ("FCT-2", "rule", "  six  CLINICS in pune ", []),
            ("FCT-3", "rule", "Six clinics in Pune", []),
            ("FCT-4", "compliance", "Data stays in India", []),
            ("FCT-5", "compliance", "data stays in india", []),
        ),
    )
    assert [f.id for f in routing.orphans] == ["FCT-1"]  # once, not three times
    assert [f.id for f in routing.general] == ["FCT-4"]


def test_a_detail_fact_nobody_received_is_an_orphan_and_a_cross_cutting_one_goes_to_the_outline() -> (
    None
):
    brief = sample_app()
    routing = derive.route_facts(
        brief,
        facts(
            ("FCT-4", "rule", "Pallets are weighed twice", []),
            ("FCT-5", "compliance", "Data stays in India", []),
            ("FCT-6", "other", "Hello", []),
        ),
    )
    assert [f.id for f in routing.orphans] == ["FCT-4"]
    assert [f.id for f in routing.general] == ["FCT-5", "FCT-6"]


def test_every_detail_fact_lands_somewhere_or_is_listed_so_nothing_is_cut_by_a_size_cap() -> None:
    brief = sample_app()
    many = facts(
        *[(f"FCT-{n}", "rule", f"Rule number {n} about appointment slots", []) for n in range(200)]
    )
    routing = derive.route_facts(brief, many)
    received = {f.id for fs in routing.by_module.values() for f in fs}
    assert received | {f.id for f in routing.orphans} == {f.id for f in many}


# ------------------------------------------------------------------------------------- outline and slices


def test_the_outline_is_ids_and_names_only() -> None:
    out = derive.outline(sample_app())
    assert out["counts"] == {"modules": 3, "screens": 13, "flows": 2, "entities": 3}
    module = out["modules"][1]  # type: ignore[index]
    assert module["id"] == "MOD-002" and [s["id"] for s in module["screens"]][:2] == [
        "SCR-006",
        "SCR-007",
    ]
    assert "roles" not in module and "provenance" not in json.dumps(out)


def test_a_module_slice_has_what_a_call_needs_and_only_values() -> None:
    brief = sample_app()
    brief.domain_facts = facts(("FCT-1", "rule", "Visit notes are private", ["ENT-002"]))
    brief.modules[2] = brief.modules[2].model_copy(
        update={
            "provenance": [Provenance(type="user_text", ref="evt_1", quote=SECRET)],
            "tier": "Locked",
        }
    )
    s = derive.module_slice(brief, "MOD-003")
    assert s["module"]["id"] == "MOD-003"  # type: ignore[index]
    assert [x["id"] for x in s["screens"]] == ["SCR-011", "SCR-012", "SCR-013"]  # type: ignore[index, union-attr]
    assert {f["id"] for f in s["flows"]} == {"FLW-002"}  # type: ignore[index, union-attr]
    assert {e["id"] for e in s["entities"]} >= {"ENT-002"}  # type: ignore[index, union-attr]
    assert {r["id"] for r in s["roles"]} == {PATIENT, DOCTOR}  # type: ignore[index, union-attr]
    assert s["details"] == [{"id": "FCT-1", "statement": "Visit notes are private", "kind": "rule"}]
    dumped = json.dumps(s)
    for leak in (
        '"provenance"',
        '"quote"',
        '"history"',
        '"confidence"',
        '"tier"',
        '"status"',
        SECRET,
    ):
        assert (
            leak not in dumped
        )  # the slice is values only (a feature's own capability_tier is a value, not the tier)
    assert all(x["phase"] in ("core", "extended", "later") for x in s["screens"])  # type: ignore[index, union-attr]


def test_a_flow_that_leaves_the_module_names_the_screens_outside_it() -> None:
    brief = sample_app()
    flow = next(f for f in derive.module_slice(brief, "MOD-003")["flows"])  # type: ignore[union-attr]
    outside = next(st for st in flow["steps"] if st["screen"] == "SCR-010")
    assert outside["name"] == "Day schedule"  # a stub: id and name, not the whole screen


def test_an_unknown_module_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        derive.module_slice(sample_app(), "MOD-099")


def large_app(modules: int = 12, per_module: int = 5) -> Brief:
    brief = Brief()
    brief.roles = [
        __import__("mde_compass.domain.brief", fromlist=["RoleRecord"]).RoleRecord(
            id=f"ROL-{n:03d}", name_in_user_words=f"Role {n}"
        )
        for n in range(1, 7)
    ]
    sid = 0
    for m in range(1, modules + 1):
        mid = f"MOD-{m:03d}"
        brief.modules.append(
            ModuleRecord(
                id=mid,
                name=f"Area {m}",
                purpose=f"Everything about area {m}",
                roles=[r.id for r in brief.roles[:3]],
            )
        )
        entity = f"ENT-{m:03d}"
        brief.entities.append(
            EntityRecord(
                id=entity,
                key=f"thing_{m}",
                name=f"Thing {m}",
                module=mid,
                access={r.id: "edit" for r in brief.roles[:3]},
            )
        )
        for k in range(per_module):
            sid += 1
            brief.screens.append(
                ScreenRecord(
                    id=f"SCR-{sid:03d}",
                    name=f"Area {m} screen {k}",
                    purpose=f"Screen {k} of area {m}",
                    module=mid,
                    roles=[r.id for r in brief.roles[:3]],
                    entities=[ScreenEntityUse(entity=entity, use="edit")],
                    pattern="list",
                )
            )
        first = sid - per_module + 1
        brief.flows.append(
            FlowRecord(
                id=f"FLW-{m:03d}",
                name=f"Journey {m}",
                role="ROL-001",
                steps=[
                    FlowStep(screen=f"SCR-{first + k:03d}", action="go") for k in range(per_module)
                ],
                acceptance=["it works"],
            )
        )
    return brief


def test_a_slice_of_a_sixty_screen_app_stays_inside_the_budget_of_one_call() -> None:
    brief = large_app()  # 12 modules x 5 screens = 60 screens, 12 flows, 12 entities, 6 roles
    assert derive.counts(brief).screens == 60
    slices = [derive.slice_tokens(derive.module_slice(brief, m.id)) for m in brief.modules]
    assert max(slices) < 1500  # a module is about a thousand tokens whatever the size of the app
    assert derive.slice_tokens(derive.outline(brief)) < 2000  # the whole outline is a page


def test_the_outline_of_a_big_app_is_small_next_to_the_whole_structure() -> None:
    brief = large_app()
    whole = len(json.dumps(brief.model_dump(mode="json")["screens"]))
    assert len(json.dumps(derive.outline(brief))) < whole
