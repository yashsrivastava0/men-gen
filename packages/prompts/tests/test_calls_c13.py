"""C13.repair: spec, templates, strict schema and the offline handler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import builder_call_checks as chk

from mde_contracts.calls import (
    BriefContext,
    C13Input,
    CriticFinding,
    OutlineEntity,
    OutlineScreen,
    SpecOutline,
    ValidatorFinding,
)
from mde_prompts.calls.c13_repair.mock import handle

CALL_ID = "C13.repair"
OUTLINE = SpecOutline(
    screens=[
        OutlineScreen(id="SCR-001", route="/home", recipe="dashboard", source="default"),
        OutlineScreen(id="SCR-002", route="/cart", recipe="list", source="default"),
        OutlineScreen(id="SCR-031", route="/extra", recipe="list", source="research"),
    ],
    entities=[OutlineEntity(id="ENT-Order", name="Order")],
)


def make(*findings: ValidatorFinding | CriticFinding) -> C13Input:
    return C13Input(
        brief=BriefContext(app_summary="A shop", archetype="commerce"),
        outline=OUTLINE,
        findings=list(findings),
    )


def validator(message: str, *targets: str) -> ValidatorFinding:
    return ValidatorFinding(validator="action_targets", target_ids=list(targets), message=message)


def test_spec_shape() -> None:
    chk.check_spec_shape(
        CALL_ID, effort="low", min_tokens=800, max_tokens=3000, fallback="builder.no_proposals"
    )


def test_output_converts_to_the_strict_schema() -> None:
    chk.check_strict_schema(CALL_ID)


def test_mock_output_is_deterministic_schema_valid_and_matches_the_golden_fixture() -> None:
    out = chk.check_mock_matches_golden(CALL_ID)
    assert [p.kind for p in out.proposals] == ["fix_reference"]  # type: ignore[attr-defined]


def test_templates_render_with_the_fixture_and_stay_small() -> None:
    system, user = chk.render_fixture(CALL_ID, max_chars=10_500)
    assert "repair round 1 of at most 2" in system and "Prefer fix_reference" in system
    assert "Findings to fix" in user and "SCR-099" in user and "Blocks involved" in user


def test_invalid_fixture_is_schema_valid() -> None:
    chk.check_invalid_fixture_parses(CALL_ID)


def test_a_dangling_reference_is_pointed_at_the_closest_existing_id() -> None:
    f = validator("Action 'x' on SCR-002 goes to SCR-003, which is not a screen", "SCR-002")
    (p,) = handle(make(f)).proposals
    assert (p.kind, p.target, p.source) == ("fix_reference", "SCR-002", "repair")
    assert (
        p.pairs is not None and p.pairs[0].key == "SCR-003" and p.pairs[0].value.startswith("SCR-0")
    )


def test_entities_and_operations_are_repaired_within_their_own_kind() -> None:
    f = validator("Operation API-x has response entity ENT-Ordr, which does not exist", "ENT-Order")
    (p,) = handle(make(f)).proposals
    assert p.pairs is not None and p.pairs[0].value == "ENT-Order"


def test_a_finding_about_an_added_item_removes_it() -> None:
    f = CriticFinding(
        kind="contradiction",
        severity="error",
        message="Extra screen is not wanted",
        target_ids=["SCR-031"],
    )
    (p,) = handle(make(f)).proposals
    assert (p.kind, p.target) == ("remove_item", "SCR-031")


def test_baseline_items_are_never_removed_and_unknown_ones_are_ignored() -> None:
    f = CriticFinding(
        kind="mismatch", severity="error", message="Wrong", target_ids=["SCR-001", "SCR-404"]
    )
    assert handle(make(f)).proposals == []
    assert handle(make()).proposals == []


def test_duplicates_are_proposed_once_and_the_output_is_capped() -> None:
    same = validator("goes to SCR-003 from SCR-002", "SCR-002")
    assert len(handle(make(same, same)).proposals) == 1
    many = [validator(f"SCR-002 goes to SCR-9{i:02d}", "SCR-002") for i in range(30)]
    assert len(handle(make(*many)).proposals) <= 10
