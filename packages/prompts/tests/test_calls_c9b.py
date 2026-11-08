"""C9b.data_api_roles: spec, templates, strict schema and the offline handler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import builder_call_checks as chk

from mde_contracts.calls import (
    BriefContext,
    C9bInput,
    OutlineEntity,
    OutlineItem,
    ResearchAdditionLite,
    SpecOutline,
)
from mde_prompts.calls.c9b_data_api_roles.mock import handle

CALL_ID = "C9b.data_api_roles"
OUTLINE = SpecOutline(
    entities=[
        OutlineEntity(id="ENT-Order", name="Order", fields=["total"]),
        OutlineEntity(id="ENT-User", name="User", fields=["name", "phone"]),
    ],
    integrations=[OutlineItem(id="INT-payments", name="Payments")],
)


def brief(**kw: object) -> BriefContext:
    return BriefContext(app_summary="An app", archetype="commerce", **kw)  # type: ignore[arg-type]


def test_spec_shape() -> None:
    chk.check_spec_shape(
        CALL_ID, effort="medium", min_tokens=1000, max_tokens=9000, fallback="builder.no_proposals"
    )  # 8000: live L2 used 4204 to 5337 (reasoning included) and retried every call at 1800 (D-082)


def test_output_converts_to_the_strict_schema() -> None:
    chk.check_strict_schema(CALL_ID)


def test_mock_output_is_deterministic_schema_valid_and_matches_the_golden_fixture() -> None:
    out = chk.check_mock_matches_golden(CALL_ID)
    kinds = [p.kind for p in out.proposals]  # type: ignore[attr-defined]
    assert "add_entity_fields" in kinds and "add_integration" in kinds


def test_templates_render_with_the_fixture_and_stay_small() -> None:
    system, user = chk.render_fixture(CALL_ID, max_chars=9000)
    assert chk.PROPOSAL_RULE in system and "never ask for real connections" in system
    assert "ENT-User" in user and "RA-003" in user


def test_invalid_fixture_is_schema_valid() -> None:
    chk.check_invalid_fixture_parses(CALL_ID)


def test_nothing_is_proposed_when_the_brief_has_nothing_to_record() -> None:
    assert handle(C9bInput(brief=brief(), outline=OUTLINE)).proposals == []


def test_sensitive_areas_regions_and_language_become_at_most_two_fields_on_the_user() -> None:
    inp = C9bInput(
        brief=brief(
            compliance_flags=["precise_location", "child_safety"], regions=["in"], language="hi"
        ),
        outline=OUTLINE,
    )
    (p,) = handle(inp).proposals
    assert p.kind == "add_entity_fields" and p.target == "ENT-User"
    assert [f.name for f in p.fields or []] == ["location_consent", "guardian_email"]


def test_fields_that_exist_are_not_proposed_again() -> None:
    outline = OUTLINE.model_copy(
        update={
            "entities": [OutlineEntity(id="ENT-User", name="User", fields=["preferred_region"])]
        }
    )
    inp = C9bInput(brief=brief(regions=["in"]), outline=outline)
    assert handle(inp).proposals == []


def test_the_first_entity_is_used_when_there_is_no_user_entity() -> None:
    outline = SpecOutline(entities=[OutlineEntity(id="ENT-Task", name="Task", fields=["title"])])
    (p,) = handle(C9bInput(brief=brief(regions=["eu"]), outline=outline)).proposals
    assert p.target == "ENT-Task"
    assert handle(C9bInput(brief=brief(regions=["eu"]), outline=SpecOutline())).proposals == []


def test_integration_additions_become_research_integrations() -> None:
    adds = [
        ResearchAdditionLite(
            id="RA-003", kind="integration", title="Store locator", rationale="Common"
        ),
        ResearchAdditionLite(id="RA-004", kind="integration", title="Payments", rationale="Common"),
        ResearchAdditionLite(id="RA-005", kind="feature", title="Wishlist", rationale="Common"),
    ]
    out = handle(C9bInput(brief=brief(), outline=OUTLINE, research=adds)).proposals
    assert [(p.kind, p.name, p.source, p.research_ref) for p in out] == [
        ("add_integration", "Store locator", "research", "RA-003")
    ]  # Payments already exists; a feature is C9a's business
