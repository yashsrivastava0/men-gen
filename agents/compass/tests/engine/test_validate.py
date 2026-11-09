from __future__ import annotations

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import AppRecord, FeatureRecord, RoleRecord
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.engine.brief_builder import empty_brief
from mde_compass.engine.slots import SlotStore
from mde_compass.engine.validate import must_slots_missing, validate_brief
from mde_core.clock import FakeClock

CAT = load_catalog()


def test_empty_brief_is_valid() -> None:
    assert validate_brief(empty_brief("ses_1", "guided", "en"), CAT) == []


def test_locked_without_evidence_is_an_error() -> None:
    b = empty_brief("ses_1", "guided", "en")
    b.idea["summary"] = BriefItem(value="x", tier="Locked", status="confirmed")
    errors = validate_brief(b, CAT)
    assert any("idea.summary" in e and "evidence" in e for e in errors)
    b.idea["summary"] = BriefItem(
        value="x",
        tier="Locked",
        status="confirmed",
        provenance=[Provenance(type="user_text", ref="evt_1")],
    )
    assert validate_brief(b, CAT) == []


def test_assumed_provenance_cannot_be_locked() -> None:
    b = empty_brief("ses_1", "guided", "en")
    b.idea["category"] = BriefItem(
        value="x", tier="Locked", provenance=[Provenance(type="pattern", ref="families.yaml")]
    )
    assert validate_brief(b, CAT)


def test_unknown_dict_key_is_an_error() -> None:
    b = empty_brief("ses_1", "guided", "en")
    b.idea["bogus"] = BriefItem(value="x", tier="Default")
    assert any("not a catalog field" in e for e in validate_brief(b, CAT))


def test_ids_unique_ledger_and_tombstones() -> None:
    b = empty_brief("ses_1", "guided", "en")
    b.apps = [AppRecord(id="APP-001", name="a"), AppRecord(id="APP-001", name="b")]
    errors = validate_brief(b, CAT)
    assert any("duplicate id APP-001" in e for e in errors) and any(
        "ahead of the id ledger" in e for e in errors
    )
    b.ids.note_existing("APP-001")
    b.apps = [AppRecord(id="APP-001", name="a")]
    assert validate_brief(b, CAT) == []
    b.ids.tombstone("APP-001")
    assert any("tombstones" in e for e in validate_brief(b, CAT))


def test_references_and_tier_c() -> None:
    b = empty_brief("ses_1", "guided", "en")
    for pfx in ("APP", "ROL", "FTR"):
        b.ids.note_existing(f"{pfx}-001")
    b.roles = [RoleRecord(id="ROL-001", name_in_user_words="owner")]
    b.apps = [AppRecord(id="APP-001", name="shop", roles=["ROL-001", "ROL-009"])]
    b.scope = [
        FeatureRecord(
            id="FTR-001",
            name="game",
            apps=["APP-001", "APP-777"],
            capability_tier="C",
            scope_status="in",
        )
    ]
    errors = validate_brief(b, CAT)
    assert any("ROL-009" in e for e in errors)
    assert any("APP-777" in e for e in errors)
    assert any("capability tier C cannot be in scope" in e for e in errors)
    b.scope[0].scope_status = "unsupported"
    b.scope[0].apps = ["APP-001"]
    b.apps[0].roles = ["ROL-001"]
    assert validate_brief(b, CAT) == []


def _store() -> SlotStore:
    return SlotStore(CAT, clock=FakeClock())


def test_must_slots_missing_respects_ask_if_and_mode() -> None:
    s = _store()
    missing = must_slots_missing(s, CAT, mode="guided")
    assert "idea.summary" in missing and "locales" in missing
    assert "idea.category" not in missing  # ask_if: filled('idea.summary')
    prov = Provenance(type="user_text", ref="evt_1")
    s.write("idea.summary", "a shop", state="volunteered", provenance=prov)
    missing = must_slots_missing(s, CAT, mode="guided")
    assert "idea.summary" not in missing and "idea.category" in missing and "context.geo" in missing
    s.write("idea.category", "internal_tool", state="volunteered", provenance=prov)
    assert "idea.business_model" not in must_slots_missing(s, CAT, mode="guided")
    s.write("idea.category", "commerce", state="volunteered", provenance=prov)
    assert "idea.business_model" in must_slots_missing(s, CAT, mode="guided")
    for sid in CAT.slots:
        if CAT.slots[sid].level == "must":
            s.write(
                sid,
                "x" if sid != "audience.segments" else ["a"],
                state="confirmed",
                provenance=prov,
            )
    assert must_slots_missing(s, CAT, mode="guided") == []
    assert must_slots_missing(s, CAT, mode="enterprise") == []
