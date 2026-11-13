import re
from pathlib import Path

import pytest

from mde_spec_core import platform_data
from mde_spec_core.platform_data import (
    default_capability_sheet,
    default_delight_patterns,
    default_script_fonts,
    load_capability_sheet,
)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- capability sheet
def test_capability_sheet_loads_with_stack_versions():
    sheet = default_capability_sheet()
    assert sheet.stack.expo_sdk_read == "58.0.0"
    assert sheet.stack.react_native_read.startswith("0.88")
    assert (
        sheet.stack.project_stack_kit_pinned is False
    )  # the Stack Kit is unpinned: recorded, not assumed
    assert sheet.omitted_unverified


def test_every_capability_has_source_date_and_tier_a_b_or_c():
    for c in default_capability_sheet().capabilities:
        assert c.tier in {"A", "B", "C"}, c.id
        assert c.source_url.strip(), c.id
        assert ISO_DATE.match(c.checked_on), c.id
        assert len(c.note) > 20, c.id


def test_capability_ids_unique_and_requested_set_present():
    sheet = default_capability_sheet()
    ids = [c.id for c in sheet.capabilities]
    assert len(ids) == len(set(ids))
    wanted = {
        "haptics", "shared_element_transitions", "canvas_2d_graphics", "vector_animation_clips",
        "biometrics", "passkeys", "wallet_payments", "dynamic_type", "predictive_back", "edge_to_edge",
        "glass_materials", "live_activities", "home_screen_widgets", "background_location", "nfc",
        "bluetooth", "push_remote", "camera", "maps", "offline_storage", "in_app_purchases",
    }  # fmt: skip
    assert wanted <= set(ids)


def test_tier_semantics_hold():
    sheet = default_capability_sheet()
    assert sheet.tier_of("camera") == "A"
    assert sheet.tier_of("nfc") == "B"
    assert sheet.tier_of("real_time_game_engine") == "C"
    for c in sheet.by_tier("B"):
        # a tier B entry is never claimed to run in Expo Go without caveat
        assert c.expo_go is not True or c.id in {"shared_element_transitions"}, c.id
    experimental = sheet.get("shared_element_transitions")
    assert experimental.production_recommended is False


def test_tier_outside_abc_is_refused():
    import yaml

    from mde_spec_core.platform_data import CAPABILITY_PATH

    raw = yaml.safe_load(CAPABILITY_PATH.read_text(encoding="utf-8"))
    raw["capabilities"][0]["tier"] = "D"
    with pytest.raises(ValueError):
        platform_data.CapabilitySheet.model_validate(raw)
    raw["capabilities"][0]["tier"] = "A"
    raw["capabilities"].append(dict(raw["capabilities"][0]))
    with pytest.raises(ValueError):
        platform_data.CapabilitySheet.model_validate(raw)


def test_load_from_explicit_path(tmp_path):
    from mde_spec_core.platform_data import CAPABILITY_PATH

    p = tmp_path / "c.yaml"
    p.write_text(CAPABILITY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_capability_sheet(p).capabilities


# ---------------------------------------------------------------- delight patterns
BANNED = [
    "lottie", "reanimated", "skia", "widget", "layout", "carousel", "modal", "bottom sheet",
    "button", "card", "toast", "snackbar", "tab bar", "grid", "flexbox", "column", "row",
    "svg", "css", "gradient", "scrollview", "flatlist",
]  # fmt: skip


def _words(p) -> str:
    return " ".join([p.name, p.moment, p.intent, p.effect, p.reduced_motion_alternative]).lower()


def test_delight_library_size_and_shape():
    lib = default_delight_patterns()
    assert 15 <= len(lib.patterns) <= 22
    ids = [p.id for p in lib.patterns]
    assert len(ids) == len(set(ids))
    for p in lib.patterns:
        assert p.tier in {"A", "B", "C"}, p.id
        assert ISO_DATE.match(p.checked_on), p.id
        assert p.basis, p.id
        for b in p.basis:
            assert b.source_url.startswith("https://"), p.id
            assert b.says.strip(), p.id
        assert p.reduced_motion_alternative.strip(), p.id


def test_delight_text_names_no_layout_widget_or_library():
    for p in default_delight_patterns().patterns:
        text = _words(p)
        for word in BANNED:
            assert re.search(rf"\b{re.escape(word)}\b", text) is None, (p.id, word)


def test_delight_tier_matches_the_capability_it_needs():
    caps = default_capability_sheet()
    for p in default_delight_patterns().patterns:
        if p.capability_ref is None:
            assert p.tier == "A", p.id
            continue
        cap = caps.get(p.capability_ref)  # raises KeyError when the reference dangles
        assert p.tier == cap.tier or (p.tier == "A" and cap.tier == "A"), (p.id, cap.id)


# ---------------------------------------------------------------- fonts
REQUIRED_SCRIPTS = [
    "Latin", "Devanagari", "Kannada", "Tamil", "Telugu", "Bengali", "Gujarati", "Gurmukhi",
    "Malayalam", "Arabic", "Hebrew", "Thai", "CJK", "Cyrillic",
]  # fmt: skip


def test_every_requested_script_has_a_free_licensed_family():
    sheet = default_script_fonts()
    assert {s.script for s in sheet.scripts} >= set(REQUIRED_SCRIPTS)
    for s in sheet.scripts:
        assert s.family.startswith("Noto "), s.script
        assert s.licence == "OFL-1.1", s.script
        assert s.source_url.startswith("https://"), s.script
        assert ISO_DATE.match(s.checked_on), s.script
        assert s.licence_url.endswith("OFL.txt"), s.script


def test_right_to_left_is_only_arabic_and_hebrew():
    rtl = {s.script for s in default_script_fonts().scripts if s.rtl}
    assert rtl == {"Arabic", "Hebrew"}


def test_cjk_has_one_family_per_region():
    sheet = default_script_fonts()
    assert sheet.family_for("cjk", "Jpan") == "Noto Sans JP"
    assert sheet.family_for("CJK", "Kore") == "Noto Sans KR"
    assert sheet.family_for("Kannada") == "Noto Sans Kannada"
    with pytest.raises(KeyError):
        sheet.family_for("Klingon")


def test_platform_data_code_names_no_model_vendor():
    text = Path(platform_data.__file__).read_text(encoding="utf-8").lower()
    for name in ("openai", "anthropic", "claude", "gemini", "gpt-"):
        assert name not in text
