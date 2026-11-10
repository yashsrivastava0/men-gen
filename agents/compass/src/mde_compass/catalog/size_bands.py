"""Size bands (ADR 0017): how big an app of each interview tier is expected to be, from ``size_bands.yaml``.

Data, not logic (hard rule 8). This module is the one reader of that file and validates it when it is first loaded
(cached): a typo raises ``CatalogError`` with the place, so it never reaches a running session.

* ``band_for_tier(tier)``: the band a tier maps to;
* ``wider(a, b)``: the larger of two bands (a document or a person can only raise the band the tier gives);
* ``band_from_claims(n)``: the band that ``n`` verified document claims call for (``None`` below the first threshold);
* ``SizeBand.in_range(kind, n)`` and ``over_limit(n)`` / ``under_floor(n)``: the warn-only size checks.

A band is guidance for the drafter and a source of warnings, never a minimum: nothing is padded to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from mde_compass.catalog.loader import CatalogError

SIZE_BANDS_PATH = Path(__file__).with_name("size_bands.yaml")
BandId = Literal["small", "medium", "large"]
BAND_ORDER: tuple[BandId, ...] = ("small", "medium", "large")
TIERS: tuple[str, ...] = ("quick", "standard", "thorough")
REVIEW_MODES: tuple[str, ...] = ("summary", "soft", "hard")
RESEARCH_DEPTHS: tuple[str, ...] = ("light", "standard", "deep")
SizeKind = Literal["screens", "modules", "flows", "entities"]
_SIZE_KINDS: tuple[SizeKind, ...] = ("screens", "modules", "flows", "entities")


@dataclass(frozen=True)
class ResearchDepth:
    """How deep research goes for an app of this size (product spec 4.2)."""

    depth: str
    searches: int
    gap_fill: bool
    max_requests: int


@dataclass(frozen=True)
class SizeBand:
    id: BandId
    tier: str
    screens: tuple[int, int]
    modules: tuple[int, int]
    flows: tuple[int, int]
    entities: tuple[int, int]
    open_upper: bool
    group_size: int  # modules per draft_module call; 0 = all in one call
    review: str
    critic: bool
    must_feature_screens_min: int
    module_screens_min: int
    research: ResearchDepth

    def range_of(self, kind: SizeKind) -> tuple[int, int]:
        return cast("tuple[int, int]", getattr(self, kind))

    def in_range(self, kind: SizeKind, n: int) -> bool:
        low, high = self.range_of(kind)
        return low <= n and ((self.open_upper and kind == "screens") or n <= high)

    def over_limit(self, n: int, factor: float) -> bool:
        """More screens than the band's top times ``factor`` (never for the open-ended top band)."""
        return not self.open_upper and n > self.screens[1] * factor

    def under_floor(self, n: int, factor: float) -> bool:
        """Fewer screens than the band's bottom times ``factor``: a warning, never a reason to add screens."""
        return n < self.screens[0] * factor


@dataclass(frozen=True)
class SuggestRule:
    """A signal that recommends ``band`` at idea time; any one satisfied condition is enough."""

    band: BandId
    size_stakes: tuple[str, ...]
    plan_depth: tuple[str, ...]
    roles_min: int
    apps_min: int
    features_min: int


@dataclass(frozen=True)
class SizeBands:
    bands: dict[BandId, SizeBand]
    tier_to_band: dict[str, BandId]
    over_band_factor: float
    thin_factor: float
    medium_min_claims: int
    large_min_claims: int
    suggest: tuple[SuggestRule, ...]


def _fail(where: str, why: str) -> CatalogError:
    return CatalogError(f"size_bands.yaml {where}: {why}")


def _pair(raw: Any, where: str) -> tuple[int, int]:
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in raw)
    ):
        raise _fail(where, "must be [low, high] with two whole numbers")
    low, high = int(raw[0]), int(raw[1])
    if low > high:
        raise _fail(where, f"low {low} is above high {high}")
    return low, high


def _whole(raw: Any, where: str, *, minimum: int = 0) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < minimum:
        raise _fail(where, f"must be a whole number of at least {minimum}")
    return int(raw)


