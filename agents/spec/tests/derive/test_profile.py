"""derive/profile.py: the user's tier is a floor; content can only raise it."""

from __future__ import annotations

from mde_compass_intake.spec_input import App, Integration, Locales, Module
from mde_spec_agent.derive.facts import facts_from_input
from mde_spec_agent.derive.profile import decide_profile, detail_profile
from mde_spec_core.config import default_config

from .conftest import tiny_input


def test_the_three_real_exports(export) -> None:
    got = {}
    for key in ("quick", "standard", "thorough"):
        si = export(key)
        got[key] = detail_profile(si, default_config(), facts_from_input(si))
    assert got == {"quick": "compact", "standard": "standard", "thorough": "deep"}


def test_tier_floor_without_content() -> None:
    cfg = default_config()
    assert detail_profile(tiny_input(tier="quick"), cfg) == "compact"
    assert detail_profile(tiny_input(tier="standard"), cfg) == "standard"
    assert detail_profile(tiny_input(tier="thorough"), cfg) == "deep"


def test_content_raises_and_records_why() -> None:
    cfg = default_config()
    d = decide_profile(tiny_input(tier="quick", sensitive_data=("health",)), cfg)
    assert (d.floor, d.profile) == (
        "compact",
        "standard",
    ) and "standard:sensitive_data" in d.raised_by
    d = decide_profile(tiny_input(tier="quick", locales=Locales(codes=("en", "hi", "kn"))), cfg)
    assert d.profile == "standard" and "standard:locales" in d.raised_by
    d = decide_profile(tiny_input(tier="quick", locales=Locales(codes=("ar",), rtl=("ar",))), cfg)
    assert d.profile == "standard"
    d = decide_profile(
        tiny_input(tier="quick", integrations=tuple(Integration(name=f"i{n}") for n in range(3))),
        cfg,
    )
    assert d.profile == "standard"
    d = decide_profile(tiny_input(tier="quick"), cfg, facts={"offline_first"})
    assert d.profile == "standard"


def test_apps_and_modules_raise_to_deep_and_content_never_lowers() -> None:
    cfg = default_config()
    four = tuple(App(id=f"APP-{n:03d}", name=f"App {n}") for n in range(1, 5))
    assert detail_profile(tiny_input(tier="quick", apps=four), cfg) == "deep"
    eight = tuple(Module(id=f"MOD-{n:03d}", name=f"M{n}") for n in range(1, 9))
    assert detail_profile(tiny_input(tier="standard", modules=eight), cfg) == "deep"
    # a thorough tier stays deep whatever the content says
    assert detail_profile(tiny_input(tier="thorough", sensitive_data=()), cfg) == "deep"


def test_a_role_named_app_is_not_counted_as_an_app() -> None:
    cfg = default_config()
    apps = (
        App(id="APP-001", name="A"),
        App(id="APP-002", name="B"),
        App(id="APP-003", name="C"),
        App(id="APP-004", name="Manager side", is_real=False),
    )
    assert detail_profile(tiny_input(tier="standard", apps=apps), cfg) == "standard"
