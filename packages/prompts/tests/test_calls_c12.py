"""C12.critic: spec, templates, strict schema and the offline handler."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import builder_call_checks as chk

from mde_contracts.calls import (
    BriefContext,
    C12Input,
    OptionLite,
    OutlineFlow,
    OutlineScreen,
    SpecOutline,
    ValidatorFinding,
)
from mde_prompts.calls.c12_critic.mock import handle

CALL_ID = "C12.critic"
OUTLINE = SpecOutline(
    screens=[
        OutlineScreen(id="SCR-001", route="/home", recipe="dashboard", purpose="Browse products"),
        OutlineScreen(id="SCR-002", route="/cart", recipe="list", purpose="Review the cart"),
    ],
    flows=[OutlineFlow(id="FLW-001", name="Order", screens=["SCR-001", "SCR-002"])],
)


def make(
    features: list[str] | None = None,
    findings: list[ValidatorFinding] | None = None,
    outline: SpecOutline = OUTLINE,
) -> C12Input:
    return C12Input(
        brief=BriefContext(
            app_summary="A shop",
            archetype="commerce",
            features=[OptionLite(id=f.lower(), label=f) for f in features or []],
        ),
        outline=outline,
        validator_findings=findings or [],
    )


def test_spec_shape() -> None:
    chk.check_spec_shape(
        CALL_ID, effort="medium", min_tokens=800, max_tokens=2500, fallback="builder.critic_pass"
    )


def test_output_converts_to_the_strict_schema() -> None:
    chk.check_strict_schema(CALL_ID)


def test_mock_output_is_deterministic_schema_valid_and_matches_the_golden_fixture() -> None:
    out = chk.check_mock_matches_golden(CALL_ID)
    assert out.verdict == "fix"  # type: ignore[attr-defined]


def test_templates_render_with_the_fixture_and_stay_small() -> None:
    system, user = chk.render_fixture(CALL_ID, max_chars=9000)
    assert 'verdict is "fix" only when' in system
    assert "Validator findings" in user and "SCR-099" in user


def test_a_matching_plan_passes() -> None:
    out = handle(make(features=["Browse products", "Cart"]))
    assert (out.verdict, out.findings) == ("pass", [])
    assert handle(make()).verdict == "pass"


def test_validator_errors_make_the_verdict_fix_and_warnings_do_not() -> None:
    err = ValidatorFinding(
        validator="action_targets", severity="error", target_ids=["SCR-001"], message="Broken"
    )
    warn = ValidatorFinding(validator="limits", severity="warn", message="Close to the budget")
    out = handle(make(findings=[err, warn]))
    assert out.verdict == "fix" and [f.severity for f in out.findings] == ["error"]
    assert out.findings[0].target_ids == ["SCR-001"]
    assert handle(make(findings=[warn])).verdict == "pass"


def test_obvious_mismatches_are_errors() -> None:
    none = handle(make(outline=SpecOutline()))
    assert none.verdict == "fix" and none.findings[0].kind == "mismatch"
    flowless = handle(make(outline=OUTLINE.model_copy(update={"flows": []})))
    assert flowless.verdict == "fix" and flowless.findings[0].kind == "missing_flow"
    one = SpecOutline(screens=[OutlineScreen(id="SCR-001", route="/home", recipe="dashboard")])
    assert handle(make(outline=one)).verdict == "pass"  # a single screen needs no journey


def test_a_feature_nothing_mentions_is_a_warning_only() -> None:
    out = handle(make(features=["Loyalty points", "Browse products"]))
    assert out.verdict == "pass"
    assert [(f.kind, f.severity) for f in out.findings] == [("missing_feature", "warn")]
    assert "Loyalty points" in out.findings[0].message


def test_findings_are_capped_at_five() -> None:
    many = make(features=[f"Feature{chr(97 + i) * 5}" for i in range(9)])
    assert len(handle(many).findings) == 5
