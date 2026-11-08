"""The change-handling matrix (spec 3.6) as data, and the light/heavy classification."""

from __future__ import annotations

from collections.abc import Sequence
from typing import get_args

import pytest

from mde_contracts.calls import ChangeOp
from mde_contracts.policy import PostHandoffPolicy
from mde_contracts.research import ResearchNote, ResearchSuggestion
from mde_contracts.slots import SLOT_IDS
from mde_flow.dependency_map import (
    DEPENDENCY_RULES,
    ChangePlan,
    PostHandoffVerdict,
    classify_post_handoff,
    plan_change,
    rule_for_slot,
)


def setop(slot: str, text: str | None = "x") -> ChangeOp:
    return ChangeOp.model_validate({"kind": "set_slot", "slot_id": slot, "value_text": text})


def note_with(*suggestions: ResearchSuggestion) -> ResearchNote:
    return ResearchNote(brief_hash="b", suggestions=list(suggestions)).with_hash()


def test_every_slot_id_resolves_to_a_rule() -> None:
    assert [s for s in SLOT_IDS if rule_for_slot(s) is None] == []
    assert len({r.pattern for r in DEPENDENCY_RULES}) == len(DEPENDENCY_RULES)


@pytest.mark.parametrize(
    ("slot", "sections", "jobs", "restart"),
    [
        ("design.direction", ["design"], [], False),
        ("design.accent", ["design"], [], False),
        ("brand.name", ["design"], [], False),
        ("scope.features", ["screens", "navigation", "data", "api"], [], False),
        ("scope.apps", ["all"], ["R1", "R2", "R4"], True),
        ("idea.archetype", ["all"], ["R1", "R2", "R4"], True),
        ("regions", ["all"], ["R1", "R2", "R4"], True),
        ("auth.method", ["screens", "navigation", "roles"], [], False),
        ("users.roles", ["screens", "navigation", "roles"], [], False),
        ("pay.model", ["screens", "api", "integrations"], ["R5"], False),
        ("pay.provider", ["screens", "api", "integrations"], ["R5"], False),
        ("integrations.list", ["screens", "api", "integrations"], ["R5"], False),
        ("behavior.offline", ["screens", "behavior"], [], False),
        ("locale", ["screens", "behavior"], [], False),
    ],
)
def test_spec_3_6_matrix_rows(
    slot: str, sections: list[str], jobs: list[str], restart: bool
) -> None:
    plan = plan_change([setop(slot)], note=note_with())
    assert plan.sections == sections
    assert plan.rerun_jobs == jobs
    assert plan.restart_discovery is restart


def test_brand_research_reruns_only_when_the_brand_site_changed() -> None:
    assert plan_change([setop("brand.assets", "a logo file")], note=note_with()).rerun_jobs == []
    assert plan_change(
        [setop("brand.assets", "https://example.com")], note=note_with()
    ).rerun_jobs == ["R3"]
    link = ChangeOp.model_validate(
        {"kind": "set_slot", "slot_id": "brand.assets", "value_list": ["www.example.com"]}
    )
    assert plan_change([link], note=note_with()).rerun_jobs == ["R3"]


def test_start_phase_reuses_the_note_unless_research_inputs_change() -> None:
    note = note_with()
    assert plan_change([setop("design.direction")], note=note).start_phase == "build"
    assert plan_change([setop("pay.model")], note=note).start_phase == "research"
    assert plan_change([setop("design.direction")], note=None).start_phase == "research"
    assert (
        plan_change([setop("design.direction")], note=note, research_stale=True).start_phase
        == "research"
    )


def test_sections_are_merged_in_a_stable_order_and_all_absorbs_the_rest() -> None:
    plan = plan_change([setop("design.direction"), setop("auth.method")], note=note_with())
    assert plan.sections == ["design", "screens", "navigation", "roles"]
    assert plan_change(
        [setop("design.direction"), setop("idea.archetype")], note=note_with()
    ).sections == ["all"]


def test_non_slot_ops() -> None:
    n = note_with()
    add = plan_change([ChangeOp(kind="add_screen", instruction="faq")], note=n)
    assert add.sections == ["screens", "navigation"] and add.new_screens_requested == 1
    assert plan_change([ChangeOp(kind="remove_screen", target_id="SCR-002")], note=n).sections == [
        "screens",
        "navigation",
    ]
    assert plan_change([ChangeOp(kind="patch_screen", target_id="SCR-002")], note=n).sections == [
        "screens"
    ]
    assert plan_change([ChangeOp(kind="set_copy")], note=n).sections == ["screens"]
    toggle = plan_change([ChangeOp(kind="toggle_feature", target_id="chat")], note=n)
    assert toggle.categories == ["scope"] and "data" in toggle.sections


