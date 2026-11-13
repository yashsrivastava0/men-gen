"""The closing question's options are matched to the Brief's features by name, by shared words, or when one label names
several features (found in a live run: paraphrased labels left the stated features at "nice" and made copies)."""

from __future__ import annotations

from mde_compass.engine import closing
from mde_compass.engine.flows import priorities


def placed(**kw: str) -> priorities.Placement:
    return priorities.Placement({k.replace("_", " "): v for k, v in kw.items()})


def prio(features: list[dict[str, str]], got: list[object]) -> dict[str, str | None]:
    return {str(f["name"]): (f.get("priority") if isinstance(f, dict) else None) for f in got}  # type: ignore[union-attr]


def test_a_label_that_holds_the_features_words_sets_its_priority() -> None:
    features: list[dict[str, str]] = [
        {"name": "produce listings"},
        {"name": "bulk ordering"},
        {"name": "online payment"},
        {"name": "delivery"},
    ]
    p = placed(
        Produce_listings_and_prices="must",
        Bulk_ordering="must",
        Online_payment="nice",
        Delivery="nice",
    )
    got = prio(features, closing.apply_to_features(features, p))  # type: ignore[arg-type]
    assert got == {
        "produce listings": "must",
        "bulk ordering": "must",
        "online payment": "nice",
        "delivery": "nice",
    }


def test_one_label_that_names_two_features_sets_both() -> None:
    features: list[dict[str, str]] = [
        {"name": "Weekly class timetable"},
        {"name": "Class booking"},
        {"name": "Class packs"},
        {"name": "Class management"},
    ]
    p = placed(
        Weekly_timetable_and_booking="must", Class_packs_and_credits="must", Staff_tools="nice"
    )
    got = prio(features, closing.apply_to_features(features, p))  # type: ignore[arg-type]
    assert got["Weekly class timetable"] == "must" and got["Class booking"] == "must"
    assert got["Class packs"] == "must"
    assert got["Class management"] is None  # nothing named it: untouched


def test_must_wins_when_two_options_reach_one_feature_and_unrelated_labels_are_ignored() -> None:
    features: list[dict[str, str]] = [{"name": "Reminders"}, {"name": "Class booking"}]
    p = placed(
        Class_reminders="nice", Send_reminders_to_members="must", Something_else_entirely="must"
    )
    got = prio(features, closing.apply_to_features(features, p))  # type: ignore[arg-type]
    assert got["Reminders"] == "must" and got["Class booking"] is None
