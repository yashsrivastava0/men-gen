"""Small shared value types of the intake: the tier of a value, the seal state, the idea, a country.

The same names and shapes as Research's ``domain/input.py`` where the two readers share a field (D-175), so the
contract test (``tests/contract/test_intake_parity.py``) can compare them directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Tier = Literal["locked", "default", "open"]
# How far the export's own hash seal could be checked. Never a refusal: a missing baseline or a changed Brief is
# read anyway and the trust profile says so.
InputBinding = Literal["verified", "unverified_no_baseline", "unverified_hash_mismatch"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Idea(Frozen):
    summary: str = ""
    category: str = ""
    subcategory: str = ""
    business_model: str = ""


class Country(Frozen):
    """One country the app is used in, as the person named it. ``code`` is lowercase alpha-2 when known."""

    name: str
    code: str | None = None


class Failure(Frozen):
    call: str
    reason: str  # timeout | zero_token


class FocusTopic(Frozen):
    topic: str
    about: tuple[str, ...] = ()


class LanguageInfo(Frozen):
    name: str
    script: str
    direction: Literal["ltr", "rtl"]
    latin: bool
