"""Size bands (ADR 0017): the data file is strict, the tiers map to the bands the owner named, and the size checks are
guidance (a band is never a minimum)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from mde_compass.catalog import size_bands as sb
from mde_compass.catalog.loader import CatalogError


def test_the_three_tiers_map_to_small_medium_and_large() -> None:
    assert sb.band_for_tier("quick").id == "small"
    assert sb.band_for_tier("standard").id == "medium"
    assert sb.band_for_tier("thorough").id == "large"
    assert sb.band_for_tier("nonsense").id == "medium"  # an unknown tier is Standard


def test_the_owners_screen_ranges() -> None:
    bands = sb.load_size_bands().bands
    assert bands["small"].screens == (8, 15)
    assert bands["medium"].screens == (20, 30)
    assert bands["large"].screens == (40, 60)
    assert bands["large"].open_upper and not bands["small"].open_upper


def test_review_critic_and_research_follow_the_band() -> None:
    bands = sb.load_size_bands().bands
    assert [bands[b].review for b in sb.BAND_ORDER] == ["summary", "soft", "hard"]
    assert [bands[b].critic for b in sb.BAND_ORDER] == [False, True, True]
    # the product spec's depth dial: light up to 6 searches, standard up to 12, deep up to 20 plus a gap-fill round
    assert [(bands[b].research.depth, bands[b].research.searches) for b in sb.BAND_ORDER] == [
        ("light", 6),
        ("standard", 12),
        ("deep", 20),
    ]
    assert [bands[b].research.gap_fill for b in sb.BAND_ORDER] == [False, False, True]


def test_a_bigger_band_never_ends_below_a_smaller_one() -> None:
    bands = sb.load_size_bands().bands
    for kind in ("screens", "modules", "flows", "entities"):
        highs = [bands[b].range_of(kind)[1] for b in sb.BAND_ORDER]  # type: ignore[arg-type]
        assert highs == sorted(highs)


def test_size_checks_are_guidance() -> None:
    data = sb.load_size_bands()
    small, large = data.bands["small"], data.bands["large"]
    assert small.in_range("screens", 8) and small.in_range("screens", 15)
    assert not small.in_range("screens", 16)
    assert large.in_range("screens", 75)  # "60 or more"
    assert small.over_limit(19, data.over_band_factor) is True  # 15 x 1.25 = 18.75
    assert small.over_limit(18, data.over_band_factor) is False
    assert (
        large.over_limit(500, data.over_band_factor) is False
    )  # the top band has nothing above it
    assert small.under_floor(5, data.thin_factor) is True  # a warning, nothing more
    assert small.under_floor(7, data.thin_factor) is False  # 8 x 0.8 = 6.4


def test_a_document_raises_the_band_by_its_verified_claims_not_by_existing() -> None:
    assert sb.band_from_claims(0) is None
    assert sb.band_from_claims(24) is None
    assert sb.band_from_claims(25) == "medium"
    assert sb.band_from_claims(59) == "medium"
    assert sb.band_from_claims(60) == "large"


def test_wider_picks_the_larger_band() -> None:
    assert sb.wider("small", "large") == "large"
    assert sb.wider("large", "medium") == "large"
    assert sb.wider("medium", "medium") == "medium"


def test_the_suggestion_rules_are_typed() -> None:
    rules = sb.load_size_bands().suggest
    assert {r.band for r in rules} == {"large", "medium"}
    assert "enterprise" in next(r for r in rules if r.band == "large").size_stakes


# ------------------------------------------------------------------------------------------- a strict loader


def _load_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any) -> None:
    data = yaml.safe_load(sb.SIZE_BANDS_PATH.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "size_bands.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(sb, "SIZE_BANDS_PATH", path)
    sb.load_size_bands.cache_clear()


@pytest.fixture(autouse=True)
def _restore_cache() -> Any:
    yield
    sb.load_size_bands.cache_clear()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["bands"].pop("large"), "exactly"),
        (lambda d: d["bands"]["small"].update(tier="thorough"), "two bands map"),
        (lambda d: d["bands"]["small"].update(screens=[15, 8]), "above high"),
        (lambda d: d["bands"]["small"].update(screens=[8]), "two whole numbers"),
        (lambda d: d["bands"]["medium"].update(review="loose"), "review"),
        (lambda d: d["bands"]["medium"]["research"].update(depth="bottomless"), "depth"),
        (lambda d: d["bands"]["medium"].update(critic="yes"), "true or false"),
        (lambda d: d["bands"]["small"].update(screens=[8, 99]), "must not end below"),
        (lambda d: d["documents"].update(medium_min_claims=70), "below large"),
        (lambda d: d.update(over_band_factor=0), "positive"),
        (lambda d: d["suggest"].append({"band": "large"}), "at least one condition"),
        (lambda d: d["suggest"].append({"band": "huge", "roles_min": 1}), "band of"),
    ],
)
def test_a_bad_data_file_is_refused_with_the_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any, message: str
) -> None:
    _load_with(tmp_path, monkeypatch, change)
    with pytest.raises(CatalogError, match=message):
        sb.load_size_bands()
