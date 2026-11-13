"""Tokens by code (doc 18 5 A4, doc 17 2.6, FN-27, FN-28): the model picks closed-set axes and a seed colour;
code builds the DTCG tokens of one design direction, computes the real WCAG contrast of every text-on-surface
pair and ASSERTS it. No model, no network. The same inputs always give the same bytes.

What is built: tonal ramps and the light and dark schemes (HCT through coloraide), semantic colours and state
roles, a data-visualisation palette, elevation, motion durations and named easings, the type scale with per-script
fallback families, spacing and radius. Spring and blur presets and `ext.*` are the front end's and are never set.

The result carries the shape V-12 reads: `names`, `refs`, `contrast` (fg, bg, ratio, scheme, body), `tree`,
`locked_roles`. A brand colour the user gave stays Locked; the text on it is DERIVED to pass 4.5:1 and the report
says so (FN-27). All tables are data: `data/pools/token_tables.yaml`.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

import yaml
from coloraide.everything import ColorAll as Color

from . import vocab
from .config import DATA_DIR, SpecConfig, default_config

TABLES_PATH = DATA_DIR / "pools" / "token_tables.yaml"
SCHEMES = ("light", "dark")
GROUPS = ("primary", "secondary", "tertiary", "error", "success", "warning", "info")
_SEMANTIC = ("error", "success", "warning", "info")


class TokenError(ValueError):
    """The seed or an input could not be turned into tokens."""


class TokenContrastError(TokenError):
    """A pair that must pass contrast does not (a Spec bug: tokens are generated, never hand-edited)."""


@cache
def token_tables() -> dict[str, Any]:
    return yaml.safe_load(TABLES_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ colour helpers


@cache
def hct_hex(hue: float, chroma: float, tone: float) -> str:
    """The sRGB hex of an HCT colour; chroma is lowered at the same tone until the colour is in gamut."""
    hue = round(hue % 360.0, 2)
    chroma = round(max(chroma, 0.0), 2)
    if tone <= 0:
        return "#000000"
    if tone >= 100:
        return "#ffffff"

    def in_gamut(c: float) -> bool:
        return bool(Color("hct", [hue, c, tone]).convert("srgb").in_gamut(tolerance=0.0005))

    if not in_gamut(chroma):
        lo, hi = 0.0, chroma
        for _ in range(22):
            mid = (lo + hi) / 2
            if in_gamut(mid):
                lo = mid
            else:
                hi = mid
        chroma = lo
    return str(Color("hct", [hue, chroma, tone]).convert("srgb").fit("srgb").to_string(hex=True))


def to_hex(colour: str) -> str:
    """Any CSS colour string to a lower-case `#rrggbb`."""
    try:
        return str(Color(colour).convert("srgb").fit("srgb").to_string(hex=True)).lower()
    except Exception as exc:  # coloraide raises several kinds for bad input
        raise TokenError(f"not a colour: {colour!r}") from exc


def hex_to_hct(colour: str) -> tuple[float, float, float]:
    h, c, t = Color(to_hex(colour)).convert("hct").coords(nans=False)
    return float(h) % 360.0, float(c), float(t)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two opaque colours (coloraide)."""
    return float(Color(fg).contrast(Color(bg)))


def _floor2(x: float) -> float:
    return math.floor(x * 100) / 100


def _circular_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ------------------------------------------------------------------ the seed


@dataclass(frozen=True)
class Seed:
    hex: str
    hue: float
    chroma: float
    tone: float
    source: str  # brand | direction_colour | direction_hue | derived


def _get(direction: Any, key: str, default: Any = None) -> Any:
    if isinstance(direction, Mapping):
        return direction.get(key, default)
    return getattr(direction, key, default)


def seed_from_hue(hue: float) -> str:
    t = token_tables()
    return hct_hex(hue, float(t["seed_chroma_default"]), float(t["seed_tone_default"]))


