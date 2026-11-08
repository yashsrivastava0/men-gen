"""Families, capabilities, frames and no-ask list; plus failure paths of the loader."""

from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

import pytest
import yaml

from mde_compass.catalog import CatalogError, load_catalog
from mde_compass.catalog import loader as loader_module

CATALOG_DIR = Path(loader_module.__file__).parent
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

FAMILY_IDS = [
    "identity_access", "catalog_commerce", "booking_scheduling", "delivery_logistics",
    "payments_wallet", "communication", "content_community", "workflows", "dashboards_admin",
    "finance", "health_wellness", "learning", "travel_hospitality", "marketplaces",
    "civic_utilities", "enterprise_workforce",
]  # fmt: skip

FRAME_IDS = [
    "idea_in_your_words", "confirm_kind_of_app", "business_model", "who_and_where", "which_apps",
    "roles", "features", "priorities", "apps_you_like", "brand_and_tone", "login_and_accounts",
    "integrations_and_systems", "scale_and_launch", "limits",
]  # fmt: skip

TIER_B_IDS = [
    "real_push", "live_location", "biometrics", "payment_sdks", "camera", "offline_sync",
    "in_app_purchases", "bluetooth", "video_calls",
]  # fmt: skip

DECLINED_IDS = [
    "games", "ar_vr_3d", "media_editors", "drm_streaming", "trading_terminals", "os_utilities",
    "safety_critical_medical", "proprietary_hardware",
]  # fmt: skip


