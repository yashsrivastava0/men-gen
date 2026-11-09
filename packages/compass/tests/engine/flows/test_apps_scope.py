"""Five or more apps: the main two now, the rest in later phases."""

from __future__ import annotations

import time

from mde_compass.engine.flows import apps_scope as aps

SIX = (
    "For food delivery in Pune I need a customer app, a driver app, a restaurant app, an admin panel, "
    "a support dashboard and a franchise portal"
)


def test_every_named_app_is_found_in_order() -> None:
    names = [a["name"] for a in aps.named_apps(SIX)]
    assert names == [
        "customer app",
        "driver app",
        "restaurant app",
        "admin panel",
        "support dashboard",
        "franchise portal",
    ]
    assert [a["surface"] for a in aps.named_apps(SIX)] == [
        "mobile",
        "mobile",
        "mobile",
        "web",
        "web",
        "web",
    ]


def test_determiners_and_generic_words_are_not_apps() -> None:
    assert aps.named_apps("I want an app, a mobile app and the web app for my shop") == []
    assert aps.named_apps("") == []


def test_six_apps_split_into_the_main_two_and_four_later() -> None:
    plan = aps.phase_plan(aps.named_apps(SIX))
    assert plan is not None
    assert [a["name"] for a in plan.now] == ["customer app", "driver app"]
    assert len(plan.later) == 4


def test_fewer_than_five_apps_are_left_alone() -> None:
    assert aps.phase_plan(aps.named_apps("a customer app, a driver app and an admin panel")) is None
    assert aps.phase_plan(None) is None


def test_later_apps_become_later_scope_records() -> None:
    plan = aps.phase_plan(aps.named_apps(SIX))
    assert plan is not None
    records = aps.later_records(plan.later, "A later phase")
    assert all(
        r["scope_status"] == "later"
        and r["priority"] == "later"
        and r["source"] == "later_phase_app"
        for r in records
    )
    assert records[0]["name"] == "restaurant app"
    merged = aps.merge_features([{"name": "Order"}, {"name": "Restaurant App"}], records)
    assert [m["name"] for m in merged] == [
        "Order",
        "Restaurant App",
        "admin panel",
        "support dashboard",
        "franchise portal",
    ]  # type: ignore[index]


def test_the_slots_own_apps_follow_the_ones_in_the_message() -> None:
    held = [{"name": "customer app", "_meta": {}}, {"name": "kiosk app", "surface": "mobile"}]
    merged = aps.union(aps.named_apps("a driver app and a customer app"), held)
    assert [a["name"] for a in merged] == ["driver app", "customer app", "kiosk app"]
    assert all("_meta" not in a for a in merged)


def test_the_matcher_is_linear_on_hostile_text() -> None:
    started = time.perf_counter()
    aps.named_apps("customer app " * 4000)
    aps.named_apps("x " * 25_000 + "app")
    assert time.perf_counter() - started < 2.0
