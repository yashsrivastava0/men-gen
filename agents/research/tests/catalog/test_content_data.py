"""The content data files: dossier layout, platform capabilities, depth by size, typed values and lane decisions (D-156, D-159).

They are data, validated at load; these tests pin what the code relies on so a typo or a half-edit fails here and not in a run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from mde_core.errors import ConfigError
from mde_research_agent.catalog import load_catalog
from mde_research_agent.catalog.content import dossier_config, typed_values
from mde_research_agent.catalog.loader import DATA_DIR, JOB_NAMES

CAT = load_catalog()


# --- dossiers.yaml -----------------------------------------------------------------------------------------------------------


def test_the_dossier_layout_names_only_known_fields_and_every_integration_need_has_one() -> None:
    cfg = CAT.dossiers
    for need in CAT.needs.needs:
        assert need in cfg.capabilities, (
            f"{need} has no dossier layout (it would use the default silently)"
        )
    for name, layout in {"default": cfg.default, **cfg.capabilities}.items():
        assert layout.required, name
        for f in (*layout.required, *layout.optional):
            assert f in cfg.fields, (name, f)


def test_only_official_docs_is_derived_and_every_extractable_field_is_explained() -> None:
    cfg = CAT.dossiers
    assert [k for k, v in cfg.fields.items() if v.derived] == ["official_docs"]
    assert all(v.text.strip() for v in cfg.fields.values())
    assert set(dossier_config().extractable()) == set(cfg.fields) - {"official_docs"}


def test_the_field_kinds_decide_what_conflicts() -> None:
    kinds = {k: v.kind for k, v in CAT.dossiers.fields.items()}
    assert {k for k, v in kinds.items() if v == "typed"} == {
        "fees",
        "limits",
        "onboarding_lead_time",
        "settlement",
    }
    assert {k for k, v in kinds.items() if v == "scalar"} == {"official_docs", "status"}
    assert {"sandbox", "expo_build", "products", "countries"} <= {
        k for k, v in kinds.items() if v == "list"
    }


def test_a_layout_that_names_an_unknown_field_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "data"
    shutil.copytree(DATA_DIR, root)
    raw = yaml.safe_load((root / "dossiers.yaml").read_text(encoding="utf-8"))
    raw["capabilities"]["payments"]["required"].append("favourite_colour")
    (root / "dossiers.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception, match="favourite_colour"):
        load_catalog(root)


# --- capabilities.yaml ---------------------------------------------------------------------------------------------------------


def test_capabilities_are_generic_and_use_signals_that_exist() -> None:
    cfg = CAT.capabilities
    assert cfg.weak_units >= 2
    structure = {"two_sided", "non_latin_locale", "many_roles", "multi_app", "sensitive_data"}
    for c in cfg.capabilities:
        assert c.strong or c.signals, f"{c.id} can never be implied"
        for s in c.signals:
            assert s in CAT.signals.signals or s in structure, (c.id, s)
        assert c.terms and c.label.strip()


def test_the_capabilities_the_owner_asked_for_are_all_there() -> None:
    ids = {c.id for c in CAT.capabilities.capabilities}
    assert ids >= {
        "store_payments",
        "push_notifications",
        "background_location",
        "camera_photo_upload",
        "offline_sync",
        "biometric_signin",
        "bluetooth_devices",
        "health_data_policies",
        "children_data",
        "deep_links",
        "map_sdks",
    }


# --- the platform job ----------------------------------------------------------------------------------------------------------


def test_the_platform_job_is_a_normal_job_with_a_capability_topic_that_is_not_protected() -> None:
    assert "platform" in JOB_NAMES and "platform" in CAT.jobs
    job = CAT.jobs["platform"]
    assert job.kinds == ("platform_constraint",) and job.lane == "integrations"
    (tpl,) = job.targets
    assert tpl.foreach == "capability" and tpl.scope == "none" and tpl.anchor == "topic"
    providers = next(t for t in CAT.jobs["services"].targets if t.id == "providers")
    assert tpl.priority < providers.priority, "platform topics rank below risk-class topics"
    assert {s.kind for s in tpl.slots} == {"platform_constraint"}


@pytest.mark.xfail(
    strict=True,
    reason="GAP-2: official_first is added to TargetTemplate by the discovery task; once it lands set `official_first: true` on "
    "the capability_rules topic in data/jobs/platform.yaml (the lead does it at the merge) and remove this pin",
)
def test_the_platform_topic_asks_official_sources_first() -> None:
    (tpl,) = CAT.jobs["platform"].targets
    assert getattr(tpl, "official_first", False) is True


def test_provider_and_platform_kinds_are_wired_in_lanes() -> None:
    for kind in ("provider_fact", "platform_constraint"):
        spec = CAT.lanes.kinds[kind]
        assert "integrations" in spec.lanes and "verify" in spec.lanes and spec.need == "BN-10"
        assert spec.min_all, kind


def test_the_services_slots_count_providers_and_dossier_fields_not_endpoints() -> None:
    slots = {s.key: s for s in CAT.jobs["services"].targets[0].slots}
    assert (
        slots["providers"].kind == "provider_fact" and slots["providers"].distinct_by == "provider"
    )
    assert (
        slots["provider_fields"].type == "distinct_values"
        and slots["provider_fields"].attr == "field"
    )
    assert (
        slots["provider_fields"].min >= 3
        and slots["provider_fields"].deep_min
        and slots["provider_fields"].deep_min > slots["provider_fields"].min
    )
    assert not any(s.attr == "endpoints_or_events" for s in slots.values()), (
        "atoms never satisfy it (D-160)"
    )
    assert (
        CAT.jobs["services"].kinds[0] == "provider_fact"
        and "service_option" in CAT.jobs["services"].kinds
    )


# --- depth.yaml ----------------------------------------------------------------------------------------------------------------


def test_depth_grows_with_the_size_class() -> None:
    d = CAT.depth.classes
    assert (
        d["focused"].platform_capabilities
        < d["standard"].platform_capabilities
        < d["deep"].platform_capabilities
    )


def test_every_depth_item_names_a_real_topic_and_slot() -> None:
    for item in CAT.depth.items:
        job = CAT.jobs[item.job]
        tpl = next(t for t in job.targets if t.id == item.template)
        assert item.slot in {s.key for s in tpl.slots}, item.id
        assert item.classes, item.id
        assert set(item.floor) <= set(item.classes), item.id


def test_a_depth_item_naming_a_missing_slot_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "data"
    shutil.copytree(DATA_DIR, root)
    raw = yaml.safe_load((root / "depth.yaml").read_text(encoding="utf-8"))
    raw["items"][0]["slot"] = "no_such_slot"
    (root / "depth.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="no_such_slot"):
        load_catalog(root)


def test_the_depth_items_that_are_only_defined_in_data_are_the_ones_the_report_names() -> None:
    """Pinned so the hand-off report stays true: these depth items have no enforcement behind them yet (unproven)."""
    data_only = {i.id for i in CAT.depth.items if i.wired == "data_only"}
    assert data_only == {
        "competitor_matrix",
        "entity_lifecycles",
        "role_workflow_exceptions",
        "realistic_data_by_entity",
        "trends",
    }


# --- typed values and lane decisions -----------------------------------------------------------------------------------------


def test_typed_value_currencies_are_three_letter_codes_with_evidence_tokens() -> None:
    cfg = typed_values()
    assert {"USD", "EUR", "INR", "XOF", "MXN", "GTQ", "CRC"} <= set(cfg.currencies)
    for code, tokens in cfg.currencies.items():
        assert len(code) == 3 and code.isupper() and tokens, code
    assert "%" in cfg.percent_units and "%" in cfg.percent_evidence


def test_every_lane_decision_can_be_informed_by_its_lane() -> None:
    lanes = CAT.lanes
    for lane, decisions in lanes.decisions.items():
        assert lane in lanes.lanes
        for d in decisions:
            fed = {lane2 for k in d.kinds for lane2 in lanes.kinds[k].lanes}
            assert lane in fed or lane == "verify", f"{d.id}: no kind of it reaches the {lane} lane"


def test_every_kind_informs_some_decision_of_one_of_its_lanes() -> None:
    lanes = CAT.lanes
    for kind, spec in lanes.kinds.items():
        covered = {lane for lane, ds in lanes.decisions.items() for d in ds if kind in d.kinds}
        assert covered & set(spec.lanes), f"no decision reads {kind} in its lanes {spec.lanes}"


def test_decision_ids_are_unique_and_every_gap_reason_has_a_note() -> None:
    ids = [d.id for ds in CAT.lanes.decisions.values() for d in ds]
    assert len(ids) == len(set(ids))
    assert set(CAT.lanes.decision_gaps) == {
        "unverified_only",
        "not_found",
        "not_run",
        "not_planned",
    }
    assert all(CAT.lanes.decision_gaps.values())
