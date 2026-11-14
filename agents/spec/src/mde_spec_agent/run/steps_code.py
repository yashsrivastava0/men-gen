"""The CODE steps of the pipeline (no model): intake, join (gate 2), freeze, assemble (+ gate 3), plus helpers.

Model-facing steps live in the lanes. Everything here is deterministic.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

from mde_compass_intake import intake as read_intake
from mde_compass_intake import read_research
from mde_spec_agent.derive.choices import everything_in_one_release, moves_in_force
from mde_spec_agent.derive.pipeline import derive_graph
from mde_spec_agent.derive.seed import research_adapter
from mde_spec_core import glossary as glossary_mod
from mde_spec_core import hero as hero_mod
from mde_spec_core.assets import data_bound_asset_needs
from mde_spec_core.callkit.ladder import weak_route_banner
from mde_spec_core.canon import canonical_json
from mde_spec_core.check import spec_check
from mde_spec_core.pack import PackBreach, PackOptions, assemble
from mde_spec_core.rcode import run_rcode
from mde_spec_core.synth import make_graph
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context

from .context import RunContext
from .orchestrator import MissingNeed, PlanInvalid  # noqa: F401  (re-exported for lanes)
from .store import NotFound as StoreNotFound


def _seed(ctx: RunContext) -> random.Random:
    return random.Random(f"{ctx.program_id}:{ctx.input_hash}")  # noqa: S311 - deterministic mock data, not security


async def intake(ctx: RunContext) -> None:
    rec = ctx.store.get_run(ctx.run_id)
    synth = rec.get("synth")
    if synth:
        ctx.graph = make_graph(synth)
        ctx.facts = set(ctx.graph.meta.facts)
        ctx.emit(
            "plan.ready",
            step="intake",
            synthetic=synth,
            screens=len(ctx.graph.screens),
            profile=ctx.graph.meta.profile,
        )
        return
    export = json.loads(await asyncio.to_thread(Path(rec["compass_path"]).read_text, "utf-8"))
    got = read_intake(export)
    if not got.readiness.ready:
        raise MissingNeed(list(got.readiness.missing))
    ctx.spec_input = got.spec_input
    rpath = rec.get("research_path")
    ctx.research = (
        read_research(json.loads(await asyncio.to_thread(Path(rpath).read_text, "utf-8")))
        if rpath
        else None
    )
    dec = derive_graph(
        ctx.spec_input,
        ctx.research,
        ctx.config,
        everything_in_one_release=everything_in_one_release(rec),
        moves=moves_in_force(rec),
    )
    ctx.derived = dec
    ctx.graph = dec.graph
    ctx.facts = set(dec.facts)
    ctx.rcode.extend(dec.seeded.rcode_log)
    ctx.emit(
        "plan.ready",
        step="intake",
        profile=dec.decision.profile,
        screens=len(dec.plan.release_screens),
        consent_required=dec.plan.consent_required,
        thin_input=bool(getattr(ctx.spec_input, "thin_input", False)),
    )


def _vocab_ctx(ctx: RunContext, **kw: Any):
    kw.setdefault("research", research_adapter(ctx.research) if ctx.research is not None else None)
    kw.setdefault(
        "degraded_locales",
        set(getattr(ctx.derived.seeded, "degraded_locales", set())) if ctx.derived else set(),
    )
    return make_context(ctx.graph, config=ctx.config, **kw)


async def join(ctx: RunContext) -> None:
    """Gate 2: R-code fixes, then the join validators. Blockers are listed (the lanes already used their one re-ask)."""
    log = run_rcode(ctx.graph, ctx.facts)
    ctx.rcode.extend(log)
    if len(log):
        ctx.emit("repair.rcode", step="join", count=len(log), by_kind=log.counts())
    findings = run_all(_vocab_ctx(ctx), gate=2)
    blockers = [f for f in findings if f.severity == "blocker"]
    ctx.emit(
        "validators.done",
        step="join",
        gate=2,
        blockers=len(blockers),
        warnings=sum(f.severity == "warning" for f in findings),
    )
    for f in blockers[:200]:
        ctx.flags.append({"kind": "gate2_blocker", **f.as_dict()})


async def freeze(ctx: RunContext) -> None:
    """The Freeze: roles, entities, operations, inventory, navigation, tokens, direction, personas, stories, journeys,
    integrations, the glossary and the hero skeleton become read-only for Screens."""
    g = ctx.graph
    if not g.hero:
        for hid, rec in hero_mod.mint_hero_skeleton(g, _seed(ctx)).items():
            g.hero[hid] = rec
    dirs = sorted(g.directions.values(), key=lambda d: d.id)
    gl = glossary_mod.build_glossary(g, dirs[0] if dirs else None)
    flat: dict[str, str] = {}
    for group in ("entities", "roles", "terms"):
        for k, v in sorted(gl.get(group, {}).items()):
            flat[f"{group}.{k}"] = v if isinstance(v, str) else canonical_json(v)
    for verb in gl.get("verbs", []):
        flat[f"verbs.{verb}"] = verb
    if gl.get("voice"):
        flat["voice"] = str(gl["voice"])
    g.glossary.update(flat)
    g.meta.frozen = True
    ctx.emit(
        "step.finished", step="freeze", hero_records=len(g.hero), glossary_terms=len(g.glossary)
    )


def reasked_screen_ids(reasked: Any, graph: Any) -> set[str]:
    """The re-asked node ids that are screens OF THIS GRAPH (so the banner can never count more screens than exist:
    a re-asked entity, story or copy key is not a screen, and an id the graph does not hold is not counted)."""
    return {str(n) for n in reasked if str(n) in graph.screens}


def refresh_rcode_evidence(ctx: RunContext) -> None:
    """Write the run's R-code log as it stands NOW into ``evidence/self-check.json``. Gate 3 writes that file at
    assemble; repairs made after it (the G2 summary) would otherwise be missing from the saved report."""
    try:
        raw = json.loads(ctx.store.read_text(ctx.run_id, "evidence/self-check.json"))
    except (StoreNotFound, ValueError):
        return
    raw["rcode"] = {
        "changes": len(ctx.rcode),
        "counts": ctx.rcode.counts(),
        "rows": ctx.rcode.rows(),
    }
    ctx.store.write_text(
        ctx.run_id,
        "evidence/self-check.json",
        json.dumps(raw, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
    )


async def assemble_and_check(ctx: RunContext) -> None:
    """Assemble the pack (code) and run gate 3 (`spec check`): the whole suite, the builder-needs rule, the coverage matrix."""
    log = run_rcode(ctx.graph, ctx.facts)
    ctx.rcode.extend(log)
    # data-bound asset needs by code (media fields and hero image briefs exist only after the Freeze and hero records)
    for need in data_bound_asset_needs(ctx.graph):
        ctx.graph.assets.setdefault(need.id, need)
    opts = PackOptions(
        config=ctx.config,
        consumer_id=ctx.consumer.id,
        route_fingerprint=ctx.versions.route_fingerprint,
        library_version=ctx.versions.library,
        # only the checks that really ran: Verify adds itself to the manifest when it FINISHES (service._verify_step),
        # so a run that stops before that never claims an independent check it did not get
        checked_by=("self_check",),
    )
    try:
        pack = assemble(ctx.graph, opts)
    except PackBreach as exc:  # a breach the packager could not re-shard: Spec bug or an un-splittable screen: fail loudly
        ctx.emit("validators.done", step="assemble", gate=3, blockers=1, error=exc.error)
        raise
    reasked = ctx.runner.ledger.reasked_nodes if ctx.runner else set()
    report = spec_check(
        ctx.graph,
        pack,
        rcode_log=ctx.rcode,
        reasks=sorted(reasked),
        flags=ctx.flags,
        banners=ctx.banners,
        options=opts,
        research_view=ctx.research,
        degraded_locales=set(getattr(ctx.derived.seeded, "degraded_locales", set()))
        if ctx.derived
        else set(),
    )
    reasked_screens = reasked_screen_ids(reasked, ctx.graph)
    banner = weak_route_banner(
        len(reasked_screens), max(1, len(ctx.graph.screens)), "screens", ctx.config
    )
    if banner:
        ctx.banners.append({"code": banner.code, "message": banner.message, "step": banner.step})
        ctx.emit("banner", code=banner.code, message=banner.message)
    ctx.pack, ctx.check = pack, report
    base = "pack"
    for path, text in pack.files.items():
        ctx.store.write_text(ctx.run_id, f"{base}/{path}", text)
    ctx.store.write_text(ctx.run_id, "evidence/self-check.json", report.to_json())
    ctx.emit(
        "validators.done",
        step="assemble",
        gate=3,
        blockers=sum(1 for f in report.findings if f.severity == "blocker"),
        warnings=sum(1 for f in report.findings if f.severity == "warning"),
        content_hash=pack.content_hash,
    )
    ctx.store.update_run(ctx.run_id, content_hash=pack.content_hash, package_hash=pack.package_hash)
    missing = [n.id for n in report.needs if n.status == "missing"]
    if missing:
        raise MissingNeed(missing)
