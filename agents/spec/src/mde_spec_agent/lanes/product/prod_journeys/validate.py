"""Local checks of a PROD-JOURNEYS answer: stages on real screens with real action keys (V-05), an emotion from the closed
set that is assumed unless a fact is cited, moments and an unhappy path on primary journeys (V-44), variants that vary a
draft flow, provenance real, no invented number, no form word."""

from __future__ import annotations

from collections import Counter

from mde_spec_agent.lanes.product.kit.common import (
    Errors,
    invented_numbers,
    new_form_words,
    source_numbers,
)

from .repair import repair
from .schema import JourneysIn, JourneysOut


def validate(inp: JourneysIn, out: JourneysOut) -> list[str]:
    out, _ = repair(
        inp, out
    )  # what code mends mechanically is not an error (the patch makes the same repair)
    e = Errors()
    source = "\n".join([*inp.slice, *inp.facts, *inp.research, inp.baseline.model_dump_json()])
    nums = source_numbers(source)
    keys = {s.id: set(s.action_keys) for s in inp.screens}
    roles, apps = set(inp.role_ids), set(inp.app_ids)
    flow_ids = Counter(f.id for f in out.flows)
    wanted = {tuple(r.split(":", 1)) for r in inp.variants_wanted if ":" in r}
    for f in out.flows:
        where = f"flow {f.id}"
        draft = f.id in inp.flow_ids
        if flow_ids[f.id] > 1:
            e.add(f"{where}: listed twice")
        if not draft and not f.id.startswith("NEW-"):
            e.add(f"{where}: use a flow id of this call or NEW-<key> for a variant")
        if f.id.startswith("NEW-"):
            if f.of not in inp.flow_ids:
                e.add(f"{where}: a variant must name the draft flow it varies in `of`")
            if f.variant == "main":
                e.add(
                    f"{where}: a variant is first_run, returning, cross_app or recovery, never main"
                )
            if f.primary:
                e.add(f"{where}: a variant is not the primary journey")
        for r in f.role_ids:
            if r not in roles:
                e.add(f"{where}: role {r} does not exist")
        for a in f.app_ids:
            if a not in apps:
                e.add(f"{where}: app {a} does not exist")
        stages = sorted((s for s in out.stages if s.flow == f.id), key=lambda s: s.n)
        if not stages:
            e.add(f"{where}: write its stages")
            continue
        if [s.n for s in stages] != list(range(1, len(stages) + 1)):
            e.add(f"{where}: stages must be numbered 1, 2, 3 ... without gaps")
        for ends in (stages[0], stages[-1]):
            if not ends.screen_ids:
                e.add(
                    f"{where}: stage {ends.n} must touch a screen (a flow starts and ends on real screens)"
                )
        for m in f.moments:
            if m not in {s.n for s in stages}:
                e.add(f"{where}: moment {m} is not a stage number")
        if f.primary and not f.moments:
            e.add(f"{where}: a primary journey names its moments that matter")
        if f.primary and not f.unhappy:
            e.add(f"{where}: a primary journey needs at least one unhappy path")
        if draft:
            base = next((b for b in inp.baseline.flows if b.id == f.id), None)
            if base is not None:
                missing = [u for u in base.unhappy if u not in f.unhappy]
                if missing:
                    e.add(
                        f"{where}: keep the flow's own alternates ({missing[0][:60]!r}) before any you add"
                    )
        for u in f.unhappy:
            for bad in invented_numbers(u, nums):
                e.add(
                    f"{where}: an unhappy path states {bad}, which no source states (never invent a number)"
                )
            for w in new_form_words(u, source):
                e.add(f"{where}: an unhappy path uses the form word {w!r}")
        for s in stages:
            sw = f"{where} stage {s.n}"
            if not s.goal.strip():
                e.add(f"{sw}: goal is empty")
            for sid in s.screen_ids:
                if sid not in keys:
                    e.add(f"{sw}: screen {sid} does not exist")
            have = {k for sid in s.screen_ids for k in keys.get(sid, set())}
            for ak in s.action_keys:
                if ak not in have:
                    e.add(f"{sw}: action {ak} is not an action of its screens")
            if s.emotion_ref and s.emotion_ref not in inp.ref_ids:
                e.add(
                    f"{sw}: emotion_ref {s.emotion_ref} is not a fact of this call; leave it empty when no fact states the feeling"
                )
            for bad in invented_numbers(f"{s.goal} {s.pain}", nums):
                e.add(
                    f"{sw}: the text states {bad}, which no source states (never invent a number)"
                )
            for w in new_form_words(f"{s.goal} {s.pain}", source):
                e.add(f"{sw}: uses the form word {w!r}")
    for fid in inp.flow_ids:
        if fid not in flow_ids:
            e.add(f"flow {fid}: no record; write one record per flow of this call")
    have_variants = {(f.variant, f.of) for f in out.flows if f.id.startswith("NEW-")}
    for variant, base in sorted(wanted):
        if (variant, base) not in have_variants:
            e.add(f"flow {base}: write its {variant} variant")
    for s in out.stages:
        if s.flow not in flow_ids:
            e.add(f"stage {s.flow}/{s.n}: the flow is not in this answer")
    return e.items