def resolve_seed(direction: Any, brand_colour: str | None = None) -> Seed:
    """Brand colour (Locked) first, then the direction's seed colour, then its seed hue, then a hue derived
    from the direction's id and name (FN-28: the user may change the seed at G2)."""
    t = token_tables()
    if brand_colour:
        hx = to_hex(brand_colour)
        h, c, tn = hex_to_hct(hx)
        return Seed(hx, h, c, tn, "brand")
    colour = _get(direction, "seed_colour")
    if colour:
        hx = to_hex(colour)
        h, c, tn = hex_to_hct(hx)
        return Seed(hx, h, c, tn, "direction_colour")
    hue = _get(direction, "seed_hue")
    source = "direction_hue"
    if hue is None:
        key = f"{_get(direction, 'id', '')}|{_get(direction, 'name', '')}"
        hue = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 360
        source = "derived"
    hue = float(hue) % 360.0
    hx = seed_from_hue(hue)
    h, c, tn = hex_to_hct(hx)
    return Seed(hx, hue, float(t["seed_chroma_default"]), tn, source)


# ------------------------------------------------------------------ the token tree


class _Tree:
    """Collects leaf tokens by dotted path, with their resolved value, and builds the nested DTCG tree."""

    def __init__(self) -> None:
        self.leaves: dict[str, dict[str, Any]] = {}
        self.refs: set[str] = set()
        self.resolved: dict[str, Any] = {}

    def add(
        self,
        path: str,
        type_: str,
        value: Any,
        *,
        ref: str | None = None,
        resolved: Any = None,
        description: str | None = None,
    ) -> None:
        if path in self.leaves:
            raise TokenError(f"duplicate token {path}")
        leaf: dict[str, Any] = {"$type": type_, "$value": f"{{{ref}}}" if ref else value}
        if description:
            leaf["$description"] = description
        self.leaves[path] = leaf
        if ref:
            self.refs.add(ref)
            self.resolved[path] = self.resolved.get(ref, resolved)
        else:
            self.resolved[path] = value if resolved is None else resolved

    def add_composite(self, path: str, type_: str, value: dict[str, Any]) -> None:
        for v in value.values():
            if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                self.refs.add(v[1:-1])
        self.leaves[path] = {"$type": type_, "$value": value}

    def nested(self) -> dict[str, Any]:
        root: dict[str, Any] = {}
        for path in sorted(self.leaves):
            node = root
            *groups, last = path.split(".")
            for g in groups:
                node = node.setdefault(g, {})
            node[last] = self.leaves[path]
        return root


