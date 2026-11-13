"""Tokens by code (doc 18 5 A4; FN-27, FN-28): contrast asserted in light and dark, brand colour Locked, directions that differ."""

import random

import pytest

from mde_spec_core import tokens as tk
from mde_spec_core.canon import canonical_json
from mde_spec_core.config import default_config
from mde_spec_core.graph import Direction
from mde_spec_core.synth import make_graph
from mde_spec_core.tokens import (
    TokenContrastError,
    build_graph_tokens,
    build_tokens,
    check_direction_set,
    contrast_ratio,
    direction_fields,
    pick_directions,
    token_names,
)
from mde_spec_core.validators import Context, run_all

NEED = default_config().checks.wcag_body_text_ratio


def _direction(hue=250.0, **kw):
    base = {
        "id": "DIR-001",
        "name": "d",
        "mood": "calm_trust",
        "seed_hue": hue,
        "motion_personality": "calm",
        "depth": "soft",
    }
    return Direction(**{**base, **kw})


def _resolve(result, name):
    """Follow `{ref}` values of the DTCG tree to a final hex (independent of the builder's own bookkeeping)."""
    node = result["tree"]
    for part in name.split("."):
        node = node[part]
    val = node["$value"]
    while isinstance(val, str) and val.startswith("{"):
        node = result["tree"]
        for part in val[1:-1].split("."):
            node = node[part]
        val = node["$value"]
    return val


def _wcag(fg: str, bg: str) -> float:
    def lum(h: str) -> float:
        rgb = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------ contrast


@pytest.mark.parametrize("seed", range(20))
def test_every_body_pair_reaches_4_5_in_light_and_dark(seed):
    rnd = random.Random(seed)
    r = build_tokens(_direction(hue=rnd.uniform(0, 360)))
    body = [p for p in r["contrast"] if p["body"]]
    assert {p["scheme"] for p in body} == {"light", "dark"}
    assert all(p["ratio"] >= NEED for p in body)
    # recomputed independently from the tree values, not from the builder's numbers
    for p in body:
        exact = _wcag(_resolve(r, p["fg"]), _resolve(r, p["bg"]))
        assert exact >= NEED
        assert abs(exact - p["ratio"]) < 0.011
        assert abs(contrast_ratio(_resolve(r, p["fg"]), _resolve(r, p["bg"])) - exact) < 0.01


@pytest.mark.parametrize(
    "brand",
    ["#ffd60a", "#ffff00", "#7fff00", "#f5a623", "#ffffff", "#00e5ff", "#000000", "#1a3a8a"],
)
def test_brand_colours_stay_locked_and_text_on_them_is_derived(brand):
    r = build_tokens(_direction(), brand_colour=brand)
    for scheme in ("light", "dark"):
        assert _resolve(r, f"color.scheme.{scheme}.brand") == brand
        on = _resolve(r, f"color.scheme.{scheme}.on_brand")
        assert _wcag(on, brand) >= NEED
    assert r["locked_roles"] == ["color.scheme.dark.brand", "color.scheme.light.brand"]
    rep = r["report"]
    assert rep["brand_locked"] is True
    derived = rep["derived_text_on_brand"]
    assert derived["ratio"] >= NEED
    white = _wcag("#ffffff", brand)
    assert abs(derived["ratio_with_white"] - white) < 0.011
    assert derived["changed_from_white"] == (white < NEED)
    assert any(brand in s for s in rep["statements"])
    assert all(p["ratio"] >= NEED for p in r["contrast"] if p["body"])


def test_a_brand_colour_that_fails_with_white_says_so_in_plain_words():
    r = build_tokens(_direction(), brand_colour="#ffd60a")
    assert r["report"]["derived_text_on_brand"]["changed_from_white"] is True
    text = " ".join(r["report"]["statements"])
    assert "1.41" in text and "stays exactly as you gave it" in text
    assert (
        "brand_on_surface_below_3.0_light" in r["report"]["flags"]
    )  # honest: the Locked colour is weak on white


def test_no_brand_colour_means_nothing_is_locked_and_the_seed_is_derived():
    r = build_tokens(_direction())
    assert r["locked_roles"] == []
    assert r["report"]["derived_text_on_brand"] is None
    assert r["report"]["seed"]["source"] == "direction_hue"
    d = Direction(id="DIR-009", name="x", mood="calm_trust")
    r2 = build_tokens(d)
    assert r2["report"]["seed"]["source"] == "derived"  # FN-28
    assert r2["report"]["statements"]


def test_a_bad_colour_is_refused_with_a_plain_error():
    with pytest.raises(tk.TokenError):
        build_tokens(_direction(), brand_colour="not a colour")


