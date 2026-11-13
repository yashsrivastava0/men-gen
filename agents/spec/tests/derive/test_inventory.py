"""derive/inventory.py: R-INV, the screen inventory derived by rules (doc 15 4.2, 4.3)."""

from __future__ import annotations

import json
from collections import Counter

from mde_compass_intake import ResearchView, read_export
from mde_compass_intake.research_reader import Norm, ResearchItem
from mde_compass_intake.spec_input import Entity, EntityField
from mde_spec_agent.derive.facts import facts_from_input
from mde_spec_agent.derive.inventory import build_inventory
from mde_spec_agent.derive.profile import detail_profile
from mde_spec_core import vocab
from mde_spec_core.config import default_config

from .conftest import EXPORTS, tiny_input


def inv_of(si, research=None):
    facts = facts_from_input(si, research)
    return build_inventory(si, research, facts, detail_profile(si, default_config(), facts))


def test_ids_are_carried_or_minted_in_the_spec_namespace(export) -> None:
    inv = inv_of(export("standard"))
    for p in inv.screens:
        assert p.compass_id is None or p.id == p.compass_id or p.id in inv.screen_alias.values()
        if p.compass_id is None:
            assert p.id.startswith("SCR-S"), p.id
    assert len({p.id for p in inv.screens}) == len(inv.screens)


def test_screen_types_come_from_the_closed_vocabulary(export) -> None:
    types = set(vocab.screen_types())
    for key in ("quick", "standard", "thorough"):
        assert {p.screen_type for p in inv_of(export(key)).screens} <= types


def test_create_and_edit_become_one_form_and_the_old_id_is_an_alias(export) -> None:
    inv = inv_of(export("standard"))
    assert "SCR-009" not in {p.id for p in inv.screens}
    base = inv.proposal("SCR-008")
    assert base is not None and base.mode == "create_edit" and base.screen_type == "form"
    assert inv.screen_alias["SCR-009"] == "SCR-008" and "SCR-009" in base.aliases
    assert inv.registry.resolve("SCR-009") == "SCR-008"
    assert {u for _, u in base.entity_uses} == {"create", "edit"}
    assert any(m.removed == "SCR-009" for m in inv.merges)


def test_a_shared_screen_is_one_node_used_by_every_app(export) -> None:
    inv = inv_of(export("standard"))
    sign_in = inv.proposal("SCR-001")
    assert sign_in is not None and len(sign_in.shared_apps) == 4
    assert [p.id for p in inv.screens].count("SCR-001") == 1


def test_the_system_set_is_added_once_with_provenance_and_links_out(export) -> None:
    inv = inv_of(export("quick"))
    names = {p.name for p in inv.screens if p.source == "system"}
    assert {
        "Profile",
        "Help and support",
        "Delete account",
    } <= names  # account deletion: accounts can be created
    assert inv.links_out == ["terms", "privacy"]  # legal pages are links out, not screens
    deletion = next(p for p in inv.screens if p.system_id == "account_deletion")
    assert deletion.system and deletion.provenance and deletion.provenance[0][0] == "policy"
    assert deletion.provenance[0][1].startswith("policy:POL-APPLE-DELETION@")
    # the Compass sign-in, verify and language screens already are system screens: not added again
    for sid in ("sign_in", "verify_code", "language", "notifications_inbox"):
        assert sum(1 for p in inv.screens if p.system_id == sid) == 1
    assert sum(1 for p in inv.screens if p.source == "system") == 5


def test_no_account_deletion_when_accounts_cannot_be_created() -> None:
    from mde_compass_intake.spec_input import Setting

    si = tiny_input(account_model=Setting(values=("admin_created",)))
    assert not any(p.system_id == "account_deletion" for p in inv_of(si).screens)


def test_entity_coverage_adds_a_form_for_a_managed_entity_and_none_for_join_or_log(export) -> None:
    si = tiny_input()
    only_list = tiny_input(
        structure=si.structure.model_copy(
            update={"screens": si.structure.screens[:1], "flows": ()}
        ),
    )
    inv = inv_of(only_list)
    forms = [p for p in inv.screens if p.source == "derived" and p.screen_type == "form"]
    assert len(forms) == 1 and forms[0].mode == "create_edit" and forms[0].role_ids == ("ROL-002",)
    # a log entity nobody opens needs no screen
    log = Entity(
        id="ENT-002",
        key="audit_log",
        name="Audit log",
        module="MOD-001",
        fields=(EntityField(key="what", type="text"),),
        access={"ROL-002": "view"},
    )
    with_log = tiny_input(
        structure=si.structure.model_copy(update={"entities": (*si.structure.entities, log)})
    )
    assert not any(p.entity_ids == ("ENT-002",) for p in inv_of(with_log).screens)


