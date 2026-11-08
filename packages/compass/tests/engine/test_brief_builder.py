from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.common import Provenance
from mde_compass.domain.hashing import brief_hash
from mde_compass.engine.brief_builder import (
    apply_slots,
    dict_targets,
    empty_brief,
    slots_from_brief,
    target_of,
)
from mde_compass.engine.slots import SlotStore
from mde_compass.engine.validate import validate_brief
from mde_core.clock import FakeClock

CAT = load_catalog()
CLOCK = FakeClock()
TAP = Provenance(type="user_tap", ref="evt_1")
PAT = Provenance(type="pattern", ref="families.yaml")


def new_store() -> SlotStore:
    return SlotStore(CAT, clock=CLOCK)


def fill(s: SlotStore) -> SlotStore:
    w: Any = s.write
    w("idea.summary", "an app for a sweets shop", state="volunteered", provenance=TAP)
    w("idea.category", "commerce", state="confirmed", provenance=TAP)
    w("context.geo", {"country": "India", "city": "Pune"}, state="volunteered", provenance=TAP)
    w("audience.segments", ["families", "office workers"], state="volunteered", provenance=TAP)
    w(
        "locales",
        {"chat": "hi", "app": ["hi", "en"], "right_to_left": False},
        state="confirmed",
        provenance=TAP,
    )
    w(
        "apps.list",
        [
            {
                "name": "Customer app",
                "purpose": "order sweets",
                "surface": "mobile",
                "roles": ["Customer"],
            },
            {"name": "Shop app", "purpose": "manage orders"},
        ],
        state="volunteered",
        provenance=TAP,
    )
    w(
        "roles.list",
        [{"name": "Customer", "goal": "order", "permissions": ["order"]}, "Shop owner"],
        state="assumed",
        provenance=PAT,
    )
    w(
        "scope.features",
        [
            {
                "name": "Order tracking",
                "status": "in",
                "priority": "must",
                "tier": "A",
                "apps": ["Customer app"],
            },
            "Group ordering",
        ],
        state="volunteered",
        provenance=TAP,
    )
    w("scope.priority", "mvp_first", state="assumed", provenance=PAT)
    w(
        "scope.references",
        [{"label": "Swiggy", "borrow": ["live tracking"]}],
        state="volunteered",
        provenance=TAP,
    )
    w("integrations.wanted", ["payments", "sms"], state="assumed", provenance=PAT)
    w("integrations.mode", "mock_first", state="assumed", provenance=PAT)
    w(
        "glossary.terms",
        [{"term": "mithai", "synonyms": ["sweets"]}],
        state="volunteered",
        provenance=TAP,
    )
    w("brand.name", "Mithai House", state="volunteered", provenance=TAP)
    return s


def test_every_slot_has_a_known_target() -> None:
    kinds = {s.id: target_of(s).kind for s in CAT.slots.values()}
    assert {k for k, v in kinds.items() if v == "skip"} == {"unknowns.research_wanted"}
    assert len(dict_targets(CAT)) == sum(
        1 for v in kinds.values() if v == "dict"
    )  # no two slots share a key


def test_maps_sections_and_typed_records() -> None:
    b = apply_slots(empty_brief("ses_1", "guided", "en"), fill(new_store()), CAT)
    assert b.idea["summary"].value == "an app for a sweets shop"
    assert b.idea["summary"].tier == "Locked" and b.idea["summary"].status == "volunteered"
    assert b.context["geo"].value == {"country": "India", "city": "Pune"}
    assert b.experience["brand.name"].value == "Mithai House"
    assert b.experience["locales"].status == "confirmed"
    assert b.constraints["scope_priority"].value == "mvp_first"
    assert b.meta.locales == ["hi", "en"]
    assert [a.id for a in b.apps] == ["APP-001", "APP-002"]
    assert [r.id for r in b.roles] == ["ROL-001", "ROL-002"]
    assert b.roles[0].name_in_user_words == "Customer" and b.roles[0].rough_permissions == ["order"]
    assert (
        b.roles[0].tier == "Default" and b.roles[0].status == "assumed"
    )  # assumed -> never Locked
    assert b.apps[0].roles == ["ROL-001"]  # role names resolved to ids
    assert [f.id for f in b.scope] == ["FTR-001", "FTR-002"]
    assert (
        b.scope[0].scope_status == "in"
        and b.scope[0].priority == "must"
        and b.scope[0].capability_tier == "A"
    )
    assert b.scope[0].apps == ["APP-001"] and b.scope[0].tier == "Locked"
    assert b.references[0].label == "Swiggy" and b.glossary[0].term == "mithai"
    assert [i.name for i in b.integrations] == ["payments", "sms"]
    assert all(i.preference == "real_later" for i in b.integrations)
    assert validate_brief(b, CAT) == []