def test_a_failing_pair_is_a_loud_error(monkeypatch):
    monkeypatch.setattr(tk, "contrast_ratio", lambda a, b: 2.0)
    with pytest.raises(TokenContrastError):
        build_tokens(_direction())


# ------------------------------------------------------------------ determinism and shape


def test_same_inputs_same_bytes():
    a = build_tokens(_direction(), brand_colour="#336699", scripts=("Latin", "Devanagari"))
    b = build_tokens(_direction(), brand_colour="#336699", scripts=("Latin", "Devanagari"))
    assert canonical_json(a) == canonical_json(b)
    c = build_tokens(_direction(hue=251.0))
    assert canonical_json(a) != canonical_json(c)


def test_the_result_has_the_shape_v12_reads():
    r = build_tokens(_direction())
    assert {"names", "refs", "contrast", "tree", "locked_roles"} <= set(r)
    assert set(r["names"]) == set(token_names(r["tree"]))
    assert set(r["refs"]) <= set(r["names"])
    assert r["refs"]
    for p in r["contrast"]:
        assert {"fg", "bg", "ratio", "scheme", "body"} <= set(p)
        assert p["fg"] in r["names"] and p["bg"] in r["names"]


def test_v12_accepts_built_tokens():
    g = make_graph("tiny")
    d = _direction()
    g.directions[d.id] = d
    g.tokens[d.id] = build_tokens(d)
    findings = [f for f in run_all(Context(g), only={"V-12"}) if f.severity == "blocker"]
    assert findings == []


def test_ext_namespace_springs_and_blur_are_never_set():
    r = build_tokens(_direction(motion_personality="springy", depth="glass"))
    assert "ext" not in r["tree"]
    for name in r["names"]:
        assert not name.startswith("ext.")
        assert "spring" not in name and "blur_preset" not in name
    # blur appears only as a shadow's own radius, never as a preset token
    assert not any(n.startswith("blur") for n in r["names"])


def test_semantic_state_dataviz_elevation_motion_type_spacing_radius_are_present():
    r = build_tokens(_direction())
    names = set(r["names"])
    for scheme in ("light", "dark"):
        for role in (
            "success",
            "warning",
            "error",
            "info",
            "on_success",
            "pressed",
            "focused",
            "disabled_content",
            "disabled_container",
            "selected",
            "on_selected",
            "surface",
            "on_surface",
        ):
            assert f"color.scheme.{scheme}.{role}" in names
        assert {f"color.dataviz.{scheme}.{i}" for i in range(1, 9)} <= names
    assert {f"elevation.level.{i}" for i in range(6)} <= names
    assert {
        "motion.duration.fast",
        "motion.duration.reduced",
        "motion.easing.standard",
        "motion.easing.default",
    } <= names
    assert {
        "font.size.body",
        "font.size.display",
        "type.body",
        "space.inset.md",
        "radius.control",
        "size.touch_target.min",
    } <= names
    assert "motion.easing.standard" in r["refs"] or "motion.easing.default" in names
    assert "state.opacity.pressed" in names


def test_every_dataviz_colour_passes_3_to_1_against_its_surfaces_and_stays_distinct():
    for hue in (20, 130, 250, 310):
        r = build_tokens(_direction(hue=hue))
        pairs = [p for p in r["contrast"] if p["fg"].startswith("color.dataviz")]
        assert pairs and all(p["ratio"] >= 3.0 for p in pairs)
        assert r["report"]["dataviz_min_delta_e"] >= tk.token_tables()["dataviz"]["min_delta_e"]


def test_motion_elevation_and_radius_follow_the_personality():
    calm = build_tokens(_direction(motion_personality="calm", depth="flat", shape="sharp"))
    brisk = build_tokens(_direction(motion_personality="brisk", depth="dramatic", shape="pill"))
    assert (
        calm["tree"]["motion"]["duration"]["base"]["$value"]
        != brisk["tree"]["motion"]["duration"]["base"]["$value"]
    )
    assert calm["tree"]["elevation"]["level"]["3"]["$value"]["offsetY"] == "0px"  # flat: no shadow
    assert brisk["tree"]["elevation"]["level"]["3"]["$value"]["offsetY"] != "0px"
    assert calm["tree"]["radius"]["control"]["$value"] == "{radius.md}"
    assert brisk["tree"]["radius"]["control"]["$value"] == "{radius.full}"


def test_type_scale_grows_and_body_is_16():
    r = build_tokens(_direction(type_personality="editorial"))
    size = lambda n: int(r["tree"]["font"]["size"][n]["$value"][:-2])  # noqa: E731
    assert size("body") == 16
    assert size("display") > size("headline") > size("title") > size("body") > size("caption") >= 11


