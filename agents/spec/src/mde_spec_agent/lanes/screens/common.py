"""Shared helpers of the Screens lane: its data file, the frozen-facts and glossary text, versions, Gate 1 and flags.

Gate 1 (doc 18 3.1) is "that call's own checks": the batch's patch is applied to a COPY of the graph and the validators
of the touched screens run on the copy. A finding about a touched id becomes a validator error (the call runner re-asks
once per node in the whole run, then falls back and flags). Nothing here calls a model.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable, Sequence
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from mde_spec_agent.run.checkpoint import Versions
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch, apply_patch
from mde_spec_core.canon import canonical_json
from mde_spec_core.config import SpecConfig
from mde_spec_core.glossary import build_glossary, glossary_text
from mde_spec_core.graph import SpecGraph
from mde_spec_core.validators import VocabView, run_all
from mde_spec_core.validators.base import Finding
from mde_spec_core.validators.context import make_context, vocab_view

DATA_DIR = Path(__file__).parent / "data"
GATE1_EXTRA = {"V-08", "V-13", "V-14", "V-22", "V-25", "V-30", "V-37"}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LaneRules(_M):
    version: int
    acceptance_extra_max: dict[str, int]
    usually_max: int
    could_per_screen_default: int
    slots_norms_max: int
    slots_delight_max: int
    slots_moments_max: int
    region_label_copy: bool
    usually_item_kinds: list[str]
    usually_applies_kinds: list[str]
    delight_by_screen_type: dict[str, list[str]]
    motion_by_pattern: dict[str, str]
    could_kind_by_emotion: dict[str, str]
    could_effect_by_kind: dict[str, str]
    could_when: str
    analytics_verb_by_intent: dict[str, str]
    accessibility: dict[str, str]
    behav_mock: dict[str, str]
    g2_template: dict[str, Any]
    g2_max_words: int


@cache
def lane_rules() -> LaneRules:
    return LaneRules.model_validate(
        yaml.safe_load((DATA_DIR / "lane.yaml").read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------- versions


def text_version(*paths: Path) -> str:
    """A short hash of template files: editing a prompt invalidates the checkpoints of the call (doc 15 8.4)."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.read_bytes())
    return h.hexdigest()[:8]


def schema_version(model: type[BaseModel]) -> str:
    return hashlib.sha256(canonical_json(model.model_json_schema()).encode("utf-8")).hexdigest()[:8]


def call_versions(
    ctx: RunContext, call_id: str, version: int, prompt: str, schema: str
) -> Versions:
    return dataclasses.replace(
        ctx.versions, call_spec=f"{call_id}@{version}", prompt=prompt, schema=schema
    )


# ---------------------------------------------------------------- the frozen facts and the glossary


def facts_text(graph: SpecGraph, spec_input: Any = None) -> str:
    """The frozen-facts summary, identical for every call of the run (so provider caching can work). Names and ids only:
    what a worker needs to say Order and Booking the same way and to cite real ids."""
    m = graph.meta
    lines: list[str] = []
    idea = getattr(getattr(spec_input, "idea", None), "summary", "") if spec_input else ""
    if idea:
        lines.append(f"APP IDEA: {' '.join(str(idea).split())}")
    lines.append(
        f"PROFILE: {m.profile}; SOURCE LANGUAGE: {m.source_language}; LANGUAGES: {', '.join(m.languages)}"
        + (f"; COUNTRY: {m.country}" if m.country else "")
    )
    for aid, a in sorted(graph.apps.items()):
        lines.append(
            f"APP {aid} {a.name} ({a.surface}); roles: {', '.join(a.role_ids)}; modules: {', '.join(a.module_ids)}"
        )
    for rid, r in sorted(graph.roles.items()):
        lines.append(f"ROLE {rid} {r.name}" + (f": {r.goal}" if r.goal else ""))
    for mid, mod in sorted(graph.modules.items()):
        lines.append(f"MODULE {mid} {mod.name}" + (f": {mod.purpose}" if mod.purpose else ""))
    for eid, e in sorted(graph.entities.items()):
        if e.later:
            continue
        st = f"; statuses: {', '.join(e.lifecycle.states)}" if e.lifecycle.states else ""
        lines.append(f"ENTITY {eid} {e.name} ({e.klass}){st}")
    for iid, i in sorted(graph.integrations.items()):
        flow = "; the person leaves for the provider's own flow" if i.provider_side_flow else ""
        lines.append(f"INTEGRATION {iid} {i.name} ({i.mode}){flow}")
    return "\n".join(lines)


def glossary_block(graph: SpecGraph, *, config: SpecConfig | None = None) -> str:
    direction = next((d for _, d in sorted(graph.directions.items())), None)
    return glossary_text(build_glossary(graph, direction, config=config))


@cache
def _vocab_cached(facts_key: tuple[str, ...]) -> VocabView:
    return vocab_view(set(facts_key))


def vocab_for(graph: SpecGraph) -> VocabView:
    return _vocab_cached(tuple(sorted(graph.meta.facts)))


# ---------------------------------------------------------------- Gate 1


@dataclasses.dataclass
class Gate1:
    """Apply a batch patch to a copy of the frozen graph and report the blockers about the touched ids."""

    graph: SpecGraph
    config: SpecConfig
    research: Any = None
    only_blockers: bool = True

    def findings(
        self, patch: Patch, touched: Iterable[str], only: set[str] | None = None
    ) -> list[Finding]:
        ids = set(touched)
        g = self.graph.model_copy(deep=True)
        apply_patch(g, patch)
        langs = g.meta.languages or [g.meta.source_language]
        ctx = make_context(
            g,
            vocab=vocab_for(g),
            config=self.config,
            research=self.research,
            degraded_locales=set(langs) - {g.meta.source_language},
        )
        if only is not None:
            found = run_all(ctx, gate=3, only=only)
        else:
            found = run_all(ctx, gate=1) + run_all(ctx, gate=3, only=GATE1_EXTRA)
        out: list[Finding] = []
        seen: set[tuple[str, str, str]] = set()
        for f in found:
            if f.where.split(".")[0] not in ids:
                continue
            if self.only_blockers and f.severity != "blocker":
                continue
            key = (f.code, f.where, f.why)
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out

    def errors(
        self, patch: Patch, touched: Iterable[str], only: set[str] | None = None
    ) -> list[str]:
        return [
            f"{f.code} {f.where}: {f.why}; {f.fix}" for f in self.findings(patch, touched, only)
        ]


# ---------------------------------------------------------------- flags


def degraded_flag(step: str, node: str, reason: str) -> dict[str, Any]:
    return {"kind": "degraded", "step": step, "node": node, "reason": reason[:300]}


def needs_decision_flag(node: str, why: str, *, owner: str = "product") -> dict[str, Any]:
    return {"kind": "needs_decision", "step": "screens", "node": node, "owner": owner, "why": why}


def chunked(items: Sequence[Any], size: int) -> list[list[Any]]:
    size = max(1, size)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def merge_flags(patches: Iterable[Patch]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in patches:
        out.extend(p.get("flags", []))
    return out