def test_empty_slots_are_omitted_not_shells() -> None:
    b = apply_slots(empty_brief("ses_1", "guided", "en"), new_store(), CAT)
    assert b.idea == {} and b.apps == [] and b.experience == {}
    s = new_store()
    s.write("brand.name", "X", state="volunteered", provenance=TAP)
    b = apply_slots(b, s, CAT)
    s.write("brand.name", None, state="volunteered", provenance=TAP, remove=True)
    assert "brand.name" not in apply_slots(b, s, CAT).experience


def test_input_not_mutated_and_idempotent() -> None:
    s = fill(new_store())
    base = empty_brief("ses_1", "guided", "en")
    before = base.model_dump_json()
    once = apply_slots(base, s, CAT)
    assert base.model_dump_json() == before
    twice = apply_slots(once, s, CAT)
    assert twice == once and twice.ids == once.ids


def test_ids_stable_across_calls_and_edits() -> None:
    s = fill(new_store())
    b1 = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    s.write(
        "scope.features",
        [{"name": "ORDER TRACKING", "priority": "nice"}, "Group ordering", "Wishlist"],
        state="volunteered",
        provenance=Provenance(type="user_text", ref="evt_5"),
    )
    b2 = apply_slots(b1, s, CAT)
    assert [(f.id, f.name) for f in b2.scope] == [
        ("FTR-001", "ORDER TRACKING"),
        ("FTR-002", "Group ordering"),
        ("FTR-003", "Wishlist"),
    ]
    assert (
        b2.scope[0].priority == "nice" and b2.scope[0].scope_status == "in"
    )  # untouched fields kept


def test_removed_record_is_tombstoned_and_id_never_reused() -> None:
    s = fill(new_store())
    b1 = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    s.write("scope.features", [{"name": "Order tracking"}], state="volunteered", provenance=TAP)
    b2 = apply_slots(b1, s, CAT)
    assert [f.id for f in b2.scope] == ["FTR-001"] and "FTR-002" in b2.ids.tombstones
    s.write(
        "scope.features", ["Order tracking", "Group ordering"], state="volunteered", provenance=TAP
    )
    b3 = apply_slots(b2, s, CAT)
    assert [f.id for f in b3.scope] == ["FTR-001", "FTR-003"]  # same name, fresh id
    assert validate_brief(b3, CAT) == []


def test_cleared_list_slot_tombstones_all() -> None:
    s = fill(new_store())
    b1 = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    s.write("apps.list", None, state="volunteered", provenance=TAP, remove=True)
    b2 = apply_slots(b1, s, CAT)
    assert b2.apps == [] and set(b2.ids.tombstones) == {"APP-001", "APP-002"}


def test_records_copy_meta_from_slot() -> None:
    s = fill(new_store())
    b = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    rec = s.get("apps.list")
    assert rec is not None
    for app in b.apps:
        assert (
            app.status == rec.status and app.tier == rec.tier and app.provenance == rec.provenance
        )
        assert app.confidence == rec.confidence