def test_every_role_has_a_screen(export) -> None:
    for key in ("quick", "standard", "thorough"):
        inv = inv_of(export(key))
        for rid in inv.roles:
            assert any(rid in p.role_ids for p in inv.screens), (key, rid)


def test_role_named_like_an_app_is_split_and_a_module_label_app_is_dropped(export) -> None:
    std = inv_of(export("standard"))
    assert (
        "ROL-005" in std.roles
        and std.roles["ROL-005"].name == "Manager"
        and std.roles["ROL-005"].assumed
    )
    assert any(a.id == "APP-004" for a in std.apps)  # it owns screens of its own
    tho = inv_of(export("thorough"))
    assert "APP-006" not in {a.id for a in tho.apps} and "ROL-018" not in tho.roles
    assert tho.role_alias["ROL-009"] == "ROL-002"  # merged duplicate role


def test_a_pay_step_is_added_only_when_payments_are_needed_and_missing() -> None:
    si = tiny_input()
    assert not any(p.screen_type == "checkout" for p in inv_of(si).screens)
    from mde_compass_intake.spec_input import Integration

    paying = tiny_input(integrations=(Integration(name="payments"),))
    pay = [p for p in inv_of(paying).screens if p.screen_type == "checkout"]
    assert len(pay) == 1 and pay[0].source == "derived"


def test_unknown_pattern_is_typed_detail_with_a_note() -> None:
    si = tiny_input()
    odd = si.structure.screens[0].model_copy(update={"pattern": "mystery"})
    inv = inv_of(
        tiny_input(
            structure=si.structure.model_copy(update={"screens": (odd, *si.structure.screens[1:])})
        )
    )
    assert inv.proposal("SCR-001").screen_type == "detail"
    assert any("mystery" in n for n in inv.notes)


def test_format_one_input_derives_screens_from_the_brief() -> None:
    path = EXPORTS / "pune-clinic-appointments.json"
    if not path.exists():
        return
    si = read_export(json.loads(path.read_text(encoding="utf-8")))
    inv = inv_of(si)
    assert inv.structure_derived and Counter(p.source for p in inv.screens)["derived"] > 0
    assert all(p.module for p in inv.screens)  # derived modules or the system module


def _peer_view(level: str, evidence: str) -> ResearchView:
    return ResearchView(
        thin=False,
        items=(
            ResearchItem(
                id="RI-001", kind="screen_pattern", label="Order tracking", evidence=evidence
            ),
        ),
        norms=(
            Norm(
                area="screen",
                key="order tracking",
                label="order tracking",
                item_ids=("RI-001",),
                prevalence={"seen_in": 3, "of": 4, "level": level},
            ),
        ),
    )


def test_peer_check_adds_a_common_screen_and_records_why_not_otherwise() -> None:
    si = tiny_input()
    added = inv_of(si, _peer_view("common", "corroborated"))
    gap = added.peers_usually_have[0]
    assert (
        gap.added
        and gap.screen_id
        and any(p.id == gap.screen_id and p.source == "peer" for p in added.screens)
    )
    for level, ev, why in (
        ("advanced", "official", "advanced"),
        ("core", "single_source", "single_source"),
    ):
        out = inv_of(si, _peer_view(level, ev))
        g = out.peers_usually_have[0]
        assert not g.added and why in g.reason
        assert not any(p.source == "peer" for p in out.screens)


def test_peer_gap_is_not_listed_when_a_screen_already_has_it() -> None:
    si = tiny_input()
    named = si.structure.screens[0].model_copy(update={"name": "Order tracking"})
    out = inv_of(
        tiny_input(
            structure=si.structure.model_copy(
                update={"screens": (named, *si.structure.screens[1:])}
            )
        ),
        _peer_view("core", "official"),
    )
    assert out.peers_usually_have == []


def test_derivation_is_deterministic(export) -> None:
    a, b = inv_of(export("thorough")), inv_of(export("thorough"))
    assert [(p.id, p.name, p.app, p.role_ids, p.feature_ids) for p in a.screens] == [
        (p.id, p.name, p.app, p.role_ids, p.feature_ids) for p in b.screens
    ]
