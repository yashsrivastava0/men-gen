"""The data files behind the structure of the app (ADR 0017): patterns and templates, realism rules, check policy. They
are strict: a typo is refused with the place, so it never reaches a running session."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from mde_compass.catalog import structure_patterns as sp
from mde_compass.catalog import structure_rules as sr
from mde_compass.catalog.loader import CatalogError, load_catalog


@pytest.fixture(autouse=True)
def _restore_caches() -> Any:
    yield
    sp.load_structure_patterns.cache_clear()
    sr.load_structure_rules.cache_clear()
    sr.load_check_policy.cache_clear()


def test_the_patterns_file_loads_with_homes_chrome_and_types() -> None:
    data = sp.load_structure_patterns()
    assert {"list", "detail", "form", "dashboard", "auth", "settings"} <= set(data.patterns)
    assert {"list", "dashboard", "calendar"} <= data.home_patterns()
    assert {"auth", "profile", "settings"} <= data.chrome_patterns()
    assert "money" in data.field_types and "ref" in data.field_types
    assert any(h.type == "money" and h.auto and "price" in h.words for h in data.field_hints)


def test_the_floor_templates_use_the_features_own_name_in_both_languages() -> None:
    floor = sp.load_structure_patterns().floor
    assert floor.screens_per_feature == {"small": 2, "medium": 3, "large": 4}
    for pattern in floor.feature_patterns:
        assert (
            "{feature}" in floor.labels[pattern]["en"]
            and "{feature}" in floor.labels[pattern]["hi"]
        )
        assert (
            sp.text_for(floor.labels[pattern], "hinglish") == floor.labels[pattern]["en"]
        )  # any other language: English
    assert [s.pattern for s in floor.account_screens] == ["auth", "auth", "profile"]


def test_the_standard_modules_are_generic_and_known_per_band() -> None:
    floor = sp.load_structure_patterns().floor
    support = {m.id: m for m in floor.support_modules}
    assert set(support) == {"notifications", "help", "administration"}
    assert support["notifications"].bands == ("medium", "large") and support["help"].bands == (
        "large",
    )
    assert support["administration"].roles_min == 3
    assert floor.actions["other"]["en"] and "{goal}" in floor.acceptance["en"]
    assert {"list", "form", "status"} <= set(floor.actions)


def test_every_family_label_the_floor_may_use_exists_in_the_catalog() -> None:
    catalog = load_catalog()
    assert all(f.label.get("en") and f.label.get("hi") for f in catalog.families.values())


def test_the_rule_pack_and_the_check_policy_load() -> None:
    rules = sr.load_structure_rules()
    assert {r.id for r in rules} >= {"health_data", "payments", "code_sign_in", "admin_role"}
    policy = sr.load_check_policy()
    assert policy["flow_role_cannot_access_step"] == sr.CheckPolicy("error", "repair")
    assert (
        policy["thin_draft"].fix == "none" and policy["over_band"].fix == "none"
    )  # size is never a quota
    expanders = {code for code, p in policy.items() if p.fix == "expand"}
    assert {
        "feature_thin",
        "module_thin",
        "fact_orphan",
    } <= expanders  # the only things that add screens


def test_words_are_split_like_the_rest_of_compass() -> None:
    assert sp.words_of("Total_Fee, price!") == ["total", "fee", "price"]
    assert sp.words_of("भुगतान की रसीद") == ["भुगतान", "की", "रसीद"]


# ------------------------------------------------------------------------------------------ strict loaders


def _patterns_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[dict[str, Any]], None]
) -> None:
    data = yaml.safe_load(sp.PATTERNS_PATH.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "structure_patterns.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(sp, "PATTERNS_PATH", path)
    sp.load_structure_patterns.cache_clear()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["patterns"]["list"].pop("home"), "home"),
        (
            lambda d: d["patterns"].update(
                {k: {**v, "home": False} for k, v in d["patterns"].items()}
            ),
            "at least one pattern",
        ),
        (lambda d: d["field_types"].append("money"), "distinct"),
        (lambda d: d["field_hints"][0].update(type="decimal"), "type must be one of"),
        (lambda d: d["floor"]["feature_patterns"].append("carousel"), "known patterns"),
        (lambda d: d["floor"]["screens_per_feature"].update(large=9), "between 1"),
        (lambda d: d["floor"]["labels"]["list"].pop("hi"), "exactly the languages"),
        (lambda d: d["floor"]["labels"]["list"].update(en="Things"), "{feature}"),
        (lambda d: d["floor"]["account_module"].update(screens=[]), "at least one screen"),
        (lambda d: d["floor"]["support_modules"][0].update(bands=["huge"]), "must be a list of"),
        (
            lambda d: d["floor"]["support_modules"].append(d["floor"]["support_modules"][0]),
            "distinct",
        ),
        (lambda d: d["floor"]["actions"].pop("other"), "'other'"),
        (lambda d: d["floor"]["acceptance"].update(en="Works."), "{goal}"),
    ],
)
def test_a_bad_patterns_file_is_refused_with_the_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    _patterns_with(tmp_path, monkeypatch, change)
    with pytest.raises(CatalogError, match=message):
        sp.load_structure_patterns()


def _rules_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[dict[str, Any]], None]
) -> None:
    data = yaml.safe_load(sr.RULES_PATH.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "structure_rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(sr, "RULES_PATH", path)
    sr.load_structure_rules.cache_clear()
    sr.load_check_policy.cache_clear()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["rules"][0].update(when={}), "at least one of"),
        (lambda d: d["rules"][0].update(when={"mood": ["happy"]}), "at least one of"),
        (lambda d: d["rules"][0].update(requires=[]), "at least one capability"),
        (lambda d: d["rules"][0]["requires"][0]["words"].pop("en"), "English required"),
        (lambda d: d["rules"][0]["requires"][0]["why"].pop("hi"), "exactly the languages"),
        (lambda d: d["rules"].append(d["rules"][0]), "distinct"),
    ],
)
def test_a_bad_rule_pack_is_refused_with_the_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    _rules_with(tmp_path, monkeypatch, change)
    with pytest.raises(CatalogError, match=message):
        sr.load_structure_rules()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["checks"].pop("thin_draft"),
        lambda d: d["checks"].update(invented_check={"severity": "error", "fix": "code"}),
        lambda d: d["checks"]["thin_draft"].update(severity="fatal"),
        lambda d: d["checks"]["thin_draft"].update(fix="sometimes"),
    ],
)
def test_a_check_policy_that_does_not_match_the_codes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[dict[str, Any]], None]
) -> None:
    _rules_with(tmp_path, monkeypatch, change)
    with pytest.raises(CatalogError):
        sr.load_check_policy()
