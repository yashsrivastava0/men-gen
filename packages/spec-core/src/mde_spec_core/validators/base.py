"""Validator framework (doc 07 section 2, doc 15 9.2, doc 17 10.3, doc 18 3.1).

Every validator is CODE. A finding is a structured, agent-readable error `{code, severity, where, why, fix}` with a
repair class (A code fix, B lane call, C needs decision) so the repair ladder knows what to do.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.graph import SpecGraph

Severity = Literal["blocker", "warning", "info"]
RepairClass = Literal["A", "B", "C"]
Gate = Literal[1, 2, 3]


@dataclass(frozen=True)
class Finding:
    code: str  # V-06
    severity: Severity
    where: str  # node id or file path
    why: str
    fix: str
    repair: RepairClass = "B"
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "where": self.where,
            "why": self.why,
            "fix": self.fix,
            "repair": self.repair,
        }


@dataclass
class VocabView:
    """The closed vocabularies a validator needs, as plain sets (built from the data files by `context.from_data`)."""

    shapes: set[str] = field(default_factory=set)
    intents: set[str] = field(default_factory=set)
    phone_blocked_intents: set[str] = field(
        default_factory=lambda: {"hover", "right_click", "tooltip", "breadcrumb"}
    )
    screen_types: set[str] = field(default_factory=set)
    state_kinds: set[str] = field(default_factory=set)
    input_types: set[str] = field(default_factory=set)
    motion_intents: set[str] = field(default_factory=set)
    essential_motion: set[str] = field(default_factory=set)
    asset_kinds: set[str] = field(default_factory=set)
    mock_profiles: set[str] = field(default_factory=set)
    required_states: dict[str, list[str]] = field(
        default_factory=dict
    )  # screen_type -> state kinds
    behavior_categories: dict[str, list[str]] = field(
        default_factory=dict
    )  # profile -> required categories
    form_words: set[str] = field(default_factory=set)
    form_word_allow: set[str] = field(default_factory=set)
    capability_ids: set[str] = field(default_factory=set)
    policy_ids: set[str] = field(default_factory=set)
    banned_library_names: set[str] = field(
        default_factory=lambda: {
            "react-native-paper",
            "native-base",
            "nativebase",
            "tamagui",
            "gluestack",
            "react-native-elements",
            "ui-kitten",
            "shadcn",
            "material-ui",
            "mui",
            "chakra",
            "antd",
            "bootstrap",
            "tailwind",
            "lottie",
            "reanimated",
            "expo-router",
            "react-navigation",
            "style-dictionary",
            "unsplash",
            "pexels",
        }
    )


@dataclass
class Context:
    graph: SpecGraph
    config: SpecConfig = field(default_factory=default_config)
    vocab: VocabView = field(default_factory=VocabView)
    pack: Any = None  # mde_spec_core.pack.Pack, present at gate 3
    research: Any = None  # a ResearchView (compass-intake) or None
    quotes: dict[str, str] = field(default_factory=dict)  # evidence text by provenance ref (V-45)
    brand_names: set[str] = field(default_factory=set)  # closed list of provider/brand names (V-45)
    degraded_locales: set[str] = field(default_factory=set)
    waivers: set[str] = field(default_factory=set)  # mobile_fit_waiver screen ids
    consents: set[str] = field(default_factory=set)  # G1.5 consent ids
    required_system_types: tuple[str, ...] = ("auth",)

    @property
    def profile(self) -> str:
        return self.graph.meta.profile


@dataclass(frozen=True)
class Validator:
    id: str
    title: str
    gate: Gate  # the earliest gate at which it can run
    fn: Callable[[Context], list[Finding]]
    superseded_by: str | None = None


REGISTRY: dict[str, Validator] = {}


def validator(
    vid: str, title: str, gate: Gate = 3, superseded_by: str | None = None
) -> Callable[[Callable[[Context], list[Finding]]], Callable[[Context], list[Finding]]]:
    def deco(fn: Callable[[Context], list[Finding]]) -> Callable[[Context], list[Finding]]:
        if vid in REGISTRY:
            raise ValueError(f"validator {vid} registered twice")
        REGISTRY[vid] = Validator(vid, title, gate, fn, superseded_by)
        return fn

    return deco


def run_all(ctx: Context, *, gate: Gate = 3, only: set[str] | None = None) -> list[Finding]:
    """Run every validator whose gate is <= `gate` (gate 3 = the whole suite). V-18 is superseded by V-26."""
    from . import _load  # noqa: F401  (imports register the validators)

    out: list[Finding] = []
    for vid in sorted(REGISTRY, key=lambda v: int(v.split("-")[1])):
        v = REGISTRY[vid]
        if v.superseded_by or v.gate > gate or (only and vid not in only):
            continue
        out.extend(v.fn(ctx))
    return out


def blockers(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "blocker"]
