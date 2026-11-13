"""The structure checks (ADR 0017; plan 5b and 5d): a valid app has no error, every fault is named by its code, the
deterministic fixes fix what they say and nothing else, and the checks only report: they never invent or add."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.catalog.size_bands import get_band
from mde_compass.catalog.structure_rules import (
    CHECK_CODES,
    load_check_policy,
    load_structure_rules,
)
from mde_compass.domain.brief import Brief, DomainFact
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.domain.structure import EntityField, ScreenEntityUse
from mde_compass.engine import structure_checks as checks
from mde_compass.engine.structure_testing import (
    BOTH,
    DOCTOR,
    FRONT_DESK,
    PATIENT,
    sample_app,
)

MEDIUM = get_band("medium")


def codes(brief: Brief, band: str = "medium", **kw: object) -> set[str]:
    return {i.code for i in checks.run_checks(brief, get_band(band), **kw)}  # type: ignore[arg-type]


def set_screen(brief: Brief, sid: str, **changes: object) -> Brief:
    out = brief.model_copy(deep=True)
    i = next(i for i, s in enumerate(out.screens) if s.id == sid)
    out.screens[i] = out.screens[i].model_copy(update=changes)
    return out


def set_entity(brief: Brief, eid: str, **changes: object) -> Brief:
    out = brief.model_copy(deep=True)
    i = next(i for i, e in enumerate(out.entities) if e.id == eid)
    out.entities[i] = out.entities[i].model_copy(update=changes)
    return out


# ------------------------------------------------------------------------------------------- a valid app


def test_the_sample_app_has_no_error_in_any_band() -> None:
    brief = sample_app()
    for band in ("small", "medium", "large"):
        assert checks.errors(checks.run_checks(brief, get_band(band))) == []


def test_a_brief_without_a_structure_has_no_issue() -> None:
    assert checks.run_checks(Brief(), MEDIUM) == []


def test_every_check_code_has_a_policy_and_the_policy_names_nothing_else() -> None:
    assert set(load_check_policy()) == set(CHECK_CODES)


def test_the_size_of_a_draft_is_only_ever_a_warning_that_never_asks_for_more_screens() -> None:
    brief = sample_app()  # 13 screens: below the medium band
    issues = checks.run_checks(brief, MEDIUM)
    thin = next(i for i in issues if i.code == "thin_draft")
    assert (thin.severity, thin.fix) == ("warn", "none")  # a band is guidance, not a quota
    assert not [i for i in checks.needs_model(issues) if i.code == "thin_draft"]


def test_over_the_band_recommends_a_larger_one_and_asks_for_nothing_else() -> None:
    brief = sample_app()
    issues = checks.run_checks(
        brief, get_band("small")
    )  # 13 fits a small band; add screens to pass 18.75
    assert "over_band" not in {i.code for i in issues}
    big = brief.model_copy(deep=True)
    for n in range(14, 21):
        big.screens.append(
            big.screens[0].model_copy(update={"id": f"SCR-{n:03d}", "name": f"Extra {n}"})
        )
    over = next(i for i in checks.run_checks(big, get_band("small")) if i.code == "over_band")
    assert (over.severity, over.fix) == ("warn", "none")


# -------------------------------------------------------------------------------------- fault injection

FAULTS: list[tuple[str, Callable[[Brief], Brief], str]] = [
    (
        "screen_without_module",
        lambda b: set_screen(b, "SCR-004", module=""),
        "screen_without_module",
    ),
    (
        "screen_unknown_module",
        lambda b: set_screen(b, "SCR-004", module="MOD-099"),
        "screen_without_module",
    ),
    ("dangling_role", lambda b: set_screen(b, "SCR-004", roles=["ROL-099"]), "dangling_ref"),
    ("dangling_feature", lambda b: set_screen(b, "SCR-007", serves=["FTR-099"]), "dangling_ref"),
    (
        "dangling_entity",
        lambda b: set_screen(b, "SCR-007", entities=[ScreenEntityUse(entity="ENT-099")]),
        "dangling_ref",
    ),
    (
        "module_without_screens",
        lambda b: b.model_copy(
            update={
                "screens": [s for s in b.screens if s.module != "MOD-003"],
                "flows": b.flows[:1],
            }
        ),
        "module_without_screens",
    ),
    ("duplicate_name", lambda b: set_screen(b, "SCR-004", name="Profile"), "screen_duplicate_name"),
    (
        "near_duplicate",
        lambda b: set_screen(b, "SCR-004", name="Privacy and consent"),
        "screen_near_duplicate",
    ),
    ("unknown_pattern", lambda b: set_screen(b, "SCR-004", pattern="carousel"), "unknown_pattern"),
    ("screen_without_roles", lambda b: set_screen(b, "SCR-004", roles=[]), "screen_without_roles"),
    (
        "entity_without_fields",
        lambda b: set_entity(b, "ENT-003", fields=[]),
        "entity_without_fields",
    ),
    ("entity_bad_key", lambda b: set_entity(b, "ENT-003", key="डॉक्टर"), "entity_key_invalid"),
    ("entity_no_access", lambda b: set_entity(b, "ENT-003", access={}), "entity_access_missing"),
    (
        "money_as_text",
        lambda b: set_entity(
            b, "ENT-001", fields=[EntityField(key="fee", name="Fee", type="text")]
        ),
        "field_type_money",
    ),
    (
        "unknown_field_type",
        lambda b: set_entity(b, "ENT-001", fields=[EntityField(key="fee", type="decimal")]),
        "unknown_field_type",
    ),
    (
        "date_as_text",
        lambda b: set_entity(b, "ENT-001", fields=[EntityField(key="due_date", type="text")]),
        "field_type_hint",
    ),
]


@pytest.mark.parametrize(("name", "break_it", "code"), FAULTS, ids=[f[0] for f in FAULTS])
def test_each_fault_is_named_by_its_code(
    name: str, break_it: Callable[[Brief], Brief], code: str
) -> None:
    assert code not in codes(sample_app())
    assert code in codes(break_it(sample_app()))


def test_a_role_that_cannot_open_a_step_of_its_own_flow_is_an_error() -> None:
    brief = set_screen(
        sample_app(), "SCR-008", roles=[FRONT_DESK]
    )  # the patient's booking step, patients locked out
    issue = next(
        i for i in checks.run_checks(brief, MEDIUM) if i.code == "flow_role_cannot_access_step"
    )
    assert issue.severity == "error" and issue.fix == "repair"
    assert issue.ids == ("FLW-001", "SCR-008", PATIENT) and issue.module == "MOD-002"


def test_a_role_cannot_edit_what_it_may_only_view() -> None:
    brief = set_screen(
        sample_app(), "SCR-012", roles=[PATIENT]
    )  # patients alone on the screen that EDITS notes: nobody can do the work
    issue = next(
        i for i in checks.run_checks(brief, MEDIUM) if i.code == "screen_entity_access_conflict"
    )
    assert issue.ids == ("SCR-012", "ENT-002", PATIENT)
    assert "may only view" in issue.detail


def test_one_screen_for_two_kinds_of_person_lets_those_with_less_access_only_look() -> None:
    brief = set_screen(
        sample_app(), "SCR-012", roles=[DOCTOR, PATIENT]
    )  # the doctor edits the notes on it, the patient has view access and only looks
    assert not [
        i for i in checks.run_checks(brief, MEDIUM) if i.code == "screen_entity_access_conflict"
    ]


def test_a_role_with_no_access_to_an_entity_at_all_cannot_use_it_on_a_screen() -> None:
    brief = set_screen(
        sample_app(), "SCR-012", roles=[DOCTOR, FRONT_DESK]
    )  # the receptionist has "none" on notes
    assert ("SCR-012", "ENT-002", FRONT_DESK) in {
        i.ids for i in checks.run_checks(brief, MEDIUM) if i.code == "screen_entity_access_conflict"
    }


def test_a_screen_for_a_feature_that_is_out_of_scope_is_an_error_with_a_code_fix() -> None:
    brief = sample_app()
    brief.scope[2] = brief.scope[2].model_copy(
        update={"scope_status": "out"}
    )  # the schedule view is ruled out
    issue = next(
        i for i in checks.run_checks(brief, MEDIUM) if i.code == "screen_for_excluded_feature"
    )
    assert (issue.severity, issue.fix, issue.ids) == ("error", "code", ("SCR-010",))


def test_an_in_scope_feature_with_no_screen_and_a_must_feature_with_no_flow_are_errors() -> None:
    brief = sample_app()
    brief.screens = [s for s in brief.screens if "FTR-002" not in s.serves]
    brief.flows = [f for f in brief.flows if f.id != "FLW-002"]
    found = codes(brief)
    assert {"feature_without_screen", "must_feature_without_flow"} <= found


def test_a_role_with_no_screen_and_an_app_with_no_screen_are_errors_that_ask_for_expansion() -> (
    None
):
    brief = sample_app()
    brief.screens = [
        s.model_copy(update={"roles": [r for r in s.roles if r != FRONT_DESK]})
        for s in brief.screens
    ]
    for i, e in enumerate(brief.entities):
        brief.entities[i] = e.model_copy(
            update={"access": {r: lv for r, lv in e.access.items() if r != FRONT_DESK}}
        )
    brief.screens = [
        s.model_copy(update={"apps": [a for a in s.apps if a != BOTH[1]] or [BOTH[0]]})
        for s in brief.screens
    ]
    brief.modules = [m.model_copy(update={"apps": [BOTH[0]]}) for m in brief.modules]
    found = {i.code: i for i in checks.run_checks(brief, MEDIUM)}
    assert (
        found["role_without_access"].fix == "expand"
        and found["app_without_screens"].fix == "expand"
    )


def test_thin_features_and_modules_depend_on_the_band() -> None:
    brief = sample_app()
    assert "feature_thin" not in codes(brief, "medium")  # two screens meet the medium minimum
    assert "feature_thin" in codes(brief, "large")  # a large app wants three for a must-have
    thin = sample_app()
    thin.screens = [s for s in thin.screens if s.module != "MOD-003"] + [
        thin.screens[-1].model_copy(update={"module": "MOD-003", "id": "SCR-013"})
    ]
    thin.flows = thin.flows[:1]
    assert "module_thin" in {i.code for i in checks.run_checks(thin, MEDIUM)}


def test_a_detail_fact_that_no_module_received_is_an_orphan_and_asks_for_expansion() -> None:
    brief = sample_app()
    brief.domain_facts.append(
        DomainFact(
            id="FCT-009", type="rule", statement="Warehouse pallets are weighed twice", kind="rule"
        )
    )
    issue = next(i for i in checks.run_checks(brief, MEDIUM) if i.code == "fact_orphan")
    assert issue.ids == ("FCT-009",) and issue.fix == "expand"
    # a cross-cutting fact (a constraint) goes to the outline and is never an orphan
    brief.domain_facts[-1] = brief.domain_facts[-1].model_copy(update={"kind": "constraint"})
    assert "fact_orphan" not in codes(brief)


def test_a_fact_that_names_a_screen_or_shares_a_real_word_reaches_its_module() -> None:
    brief = sample_app()
    brief.domain_facts = [
        DomainFact(
            id="FCT-010",
            type="rule",
            statement="Slots are fifteen minutes long",
            kind="rule",
            refs=["SCR-008"],
        ),
        DomainFact(
            id="FCT-011",
            type="rule",
            statement="An appointment can be cancelled until two hours before",
            kind="rule",
        ),
    ]
    assert "fact_orphan" not in codes(brief)


# -------------------------------------------------------------------------------------- realism rule pack


def test_health_data_without_a_consent_screen_is_a_finding_that_names_the_rule() -> None:
    brief = sample_app()
    assert "rule_unmet" not in codes(brief)
    brief.screens = [s for s in brief.screens if s.id != "SCR-005"]
    brief.modules[0] = brief.modules[0].model_copy(update={"name": "Account"})
    unmet = [i for i in checks.run_checks(brief, MEDIUM) if i.code == "rule_unmet"]
    assert ("health_data", "consent_privacy") in {i.ids for i in unmet}
    assert all(i.fix == "repair" and i.severity == "warn" for i in unmet)


def test_a_rule_that_does_not_apply_stays_silent() -> None:
    brief = sample_app()
    brief.data = {"sensitive": BriefItem(value=["none"])}
    brief.screens = [s for s in brief.screens if s.id not in ("SCR-005", "SCR-013")]
    assert "rule_unmet" not in codes(brief)


def test_the_finding_is_in_the_language_asked_for() -> None:
    brief = sample_app()
    brief.screens = [s for s in brief.screens if s.id != "SCR-005"]
    brief.modules[0] = brief.modules[0].model_copy(update={"name": "Account"})
    hi = next(i for i in checks.run_checks(brief, MEDIUM, lang="hi") if i.code == "rule_unmet")
    assert "सहमति" in hi.detail


def test_every_rule_names_its_conditions_and_its_reasons_in_both_languages() -> None:
    for rule in load_structure_rules():
        assert rule.requires
        for req in rule.requires:
            assert req.words and set(req.why) == {"en", "hi"}


# ----------------------------------------------------------------------------------------- deterministic fixes


def test_the_fixes_prune_dangling_pointers_and_only_those() -> None:
    brief = set_screen(sample_app(), "SCR-004", roles=[PATIENT, "ROL-099"], serves=["FTR-099"])
    before = brief.model_dump(mode="json")
    done = checks.apply_fixes(brief)
    assert done and any("pointer" in line for line in done)
    screen = next(s for s in brief.screens if s.id == "SCR-004")
    assert screen.roles == [PATIENT] and screen.serves == []
    # nothing else moved
    after = brief.model_dump(mode="json")
    changed = [k for k in before if before[k] != after[k]]
    assert changed == ["screens"]


def test_the_fixes_drop_screens_of_ruled_out_features_and_the_flow_steps_that_pointed_at_them() -> (
    None
):
    brief = sample_app()
    brief.scope[2] = brief.scope[2].model_copy(update={"scope_status": "out"})
    checks.apply_fixes(brief)
    assert "SCR-010" not in {s.id for s in brief.screens} and "SCR-010" in brief.ids.tombstones
    assert "FLW-002" in {f.id for f in brief.flows}  # two steps are left: the flow stays
    assert all(step.screen != "SCR-010" for f in brief.flows for step in f.steps)


def test_a_flow_left_with_fewer_than_two_steps_is_dropped_and_tombstoned() -> None:
    brief = sample_app()
    brief.flows[1] = brief.flows[1].model_copy(update={"steps": brief.flows[1].steps[:1]})
    checks.apply_fixes(brief)
    assert [f.id for f in brief.flows] == ["FLW-001"] and "FLW-002" in brief.ids.tombstones


def test_the_fixes_retype_money_fields_and_tidy_identifiers() -> None:
    brief = set_entity(
        sample_app(),
        "ENT-001",
        key="Appointment!",
        fields=[
            EntityField(key="Total Fee", name="Total fee", type="number"),
            EntityField(key="कीमत", name="कीमत", type="text"),
        ],
    )
    checks.apply_fixes(brief)
    entity = next(e for e in brief.entities if e.id == "ENT-001")
    assert entity.key == "appointment"
    assert next((f.key, f.type) for f in entity.fields) == ("total_fee", "money")
    assert entity.fields[1].type == "money"  # a Hindi price word is a price too
    assert all(checks.KEY_OK.match(f.key) for f in entity.fields)


def test_a_repeated_screen_name_is_numbered_and_an_unused_entity_gets_a_screen() -> None:
    brief = set_screen(sample_app(), "SCR-004", name="Profile")
    brief.screens = [
        s.model_copy(update={"entities": [u for u in s.entities if u.entity != "ENT-003"]})
        for s in brief.screens
    ]
    checks.apply_fixes(brief)
    assert next(s for s in brief.screens if s.id == "SCR-004").name == "Profile (2)"
    shown = {u.entity for s in brief.screens for u in s.entities}
    assert "ENT-003" in shown  # attached to a list or detail screen of its owner module


def test_a_module_with_a_missing_entry_gets_one() -> None:
    brief = sample_app()
    brief.modules[1] = brief.modules[1].model_copy(update={"entry": ""})
    checks.apply_fixes(brief)
    assert brief.modules[1].entry == "SCR-006"  # the first home-pattern screen (a list)


def test_fixing_a_clean_structure_changes_nothing() -> None:
    brief = sample_app()
    before = brief.model_dump(mode="json")
    assert checks.apply_fixes(brief) == []
    assert brief.model_dump(mode="json") == before


def test_after_the_fixes_no_code_fixable_issue_is_left() -> None:
    brief = sample_app()
    brief = set_screen(brief, "SCR-004", roles=["ROL-099"], name="Profile", pattern="carousel")
    brief = set_entity(
        brief, "ENT-001", key="Bad Key", fields=[EntityField(key="Fee Amount", type="text")]
    )
    brief.flows[0] = brief.flows[0].model_copy(update={"steps": brief.flows[0].steps[:1]})
    checks.apply_fixes(brief)
    left = [i for i in checks.run_checks(brief, MEDIUM) if i.fix == "code"]
    assert left == []


# ---------------------------------------------------------------------------------------- provenance guard


def test_a_record_that_rests_on_a_fact_that_is_gone_is_downgraded_and_proposed_again() -> None:
    brief = sample_app()
    evidence = Provenance(type="user_text", ref="FCT-007", quote="we have a pharmacy counter")
    brief.modules[2] = brief.modules[2].model_copy(
        update={
            "tier": "Locked",
            "decision": "accepted",
            "status": "volunteered",
            "provenance": [evidence],
        }
    )
    brief.screens[0] = brief.screens[0].model_copy(
        update={
            "tier": "Locked",
            "status": "volunteered",
            "provenance": [Provenance(type="user_text", ref="evt_TURN1")],
        }
    )
    changed = checks.downgrade_unsupported(brief, active={"FCT-001"}, verified={"FCT-001"})
    assert changed == [
        "MOD-003"
    ]  # the one that cites a fact nobody still holds; a turn id is left alone
    module = brief.modules[2]
    assert (module.tier, module.decision, module.status) == ("Default", "proposed", "assumed")
    assert brief.screens[0].tier == "Locked"


def test_a_record_with_any_fact_still_active_and_verified_keeps_its_standing() -> None:
    brief = sample_app()
    brief.modules[0] = brief.modules[0].model_copy(
        update={
            "tier": "Locked",
            "provenance": [
                Provenance(type="user_text", ref="FCT-001"),
                Provenance(type="user_text", ref="FCT-002"),
            ],
        }
    )
    assert checks.downgrade_unsupported(brief, active={"FCT-002"}, verified={"FCT-002"}) == []
    assert checks.downgrade_unsupported(brief, active={"FCT-002"}, verified=set()) == [
        "MOD-001"
    ]  # unverified is not support


# ------------------------------------------------------------------------------------------- properties


@settings(max_examples=60, deadline=None)
@given(drop=st.sets(st.integers(min_value=0, max_value=12), max_size=6))
def test_removing_screens_never_makes_the_checks_crash_and_the_fixes_never_add_any(
    drop: set[int],
) -> None:
    brief = sample_app()
    brief.screens = [s for i, s in enumerate(brief.screens) if i not in drop]
    before = len(brief.screens)
    issues = checks.run_checks(brief, MEDIUM)
    assert all(i.code in CHECK_CODES for i in issues)
    checks.apply_fixes(brief)
    assert len(brief.screens) <= before  # the checker prunes and tidies; it never adds a screen
    for i in checks.run_checks(brief, MEDIUM):
        assert i.fix != "code"  # nothing the code can fix is left


def _flow_with_foreign_screen(module_has_role: bool):  # type: ignore[no-untyped-def]
    brief = sample_app()
    flow = brief.flows[0]
    step = flow.steps[1]
    k = next(n for n, s in enumerate(brief.screens) if s.id == step.screen)
    screen = brief.screens[k]
    others = [r for r in (PATIENT, DOCTOR, FRONT_DESK) if r != flow.role]
    brief.screens[k] = screen.model_copy(update={"roles": [others[0]]})
    m = next(n for n, mod in enumerate(brief.modules) if mod.id == screen.module)
    if module_has_role:
        mod_roles = list(dict.fromkeys([*brief.modules[m].roles, flow.role]))
    else:
        mod_roles = [r for r in brief.modules[m].roles if r != flow.role] or [others[0]]
    brief.modules[m] = brief.modules[m].model_copy(update={"roles": mod_roles})
    assert any(i.code == "flow_role_cannot_access_step" for i in checks.run_checks(brief, MEDIUM))
    return brief, flow, step


def test_a_role_the_module_belongs_to_is_added_to_the_screen_it_was_left_off() -> None:
    brief, flow, step = _flow_with_foreign_screen(module_has_role=True)
    notes = checks.fix_flow_access(brief)
    assert notes and "can now be opened by" in notes[0]
    screen = next(s for s in brief.screens if s.id == step.screen)
    assert flow.role in screen.roles
    assert [s.screen for s in brief.flows[0].steps] == [s.screen for s in flow.steps]  # untouched
    assert not [
        i for i in checks.run_checks(brief, MEDIUM) if i.code == "flow_role_cannot_access_step"
    ]
    assert checks.fix_flow_access(brief) == []  # nothing more to do


def test_a_journey_that_strays_into_somebody_elses_module_loses_that_step_not_the_roles_access() -> (
    None
):
    brief, _flow, step = _flow_with_foreign_screen(module_has_role=False)
    before_roles = next(s for s in brief.screens if s.id == step.screen).roles
    notes = checks.fix_flow_access(brief)
    assert notes and "no longer goes through" in notes[0]
    assert (
        next(s for s in brief.screens if s.id == step.screen).roles == before_roles
    )  # nobody widened
    assert step.screen not in [s.screen for s in brief.flows[0].steps]
    assert not [
        i for i in checks.run_checks(brief, MEDIUM) if i.code == "flow_role_cannot_access_step"
    ]


def test_a_role_the_entity_forgot_gets_view_access_to_data_its_screen_shows_but_no_more() -> None:
    brief = sample_app()
    shown = next(s for s in brief.screens if any(u.use == "view" for u in s.entities))
    use = next(u for u in shown.entities if u.use == "view")
    k = next(i for i, e in enumerate(brief.entities) if e.id == use.entity)
    role = shown.roles[0]
    without = {r: lv for r, lv in brief.entities[k].access.items() if r != role} or {
        "ROL-999": "view"
    }
    brief.entities[k] = brief.entities[k].model_copy(update={"access": without})
    assert any(i.code == "screen_entity_access_conflict" for i in checks.run_checks(brief, MEDIUM))
    notes = checks.apply_fixes(brief)
    assert any("view access" in n for n in notes)
    assert brief.entities[k].access[role] == "view"
    assert not [
        i
        for i in checks.run_checks(brief, MEDIUM)
        if i.code == "screen_entity_access_conflict" and role in i.ids[2:] and i.ids[0] == shown.id
    ]
    # an explicit "none" is a contradiction, never silently widened
    explicit = sample_app()
    j = next(i for i, e in enumerate(explicit.entities) if e.id == use.entity)
    explicit.entities[j] = explicit.entities[j].model_copy(
        update={"access": {**explicit.entities[j].access, role: "none"}}
    )
    checks.apply_fixes(explicit)
    assert explicit.entities[j].access[role] == "none"


def test_one_issue_names_every_role_that_cannot_see_what_a_shared_screen_shows() -> None:
    brief = sample_app()
    shown = next(s for s in brief.screens if any(u.use == "view" for u in s.entities))
    use = next(u for u in shown.entities if u.use == "view")
    k = next(i for i, e in enumerate(brief.entities) if e.id == use.entity)
    brief.entities[k] = brief.entities[k].model_copy(
        update={"access": {PATIENT: "manage", DOCTOR: "none", FRONT_DESK: "none"}}
    )
    i = next(n for n, s in enumerate(brief.screens) if s.id == shown.id)
    brief.screens[i] = shown.model_copy(update={"roles": [PATIENT, DOCTOR, FRONT_DESK]})
    found = [
        x
        for x in checks.run_checks(brief, MEDIUM)
        if x.code == "screen_entity_access_conflict"
        and x.ids[0] == shown.id
        and x.ids[1] == use.entity
    ]
    assert len(found) == 1 and set(found[0].ids[2:]) == {DOCTOR, FRONT_DESK}


def test_a_shared_screen_drops_a_data_link_most_roles_cannot_see_only_if_another_screen_shows_it() -> (
    None
):
    brief = sample_app()
    shown = next(s for s in brief.screens if any(u.use == "view" for u in s.entities))
    use = next(u for u in shown.entities if u.use == "view")
    k = next(i for i, e in enumerate(brief.entities) if e.id == use.entity)
    brief.entities[k] = brief.entities[k].model_copy(
        update={"access": {PATIENT: "manage", DOCTOR: "none", FRONT_DESK: "none"}}
    )
    i = next(n for n, s in enumerate(brief.screens) if s.id == shown.id)
    brief.screens[i] = shown.model_copy(update={"roles": [PATIENT, DOCTOR, FRONT_DESK]})
    j = next(
        n
        for n, s in enumerate(brief.screens)
        if s.id != shown.id and not any(u.entity == use.entity for u in s.entities)
    )
    brief.screens[j] = brief.screens[j].model_copy(
        update={"roles": [PATIENT], "entities": [*brief.screens[j].entities, use]}
    )  # another screen (the patient's own) shows the same data
    notes = checks.apply_fixes(brief)
    assert not any(u.entity == use.entity for u in brief.screens[i].entities)
    assert any(u.entity == use.entity for u in brief.screens[j].entities)
    assert any("no longer claim" in n for n in notes)
    # the ONLY screen that shows an entity keeps the link: the person sees the conflict instead of losing the data
    lone = sample_app()
    lone.entities[k] = lone.entities[k].model_copy(
        update={"access": {PATIENT: "manage", DOCTOR: "none", FRONT_DESK: "none"}}
    )
    lone.screens = [
        s.model_copy(update={"entities": [u for u in s.entities if u.entity != use.entity]})
        if s.id != shown.id
        else s.model_copy(update={"roles": [PATIENT, DOCTOR, FRONT_DESK]})
        for s in lone.screens
    ]
    checks.apply_fixes(lone)
    kept = next(s for s in lone.screens if s.id == shown.id)
    assert any(u.entity == use.entity for u in kept.entities)
