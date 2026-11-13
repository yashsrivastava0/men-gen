"""Asset needs by code (doc 17 2.7, doc 18 5 B4): data-bound from media fields and hero briefs, unbound brand needs."""

import random

from mde_spec_core import vocab
from mde_spec_core.assets import (
    asset_needs,
    asset_rules,
    data_bound_asset_needs,
    lint_brief,
    missing_media_needs,
    unbound_asset_needs,
)
from mde_spec_core.graph import FieldDef, Region
from mde_spec_core.hero import mint_hero_skeleton
from mde_spec_core.synth import make_graph
from mde_spec_core.validators import Context, run_all


def _media_graph():
    g = make_graph("small")
    g.entities["ENT-001"].fields += [
        FieldDef(key="cover_photo", type="file", media=True),
        FieldDef(key="seller_avatar", type="file", media=True),
    ]
    g.screens["SCR-001"].regions.append(
        Region(key="cover", what="how the item looks", shape="media", bind=["ENT-001.cover_photo"])
    )
    return g


def test_one_need_per_media_field_with_kind_from_the_field_key():
    g = _media_graph()
    needs = data_bound_asset_needs(g)
    media = [(e.id, f.key) for e in g.entities.values() for f in e.fields if f.media]
    assert len(needs) == len(media)
    by_ref = {n.used_by[0]: n for n in needs}
    assert by_ref["ENT-001.cover_photo"].kind == "photo"
    assert by_ref["ENT-001.seller_avatar"].kind == "avatar"
    assert all(n.bound for n in needs)
    assert len({n.id for n in needs}) == len(needs)
    assert all(
        n.kind in vocab.asset_kinds() and vocab.asset_kinds()[n.kind].data_bound_capable
        for n in needs
    )
    assert all(n.purpose in vocab.asset_kinds_file().purposes for n in needs)
    assert all(n.source_policy in vocab.asset_kinds()[n.kind].source_policies for n in needs)


def test_a_need_names_the_screens_that_bind_the_field():
    g = _media_graph()
    need = next(n for n in data_bound_asset_needs(g) if n.used_by[0] == "ENT-001.cover_photo")
    assert need.used_by == ["ENT-001.cover_photo", "SCR-001"]


def test_hero_briefs_give_subject_count_and_avoid_list():
    g = _media_graph()
    g.hero = mint_hero_skeleton(g, random.Random(1))
    recs = [r for r in g.hero.values() if r.entity == "ENT-001"]
    for r in recs:
        r.image_brief = "ripe tomatoes in a crate; count: 6; avoid: price tags, brand marks"
    need = next(n for n in data_bound_asset_needs(g) if n.used_by[0] == "ENT-001.cover_photo")
    assert need.subject == "ripe tomatoes in a crate"
    assert "price tags" in need.avoid and "brand marks" in need.avoid
    assert need.count == asset_rules()["data_bound_count_cap"] == 6
    assert need.fallback and need.alt_text_rule and need.license_rule


def test_without_briefs_the_subject_comes_from_the_entity_and_field_names():
    g = _media_graph()
    need = next(n for n in data_bound_asset_needs(g) if n.used_by[0] == "ENT-001.cover_photo")
    assert need.subject == f"{g.entities['ENT-001'].name}: cover photo"
    assert need.avoid and need.count >= 1


def test_no_need_states_lighting_placement_crop_size_or_variant():
    g = _media_graph()
    g.hero = mint_hero_skeleton(g, random.Random(1))
    for n in asset_needs(g):
        assert lint_brief(n.subject, *n.avoid) == [], n.id
    assert {"golden hour", "crop", "16:9"} <= set(
        lint_brief("golden hour light, centered, 16:9 crop")
    )
    assert lint_brief("ripe tomatoes in a crate") == []


def test_v41_media_check_passes_once_the_needs_are_added():
    g = _media_graph()
    before = run_all(Context(g), only={"V-41"})
    g.hero = mint_hero_skeleton(g, random.Random(1))
    assert missing_media_needs(g, []) and missing_media_needs(g, data_bound_asset_needs(g)) == []
    for n in data_bound_asset_needs(g):
        g.assets[n.id] = n
    after = [f for f in run_all(Context(g), only={"V-41"}) if "media field" in f.why]
    assert before and after == []


def test_ids_continue_after_existing_assets():
    g = _media_graph()
    first = data_bound_asset_needs(g)
    g.assets[first[0].id] = first[0]
    again = data_bound_asset_needs(g)
    assert again[0].id != first[0].id
    assert int(again[0].id.rsplit("S", 1)[1]) == int(first[0].id.rsplit("S", 1)[1]) + 1


def test_unbound_brand_needs_are_brand_marks_per_surface():
    g = make_graph("medium")  # four apps, the last one a web portal
    needs = unbound_asset_needs(g)
    phone_apps = [a for a in g.apps.values() if a.surface != "web"]
    web_apps = [a for a in g.apps.values() if a.surface == "web"]
    assert len(needs) == 4 * len(phone_apps) + len(web_apps)
    assert all(n.kind == "brand_mark" and not n.bound and n.count == 1 for n in needs)
    subjects = " | ".join(n.subject for n in needs)
    assert (
        "app icon" in subjects
        and "adaptive icon" in subjects
        and "launch screen" in subjects
        and "brand mark" in subjects
    )
    assert all(lint_brief(n.subject, *n.avoid) == [] for n in needs)
    assert all(n.used_by[0] in g.apps for n in needs)
    assert {n.source_policy for n in needs} == {"generate"}
    assert {n.source_policy for n in unbound_asset_needs(g, has_logo=True)} == {"user_upload"}


def test_combined_needs_have_consecutive_unique_ids_and_are_deterministic():
    g = _media_graph()
    needs = asset_needs(g)
    nums = [int(n.id.rsplit("S", 1)[1]) for n in needs]
    assert nums == list(range(nums[0], nums[0] + len(nums)))
    assert [n.model_dump() for n in needs] == [n.model_dump() for n in asset_needs(g)]
    assert [n.bound for n in needs] == sorted((n.bound for n in needs), reverse=True)


def test_a_graph_without_media_fields_has_only_brand_needs():
    g = make_graph("tiny")
    for e in g.entities.values():
        for f in e.fields:
            f.media = False
    assert data_bound_asset_needs(g) == []
    assert all(not n.bound for n in asset_needs(g))
