"""DSGN input and output. The output is the PORTABLE SUBSET (ADR 0014): flat closed records in arrays, no open maps, no
Any, small enums built from the closed vocabularies (data), at most four nullable fields, depth 3.

The model writes direction (words and closed-set axes), a seed colour only when the user gave no brand colour, and the
UNBOUND asset needs. It never writes a layout, a widget, a position, a per-screen visual hint or a colour outside the
seed colour; code builds the tokens and checks contrast.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput
from mde_spec_agent.lanes.design._data import design_data
from mde_spec_core import vocab


def _literal(values: tuple[str, ...]) -> Any:
    """A `Literal[...]` of closed values read from a data file (never typed here)."""
    return Literal[values]  # pyright: ignore[reportInvalidTypeArguments]


def _lit(axis: str) -> Any:
    return _literal(tuple(vocab.direction_axes()[axis].value_names))


MoodT = _lit("mood_family")
ShapeT = _lit("shape")
DensityT = _lit("density")
DepthT = _lit("depth")
MotionT = _lit("motion_personality")
ImageryT = _lit("imagery")
TypeT = _lit("type_personality")
AmbitionT = _lit("ambition")
DarkT = _lit("dark_stance")

KindT = _literal(tuple(design_data().model_asset_kinds))
PurposeT = _literal(tuple(design_data().model_asset_purposes))

Profile = Literal["compact", "standard", "deep"]


class DsgnIn(CallInput):
    # trusted, built by code
    language: str
    profile: Profile
    count: int  # how many candidate directions to write (config.profiles.directions)
    brand_locked: bool  # the user gave a brand colour: it stays exactly as given
    brand_colour: str  # that colour, or an empty string
    has_logo: bool
    languages: list[str]
    scripts: list[str]
    app_ids: list[str]
    app_classes: list[str]  # "APP-001 consumer": code's reading of each app's audience
    ambition_hint: str  # the default floor for this audience
    min_axis_differences: int
    min_hue_gap: float
    max_assets: int
    seed: int  # stable per program: the mock and the fallback choose with it
    vocabularies: list[str]  # "axis: a | b | c": the closed sets, from the data files
    # untrusted: the Brief's own words and names
    brief: str
    audience: str  # one person or group per line
    apps: str  # one app per line: "APP-001 | name | surface"
    entities: str  # one entity per line: "Name | class"


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DirectionOut(W):
    name: str
    mood: MoodT  # type: ignore[valid-type]
    personality: str
    audience_context: str
    emotional_goals: list[str]  # one line per journey stage: "Stage: the feeling"
    motion_personality: MotionT  # type: ignore[valid-type]
    depth: DepthT  # type: ignore[valid-type]
    shape: ShapeT  # type: ignore[valid-type]
    density: DensityT  # type: ignore[valid-type]
    imagery: ImageryT  # type: ignore[valid-type]
    type_personality: TypeT  # type: ignore[valid-type]
    dark_stance: DarkT  # type: ignore[valid-type]
    ambition: AmbitionT  # type: ignore[valid-type]
    delight_budget: int
    voice_line: str
    avoid: list[str]
    seed_colour: str | None  # null when a brand colour is Locked
    admin_density: DensityT | None  # type: ignore[valid-type]  # deep only, else null
    admin_motion: MotionT | None  # type: ignore[valid-type]
    admin_ambition: AmbitionT | None  # type: ignore[valid-type]
    admin_note: str  # deep only, else an empty string


class AssetOut(W):
    kind: KindT  # type: ignore[valid-type]
    purpose: PurposeT  # type: ignore[valid-type]
    subject: str
    count: int
    avoid: list[str]
    app: str  # an app id, or an empty string for every app


class DsgnOut(W):
    directions: list[DirectionOut]
    assets: list[AssetOut]
