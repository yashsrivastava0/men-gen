import shutil
from pathlib import Path

import pytest
import yaml

from mde_core.errors import ConfigError
from mde_research_agent.catalog import DATA_DIR, EVENT_TYPES, JOB_NAMES, load_catalog


def test_the_packaged_data_loads_and_is_hashed() -> None:
    cat = load_catalog()
    assert set(cat.jobs) == set(JOB_NAMES)
    assert len(cat.catalog_hash) == 64
    assert load_catalog().catalog_hash == cat.catalog_hash  # deterministic
    assert {"focused", "standard", "deep"} == {c.name for c in cat.load.classes}


def test_every_event_type_has_a_label_and_every_slot_kind_has_a_lane() -> None:
    cat = load_catalog()
    assert set(EVENT_TYPES) <= set(cat.labels["en"].events)
    for job in cat.jobs.values():
        for target in job.targets:
            for slot in target.slots:
                assert slot.kind in job.kinds and slot.kind in cat.lanes.kinds


def test_authority_registry_is_organised_by_country_and_has_global_entries() -> None:
    cat = load_catalog()
    countries = {e.country for e in cat.authority}
    assert {"global", "in"} <= countries
    assert all("." in e.domain and "/" not in e.domain for e in cat.authority)


def test_every_job_has_a_required_slot_on_its_main_targets() -> None:
    cat = load_catalog()
    for job in cat.jobs.values():
        if job.job == "trust":  # conditional and informational: every slot is optional (doc 13 4.1)
            assert not any(s.required for t in job.targets for s in t.slots)
            continue
        assert any(any(s.required for s in t.slots) for t in job.targets), job.job


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "data"
    shutil.copytree(DATA_DIR, dst)
    return dst


def test_a_missing_event_label_fails_at_load(tmp_path: Path) -> None:
    dst = _copy(tmp_path)
    path = dst / "labels" / "en.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    del raw["events"]["page.read"]
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"page\.read"):
        load_catalog(dst)


def test_a_slot_kind_outside_the_job_fails_at_load(tmp_path: Path) -> None:
    dst = _copy(tmp_path)
    path = dst / "jobs" / "local.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("kind: local_format", "kind: competitor", 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="not in job kinds"):
        load_catalog(dst)


def test_hard_ceilings_below_soft_fail(tmp_path: Path) -> None:
    dst = _copy(tmp_path)
    path = dst / "ceilings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["focused"]["hard"]["searches"] = 1
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="hard limits"):
        load_catalog(dst)


def test_changing_a_data_value_changes_the_hash(tmp_path: Path) -> None:
    dst = _copy(tmp_path)
    before = load_catalog(dst).catalog_hash
    path = dst / "load.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("app: 3", "app: 4"), encoding="utf-8")
    assert load_catalog(dst).catalog_hash != before


def test_a_page_ceiling_that_cannot_feed_round_one_fails_at_load(tmp_path: Path) -> None:
    """Small run 2026-11-11: ten topics x five pages needs more than the old hard ceiling of 40."""
    dst = _copy(tmp_path)
    path = dst / "ceilings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["focused"]["hard"]["pages"] = 40
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="round one"):
        load_catalog(dst)


def test_the_shipped_ceilings_let_every_topic_read_its_pages() -> None:
    cat = load_catalog()
    for cls in ("focused", "standard", "deep"):
        cs = cat.ceilings.for_class(cls)  # type: ignore[arg-type]
        assert cs.hard.pages >= cs.soft.targets * cat.ceilings.pages_per_topic.max