def test_toggling_a_research_item_uses_what_the_suggestion_applies_to() -> None:
    n = note_with(
        ResearchSuggestion(
            id="RA-001", kind="feature", title="a", rationale="r", applies_to="scope.features"
        ),
        ResearchSuggestion(id="RA-002", kind="pattern", title="b", rationale="r"),
        ResearchSuggestion(
            id="RA-003", kind="brand", title="c", rationale="r", applies_to="design"
        ),
        ResearchSuggestion(id="RA-004", kind="integration", title="d", rationale="r"),
        ResearchSuggestion(id="RA-005", kind="compliance", title="e", rationale="r"),
    )

    def toggle(ref: str) -> ChangePlan:
        return plan_change([ChangeOp(kind="toggle_research_item", target_id=ref)], note=n)

    assert toggle("RA-001").categories == ["scope"]
    assert toggle("RA-002").sections == ["screens"] and not toggle("RA-002").categories
    assert toggle("RA-003").sections == ["design"]
    assert toggle("RA-004").categories == ["integrations"]
    assert toggle("RA-005").sections == ["screens", "behavior"]
    unknown = toggle("RA-999")
    assert unknown.unknown and unknown.sections == ["all"]
    assert toggle("RA-001").rerun_jobs == []  # toggling never re-runs research


def test_unclassified_ops_are_flagged_unknown() -> None:
    plan = plan_change([ChangeOp(kind="other", instruction="do something odd")], note=note_with())
    assert plan.unknown and plan.sections == ["all"]
    assert plan_change([ChangeOp(kind="set_slot")], note=note_with()).unknown


def test_assumption_toggle_resolves_by_slot() -> None:
    op = ChangeOp.model_validate(
        {"kind": "other", "slot_id": "behavior.offline", "instruction": "toggle_assumption"}
    )
    plan = plan_change([op], note=note_with())
    assert plan.sections == ["screens", "behavior"]
    assert not plan.unknown


def test_a_change_that_only_touches_policy_slots_needs_no_rebuild() -> None:
    plan = plan_change([setop("approvers")], note=note_with())
    assert plan.sections == [] and not plan.needs_rebuild
    assert plan_change([setop("design.direction")], note=note_with()).needs_rebuild


# ----------------------------------------------------------------------------- light vs heavy

DEFAULT = PostHandoffPolicy()


def verdict(
    ops: list[ChangeOp],
    *,
    policy: PostHandoffPolicy = DEFAULT,
    plan_roles: Sequence[str] = ("requester",),
    new_screens: int = 0,
    repaired: bool = False,
    research_ran: bool = False,
    derived: bool = False,
) -> PostHandoffVerdict:
    plan = plan_change(ops, note=note_with())
    return classify_post_handoff(
        plan,
        policy=policy,
        plan_roles=plan_roles,
        new_screens=new_screens,
        repaired=repaired,
        research_ran=research_ran,
        derived=derived,
    )


def test_design_copy_and_layout_changes_are_light() -> None:
    assert verdict([setop("design.direction")]).kind == "light"
    assert verdict([ChangeOp(kind="set_copy")]).kind == "light"
    assert verdict([ChangeOp(kind="patch_screen", target_id="SCR-001")]).kind == "light"
    assert verdict([setop("behavior.offline")]).kind == "light"


@pytest.mark.parametrize(
    ("slot", "reason"),
    [
        ("scope.features", "category:scope"),
        ("auth.method", "category:auth"),
        ("pay.model", "category:pay"),
        ("integrations.list", "category:integrations"),
    ],
)
def test_scope_auth_pay_integrations_are_heavy_by_default(slot: str, reason: str) -> None:
    v = verdict([setop(slot)])
    assert v.kind == "heavy" and reason in v.reasons


def test_heavy_if_comes_from_policy() -> None:
    policy = PostHandoffPolicy(heavy_if=["design"], max_new_screens_light=2)
    assert verdict([setop("design.direction")], policy=policy).kind == "heavy"
    assert verdict([setop("auth.method")], policy=policy).kind == "light"  # not listed here


def test_new_screen_limit_is_a_policy_number() -> None:
    policy = PostHandoffPolicy(heavy_if=[], max_new_screens_light=3)
    assert verdict([ChangeOp(kind="add_screen")], policy=policy, new_screens=3).kind == "light"
    heavy = verdict([ChangeOp(kind="add_screen")], policy=policy, new_screens=4)
    assert heavy.kind == "heavy" and heavy.reasons == ["screens:4>3"]


def test_discovery_restart_unknown_research_rerun_and_repair_are_heavy() -> None:
    assert "discovery_restart" in verdict([setop("idea.archetype")]).reasons
    assert "unclassified_change" in verdict([ChangeOp(kind="other")]).reasons
    assert "research_rerun" in verdict([setop("design.direction")], research_ran=True).reasons
    assert "engine_repair" in verdict([setop("design.direction")], repaired=True).reasons


def test_every_heavy_vocabulary_word_exists() -> None:
    from mde_contracts.policy import HeavyChange

    cats = set(get_args(HeavyChange))
    used = {c for r in DEPENDENCY_RULES for c in r.categories}
    assert used <= cats
