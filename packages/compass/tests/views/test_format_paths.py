"""Formatting helpers and path helpers; robustness against arbitrary values."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.views.build import build_view_model
from mde_compass.views.consumers import build_handoff
from mde_compass.views.format import (
    FALLBACK_STRINGS,
    Labels,
    badge,
    badge_kinds,
    humanize,
    to_view_value,
)
from mde_compass.views.paths import (
    resolve_item,
    slot_filled,
    slot_for_key,
    view_section_for_path,
)
from mde_compass.views.testing import empty_brief, make_session, rich_brief

CATALOG = load_catalog()
EN = Labels(CATALOG, "en")
HI = Labels(CATALOG, "hi")


def test_value_formatting() -> None:
    assert to_view_value("Pune", EN).kind == "text"
    assert to_view_value(None, EN).kind == "empty" and to_view_value("", EN).kind == "empty"
    assert to_view_value([], EN).kind == "empty" and to_view_value({}, EN).kind == "empty"
    assert to_view_value(["a", "b"], EN).kind == "chips"
    assert to_view_value(True, EN).text == "Yes" and to_view_value(False, HI).text == "नहीं"
    assert to_view_value(12, EN).text == "12"
    mixed = to_view_value([{"name": "Sheet", "kind": "csv"}, "plain", ["x", "y"]], EN)
    assert mixed.kind == "list" and mixed.items == ["Sheet · csv", "plain", "x, y"]
    geo = to_view_value({"country": "India", "city": "Pune"}, EN, CATALOG.slots["context.geo"])
    assert geo.text == "Pune, India"


def test_enum_values_use_the_catalog_option_labels_in_both_languages() -> None:
    slot = CATALOG.slots["look.tone_words"]
    en = to_view_value(["calm_simple"], EN, slot).items
    hi = to_view_value(["calm_simple"], HI, slot).items
    assert en == ["Calm and simple"] and hi != en and hi[0] != "calm_simple"
    assert to_view_value("some_new_token", EN, slot).text == "Some new token"  # humanized fallback


def test_catalog_key_wins_over_fallback() -> None:
    key = "view.untitled"
    assert key in FALLBACK_STRINGS and EN.t(key) == "Untitled project"
    en = {**CATALOG.strings["en"], key: "From the catalog"}
    hi = {**CATALOG.strings["hi"], key: "कैटलॉग से"}
    patched = CATALOG.model_copy(update={"strings": {"en": en, "hi": hi}})
    assert Labels(patched, "en").t(key) == "From the catalog"
    assert Labels(patched, "hi").t(key) == "कैटलॉग से"
    assert EN.t("no.such.key") == "no.such.key"


def test_every_fallback_string_has_english_and_hindi() -> None:
    for key, texts in FALLBACK_STRINGS.items():
        assert texts["en"].strip() and texts["hi"].strip(), key
        assert texts["en"].count("{value}") == texts["hi"].count("{value}"), key


def test_badge_builder_and_kinds() -> None:
    b = badge("assumed", HI)
    assert b.text == CATALOG.t("badge.assumed", "hi") and b.icon == "▲"
    p = [Provenance(type="voice", ref="evt_x"), Provenance(type="user_tap", ref="evt_y")]
    assert badge_kinds(p) == ["you_said"]  # distinct
    assert badge_kinds([], status="assumed") == ["assumed"]
    assert badge_kinds([], status="confirmed") == []
    assert badge_kinds([Provenance(type="org_profile", ref="x")]) == ["from_document"]


def test_humanize() -> None:
    assert humanize("calm_simple") == "Calm simple" and humanize("") == ""


def test_slot_mapping_to_the_brief() -> None:
    brief = rich_brief()
    assert slot_for_key(CATALOG, "context", "geo") is CATALOG.slots["context.geo"]
    assert slot_for_key(CATALOG, "experience", "brand.name") is CATALOG.slots["brand.name"]
    assert slot_for_key(CATALOG, "experience", "nonsense") is None
    for slot_id in (
        "context.geo",
        "brand.name",
        "look.tone_words",
        "auth.methods",
        "apps.list",
        "scope.features",
    ):
        assert slot_filled(brief, CATALOG.slots[slot_id]), slot_id
    for slot_id in ("context.geo", "brand.name", "apps.list", "scope.features", "glossary.terms"):
        assert not slot_filled(empty_brief(), CATALOG.slots[slot_id]), slot_id


def test_view_section_for_path() -> None:
    assert view_section_for_path("context.geo") == "who"
    assert view_section_for_path("context.scale_launch") == "idea"
    assert view_section_for_path("scope.FTR-001.priority") == "features"
    assert view_section_for_path("research_requests.RR-001") == "lookup"
    assert view_section_for_path("bogus") is None


def test_resolve_item_paths() -> None:
    brief = rich_brief()
    assert resolve_item(brief, "scope.FTR-001.tier") is not None
    assert resolve_item(brief, "experience.brand.name.tier") is not None
    assert resolve_item(brief, "integrations.payments") is not None
    assert resolve_item(brief, "declined.Drone delivery") is not None
    assert resolve_item(brief, "scope.FTR-001.bogus") is None


_json = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=6), children, max_size=4)
    ),
    max_leaves=12,
)


@settings(max_examples=60, deadline=None)
@given(value=_json, key=st.text(min_size=1, max_size=12), lang=st.sampled_from(["en", "hi", "xx"]))
def test_arbitrary_item_values_never_crash_the_view_or_the_handoff(
    value: JsonValue, key: str, lang: str
) -> None:
    brief = rich_brief()
    for section in (
        brief.idea,
        brief.context,
        brief.audience,
        brief.experience,
        brief.access,
        brief.data,
        brief.constraints,
    ):
        section[key] = BriefItem(value=value, status="assumed")
    vm = build_view_model(
        brief, catalog=CATALOG, language=lang, session=make_session("PROBING"), blockers=[]
    )
    assert len(vm.sections) == 14
    build_handoff(brief)
