"""SPC-CL-01..10: component library and tools (doc 17 section 11.1). The pack states needs and never a library; what a consumer
does with them is the front end's business, so these cases prove the pack gives it what it needs in each situation."""

from __future__ import annotations

import json
from functools import cache

import pytest
from edge_support.ea_support import cached_flow, codes, raw, validate
from edge_support.registry import case

from mde_spec_core.graph import AssetNeed, Region
from mde_spec_core.lint_pack import lint_pack_files
from mde_spec_core.pack import PackOptions, assemble
from mde_spec_core.synth import make_graph
from mde_spec_core.validators.base import Finding


@cache
def small():
    return make_graph("small")


def _needs(graph, **opts):
    return json.loads(
        assemble(graph, PackOptions(**opts) if opts else None).files["global/needs.json"]
    )


def _with_region(shape: str, key: str, *, kind: str | None = None, attrs=()):
    g = small().model_copy(deep=True)
    s = g.screens["SCR-001"]
    s.regions.append(
        Region(
            key=key,
            what="a thing the screen shows",
            shape=shape,
            shape_kind=kind,
            attrs=list(attrs),
            bind=[],
            cardinality="few" if shape == "collection" else None,
        )
    )
    return g


def _names(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings if f.code in ("V-23", "LINT-NAMES")}


# ------------------------------------------------------------------------------------------- CL-01, CL-02, CL-03