def token_names(tree: Mapping[str, Any]) -> list[str]:
    """Dotted names of every leaf token in a DTCG tree, sorted. Only names go into the shared prefix."""
    out: list[str] = []

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, val in node.items():
            if key.startswith("$"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(val, Mapping) and "$value" in val:
                out.append(path)
            elif isinstance(val, Mapping):
                walk(val, path)

    walk(tree, "")
    return sorted(out)


# ------------------------------------------------------------------ ramps and schemes


def _ramp(hue: float, chroma: float, tones: Iterable[int]) -> dict[int, str]:
    return {int(t): hct_hex(hue, chroma, int(t)) for t in tones}


def _primary_chroma(seed: Seed) -> float:
    t = token_tables()
    if seed.chroma < float(t["achromatic_below"]):
        return seed.chroma
    return min(max(seed.chroma, float(t["primary_chroma_min"])), float(t["primary_chroma_max"]))


def _derive_on_brand(brand_hex: str, seed: Seed, need: float) -> dict[str, Any]:
    """The text colour on a Locked brand colour, derived to pass `need` (FN-27). White first (the usual look), then a
    near-black tinted with the brand hue, then pure black. One of white and black always reaches 4.58 or more."""
    candidates = [
        ("white", "#ffffff"),
        ("tinted_near_black", hct_hex(seed.hue, min(seed.chroma, 8.0), 8)),
        ("black", "#000000"),
    ]
    with_white = contrast_ratio("#ffffff", brand_hex)
    for name, hx in candidates:
        r = contrast_ratio(hx, brand_hex)
        if r >= need:
            return {
                "hex": hx,
                "choice": name,
                "ratio": _floor2(r),
                "ratio_with_white": _floor2(with_white),
                "changed_from_white": name != "white",
            }
    raise TokenContrastError(
        f"no text colour reaches {need}:1 on {brand_hex}"
    )  # unreachable in practice


def _tone_until(
    hue: float, chroma: float, tone: int, bgs: Sequence[str], need: float, step: int
) -> tuple[str, int]:
    """Move the tone one step at a time (darker on light surfaces, lighter on dark) until every `bg` passes."""
    while True:
        hx = hct_hex(hue, chroma, tone)
        if all(contrast_ratio(hx, bg) >= need for bg in bgs):
            return hx, tone
        tone += step
        if tone <= 0 or tone >= 100:
            raise TokenContrastError(f"no tone of hue {hue} reaches {need}:1 on {list(bgs)}")


def _pair(
    fg: str, bg: str, fg_hex: str, bg_hex: str, scheme: str, body: bool, need: float
) -> dict[str, Any]:
    ratio = contrast_ratio(fg_hex, bg_hex)
    return {
        "fg": fg,
        "bg": bg,
        "ratio": _floor2(ratio),
        "scheme": scheme,
        "body": body,
        "need": need,
        "_exact": ratio,
    }


def _build_colours(
    tree: _Tree, seed: Seed, brand_locked: bool, cfg: SpecConfig, report: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    t = token_tables()
    tones: list[int] = [int(x) for x in t["tones"]]
    p_chroma = _primary_chroma(seed)
    ramp_defs: dict[str, tuple[float, float]] = {}
    for name, d in t["ramps"].items():
        chroma = p_chroma if d["chroma"] == "seed" else float(d["chroma"])
        ramp_defs[name] = ((seed.hue + float(d["hue_shift"])) % 360.0, chroma)
    for name in _SEMANTIC:
        d = t["semantic"][name]
        ramp_defs[name] = (float(d["hue"]), float(d["chroma"]))
    palette: dict[str, dict[int, str]] = {n: _ramp(h, c, tones) for n, (h, c) in ramp_defs.items()}
    for rname, ramp in palette.items():
        for tone, hx in ramp.items():
            tree.add(f"color.palette.{rname}.{tone}", "color", hx)

    def ref_role(scheme: str, role: str, ramp: str, tone: int) -> None:
        tree.add(
            f"color.scheme.{scheme}.{role}",
            "color",
            None,
            ref=f"color.palette.{ramp}.{tone}",
            resolved=palette[ramp][tone],
        )

    for si, scheme in enumerate(SCHEMES):
        for role, (ramp, light, dark) in t["neutral_roles"].items():
            ref_role(scheme, role, ramp, int((light, dark)[si]))
        for g in GROUPS:
            r = t["group_roles"]
            ref_role(scheme, g, g, int(r["base"][si]))
            ref_role(scheme, f"on_{g}", g, int(r["on_base"][si]))
            ref_role(scheme, f"{g}_container", g, int(r["container"][si]))
            ref_role(scheme, f"on_{g}_container", g, int(r["on_container"][si]))
        for role, (ramp, light, dark) in t["state_roles"].items():
            ref_role(scheme, role, ramp, int((light, dark)[si]))
    locked: list[str] = []
    need_body = cfg.checks.wcag_body_text_ratio
    if brand_locked:
        on = _derive_on_brand(seed.hex, seed, need_body)
        for scheme in SCHEMES:
            tree.add(f"color.scheme.{scheme}.brand", "color", seed.hex)
            tree.add(f"color.scheme.{scheme}.on_brand", "color", on["hex"])
            locked.append(f"color.scheme.{scheme}.brand")
        report["derived_text_on_brand"] = on
        what = "a different text colour than white" if on["changed_from_white"] else "white"
        report["statements"].append(
            f"Your brand colour {seed.hex} has contrast {on['ratio_with_white']}:1 with white text. "
            f"It stays exactly as you gave it. The text on it is derived ({what}, "
            f"{on['choice'].replace('_', ' ')}) and reaches {on['ratio']}:1, above the {need_body}:1 needed."
        )
    else:
        for scheme in SCHEMES:
            tree.add(
                f"color.scheme.{scheme}.brand",
                "color",
                None,
                ref=f"color.scheme.{scheme}.primary",
                resolved=tree.resolved[f"color.scheme.{scheme}.primary"],
            )
            tree.add(
                f"color.scheme.{scheme}.on_brand",
                "color",
                None,
                ref=f"color.scheme.{scheme}.on_primary",
                resolved=tree.resolved[f"color.scheme.{scheme}.on_primary"],
            )
        report["derived_text_on_brand"] = None

    # data-visualisation palette: distinct hues, each tone moved until it passes against the surfaces
    dv = t["dataviz"]
    graphic = float(t["graphic_ratio"])
    dv_hex: dict[str, list[str]] = {}
    for si, scheme in enumerate(SCHEMES):
        tones_dv = dv["tones_light"] if si == 0 else dv["tones_dark"]
        bgs = [str(tree.resolved[f"color.scheme.{scheme}.{r}"]) for r in dv["on_surfaces"]]
        series: list[str] = []
        for i, hue in enumerate(dv["hues"]):
            hx, _tone = _tone_until(
                float(hue),
                float(dv["chroma"]),
                int(tones_dv[i]),
                bgs,
                graphic,
                -2 if si == 0 else 2,
            )
            tree.add(f"color.dataviz.{scheme}.{i + 1}", "color", hx)
            series.append(hx)
        dv_hex[scheme] = series
    min_de = min(
        float(Color(a).delta_e(Color(b), method="2000"))
        for hexes in dv_hex.values()
        for i, a in enumerate(hexes)
        for b in hexes[i + 1 :]
    )
    report["dataviz_min_delta_e"] = round(min_de, 1)

    # contrast pairs
    pairs: list[dict[str, Any]] = []
    for scheme in SCHEMES:

        def val(role: str, scheme: str = scheme) -> str:
            return str(tree.resolved[f"color.scheme.{scheme}.{role}"])

        for fg, bg in t["body_pairs"]:
            pairs.append(
                _pair(
                    f"color.scheme.{scheme}.{fg}",
                    f"color.scheme.{scheme}.{bg}",
                    val(fg),
                    val(bg),
                    scheme,
                    True,
                    need_body,
                )
            )
        pairs.append(
            _pair(
                f"color.scheme.{scheme}.on_brand",
                f"color.scheme.{scheme}.brand",
                val("on_brand"),
                val("brand"),
                scheme,
                True,
                need_body,
            )
        )
        for fg, bg in t["graphic_pairs"]:
            pairs.append(
                _pair(
                    f"color.scheme.{scheme}.{fg}",
                    f"color.scheme.{scheme}.{bg}",
                    val(fg),
                    val(bg),
                    scheme,
                    False,
                    graphic,
                )
            )
        brand_on_surface = _pair(
            f"color.scheme.{scheme}.brand",
            f"color.scheme.{scheme}.surface",
            val("brand"),
            val("surface"),
            scheme,
            False,
            graphic,
        )
        pairs.append(brand_on_surface)
        if brand_locked and brand_on_surface["_exact"] < graphic:
            report["flags"].append(f"brand_on_surface_below_{graphic}_{scheme}")
        for i, hx in enumerate(dv_hex[scheme]):
            for r in dv["on_surfaces"]:
                pairs.append(
                    _pair(
                        f"color.dataviz.{scheme}.{i + 1}",
                        f"color.scheme.{scheme}.{r}",
                        hx,
                        val(r),
                        scheme,
                        False,
                        graphic,
                    )
                )
    return pairs, locked


def _assert_contrast(pairs: list[dict[str, Any]], brand_locked: bool) -> None:
    """Every body-text pair must reach its ratio in light AND dark. The brand colour on a surface is the only
    graphic pair allowed to fall short (the user's Locked colour is kept; the report flags it)."""
    bad = []
    for p in pairs:
        if p["_exact"] >= p["need"]:
            continue
        if brand_locked and p["fg"].endswith(".brand") and not p["body"]:
            continue
        bad.append(f"{p['fg']} on {p['bg']} ({p['scheme']}) is {p['ratio']}, needs {p['need']}")
    if bad:
        raise TokenContrastError(
            "; ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else "")
        )


# ------------------------------------------------------------------ the other token groups


def _elevation(tree: _Tree, depth: str) -> None:
    e = token_tables()["elevation"]
    scale = float(e["scale_by_depth"].get(depth, e["scale_by_depth"]["soft"]))
    for n in range(int(e["levels"])):
        alpha = round(float(e["alpha"][n]) * min(scale, 1.6), 3)
        a = format(max(0, min(255, round(alpha * 255))), "02x")
        tree.add_composite(
            f"elevation.level.{n}",
            "shadow",
            {
                "color": f"#000000{a}",
                "offsetX": "0px",
                "offsetY": f"{round(float(e['y'][n]) * scale)}px",
                "blur": f"{round(float(e['blur'][n]) * scale)}px",
                "spread": "0px",
            },
        )
    tree.add("elevation.scrim.opacity", "number", float(e["scrim_opacity"]))
    if depth == "glass":
        tree.add("elevation.glass.opacity", "number", float(e["glass_surface_opacity"]))


def _motion(tree: _Tree, personality: str) -> None:
    m = token_tables()["motion"]
    pers = personality if personality in m["durations_ms"] else "calm"
    for name, ms in zip(
        ("instant", "fast", "base", "slow", "slower"), m["durations_ms"][pers], strict=True
    ):
        tree.add(f"motion.duration.{name}", "duration", f"{int(ms)}ms")
    tree.add("motion.duration.reduced", "duration", f"{int(m['reduced_motion_duration_ms'])}ms")
    for name, pts in m["easings"][pers].items():
        tree.add(f"motion.easing.{name}", "cubicBezier", [float(x) for x in pts])
    tree.add("motion.easing.linear", "cubicBezier", [0.0, 0.0, 1.0, 1.0])
    tree.add(
        "motion.easing.default",
        "cubicBezier",
        None,
        ref=f"motion.easing.{m['default_easing']}",
        resolved=m["easings"][pers][m["default_easing"]],
    )


def _script_key(script: str) -> str:
    return "".join(ch for ch in script.lower() if ch.isalnum())


def _script_font_sheet() -> Any:
    from .platform_data import default_script_fonts

    return default_script_fonts()


def _type_and_scripts(
    tree: _Tree, personality: str, density: str, scripts: Sequence[str], report: dict[str, Any]
) -> None:
    t = token_tables()["type"]
    pers_name = personality if personality in t["personalities"] else "neutral"
    pers = t["personalities"][pers_name]

    sheet: Any = None
    try:
        sheet = _script_font_sheet()
    except Exception:  # the data agent's table may be absent: a Latin-only stack, flagged
        report["flags"].append("script_fallbacks_missing")
    latin_family: str | None = None
    script_rows: list[dict[str, Any]] = []
    wanted = ["Latin", *[s for s in scripts if s.lower() != "latin"]]
    for script in wanted:
        row: dict[str, Any] = {"script": script, "family": None, "rtl": False, "source": "missing"}
        if sheet is not None:
            try:
                entry = sheet.get(script)
                row.update(family=entry.family, rtl=bool(entry.rtl), source="table")
            except KeyError:
                report["flags"].append(f"script_unverified:{script}")
        script_rows.append(row)
        if script.lower() == t["latin_script"].lower() and row["family"]:
            latin_family = row["family"]
    report["scripts"] = script_rows

    for slot in ("heading", "body", "label"):
        cls = pers["families"][slot]
        stack = list(t["class_stacks"][cls])
        if cls in ("sans", "rounded") and latin_family:
            stack.insert(len(stack) - 1, latin_family)
        tree.add(f"font.family.{slot}", "fontFamily", stack)
        tree.add(f"font.weight.{slot}", "fontWeight", int(pers[slot]))
    for row in script_rows:
        fam = [row["family"], "sans-serif"] if row["family"] else ["system-ui", "sans-serif"]
        tree.add(f"font.script.{_script_key(row['script'])}", "fontFamily", fam)

    base, ratio, floor = float(t["base_px"]), float(pers["ratio"]), int(t["min_px"])
    body_line = float(t["line_height_body_by_density"].get(density, 1.5))
    for name, (step, line, wslot, spacing, fslot) in t["styles"].items():
        size = max(floor, round(base * ratio ** float(step)))
        tree.add(f"font.size.{name}", "dimension", f"{size}px")
        lh = body_line if line == "density" else float(line)
        tree.add_composite(
            f"type.{name}",
            "typography",
            {
                "fontFamily": f"{{font.family.{fslot}}}",
                "fontSize": f"{{font.size.{name}}}",
                "fontWeight": f"{{font.weight.{wslot}}}",
                "lineHeight": lh,
                "letterSpacing": f"{spacing}em",
            },
        )


def _radius_spacing(tree: _Tree, shape: str, density: str) -> None:
    r = token_tables()["radius"]
    by = r["by_shape"].get(shape, r["by_shape"]["rounded"])
    for k in ("xs", "sm", "md", "lg", "xl"):
        tree.add(f"radius.{k}", "dimension", f"{int(by[k])}px")
    tree.add("radius.full", "dimension", f"{int(r['full'])}px")
    tree.add("radius.control", "dimension", None, ref=f"radius.{by['control']}")
    s = token_tables()["spacing"]
    unit = int(s["unit_px"])
    for n in s["scale_steps"]:
        tree.add(f"space.scale.{n}", "dimension", f"{int(n) * unit}px")
    insets = s["inset_px"].get(density, s["inset_px"]["comfortable"])
    for k, px in zip(("xs", "sm", "md", "lg", "xl"), insets, strict=True):
        tree.add(f"space.inset.{k}", "dimension", f"{int(px)}px")
    tree.add("size.touch_target.min", "dimension", f"{int(s['touch_target_min_px'])}px")


def _state_opacity(tree: _Tree) -> None:
    for k, v in token_tables()["state_opacity"].items():
        tree.add(f"state.opacity.{k}", "number", float(v))


# ------------------------------------------------------------------ public API


def build_tokens(
    direction: Any,
    brand_colour: str | None = None,
    scripts: Sequence[str] = ("Latin",),
    *,
    config: SpecConfig | None = None,
) -> dict[str, Any]:
    """The DTCG tokens of one direction, with the contrast evidence V-12 reads. Deterministic."""
    cfg = config or default_config()
    seed = resolve_seed(direction, brand_colour)
    brand_locked = seed.source == "brand"
    report: dict[str, Any] = {
        "seed": {"hex": seed.hex, "hue": round(seed.hue, 1), "source": seed.source},
        "brand_locked": brand_locked,
        "statements": [],
        "flags": [],
        "dark_stance": _get(direction, "dark_stance", "supported"),
    }
    if seed.source == "derived":
        report["statements"].append(
            "No brand colour and no seed colour were given: the seed hue comes from the direction; "
            "you can change it at Gate 2."
        )
    tree = _Tree()
    pairs, locked = _build_colours(tree, seed, brand_locked, cfg, report)
    _assert_contrast(pairs, brand_locked)
    depth = str(_get(direction, "depth", "soft"))
    _elevation(tree, depth)
    _motion(tree, str(_get(direction, "motion_personality", "calm")))
    density = str(_get(direction, "density", "comfortable"))
    _type_and_scripts(
        tree, str(_get(direction, "type_personality", "neutral")), density, scripts, report
    )
    _radius_spacing(tree, str(_get(direction, "shape", "rounded")), density)
    _state_opacity(tree)

    names = sorted(tree.leaves)
    unresolved = sorted(r for r in tree.refs if r not in tree.leaves)
    if unresolved:
        raise TokenError(f"references do not resolve: {unresolved}")
    contrast = [{k: v for k, v in p.items() if k != "_exact"} for p in pairs]
    report["min_body_ratio"] = {
        s: min(p["ratio"] for p in contrast if p["scheme"] == s and p["body"]) for s in SCHEMES
    }
    return {
        "direction": _get(direction, "id", None),
        "version": token_tables()["version"],
        "names": names,
        "refs": sorted(tree.refs),
        "contrast": contrast,
        "tree": tree.nested(),
        "locked_roles": sorted(locked),
        "report": report,
    }


# ------------------------------------------------------------------ choosing directions


def _diff_axes() -> list[str]:
    return list(vocab.direction_axes_file().diff_axes)


def pick_directions(
    n: int,
    axes: Mapping[str, Sequence[str]] | None = None,
    brand_locked: bool = False,
    rnd: random.Random | None = None,
    *,
    config: SpecConfig | None = None,
) -> list[dict[str, Any]]:
    """Choose `n` directions (axis name -> value, plus `seed_hue`) that pairwise differ on at least
    `min_direction_axis_differences` diff axes and, when no brand colour is Locked, by at least
    `min_seed_hue_gap_degrees` of seed hue. `axes` narrows the allowed values per axis (default: the whole
    vocabulary). With a Locked brand colour `seed_hue` is None for every direction."""
    cfg = config or default_config()
    rnd = rnd or random.Random(0)  # noqa: S311 - seeded choice, not security
    if n < 1:
        raise ValueError("n must be at least 1")
    allowed = {
        a: list(axes[a]) if axes and a in axes else list(vocab.direction_axes()[a].value_names)
        for a in _diff_axes()
    }
    need = min(
        cfg.checks.min_direction_axis_differences,
        sum(1 for v in allowed.values() if len(set(v)) > 1),
    )
    gap_need = cfg.checks.min_seed_hue_gap_degrees
    if not brand_locked and n > 1 and 360.0 / n < gap_need:
        raise ValueError(f"{n} directions cannot be {gap_need} degrees apart in hue")

    picks: list[dict[str, Any]] = []
    for _ in range(n):
        for _try in range(2000):
            cand = {a: rnd.choice(vals) for a, vals in allowed.items()}
            if all(len(vocab.direction_axis_differences(cand, p)) >= need for p in picks):
                picks.append(cand)
                break
        else:
            raise ValueError(f"could not find {n} directions that differ on {need} axes")

    if brand_locked:
        for p in picks:
            p["seed_hue"] = None
    else:
        base = rnd.uniform(0.0, 360.0)
        hues = [round((base + i * 360.0 / n) % 360.0, 1) for i in range(n)]
        rnd.shuffle(hues)
        for p, h in zip(picks, hues, strict=True):
            p["seed_hue"] = h
    return picks


def check_direction_set(
    picks: Sequence[Mapping[str, Any]], brand_locked: bool, *, config: SpecConfig | None = None
) -> list[str]:
    """Plain problems with a set of directions (empty = fine): the rule of doc 18 5."""
    cfg = config or default_config()
    out: list[str] = []
    for i, a in enumerate(picks):
        for j in range(i + 1, len(picks)):
            b = picks[j]
            diff = vocab.direction_axis_differences(
                {k: str(v) for k, v in a.items()}, {k: str(v) for k, v in b.items()}
            )
            if len(diff) < cfg.checks.min_direction_axis_differences:
                out.append(f"directions {i} and {j} differ on only {len(diff)} axes")
            ha, hb = a.get("seed_hue"), b.get("seed_hue")
            if (
                not brand_locked
                and ha is not None
                and hb is not None
                and _circular_gap(float(ha), float(hb)) < cfg.checks.min_seed_hue_gap_degrees
            ):
                out.append(f"directions {i} and {j} are closer than the hue gap")
    return out


def direction_fields(pick: Mapping[str, Any]) -> dict[str, Any]:
    """Axis name -> Direction field name (mood_family fills `mood`); `seed_hue` is carried as it is."""
    axes = vocab.direction_axes()
    out: dict[str, Any] = {}
    for k, v in pick.items():
        out[axes[k].field if k in axes else k] = v
    return out


def build_graph_tokens(
    graph: Any, *, config: SpecConfig | None = None
) -> dict[str, dict[str, Any]]:
    """Tokens for every direction of a graph, keyed by direction id. A Locked direction with a seed colour is the
    user's brand colour. Scripts come from the graph's languages (the pool table maps language to script)."""
    from .mock import script_for_language

    scripts = sorted({script_for_language(lang) for lang in graph.meta.languages})
    out: dict[str, dict[str, Any]] = {}
    for did in sorted(graph.directions):
        d = graph.directions[did]
        brand = d.seed_colour if (d.tier == "locked" and d.seed_colour) else None
        out[did] = build_tokens(d, brand, scripts, config=config)
    return out