def _factor(raw: Any, where: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0:
        raise _fail(where, "must be a positive number")
    return float(raw)


def _research(raw: Any, where: str) -> ResearchDepth:
    if not isinstance(raw, dict):
        raise _fail(where, "must be a mapping")
    depth = raw.get("depth")
    if depth not in RESEARCH_DEPTHS:
        raise _fail(f"{where}.depth", f"must be one of {RESEARCH_DEPTHS}")
    gap_fill = raw.get("gap_fill")
    if not isinstance(gap_fill, bool):
        raise _fail(f"{where}.gap_fill", "must be true or false")
    return ResearchDepth(
        depth=str(depth),
        searches=_whole(raw.get("searches"), f"{where}.searches", minimum=1),
        gap_fill=gap_fill,
        max_requests=_whole(raw.get("max_requests"), f"{where}.max_requests", minimum=1),
    )


def _band(band_id: BandId, raw: Any) -> SizeBand:
    where = f"bands.{band_id}"
    if not isinstance(raw, dict):
        raise _fail(where, "must be a mapping")
    tier = raw.get("tier")
    if tier not in TIERS:
        raise _fail(f"{where}.tier", f"must be one of {TIERS}")
    review = raw.get("review")
    if review not in REVIEW_MODES:
        raise _fail(f"{where}.review", f"must be one of {REVIEW_MODES}")
    critic = raw.get("critic")
    open_upper = raw.get("open_upper", False)
    if not isinstance(critic, bool) or not isinstance(open_upper, bool):
        raise _fail(where, "critic and open_upper must be true or false")
    sizes = {kind: _pair(raw.get(kind), f"{where}.{kind}") for kind in _SIZE_KINDS}
    return SizeBand(
        id=band_id,
        tier=str(tier),
        screens=sizes["screens"],
        modules=sizes["modules"],
        flows=sizes["flows"],
        entities=sizes["entities"],
        open_upper=open_upper,
        group_size=_whole(raw.get("group_size"), f"{where}.group_size"),
        review=str(review),
        critic=critic,
        must_feature_screens_min=_whole(
            raw.get("must_feature_screens_min"), f"{where}.must_feature_screens_min", minimum=1
        ),
        module_screens_min=_whole(
            raw.get("module_screens_min"), f"{where}.module_screens_min", minimum=1
        ),
        research=_research(raw.get("research"), f"{where}.research"),
    )


def _suggest(raw: Any) -> tuple[SuggestRule, ...]:
    if not isinstance(raw, list):
        raise _fail("suggest", "must be a list")
    rules: list[SuggestRule] = []
    for i, item in enumerate(raw):
        where = f"suggest[{i}]"
        if not isinstance(item, dict) or item.get("band") not in BAND_ORDER:
            raise _fail(where, f"needs a band of {BAND_ORDER}")
        for key in ("size_stakes", "plan_depth"):
            if not isinstance(item.get(key, []), list):
                raise _fail(f"{where}.{key}", "must be a list")
        rule = SuggestRule(
            band=cast(BandId, item["band"]),
            size_stakes=tuple(str(v) for v in item.get("size_stakes", [])),
            plan_depth=tuple(str(v) for v in item.get("plan_depth", [])),
            roles_min=_whole(item.get("roles_min", 0), f"{where}.roles_min"),
            apps_min=_whole(item.get("apps_min", 0), f"{where}.apps_min"),
            features_min=_whole(item.get("features_min", 0), f"{where}.features_min"),
        )
        if not (
            rule.size_stakes
            or rule.plan_depth
            or rule.roles_min
            or rule.apps_min
            or rule.features_min
        ):
            raise _fail(where, "needs at least one condition")
        rules.append(rule)
    return tuple(rules)


@lru_cache(maxsize=1)
def load_size_bands() -> SizeBands:
    data = yaml.safe_load(SIZE_BANDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise _fail("version", "must be 1")
    raw_bands = data.get("bands")
    if not isinstance(raw_bands, dict) or set(raw_bands) != set(BAND_ORDER):
        raise _fail("bands", f"must be exactly {BAND_ORDER}")
    bands: dict[BandId, SizeBand] = {
        band_id: _band(band_id, raw_bands[band_id]) for band_id in BAND_ORDER
    }
    tier_to_band: dict[str, BandId] = {}
    for band in bands.values():
        if band.tier in tier_to_band:
            raise _fail("bands", f"two bands map to the tier {band.tier!r}")
        tier_to_band[band.tier] = band.id
    if set(tier_to_band) != set(TIERS):
        raise _fail("bands", f"the tiers must be exactly {TIERS}")
    # a bigger band is never smaller: the order small < medium < large holds for every size
    for lower, upper in pairwise(BAND_ORDER):
        for kind in _SIZE_KINDS:
            if bands[lower].range_of(kind)[1] > bands[upper].range_of(kind)[1]:
                raise _fail(f"bands.{upper}.{kind}", f"must not end below {lower}")
    docs = data.get("documents")
    if not isinstance(docs, dict):
        raise _fail("documents", "must be a mapping")
    medium_claims = _whole(docs.get("medium_min_claims"), "documents.medium_min_claims", minimum=1)
    large_claims = _whole(docs.get("large_min_claims"), "documents.large_min_claims", minimum=1)
    if medium_claims >= large_claims:
        raise _fail("documents", "medium_min_claims must be below large_min_claims")
    return SizeBands(
        bands=bands,
        tier_to_band=tier_to_band,
        over_band_factor=_factor(data.get("over_band_factor"), "over_band_factor"),
        thin_factor=_factor(data.get("thin_factor"), "thin_factor"),
        medium_min_claims=medium_claims,
        large_min_claims=large_claims,
        suggest=_suggest(data.get("suggest")),
    )


def band_for_tier(tier: str) -> SizeBand:
    """The band an interview tier maps to; an unknown tier is the Standard one (medium)."""
    data = load_size_bands()
    return data.bands[data.tier_to_band.get(tier, data.tier_to_band["standard"])]


def get_band(band_id: str) -> SizeBand:
    data = load_size_bands()
    if band_id not in data.bands:
        raise KeyError(f"unknown size band {band_id!r}")
    return data.bands[cast(BandId, band_id)]


def wider(a: BandId, b: BandId) -> BandId:
    """The larger of two bands."""
    return a if BAND_ORDER.index(a) >= BAND_ORDER.index(b) else b


def band_from_claims(claims: int) -> BandId | None:
    """The band that this many verified document claims call for, or None below the first threshold."""
    data = load_size_bands()
    if claims >= data.large_min_claims:
        return "large"
    if claims >= data.medium_min_claims:
        return "medium"
    return None


__all__ = [
    "BAND_ORDER",
    "TIERS",
    "BandId",
    "ResearchDepth",
    "SizeBand",
    "SizeBands",
    "SuggestRule",
    "band_for_tier",
    "band_from_claims",
    "get_band",
    "load_size_bands",
    "wider",
]
