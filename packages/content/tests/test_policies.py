from __future__ import annotations

from importlib import resources

import pytest
import yaml

from mde_content.policies import (
    MAX_INHERITANCE_LEVELS,
    PolicyInheritanceError,
    inheritance_chain,
    list_policies,
    load_policy,
    policy_ref,
    resolve_policy,
)
from mde_contracts.policy import Policy
from mde_contracts.slots import SLOT_IDS

IDS = ["enterprise-it-services-firm", "guided-default", "platform-default", "regulated-bank"]


def _p(policy_id: str, inherits: str | None = None, **fields: object) -> Policy:
    return Policy.model_validate({"id": policy_id, "inherits": inherits, **fields})


# --------------------------------------------------------------------------- packaged files


def test_four_profiles_are_packaged_and_sorted() -> None:
    assert [p.id for p in list_policies()] == IDS


def test_file_names_match_ids_and_every_file_validates() -> None:
    folder = resources.files("mde_content").joinpath("data/policies")
    names = sorted(
        f.name.removesuffix(".yaml") for f in folder.iterdir() if f.name.endswith(".yaml")
    )
    assert names == IDS
    for name in names:
        raw = yaml.safe_load(folder.joinpath(f"{name}.yaml").read_text(encoding="utf-8"))
        assert raw.pop("version") == 1
        assert Policy.model_validate(raw).id == name


def test_refs_and_lookup() -> None:
    assert policy_ref("guided-default") == "guided-default@1"
    assert load_policy("guided-default@1").id == "guided-default"
    assert load_policy("guided-default").inherits == "platform-default@1"
    with pytest.raises(KeyError):
        load_policy("guided-default@9")
    with pytest.raises(KeyError):
        load_policy("nope")
    with pytest.raises(KeyError):
        policy_ref("nope")


def test_every_inherits_target_exists_and_chains_resolve() -> None:
    for p in list_policies():
        chain = inheritance_chain(p)
        assert len(chain) <= MAX_INHERITANCE_LEVELS
        assert resolve_policy(p.id).id == p.id


def test_slot_policy_keys_are_real_slots() -> None:
    for p in list_policies():
        assert set(p.slot_policy) <= set(SLOT_IDS), p.id


def test_resolution_is_deterministic_and_does_not_mutate() -> None:
    before = load_policy("regulated-bank").model_dump()
    assert resolve_policy("regulated-bank") == resolve_policy("regulated-bank")
    assert load_policy("regulated-bank").model_dump() == before


# --------------------------------------------------------------------------- the four profiles


def test_platform_default_is_the_base() -> None:
    p = resolve_policy("platform-default")
    assert p.inherits is None and p.org == "platform" and p.mode == "guided"
    assert p.limits.max_screens == 50 and p.model_policy.retention == "none"


def test_guided_default_values() -> None:
    p = resolve_policy("guided-default")
    assert p.mode == "guided" and p.org == "platform"
    assert p.inherits == "platform-default@1"
    assert p.research.enabled and p.research.depth == "light"
    assert p.gates.brief.approvers == ["requester"]
    assert p.gates.plan.approvers == ["requester"]
    assert not p.gates.plan.full_file_view
    assert (p.limits.max_cards, p.limits.max_turns, p.limits.max_screens) == (10, 30, 50)
    assert p.slot_policy == {}


def test_enterprise_example_resolves_to_the_spec_10_values() -> None:
    p = resolve_policy("enterprise-it-services-firm")
    assert p.org == "example-services-firm"
    assert p.mode == "enterprise"
    assert p.inherits == "platform-default@1"
    assert p.slot_policy == {
        "auth.sso": "must",
        "data.classification": "must",
        "design.direction": "should",
    }
    assert p.research.depth == "deep"
    assert p.research.web.allow_company_name_in_queries is False
    assert p.research.web.blocked_domains == []
    assert p.research.connectors == ["docs", "wiki", "tickets"]
    assert p.gates.brief.approvers == ["requester"]
    assert p.gates.plan.approvers == ["client_product_owner", "delivery_lead"]
    assert p.gates.plan.full_file_view is True
    assert p.gates.post_handoff.heavy_if == ["scope", "auth", "pay", "integrations"]
    assert p.gates.post_handoff.max_new_screens_light == 5
    assert p.limits.max_screens == 60
    assert p.model_policy.providers == ["approved-a", "approved-b"]
    assert p.model_policy.retention == "none"
    # Inherited from the platform baseline, not restated in the file.
    assert p.limits.max_cards == 10 and p.limits.session_budget_usd == "0.50"