def _raw(name: str) -> dict:
    return yaml.safe_load((CATALOG_DIR / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------- families


def test_sixteen_families_each_with_enough_neutral_content() -> None:
    cat = load_catalog()
    assert [f for f in FAMILY_IDS if f not in cat.families] == []
    assert len(cat.families) >= 16
    for fam in cat.families.values():
        assert len(fam.typical_features) >= 6, fam.id
        assert len(fam.typical_roles) >= 2, fam.id
        assert fam.typical_integrations, fam.id
        assert fam.typical_business_models, fam.id
        assert len({f.id for f in fam.typical_features}) == len(fam.typical_features), fam.id
        for feature in fam.typical_features:
            assert feature.intent.strip().endswith("."), f"{fam.id}.{feature.id}"
            assert len(feature.intent.split()) >= 5, f"{fam.id}.{feature.id}"
            assert DEVANAGARI.search(feature.name.hi), f"{fam.id}.{feature.id}"
            assert feature.name.en != feature.name.hi


def test_family_keywords_cover_english_hindi_and_hinglish() -> None:
    for entry in _raw("families.yaml")["families"]:
        kw = entry["keywords"]
        assert set(kw) == {"en", "hi", "hinglish"}, entry["id"]
        assert len(kw["en"]) >= 3 and len(kw["hi"]) >= 3 and len(kw["hinglish"]) >= 3, entry["id"]
        assert all(DEVANAGARI.search(k) for k in kw["hi"]), entry["id"]
        assert not any(DEVANAGARI.search(k) for k in kw["en"] + kw["hinglish"]), entry["id"]


def test_loader_flattens_keywords_in_declared_order() -> None:
    fam = load_catalog().families["identity_access"]
    raw = _raw("families.yaml")["families"][0]["keywords"]
    assert fam.keywords == [*raw["en"], *raw["hi"], *raw["hinglish"]]


def test_family_text_has_no_brand_names() -> None:
    brands = {"amazon", "uber", "zomato", "swiggy", "paytm", "phonepe", "razorpay", "stripe"}
    for fam in load_catalog().families.values():
        words = " ".join(
            [fam.label.en, *(f.name.en + " " + f.intent for f in fam.typical_features)]
        ).lower()
        assert not brands & set(re.findall(r"[a-z]+", words)), fam.id


# ------------------------------------------------------------------------------ capabilities


def test_tier_b_and_declined_lists_are_complete() -> None:
    cat = load_catalog()
    assert [c.id for c in cat.tier_b()] == TIER_B_IDS
    assert [c.id for c in cat.declined()] == DECLINED_IDS


def test_every_capability_has_keywords_in_all_three_styles() -> None:
    for entry in _raw("capabilities.yaml")["capabilities"]:
        kw = entry["keywords"]
        assert set(kw) == {"en", "hi", "hinglish"}, entry["id"]
        assert all(len(kw[style]) >= 2 for style in kw), entry["id"]
        assert all(DEVANAGARI.search(k) for k in kw["hi"]), entry["id"]
        assert not any(DEVANAGARI.search(k) for k in kw["en"] + kw["hinglish"]), entry["id"]


def test_declined_categories_have_reason_and_alternative_in_both_languages() -> None:
    for cap in load_catalog().declined():
        assert cap.reason is not None and cap.alternative is not None, cap.id
        for text in (cap.reason, cap.alternative, cap.label):
            assert DEVANAGARI.search(text.hi), cap.id
            assert text.en.strip() and text.hi.strip()
        assert cap.alternative.en.startswith("We can"), cap.id


def test_tier_b_capabilities_need_no_decline_texts() -> None:
    for cap in load_catalog().tier_b():
        assert cap.reason is None and cap.alternative is None, cap.id


def test_keywords_are_unique_across_capabilities() -> None:
    counts = Counter(k.casefold() for c in load_catalog().capabilities.values() for k in c.keywords)
    assert [k for k, n in counts.items() if n > 1] == []


def test_decline_notice_composes_with_the_string_table() -> None:
    cat = load_catalog()
    games = cat.capabilities["games"]
    assert games.alternative is not None
    text = cat.t("notice.declined_category", "en", what="A game", alternative=games.alternative.en)
    assert text.startswith("A game is not something we can build here. We can build")


# ------------------------------------------------------------------------------------ frames


def test_expected_frames_exist_in_order() -> None:
    cat = load_catalog()
    ordered = [f.id for f in sorted(cat.frames.values(), key=lambda f: f.order)]
    assert [f for f in FRAME_IDS if f not in ordered] == []
    assert ordered[: len(FRAME_IDS)] == FRAME_IDS
    assert len({f.order for f in cat.frames.values()}) == len(cat.frames)


def test_frames_reference_existing_slots_and_cover_every_slot_once() -> None:
    cat = load_catalog()
    counts = Counter(s for f in cat.frames.values() for s in f.slots)
    assert [s for s in counts if s not in cat.slots] == []
    assert [s for s in cat.slots if s not in counts] == []
    assert [s for s, n in counts.items() if n > 1] == []


def test_frame_shape_rules() -> None:
    for frame in load_catalog().frames.values():
        assert frame.max_question_words == 20 and frame.max_option_words == 8, frame.id
        assert frame.guidance.strip() and frame.purpose.strip(), frame.id
        if frame.card_type == "choose_one":
            assert frame.option_count == 3, frame.id
        if frame.card_type in {"pick_many", "priority", "open_prompt", "confirm"}:
            assert frame.option_count is None, frame.id


def test_frames_contain_no_brand_names_or_stack_choices() -> None:
    banned = {"google", "amazon", "uber", "react", "expo", "postgres", "firebase", "aws"}
    for frame in load_catalog().frames.values():
        words = set(re.findall(r"[a-z]+", (frame.purpose + " " + frame.guidance).lower()))
        assert not banned & words, frame.id


# ------------------------------------------------------------------------------------ no_ask


def test_no_ask_list_names_the_forbidden_topics() -> None:
    no_ask = load_catalog().no_ask
    for topic in ("stack_and_framework", "hex_colors", "hosting_and_database", "legal_wording"):
        assert topic in no_ask
    assert len(set(no_ask)) == len(no_ask)


def test_no_slot_asks_for_a_no_ask_topic() -> None:
    cat = load_catalog()
    for slot in cat.slots.values():
        text = (slot.id + " " + slot.fallback_card.question.en).lower()
        assert "hex" not in text and "framework" not in text and "hosting" not in text, slot.id


# ----------------------------------------------------------------------------- failure paths


@pytest.fixture
def broken_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable copy of the catalog the loader reads instead of the real one."""
    target = tmp_path / "catalog"
    shutil.copytree(CATALOG_DIR, target, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    monkeypatch.setattr(loader_module, "CATALOG_DIR", target)
    return target


def _load_fresh() -> None:
    loader_module.load_catalog.__wrapped__()


def test_copy_of_the_catalog_loads(broken_catalog: Path) -> None:
    _load_fresh()


def test_unknown_slot_in_a_frame_is_reported(broken_catalog: Path) -> None:
    path = broken_catalog / "frames" / "01_idea_in_your_words.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["slots"].append("no.such_slot")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(CatalogError, match=r"no\.such_slot"):
        _load_fresh()


def test_unknown_slot_in_ask_if_is_reported(broken_catalog: Path) -> None:
    path = broken_catalog / "slots.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["slots"][1]["ask_if"] = "filled('ghost.slot')"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match=r"ghost\.slot"):
        _load_fresh()


def test_choose_one_with_two_options_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "slots.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    slot = next(s for s in data["slots"] if s["fallback_card"]["type"] == "choose_one")
    slot["fallback_card"]["options"].pop()
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="exactly 3"):
        _load_fresh()


def test_out_of_range_impact_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "slots.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["slots"][0]["impact"] = 9
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="impact"):
        _load_fresh()


def test_missing_hindi_text_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "slots.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["slots"][0]["fallback_card"]["question"]["hi"] = "  "
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="empty text"):
        _load_fresh()


def test_string_key_mismatch_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "strings" / "hi.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"ui.back"', '"ui.backk"'))
    with pytest.raises(CatalogError, match="key mismatch"):
        _load_fresh()


def test_placeholder_mismatch_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "strings" / "hi.json"
    path.write_text(path.read_text(encoding="utf-8").replace("{items}", "{item}"))
    with pytest.raises(CatalogError, match="placeholder mismatch"):
        _load_fresh()


def test_todo_text_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "strings" / "en.json"
    path.write_text(path.read_text(encoding="utf-8").replace("Compass", "TODO", 1))
    with pytest.raises(CatalogError, match="TODO"):
        _load_fresh()


def test_declined_category_without_alternative_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "capabilities.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    declined = next(c for c in data["capabilities"] if c["kind"] == "declined")
    del declined["alternative"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="alternative"):
        _load_fresh()


def test_keywords_in_the_wrong_script_are_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "capabilities.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["capabilities"][0]["keywords"]["en"].append("पुश")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="wrong script"):
        _load_fresh()


def test_duplicate_slot_ids_are_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "slots.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["slots"].append(data["slots"][0])
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="duplicate slot"):
        _load_fresh()


def test_real_catalog_is_unchanged_by_the_failure_tests() -> None:
    assert len(load_catalog().slots) >= 42


def test_todo_in_a_family_feature_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "families.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["families"][0]["typical_features"][0]["intent"] = "TODO write this"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(CatalogError, match="TODO"):
        _load_fresh()


def test_empty_frame_guidance_is_rejected(broken_catalog: Path) -> None:
    path = broken_catalog / "frames" / "01_idea_in_your_words.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["guidance"] = " "
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(CatalogError, match="empty text"):
        _load_fresh()
