"""derive/facts.py: predicate facts that hold for an input."""

from __future__ import annotations

import pytest

from mde_compass_intake.spec_input import Feature, Integration, Locales, Setting, Trust
from mde_spec_agent.derive.facts import facts_from_input
from mde_spec_core import vocab

from .conftest import tiny_input


def test_every_fact_is_a_known_predicate(export) -> None:
    known = set(vocab.predicates())
    for key in ("quick", "standard", "thorough"):
        assert facts_from_input(export(key)) <= known


@pytest.mark.parametrize(
    ("key", "must_hold", "must_not_hold"),
    [
        (
            "quick",
            {
                "accounts",
                "phone_code_login",
                "multi_language",
                "payments",
                "provider_side_flow",
                "push_notifications",
            },
            {"sensitive_data", "right_to_left", "email_password_login"},
        ),
        (
            "standard",
            {
                "accounts",
                "email_password_login",
                # FCT-036 (farmers and drivers use a phone code) is read per role, not only the one setting (BUG-IN-4)
                "phone_code_login",
                "multi_language",
                "payments",
                "multi_role_user",
            },
            # the person put live tracking off: a feature they deferred is not read
            {"realtime", "sensitive_data"},
        ),
        (
            "thorough",
            {
                "phone_code_login",
                "email_password_login",
                "sensitive_data",
                "minors_or_health_or_identity",
                "payments",
            },
            {"right_to_left"},
        ),
    ],
)
def test_real_exports(export, key, must_hold, must_not_hold) -> None:
    facts = facts_from_input(export(key))
    assert must_hold <= facts, must_hold - facts
    assert not (must_not_hold & facts), must_not_hold & facts


def test_always_holds_and_is_deterministic(export) -> None:
    si = export("standard")
    assert "always" in facts_from_input(si)
    assert facts_from_input(si) == facts_from_input(si)


def test_sign_in_methods_and_managed_accounts() -> None:
    si = tiny_input(
        auth_methods=Setting(values=("google", "email_password")),
        account_model=Setting(values=("admin_created",)),
    )
    facts = facts_from_input(si)
    assert {"social_login", "email_password_login", "accounts"} <= facts
    assert (
        "accounts_creatable_in_app" not in facts
    )  # an owner creates the accounts, so no deletion screen is implied


def test_right_to_left_offline_and_integrations() -> None:
    si = tiny_input(
        locales=Locales(codes=("ar", "en"), rtl=("ar",)),
        integrations=(Integration(name="payments"), Integration(name="maps")),
        features=(
            Feature(
                id="FTR-001",
                name="Work offline in the field",
                intent="Works without a network",
                priority="must",
            ),
        ),
        sensitive_data=("health",),
    )
    facts = facts_from_input(si)
    assert {
        "right_to_left",
        "multi_language",
        "offline_first",
        "payments",
        "provider_side_flow",
        "sensitive_permission",
        "sensitive_data",
        "minors_or_health_or_identity",
    } <= facts


def test_a_deferred_feature_is_not_read() -> None:
    later = Feature(
        id="FTR-009",
        name="Live chat",
        intent="Chat live",
        scope_status="later",
        deferred_by_user=True,
        trust=Trust(grade="locked"),
    )
    si = tiny_input(features=(*tiny_input().features, later))
    assert "realtime" not in facts_from_input(si)


def test_research_signal_is_a_hint_only_when_research_is_not_thin(research, export) -> None:
    si = export("standard")
    view = research(si)
    if view is None:
        pytest.skip("no complete Research run on disk")
    assert facts_from_input(si, view) >= facts_from_input(si)