# ------------------------------------------------------------------ script fallbacks


def test_script_fallback_families_come_from_the_data_table():
    r = build_tokens(_direction(), scripts=("Latin", "Devanagari", "Kannada", "Arabic"))
    fam = r["tree"]["font"]["script"]
    assert fam["devanagari"]["$value"][0] == "Noto Sans Devanagari"
    assert fam["kannada"]["$value"][0] == "Noto Sans Kannada"
    assert fam["arabic"]["$value"][0] == "Noto Sans Arabic"
    rows = {s["script"]: s for s in r["report"]["scripts"]}
    assert rows["Arabic"]["rtl"] is True and rows["Latin"]["source"] == "table"
    assert "Noto Sans" in r["tree"]["font"]["family"]["body"]["$value"]


def test_missing_script_table_gives_a_latin_only_stack_with_a_flag(monkeypatch):
    def boom():
        raise FileNotFoundError("script_fallbacks.yaml")

    monkeypatch.setattr(tk, "_script_font_sheet", boom)
    r = build_tokens(_direction(), scripts=("Latin", "Kannada"))
    assert "script_fallbacks_missing" in r["report"]["flags"]
    assert set(r["tree"]["font"]["script"]) == {"latin", "kannada"}
    assert r["tree"]["font"]["script"]["kannada"]["$value"] == ["system-ui", "sans-serif"]
    assert all(p["ratio"] >= NEED for p in r["contrast"] if p["body"])


def test_an_unverified_script_is_flagged_not_invented():
    r = build_tokens(_direction(), scripts=("Latin", "Klingon"))
    assert "script_unverified:Klingon" in r["report"]["flags"]


# ------------------------------------------------------------------ choosing directions


@pytest.mark.parametrize("seed", range(12))
def test_three_directions_differ_on_two_axes_and_sixty_degrees(seed):
    cfg = default_config()
    picks = pick_directions(3, brand_locked=False, rnd=random.Random(seed))
    assert len(picks) == 3
    assert check_direction_set(picks, brand_locked=False) == []
    hues = [p["seed_hue"] for p in picks]
    for i in range(3):
        for j in range(i + 1, 3):
            gap = abs(hues[i] - hues[j]) % 360
            assert min(gap, 360 - gap) >= cfg.checks.min_seed_hue_gap_degrees


def test_with_a_locked_brand_colour_the_hue_gap_does_not_apply():
    picks = pick_directions(3, brand_locked=True, rnd=random.Random(1))
    assert all(p["seed_hue"] is None for p in picks)
    assert check_direction_set(picks, brand_locked=True) == []


def test_a_bad_set_is_reported():
    a = {"mood_family": "calm_trust", "shape": "sharp", "seed_hue": 10.0}
    b = {"mood_family": "calm_trust", "shape": "sharp", "seed_hue": 40.0}
    problems = check_direction_set([a, b], brand_locked=False)
    assert len(problems) == 2


def test_pick_is_deterministic_and_respects_a_narrowed_axis():
    a = pick_directions(3, rnd=random.Random(5))
    assert a == pick_directions(3, rnd=random.Random(5))
    narrowed = pick_directions(2, axes={"shape": ["sharp", "pill"]}, rnd=random.Random(5))
    assert {p["shape"] for p in narrowed} <= {"sharp", "pill"}
    assert len(pick_directions(1, rnd=random.Random(5))) == 1


def test_too_many_directions_for_the_hue_gap_is_refused():
    with pytest.raises(ValueError):
        pick_directions(7, brand_locked=False, rnd=random.Random(1))


def test_picked_directions_build_into_tokens_that_pass_contrast():
    picks = pick_directions(3, rnd=random.Random(3))
    for i, p in enumerate(picks):
        fields = direction_fields(p)
        d = Direction(id=f"DIR-{i + 1:03d}", name=f"d{i}", **fields)
        r = build_tokens(d)
        assert all(x["ratio"] >= NEED for x in r["contrast"] if x["body"])
    assert "mood" in direction_fields(picks[0]) and "mood_family" not in direction_fields(picks[0])


def test_graph_tokens_use_a_locked_seed_colour_as_the_brand():
    g = make_graph("tiny")
    g.meta.languages = ["en", "kn"]
    g.directions["DIR-001"] = _direction(seed_colour="#ffd60a", tier="locked")
    g.directions["DIR-002"] = Direction(
        id="DIR-002", name="b", mood="warm_friendly", seed_hue=100.0
    )
    out = build_graph_tokens(g)
    assert out["DIR-001"]["report"]["brand_locked"] is True
    assert out["DIR-002"]["report"]["brand_locked"] is False
    assert "kannada" in out["DIR-001"]["tree"]["font"]["script"]
