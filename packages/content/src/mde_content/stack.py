"""Stack Profile loader (brief 8.6): capability matrix and unsupported app types.

``data/stack-profile.yaml`` is validated by ``mde_contracts.content.StackProfile``. No Expo SDK or
React Native version number is stored anywhere: ``StackProfile.pinned_at_build`` names what the
build step pins.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from importlib import resources

import yaml

from mde_contracts.content import Capability, StackProfile, UnsupportedType


@lru_cache(maxsize=1)
def load_stack_profile() -> StackProfile:
    """Load and validate the Stack Profile (cached; the result is immutable)."""
    text = (
        resources.files("mde_content")
        .joinpath("data/stack-profile.yaml")
        .read_text(encoding="utf-8")
    )
    return StackProfile.model_validate(yaml.safe_load(text))


def capability(capability_id: str) -> Capability:
    """Return one capability by id; ``KeyError`` for an unknown id."""
    for item in load_stack_profile().capabilities:
        if item.id == capability_id:
            return item
    raise KeyError(f"unknown capability: {capability_id!r}")


def unsupported_type(type_id: str) -> UnsupportedType:
    """Return one unsupported app type by id; ``KeyError`` for an unknown id."""
    for item in load_stack_profile().unsupported_types:
        if item.id == type_id:
            return item
    raise KeyError(f"unknown unsupported type: {type_id!r}")


def dev_build_capabilities() -> list[Capability]:
    """Capabilities that need a development build (flagged by validator 8, spec 6.4)."""
    return [c for c in load_stack_profile().capabilities if c.supported and c.needs_dev_build]


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


@lru_cache(maxsize=2048)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    # Whole-word match: "ar" must not match "car" and "pay" must not match "payroll".
    return re.compile(rf"(?<!\w){re.escape(_normalize(keyword))}(?!\w)")


def _hits(text: str, keywords: list[str]) -> list[str]:
    haystack = _normalize(text)
    return [k for k in keywords if _keyword_pattern(k).search(haystack)]


def match_unsupported(text: str) -> list[UnsupportedType]:
    """Unsupported app types whose keywords appear in ``text`` (any language), in file order.

    Whole-word, case-insensitive matching on the keyword lists, which include Hindi and Hinglish
    forms. The caller decides what to do with a match (offer ``closest_option``); this never
    blocks anything by itself.
    """
    return [u for u in load_stack_profile().unsupported_types if _hits(text, u.keywords)]


def match_capabilities(text: str) -> list[Capability]:
    """Capabilities whose keywords appear in ``text``, in file order (advisory, keyword based)."""
    return [c for c in load_stack_profile().capabilities if _hits(text, c.keywords)]
