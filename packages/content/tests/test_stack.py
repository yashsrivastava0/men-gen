from __future__ import annotations

import re
from importlib import resources

import pytest
import yaml

from mde_content.stack import (
    capability,
    dev_build_capabilities,
    load_stack_profile,
    match_capabilities,
    match_unsupported,
    unsupported_type,
)
from mde_contracts.content import StackProfile

REQUIRED_CAPABILITIES = {
    "camera",
    "location_foreground",
    "push_notifications",
    "biometrics",
    "maps",
    "payments",
    "offline_storage",
    "deep_links",
    "file_upload",
    "barcode_scan",
    "background_sync",
    "share_sheet",
    "calendar",
    "contacts",
    "speech_recognition",
    "speech_synthesis",
}
UNSUPPORTED = {"game", "ar", "desktop", "background_audio", "hardware_heavy"}


def test_profile_loads_and_is_expo_managed() -> None:
    profile = load_stack_profile()
    assert isinstance(profile, StackProfile)
    assert profile.id == "expo-managed"
    assert {"expo_sdk", "react_native"} <= set(profile.pinned_at_build)
    assert profile.max_screens_guided == 50
    assert load_stack_profile() is profile


def test_no_version_numbers_are_invented() -> None:
    raw = resources.files("mde_content").joinpath("data/stack-profile.yaml").read_text("utf-8")
    assert not re.search(r"\bsdk\s*\d", raw, re.IGNORECASE)
    assert not re.search(r"\b\d+\.\d+(\.\d+)?\b", raw)
    assert not re.search(r"\bexpo[ -]?\d", raw, re.IGNORECASE)


def test_capability_matrix_is_complete_and_unique() -> None:
    caps = load_stack_profile().capabilities
    ids = [c.id for c in caps]
    assert len(ids) == len(set(ids))
    assert set(ids) >= REQUIRED_CAPABILITIES
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", i) for i in ids)
    assert all(c.label and c.keywords for c in caps)


def test_development_build_flags() -> None:
    flagged = {c.id for c in dev_build_capabilities()}
    assert {"push_notifications", "maps", "payments", "background_sync"} <= flagged
    assert not {"camera", "offline_storage", "share_sheet"} & flagged
    assert capability("payments").needs_dev_build


def test_preview_limits_explain_what_the_preview_cannot_show() -> None:
    for cap_id in ("camera", "push_notifications", "maps", "payments", "biometrics"):
        assert capability(cap_id).preview_limit, cap_id


def test_unsupported_types_are_exactly_the_five() -> None:
    types = load_stack_profile().unsupported_types
    assert {u.id for u in types} == UNSUPPORTED
    for u in types:
        assert u.reason and u.closest_option
        assert len(u.keywords) >= 5
        # Hindi / Hinglish coverage: at least one Devanagari keyword per type.
        assert any(re.search(r"[ऀ-ॿ]", k) for k in u.keywords), u.id
    assert unsupported_type("game").label == "Games"


def test_unknown_lookups_raise() -> None:
    with pytest.raises(KeyError):
        capability("teleport")
    with pytest.raises(KeyError):
        unsupported_type("teleport")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I want a 3D multiplayer game with levels", {"game"}),
        ("an AR/VR furniture preview app", {"ar"}),
        ("music streaming app that keeps playing in the background", {"background_audio"}),
        ("a desktop app for Windows and Mac", {"desktop"}),
        ("app to control my drone and raspberry pi", {"hardware_heavy"}),
        ("ek ludo game banana hai", {"game"}),
        ("गेम बनाना है", {"game"}),
        ("डेस्कटॉप ऐप चाहिए", {"desktop"}),
        ("संगीत ऐप", {"background_audio"}),
        ("a game and a podcast player", {"game", "background_audio"}),
    ],
)
def test_match_unsupported_positive(text: str, expected: set[str]) -> None:
    assert {u.id for u in match_unsupported(text)} == expected


@pytest.mark.parametrize(
    "text",
    [
        "an app to book a car wash",  # "ar" must not match inside "car"
        "online grocery store with delivery",
        "staff timesheets and approvals",
        "a gamified loyalty program",  # "gamified" is not "game"
        "",
    ],
)
def test_match_unsupported_negative(text: str) -> None:
    assert match_unsupported(text) == []


def test_match_is_case_and_whitespace_insensitive() -> None:
    assert [u.id for u in match_unsupported("  AUGMENTED   REALITY  try-on ")] == ["ar"]


def test_match_capabilities() -> None:
    ids = {c.id for c in match_capabilities("scan a QR code, show a map and take payments")}
    assert {"barcode_scan", "maps", "payments"} <= ids
    assert match_capabilities("a plain list of articles") == []


def test_yaml_round_trips_through_the_contract_model() -> None:
    profile = load_stack_profile()
    again = StackProfile.model_validate(yaml.safe_load(yaml.safe_dump(profile.model_dump())))
    assert again == profile
