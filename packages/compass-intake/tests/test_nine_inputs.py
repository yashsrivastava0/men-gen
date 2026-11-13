"""The nine Compass exports of ``docs/compass/research-inputs/`` each load into a ``SpecInput`` (PS-1, snapshot style).

The table is the snapshot: counts and flags written out as expected values, so a change in the reader (or in an
export) shows up as a diff of one row. Three real runs, three older real exports (format 1, trimmed) and three
synthetic format-2 files.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import SpecInput, check_readiness, read_export

NINE = Path(__file__).resolve().parents[3] / "docs" / "compass" / "research-inputs"

# name -> expected values. ``trust`` = (degraded, failing probes). ``roles`` = (all, clean, derived).
# ``struct`` = (modules, screens, flows, entities). ``facts`` = (all, locked, rules and numbers).
TABLE: dict[str, dict[str, Any]] = {
    "delhi-restaurant-delivery": dict(
        fmt=1, binding="unverified_no_baseline", tier="standard", band=("medium", "tier"),
        features=3, roles=(4, 4, 0), apps=1, struct=(0, 0, 0, 0), facts=(53, 49, 14), integrations=1,
        locales=("en", "hi"), locale_source="view", countries=[], trust=(True, ("blueprint",)),
        cleaning=(), drift=("size_band_from_tier",), weak=True,
    ),
    "hospital-chain-enterprise": dict(
        fmt=2, binding="verified", tier="thorough", band=("large", "tier"),
        features=13, roles=(10, 9, 0), apps=1, struct=(0, 0, 0, 0), facts=(39, 35, 13), integrations=0,
        locales=("mr", "gu", "hi", "en"), locale_source="experience", countries=["in"], trust=(False, ()),
        cleaning=("junk_roles:1", "priorities_mostly_nice"), drift=("size_band_from_tier",), weak=False,
    ),
    "pune-clinic-appointments": dict(
        fmt=1, binding="verified", tier="standard", band=("medium", "tier"),
        features=9, roles=(4, 4, 0), apps=3, struct=(0, 0, 0, 0), facts=(72, 70, 26), integrations=0,
        locales=("en", "hi"), locale_source="view", countries=[], trust=(False, ()),
        cleaning=(), drift=("size_band_from_tier",), weak=False,
    ),
    "quick-yoga-studio-bengaluru": dict(
        fmt=2, binding="verified", tier="quick", band=("small", "structure"),
        features=6, roles=(4, 4, 0), apps=1, struct=(4, 13, 4, 5), facts=(33, 33, 11), integrations=1,
        locales=("en", "kn"), locale_source="view", countries=[], trust=(False, ()),
        cleaning=(), drift=(), weak=False,
    ),
    "standard-produce-marketplace-nashik": dict(
        fmt=2, binding="verified", tier="standard", band=("medium", "structure"),
        features=9, roles=(9, 8, 0), apps=4, struct=(5, 28, 7, 10), facts=(44, 44, 14), integrations=0,
        locales=("mr", "hi", "en"), locale_source="view", countries=["in"], trust=(True, ("design",)),
        cleaning=("duplicate_features:1", "role_app_name_clash:1"), drift=(), weak=True,
    ),
    "synthetic-big-site-safety-central-america": dict(
        fmt=2, binding="verified", tier="thorough", band=("large", "structure"),
        features=29, roles=(11, 11, 0), apps=3, struct=(10, 57, 20, 17), facts=(39, 39, 20), integrations=3,
        locales=("es", "en"), locale_source="view", countries=["mx", "gt", "cr"], trust=(False, ()),
        cleaning=("duplicate_features:1",), drift=(), weak=False,
    ),
    "synthetic-medium-school-parents-dakar": dict(
        fmt=2, binding="verified", tier="standard", band=("medium", "structure"),
        features=13, roles=(3, 3, 0), apps=2, struct=(6, 28, 12, 9), facts=(19, 19, 6), integrations=2,
        locales=("fr", "en"), locale_source="view", countries=["sn"], trust=(False, ()),
        cleaning=(), drift=(), weak=False,
    ),
    "synthetic-small-bike-repair-porto": dict(
        fmt=2, binding="verified", tier="quick", band=("small", "structure"),
        features=7, roles=(3, 3, 0), apps=1, struct=(4, 15, 3, 5), facts=(17, 17, 4), integrations=1,
        locales=("pt-PT", "en"), locale_source="view", countries=["pt"], trust=(False, ()),
        cleaning=(), drift=(), weak=False,
    ),
    "thorough-freight-fleet-india": dict(
        fmt=2, binding="verified", tier="thorough", band=("large", "structure"),
        features=30, roles=(18, 15, 0), apps=6, struct=(11, 132, 20, 22), facts=(151, 116, 51), integrations=2,
        locales=("en", "hi"), locale_source="view", countries=["in"], trust=(False, ()),
        cleaning=(
            "duplicate_features:1", "duplicate_roles:2", "role_app_name_clash:1", "priorities_mostly_nice",
        ),
        drift=(), weak=False,
    ),
}  # fmt: skip


@cache
def spec_input(name: str) -> SpecInput:
    return read_export(json.loads((NINE / f"{name}.json").read_text(encoding="utf-8")))


def test_the_table_covers_every_file_of_the_folder() -> None:
    assert sorted(TABLE) == sorted(p.stem for p in NINE.glob("*.json"))


@pytest.mark.parametrize("name", sorted(TABLE))
def test_each_export_loads_into_a_spec_input_with_the_expected_shape(name: str) -> None:
    want, si = TABLE[name], spec_input(name)
    assert (si.compass_format, si.input_binding, si.tier) == (
        want["fmt"],
        want["binding"],
        want["tier"],
    )
    assert (si.size_band, si.size_band_source) == want["band"]
    assert len(si.features) == want["features"] and len(si.apps) == want["apps"]
    assert (
        len(si.roles),
        len(si.clean_roles()),
        sum(r.assumed for r in si.roles),
    ) == want["roles"]
    assert (
        len(si.modules),
        len(si.structure.screens),
        len(si.structure.flows),
        len(si.structure.entities),
    ) == want["struct"]
    assert (
        len(si.domain_facts),
        sum(f.trust.grade == "locked" for f in si.domain_facts),
        len(si.rules_and_numbers),
    ) == want["facts"]
    assert len(si.integrations) == want["integrations"]
    assert si.locales.codes == want["locales"] and si.locales.source == want["locale_source"]
    assert [c.code for c in si.geography.countries] == want["countries"]
    assert (si.trust.degraded, si.trust.probes) == want["trust"]
    assert si.trust.weak_inputs is want["weak"]
    assert set(si.cleaning_flags) == set(want["cleaning"])
    assert set(si.drift_flags) == set(want["drift"])


@pytest.mark.parametrize("name", sorted(TABLE))
def test_every_export_is_ready_and_serialisable_and_has_a_brief_hash(name: str) -> None:
    si = spec_input(name)
    assert check_readiness(si).ready
    assert si.idea.summary and si.brief_hash and si.session_id and si.program_id == si.session_id
    assert SpecInput.model_validate_json(si.model_dump_json()) == si


@pytest.mark.parametrize("name", sorted(TABLE))
def test_feature_existence_is_locked_only_with_a_verified_quote(name: str) -> None:
    """In all nine, every feature carries a verbatim quote that is found in the interview record."""
    si = spec_input(name)
    for f in si.features:
        assert f.trust.grade == ("locked" if f.user_evidenced else "default"), f.id
        assert f.user_evidenced and f.quote_ids, f"{name}: {f.id} has no verified quote"


@pytest.mark.parametrize("name", sorted(TABLE))
def test_priority_is_default_unless_the_user_ranked_it_and_structure_is_a_proposal(
    name: str,
) -> None:
    si = spec_input(name)
    for f in si.features:
        assert f.priority_trust.grade == ("locked" if f.deferred_by_user else "default"), f.id
        assert f.deferred_by_user is (f.priority == "later" or f.scope_status == "later")
    assert si.structure.grade == "default"


def test_default_tone_is_assumed_and_never_user_evidence() -> None:
    """SPC-IN-14: tone is a Default at confidence 0.4 in the real runs."""
    for name in (
        "quick-yoga-studio-bengaluru",
        "standard-produce-marketplace-nashik",
        "thorough-freight-fleet-india",
    ):
        tone = spec_input(name).tone
        assert tone.values and tone.trust.grade == "default" and tone.trust.assumed
        assert tone.trust.confidence == pytest.approx(0.4)


def test_locked_facts_with_low_confidence_are_graded_default() -> None:
    """SPC-IN-16: 33 of the Thorough facts are Locked by Compass at a confidence under 0.7."""
    si = spec_input("thorough-freight-fleet-india")
    low = [f for f in si.domain_facts if f.tier == "locked" and (f.trust.confidence or 1) < 0.7]
    assert len(low) == 33 and all(f.trust.grade == "default" for f in low)


def test_thorough_roles_are_cleaned_with_the_merges_recorded() -> None:
    """SPC-IN-06: 18 roles with singular and plural copies; ``Fleet maintenance screens`` is an app."""
    si = spec_input("thorough-freight-fleet-india")
    merged = {(m.from_id, m.into_id) for m in si.merges if m.kind == "role"}
    names = {r.id: r.name for r in si.roles}
    assert {(names[a], names[b]) for a, b in merged} == {
        ("dispatcher", "dispatchers"),
        ("driver", "drivers"),
    }
    assert [(c.name, c.app_id is not None) for c in si.name_clashes] == [
        ("Fleet maintenance screens", True)
    ]
    assert all(r.trust.grade == "default" for r in si.roles if r.duplicate_of or r.is_app_name)


def test_manager_side_is_both_a_role_and_an_app_in_the_standard_run() -> None:
    si = spec_input("standard-produce-marketplace-nashik")
    assert [c.name for c in si.name_clashes] == ["Manager side"]
    assert [a.name for a in si.apps if not a.is_real] == ["Manager side"]


def test_standard_duplicate_online_payment_is_merged_and_never_dropped() -> None:
    """SPC-IN-07: ``Online payment`` and ``Pay for orders online`` are one feature."""
    si = spec_input("standard-produce-marketplace-nashik")
    dup = next(f for f in si.features if f.duplicate_of)
    head = next(f for f in si.features if f.id == dup.duplicate_of)
    assert {dup.name, head.name} == {"Online payment", "Pay for orders online"}
    assert dup.id in head.alias_ids and head.user_evidenced
    assert len(si.features) == 9 and len(si.in_scope_features()) == 8


def test_the_structure_of_the_standard_run_carries_its_issues_as_a_proposal() -> None:
    si = spec_input("standard-produce-marketplace-nashik")
    errors = [i for i in si.structure.issues if i.severity == "error"]
    assert len(errors) == 3 and {i.code for i in errors} == {"screen_entity_access_conflict"}
    assert si.structure.navigation and si.structure.role_matrix
    assert si.trust.structure_source in {"model", "mixed"}


def test_user_deferred_feature_is_marked_and_kept_in_the_ledger() -> None:
    """SPC-IN-21: live tracking was deferred by the user in the Standard run."""
    si = spec_input("standard-produce-marketplace-nashik")
    later = [f for f in si.features if f.scope_status == "later"]
    assert (
        len(later) == 1 and later[0].deferred_by_user and later[0].priority_trust.grade == "locked"
    )


def test_existing_systems_sensitive_data_and_multi_country_are_carried() -> None:
    thorough = spec_input("thorough-freight-fleet-india")
    assert [s.name for s in thorough.existing_systems][:2] == ["Spreadsheets", "Tally"]
    assert thorough.sensitive_data == ("personal_ids", "financial")
    big = spec_input("synthetic-big-site-safety-central-america")
    assert [c.name for c in big.geography.countries] == ["Mexico", "Guatemala", "Costa Rica"]
    assert "multi_country" in big.flags and big.geography.country_code == "mx"
    assert spec_input("pune-clinic-appointments").sensitive_data == ("health",)


def test_the_thorough_export_with_its_fallback_structure_is_flagged_in_the_trust_profile() -> None:
    assert spec_input("thorough-freight-fleet-india").trust.fallback_count == 9
    assert spec_input("standard-produce-marketplace-nashik").trust.degraded