@case("spc-cl-01")
def test_the_needs_do_not_depend_on_a_tool_and_the_fallback_policy_is_in_the_pack(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    assert not [x for x in lint_pack_files(f.pack_files) if x.severity == "blocker"]
    assert "V-23" not in codes(validate(f.graph), "blocker")
    assert not [n for n in json.loads(f.pack_files["global/needs.json"]) if "tool" in json.dumps(n)]
    behaviors = f.pack_files["global/behaviors.md"]
    assert "fallback" in behaviors  # a missing or slow asset shows its fallback
    assets = json.loads(f.pack_files["global/assets.json"])
    assert assets and all(
        a["fallback"] for a in assets
    )  # every asset need carries its own fallback
    # the same graph gives the same needs bytes whoever reads it
    a = assemble(f.graph).files["global/needs.json"]
    b = assemble(f.graph, PackOptions(consumer_id="another-tool")).files["global/needs.json"]
    assert a == b == f.pack_files["global/needs.json"]


@case("spc-cl-02")
def test_a_shape_the_library_lacks_is_a_gap_the_consumer_can_list_and_nothing_changes():
    g = _with_region("places", "route", kind="route")
    assert "V-38" not in codes(
        validate(g, pack=assemble(g)), "blocker"
    )  # a known shape: no deviation
    needs = _needs(g)
    catalogue = {n["shape"] for n in needs} - {"places"}  # the consumer's catalogue lacks `places`
    gaps = [n for n in needs if n["shape"] not in catalogue]
    assert [n["kind"] for n in gaps] == ["route"] and "SCR-001.route" in gaps[0]["used_by"]
    # the need itself is untouched by the gap: the same needs without the consumer's view
    assert needs == _needs(g)
    # a shape that is in no vocabulary is written other:<name> and goes to the gap list (info, not a failure)
    h = _with_region("other:hologram", "holo")
    info = [f for f in validate(h, pack=assemble(h), only={"V-38"}) if "gap list" in f.why]
    assert info and all(f.severity == "info" for f in info)


@case("spc-cl-03")
def test_a_missing_attribute_is_a_gap_per_attribute_not_per_shape():
    g = _with_region("collection", "wide", attrs=["columns=4", "sticky_columns=1"])
    g2 = g.model_copy(deep=True)
    g2.screens["SCR-002"].regions.append(
        Region(
            key="plain",
            what="a plain table",
            shape="collection",
            attrs=["columns=4"],
            cardinality="few",
        )
    )
    needs = _needs(g2)
    tables = [
        n for n in needs if n["shape"] == "collection" and any("columns=4" in a for a in n["attrs"])
    ]
    sticky = [n for n in tables if "sticky_columns=1" in n["attrs"]]
    plain = [n for n in tables if "sticky_columns=1" not in n["attrs"]]
    assert (
        sticky and plain and sticky[0]["id"] != plain[0]["id"]
    )  # the attribute makes a need of its own
    # a consumer that cannot make sticky columns has a gap on that attribute only, the shape still maps
    gaps = [n["id"] for n in needs if any(a.startswith("sticky_columns") for a in n["attrs"])]
    assert gaps == [sticky[0]["id"]]
    assert all(n["shape"] == "collection" for n in sticky + plain)


# ------------------------------------------------------------------------------------------- CL-04, CL-05, CL-06


@case("spc-cl-04")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-CL-1: the pack never says the tokens are the starting theme a consumer may override or extend: no file of a full pack contains the words theme, starting, override or extend (doc 17 section 2.6)",
)
def test_the_pack_says_tokens_are_the_starting_theme(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    prose = " ".join(
        t.lower()
        for p, t in f.pack_files.items()
        if p.endswith(".md") and not p.startswith("screens/")
    )
    assert "starting theme" in prose or ("theme" in prose and "override" in prose)


@case("spc-cl-04")
def test_the_tokens_are_role_named_and_ship_in_the_pack_for_any_library_to_theme_from(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    tokens = json.loads(f.pack_files["global/tokens.dtcg.json"])
    names = " ".join(next(iter(tokens.values()))["names"])
    for role in ("color", "font.family", "radius", "space", "motion.duration", "elevation"):
        assert role in names
    assert "V-12" not in codes(validate(f.graph), "blocker")  # contrast met in light and dark


@case("spc-cl-05")
def test_a_library_version_change_changes_no_need():
    g = small()
    base = assemble(g, PackOptions(library_version="1.0.0", consumer_id="a"))
    other = assemble(
        g, PackOptions(library_version="2.0.0", consumer_id="b", route_fingerprint="x")
    )
    assert base.files["global/needs.json"] == other.files["global/needs.json"]
    assert base.needs == other.needs
    assert base.content_hash == other.content_hash  # an approval binds the content, not the tool


@case("spc-cl-06")
def test_two_libraries_attached_the_needs_stay_library_neutral():
    g = small().model_copy(deep=True)
    clean = assemble(g)
    assert not _names(lint_pack_files(clean.files)) and "V-23" not in codes(validate(g), "blocker")
    # a screen that names a library is a blocker (V-23) and the pack linter says so too
    dirty = g.model_copy(deep=True)
    dirty.screens["SCR-001"].purpose = "Use tamagui and react-native-paper for this screen"
    assert "V-23" in codes(validate(dirty), "blocker")
    assert "LINT-NAMES" in _names(lint_pack_files(assemble(dirty).files))
    # the needs are made from shapes, intents and attributes only
    assert all(set(n) >= {"shape", "attrs", "intents", "cardinality"} for n in _needs(g))


# ------------------------------------------------------------------------------------------- CL-07, CL-08, CL-09, CL-10


@case("spc-cl-07")
def test_an_asset_need_without_a_fallback_or_alt_text_rule_is_a_blocker():
    g = small().model_copy(deep=True)
    need = AssetNeed(
        id="ASSET-S900",
        kind="photo",
        purpose="primary_visual",
        subject="a harvest",
        count=1,
        fallback="A neutral placeholder of the same proportions.",
        alt_text_rule="Describe the subject in one sentence.",
        source_policy="generate",
    )
    g.assets[need.id] = need
    assert "V-43" not in codes(validate(g), "blocker")
    for broken in (
        need.model_copy(update={"fallback": ""}),
        need.model_copy(update={"alt_text_rule": ""}),
    ):
        h = g.model_copy(deep=True)
        h.assets[need.id] = broken
        found = [f for f in validate(h, only={"V-43"}) if f.severity == "blocker"]
        assert found and found[0].where == need.id


@case("spc-cl-08")
def test_images_are_user_upload_or_generated_and_placeholders_are_marked_synthetic(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    assets = list(f.graph.assets.values())
    assert assets and {a.source_policy for a in assets} <= {"user_upload", "generate"}
    assert all(a.license_rule for a in assets if a.kind == "brand_mark")
    assert not [a for a in assets if a.source_policy == "licensed_stock"]
    prose = json.dumps(f.pack_files["global/assets.json"]).lower()
    assert "unsplash" not in prose and "pexels" not in prose  # no stock service is named
    assert f.graph.hero and all(h.synthetic for h in f.graph.hero.values())
    assert "V-15" not in codes(validate(f.graph), "blocker")


@case("spc-cl-09")
def test_sixty_hero_visuals_become_one_need_with_a_count_not_sixty_needs():
    from mde_spec_core.assets import asset_rules, data_bound_asset_needs
    from mde_spec_core.graph import FieldDef, HeroRecord

    g = small().model_copy(deep=True)
    ent = next(iter(g.entities.values()))
    ent.fields.append(FieldDef(key="photo", type="file", media=True))
    for i in range(60):
        hid = f"HERO-{ent.id}-{i + 1:02d}"
        g.hero[hid] = HeroRecord(
            id=hid, entity=ent.id, image_brief=f"produce in a harvest setting {i}; avoid: logos"
        )
    needs = [n for n in data_bound_asset_needs(g) if n.used_by[0].startswith(ent.id)]
    assert len(needs) == 1  # grouped by purpose and field, not one per visual
    cap = int(asset_rules()["data_bound_count_cap"])
    assert needs[0].count == min(60, cap) and needs[0].purpose and needs[0].bound


@case("spc-cl-10")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-CL-2: `needs_access_change` exists in no code or data file: a USUALLY or COULD binding that fails the access check is dropped or narrowed (see the 'narrowed away' repair note) and never becomes a suggestion at G2",
)
def test_a_could_or_usually_binding_that_fails_the_access_check_becomes_a_suggestion(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    text = json.dumps([f.g2()["suggestions"], f.check.get("flags", [])])
    assert "needs_access_change" in text
