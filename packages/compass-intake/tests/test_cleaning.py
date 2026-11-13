"""Cleaning flags and recorded merges (SPC-IN-06, 07, 08): junk and duplicate roles and apps, duplicate features,
priorities that are all or mostly ``nice``. Nothing is dropped; every merge is listed; evidence is never lost."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mde_compass_intake import read_export

HERE = Path(__file__).resolve().parent


def small() -> dict[str, Any]:
    return json.loads(
        (HERE / "fixtures" / "formats" / "format2_full.json").read_text(encoding="utf-8")
    )


def role(rid: str, name: str, **kw: Any) -> dict[str, Any]:
    return {"id": rid, "name_in_user_words": name, "goal": "", "tier": "Locked", **kw}


def test_singular_and_plural_roles_are_merged_with_the_merge_recorded() -> None:
    data = small()
    data["brief"]["roles"] = [
        role("ROL-001", "Dispatchers"),
        role("ROL-002", "Dispatcher"),
        role("ROL-003", "Driver"),
    ]
    si = read_export(data)
    assert [(m.from_id, m.into_id) for m in si.merges if m.kind == "role"] == [
        ("ROL-002", "ROL-001")
    ]
    head = si.roles[0]
    assert head.alias_ids == ("ROL-002",) and head.duplicate_of is None
    assert si.roles[1].duplicate_of == "ROL-001" and si.roles[1].trust.grade == "default"
    assert [r.id for r in si.clean_roles()] == ["ROL-001", "ROL-003"]
    assert "duplicate_roles:1" in si.cleaning_flags and len(si.roles) == 3, "nothing is dropped"


def test_junk_role_names_are_flagged_and_never_locked() -> None:
    data = small()
    data["brief"]["roles"] = [
        role("ROL-001", "Customers"),
        role("ROL-002", "Roles"),
        role("ROL-003", ""),
    ]
    si = read_export(data)
    assert [r.junk for r in si.roles] == [False, True, True]
    assert "junk_roles:2" in si.cleaning_flags
    assert [r.id for r in si.clean_roles()] == ["ROL-001"]


def test_a_name_used_as_role_and_app_is_split_and_recorded_as_a_clash() -> None:
    """SPC-IN-06 ("Manager side")."""
    data = small()
    data["brief"]["apps"][0]["name"] = "Manager side"
    data["brief"]["roles"] = [role("ROL-001", "Customers"), role("ROL-002", "Manager side")]
    si = read_export(data)
    assert [(c.name, c.role_id, c.app_id) for c in si.name_clashes] == [
        ("Manager side", "ROL-002", "APP-001")
    ]
    assert si.roles[1].is_app_name and not si.apps[0].is_real
    assert "role_app_name_clash:1" in si.cleaning_flags
    assert [r.id for r in si.clean_roles()] == ["ROL-001"]


def test_a_role_named_screens_is_an_app_name() -> None:
    data = small()
    data["brief"]["roles"] = [
        role("ROL-001", "Customers"),
        role("ROL-002", "Fleet maintenance screens"),
    ]
    si = read_export(data)
    assert si.roles[1].is_app_name and si.name_clashes[0].app_id is None


def test_duplicate_and_junk_apps_are_flagged() -> None:
    data = small()
    app = data["brief"]["apps"][0]
    data["brief"]["apps"] = [
        app,
        {**app, "id": "APP-002", "name": "Roda Viva apps"},
        {**app, "id": "APP-003", "name": "app"},
    ]
    si = read_export(data)
    assert [a.duplicate_of for a in si.apps] == [None, "APP-001", None]
    assert [a.junk for a in si.apps] == [False, False, True]
    assert {"duplicate_apps:1", "junk_apps:1"} <= set(si.cleaning_flags)
    assert any(m.kind == "app" and m.from_id == "APP-002" for m in si.merges)


def test_duplicate_features_are_merged_with_an_alias_and_nothing_is_dropped() -> None:
    """SPC-IN-07."""
    data = small()
    scope = data["brief"]["scope"]
    scope.append(
        {**scope[3], "id": "FTR-008", "name": "Pay for repairs online", "priority": "nice"}
    )
    si = read_export(data)
    dup = si.features[-1]
    assert dup.duplicate_of == "FTR-004" and len(si.features) == 8
    pay = next(f for f in si.features if f.id == "FTR-004")
    assert pay.alias_ids == ("FTR-008",)
    assert [(m.kind, m.from_id, m.into_id) for m in si.merges if m.kind == "feature"] == [
        ("feature", "FTR-008", "FTR-004")
    ]
    assert "duplicate_features:1" in si.cleaning_flags
    assert len(si.in_scope_features()) == 7


def test_a_user_evidenced_must_in_a_duplicate_is_never_lost() -> None:
    """SPC-IN-07: the representative takes the strongest evidence and priority of the group."""
    data = json.loads(
        (
            HERE.parents[2]
            / "docs/compass/research-inputs/standard-produce-marketplace-nashik.json"
        ).read_text(encoding="utf-8")
    )
    scope = data["brief"]["scope"]
    names = {f["name"]: f for f in scope}
    online, pay = names["Online payment"], names["Pay for orders online"]
    online["priority"], pay["priority"] = "nice", "must"
    si = read_export(data)
    head = next(f for f in si.features if f.id == online["id"])
    assert head.priority == "must" and head.user_evidenced and pay["id"] in head.alias_ids


def test_all_priorities_nice_is_flagged_and_a_must_is_still_carried() -> None:
    """SPC-IN-08: priority is a default; the flag tells the next stage not to trust the order."""
    data = small()
    for f in data["brief"]["scope"]:
        f["priority"] = "nice"
    si = read_export(data)
    assert "priorities_mostly_nice" in si.cleaning_flags
    assert all(f.priority_trust.grade == "default" for f in si.features)
    data["brief"]["scope"][0]["priority"] = "must"
    assert "priorities_mostly_nice" in read_export(data).cleaning_flags, (
        "6 of 7 is still mostly nice"
    )
    for f in data["brief"]["scope"][:4]:
        f["priority"] = "must"
    si = read_export(data)
    assert "priorities_mostly_nice" not in si.cleaning_flags
    assert si.features[0].priority == "must", "a must is never demoted by the reader"


def test_too_few_features_never_make_priorities_mostly_nice() -> None:
    data = small()
    data["brief"]["scope"] = [{**f, "priority": "nice"} for f in data["brief"]["scope"][:2]]
    assert "priorities_mostly_nice" not in read_export(data).cleaning_flags