def test_roundtrip_slots_from_brief() -> None:
    s = fill(new_store())
    b = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    restored = new_store()
    restored.load(slots_from_brief(b, CAT, CLOCK))
    b2 = apply_slots(b, restored, CAT)
    assert brief_hash(b2) == brief_hash(b) and b2.ids == b.ids
    assert set(restored.all()) >= {
        "idea.summary",
        "apps.list",
        "integrations.mode",
        "scope.priority",
    }
    assert restored.get("integrations.mode").value == "mock_first"  # type: ignore[union-attr]
    # a fresh Brief rebuilt from the restored slots has the same content (ids by name)
    b3 = apply_slots(empty_brief("ses_1", "guided", "en"), restored, CAT)
    assert [a.id for a in b3.apps] == [a.id for a in b.apps] or {a.name for a in b3.apps} == {
        a.name for a in b.apps
    }


def test_per_record_meta_survives_roundtrip() -> None:
    s = fill(new_store())
    b = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    b.scope[1] = b.scope[1].model_copy(
        update={
            "status": "confirmed",
            "tier": "Locked",
            "provenance": [Provenance(type="user_text", ref="evt_9")],
        }
    )
    restored = new_store()
    restored.load(slots_from_brief(b, CAT, CLOCK))
    b2 = apply_slots(b, restored, CAT)
    assert b2.scope[1].status == "confirmed" and b2.scope[1].tier == "Locked"
    assert b2.scope[0].status == "volunteered"


def test_tier_is_clamped_to_evidence() -> None:
    s = new_store()
    s.write(
        "idea.summary", "x", state="volunteered", provenance=Provenance(type="user_text", ref="e1")
    )
    rec = s.get("idea.summary")
    assert rec is not None
    s.load([rec.model_copy(update={"provenance": [], "tier": "Locked"})])
    b = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    assert b.idea["summary"].tier == "Default"


def test_bad_record_fields_are_dropped_not_fatal() -> None:
    s = new_store()
    s.write(
        "scope.features",
        [{"name": "X", "status": "banana", "priority": "must"}],
        state="volunteered",
        provenance=TAP,
    )
    b = apply_slots(empty_brief("ses_1", "guided", "en"), s, CAT)
    assert (
        b.scope[0].name == "X" and b.scope[0].scope_status == "in" and b.scope[0].priority == "must"
    )


def test_org_slots_map_into_dict_sections() -> None:
    s = new_store()
    s.write(
        "org.design_system",
        ["Acme DS"],
        state="confirmed",
        provenance=Provenance(type="org_profile", ref="org:1"),
    )
    s.write(
        "org.approvers",
        [{"name": "Asha", "role": "approver"}],
        state="confirmed",
        provenance=Provenance(type="org_profile", ref="org:1"),
    )
    b = apply_slots(empty_brief("ses_1", "enterprise", "en"), s, CAT)
    assert b.experience["org.design_system"].value == ["Acme DS"]
    assert b.context["org.approvers"].tier == "Locked"
    assert validate_brief(b, CAT) == []


names = st.sampled_from(["alpha", "Beta", "gamma", "Delta", "epsilon", "ALPHA", "beta"])


@settings(max_examples=60, deadline=None)
@given(st.lists(st.lists(names, max_size=4), min_size=1, max_size=5))
def test_property_ids_stable_unique_never_reused(rounds: list[list[str]]) -> None:
    s = new_store()
    brief = empty_brief("ses_1", "guided", "en")
    ever: dict[str, str] = {}  # id -> casefolded name
    for names_ in rounds:
        s.write("scope.features", names_, state="volunteered", provenance=TAP)
        brief = apply_slots(brief, s, CAT)
        again = apply_slots(brief, s, CAT)
        assert again == brief
        ids = [f.id for f in brief.scope]
        assert len(set(ids)) == len(ids)
        for f in brief.scope:
            assert (
                ever.setdefault(f.id, f.name.casefold()) == f.name.casefold()
            )  # an id is one name forever
            assert f.id not in brief.ids.tombstones
        assert validate_brief(brief, CAT) == []