def test_regulated_bank_values() -> None:
    p = resolve_policy("regulated-bank")
    assert p.mode == "enterprise"
    assert p.slot_policy["compliance.flags"] == "must"
    assert p.slot_policy["data.classification"] == "must"
    assert p.slot_policy["regions"] == "must"
    assert p.research.depth == "standard"
    assert p.research.web.allowed_domains, "research is limited to named sources"
    assert p.research.web.blocked_domains
    assert not set(p.research.web.allowed_domains) & set(p.research.web.blocked_domains)
    assert p.research.web.allow_company_name_in_queries is False
    assert p.allowed_regions
    assert p.model_policy.providers == ["approved-a"]
    assert p.gates.plan.full_file_view is True
    assert len(p.gates.plan.approvers) >= 3
    assert p.gates.plan.reminder_hours == 24
    assert p.role_precedence[0] == "compliance_officer"
    assert {"data", "roles"} <= set(p.gates.post_handoff.heavy_if)


# --------------------------------------------------------------------------- merge semantics


def test_child_overrides_scalars_and_keeps_unset_parent_fields() -> None:
    base = _p("base", limits={"max_screens": 40, "max_cards": 12}, org="o")
    child = _p("child", "base@1", limits={"max_screens": 70})
    out = resolve_policy(child, {"base@1": base})
    assert out.limits.max_screens == 70
    assert out.limits.max_cards == 12, "parent value survives when the child does not set it"
    assert out.org == "o"
    assert out.id == "child" and out.inherits == "base@1"


def test_dicts_merge_and_lists_replace() -> None:
    base = _p(
        "base",
        slot_policy={"auth.sso": "must", "regions": "should"},
        research={"web": {"allowed_domains": ["a.com", "b.com"], "blocked_domains": ["x.com"]}},
        gates={"plan": {"approvers": ["requester"], "reminder_hours": 12}},
    )
    child = _p(
        "child",
        "base@1",
        slot_policy={"regions": "must", "approvers": "must"},
        research={"web": {"allowed_domains": ["c.com"]}, "connectors": ["docs"]},
        gates={"plan": {"approvers": ["lead"], "full_file_view": True}},
    )
    out = resolve_policy(child, {"base@1": base})
    assert out.slot_policy == {"auth.sso": "must", "regions": "must", "approvers": "must"}
    assert out.research.web.allowed_domains == ["c.com"], "lists are replaced, not merged"
    assert out.research.web.blocked_domains == ["x.com"], "sibling keys of a merged dict survive"
    assert out.research.connectors == ["docs"]
    assert out.gates.plan.approvers == ["lead"]
    assert out.gates.plan.full_file_view is True
    assert out.gates.plan.reminder_hours == 12, "nested dicts merge field by field"


def test_child_can_clear_a_list_explicitly() -> None:
    base = _p("base", research={"connectors": ["docs"]})
    child = _p("child", "base@1", research={"connectors": []})
    assert resolve_policy(child, {"base@1": base}).research.connectors == []


def test_three_levels_work_and_merge_in_order() -> None:
    org = _p("org", "platform-default@1", org="acme", limits={"max_screens": 60})
    app = _p("app", "org@1", limits={"max_cards": 15})
    out = resolve_policy(app, {"org@1": org})
    assert out.org == "acme"
    assert out.limits.max_screens == 60 and out.limits.max_cards == 15
    assert out.limits.max_turns == 30, "platform default reaches the third level"
    assert inheritance_chain(app, {"org@1": org}) == ["app", "org@1", "platform-default@1"]


def test_a_fourth_level_is_rejected() -> None:
    avail = {
        "a@1": _p("a", "platform-default@1"),
        "b@1": _p("b", "a@1"),
        "c@1": _p("c", "b@1"),
    }
    with pytest.raises(PolicyInheritanceError, match="deeper than 3 levels"):
        resolve_policy("c@1", avail)
    # The same chain one level shorter is fine.
    assert resolve_policy("b@1", avail).id == "b"


def test_cycles_are_detected() -> None:
    avail = {"a@1": _p("a", "b@1"), "b@1": _p("b", "a@1")}
    with pytest.raises(PolicyInheritanceError, match="cycle"):
        resolve_policy("a@1", avail)
    with pytest.raises(PolicyInheritanceError, match="cycle"):
        resolve_policy(_p("self", "self@1"), {"self@1": _p("self", "self@1")})


def test_unknown_parent_and_version_mismatch() -> None:
    with pytest.raises(PolicyInheritanceError, match="unknown parent"):
        resolve_policy(_p("orphan", "ghost@1"))
    with pytest.raises(PolicyInheritanceError, match="unknown parent"):
        resolve_policy(_p("late", "platform-default@7"))


def test_malformed_reference() -> None:
    with pytest.raises(PolicyInheritanceError, match="malformed"):
        resolve_policy(_p("bad", "Not A Ref"))
    with pytest.raises(PolicyInheritanceError, match="malformed"):
        resolve_policy("platform-default@x")


def test_inherits_without_a_version_uses_the_latest_available() -> None:
    base = _p("base", limits={"max_screens": 33})
    out = resolve_policy(_p("child", "base"), {"base@1": base})
    assert out.limits.max_screens == 33
