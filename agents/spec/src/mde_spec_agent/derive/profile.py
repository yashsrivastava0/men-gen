"""Detail profile (doc 15 4.1): the user's tier is a floor; content can only raise it.

``quick`` -> ``compact``, ``standard`` -> ``standard``, ``thorough`` -> ``deep`` (config ``profiles.tier_floor``). Content
raises the floor to ``standard`` or ``deep`` by the thresholds in ``config.profiles.raise_to_standard_if`` and
``raise_to_deep_if``; it never lowers it. The apps counted are the ones the intake graded real (a name that is only a
role, such as "Manager side", is a clash, not an app, for this threshold); the inventory still keeps an app that owns screens.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from mde_compass_intake import SpecInput
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.vocab import PROFILE_ORDER

_BY_ORDER = {v: k for k, v in PROFILE_ORDER.items()}


@dataclass(frozen=True)
class ProfileDecision:
    """The profile and why: the floor and every raise that fired (all recorded, so the G1.5 page can say it)."""

    profile: str
    floor: str
    raised_by: tuple[str, ...]


def _real_apps(spec_input: SpecInput) -> int:
    return sum(1 for a in spec_input.apps if a.is_real and not a.junk and a.duplicate_of is None)


def decide_profile(
    spec_input: SpecInput,
    config: SpecConfig | None = None,
    facts: Collection[str] = (),
) -> ProfileDecision:
    cfg = (config or default_config()).profiles
    floor = cfg.tier_floor.get(spec_input.tier, cfg.tier_floor["standard"])
    level = PROFILE_ORDER[floor]
    raised: list[str] = []

    std = cfg.raise_to_standard_if
    apps = _real_apps(spec_input)
    checks_std = (
        ("apps", apps >= std.get("apps_at_least", 10**9)),
        ("sensitive_data", bool(std.get("sensitive_data")) and bool(spec_input.sensitive_data)),
        (
            "integrations",
            len(spec_input.integrations) >= std.get("integrations_at_least", 10**9),
        ),
        ("locales", len(spec_input.locales.codes) >= std.get("locales_at_least", 10**9)),
        ("right_to_left", bool(std.get("right_to_left")) and bool(spec_input.locales.rtl)),
        (
            "offline_first",
            bool(std.get("offline_first")) and "offline_first" in set(facts),
        ),
    )
    for name, hit in checks_std:
        if hit:
            raised.append(f"standard:{name}")
            level = max(level, PROFILE_ORDER["standard"])

    deep = cfg.raise_to_deep_if
    checks_deep = (
        ("apps", apps >= deep.get("apps_at_least", 10**9)),
        ("modules", len(spec_input.modules) >= deep.get("modules_at_least", 10**9)),
    )
    for name, hit in checks_deep:
        if hit:
            raised.append(f"deep:{name}")
            level = max(level, PROFILE_ORDER["deep"])

    return ProfileDecision(profile=_BY_ORDER[level], floor=floor, raised_by=tuple(raised))


def detail_profile(
    spec_input: SpecInput,
    config: SpecConfig | None = None,
    facts: Collection[str] = (),
) -> str:
    """The detail profile (``compact`` | ``standard`` | ``deep``) of an input."""
    return decide_profile(spec_input, config, facts).profile
