from __future__ import annotations

from mde_content.bundle import ContentBundle, load_bundle, validate_bundle


def test_bundle_loads_everything() -> None:
    b = load_bundle()
    assert isinstance(b, ContentBundle)
    assert set(b.packs) == {
        "commerce", "food_delivery", "booking_services", "internal_workforce", "custom"
    }  # fmt: skip
    assert len(b.themes.themes) == 6 and b.stack.capabilities
    assert {"guided-default", "platform-default"} <= set(b.policies)


def test_bundle_is_cross_consistent() -> None:
    problems = validate_bundle()
    assert problems == [], "\n".join(problems)


def test_bundle_check_actually_detects_a_break() -> None:
    b = load_bundle()
    pack = b.packs["commerce"]
    broken = pack.model_copy(update={"theme_candidates": ["no-such-theme"]})
    bad = ContentBundle(
        catalogs=b.catalogs, questions=b.questions, packs={**b.packs, "commerce": broken},
        recipes=b.recipes, themes=b.themes, stack=b.stack, policies=b.policies,
    )  # fmt: skip
    assert any("no-such-theme" in p for p in validate_bundle(bad))
