"""Batch H: the structure of the app drafted on the FIXED ledgers of the two real exports (ADR 0017, plan section 8a).

Only the structure calls run (``cmp.draft_outline``, ``cmp.draft_module``, ``cmp.review_structure`` and the one repair
pass): the Brief and the fact ledger of a recorded session are the input, so this is the cheap loop for calibrating
prompts, token caps, the rule pack and the critic, and the way a 50-60 screen case is exercised without paying for a
30-question interview each time.

    uv run python -m mde_compass.live_structure --replay pune --band medium --dry-run
    uv run python -m mde_compass.live_structure --replay pune --band medium --mock      # offline, free
    LLM_MODE=live ALLOW_LIVE_LLM=1 uv run python -m mde_compass.live_structure --replay pune --band large

A live run needs ``LLM_MODE=live`` and the second key in the OUTER environment, like every live batch (the same
``preflight`` of ``live_batches`` refuses without them and checks the allowance against the ledger). It prints the
counts against the band, the checks that are left, the calls, the wall-clock and the cost, then the structure itself as
plain text for a manual realism review, and writes the full report to ``agents/compass/evals/reports`` (git-ignored).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from mde_compass.catalog.size_bands import BandId, band_for_tier, get_band
from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG, BudgetPolicy, CompassConfig, load_budget_policy
from mde_compass.domain.brief import Brief
from mde_compass.domain.interview import Fact
from mde_compass.engine import structure_checks as checks
from mde_compass.engine import structure_derive as derive
from mde_compass.engine import structure_draft as draft
from mde_compass.engine.brief_builder import slots_from_brief
from mde_compass.engine.compile_plan import project_card
from mde_compass.engine.context_pack import full_ledger
from mde_compass.engine.ports import Ports, TurnContext
from mde_compass.engine.service import Service
from mde_compass.engine.slots import SlotStore
from mde_compass.ledger_spend import compass_spent_micros
from mde_compass.live_batches import PKG_DIR, REPORT_DIR, preflight
from mde_core.clock import FakeClock, SystemClock
from mde_core.money import usd_to_micros
from mde_core.settings import load_settings

REPO_ROOT = PKG_DIR.parents[3]
REPLAYS: dict[str, Path] = {
    "delhi": REPO_ROOT / "docs" / "compass" / "outputs" / "delhi-restaurant-delivery.json",
    "pune": REPO_ROOT / "docs" / "compass" / "outputs" / "pune-clinic-appointments.json",
    "hospital": REPO_ROOT / "docs" / "compass" / "outputs" / "hospital-chain-enterprise.json",
}
DEFAULT_ALLOWANCE_USD = "0.10"
BANDS: tuple[BandId, ...] = ("small", "medium", "large")


@dataclass
class Replay:
    """One recorded session: its Brief (no structure in it) and its active facts."""

    name: str
    brief: Brief
    facts: list[Fact]
    language: str
    tier: str


def load_replay(name_or_path: str) -> Replay:
    path = REPLAYS.get(name_or_path, Path(name_or_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = {k: v for k, v in data["brief"].items() if not k.startswith("_")}
    for key in (
        "modules",
        "screens",
        "flows",
        "entities",
    ):  # an export from after the structure: start clean
        raw.pop(key, None)
    brief = Brief.model_validate(raw)
    facts = [Fact.model_validate(f) for f in data["interview"]["facts"]]
    plan = brief.meta.plan
    return Replay(
        name=path.stem,
        brief=brief,
        facts=[f for f in facts if f.status == "active"],
        language=str(data["session"].get("locale", "en")),
        tier=(plan.interview_budget if plan is not None else None) or "standard",
    )


# ------------------------------------------------------------------------------------------ timing the calls
@dataclass
class CallTimes:
    outline: list[float] = field(default_factory=list)
    module: list[float] = field(default_factory=list)
    review: list[float] = field(default_factory=list)


class _Timed:
    """Wraps one structure port: the same call, plus the seconds it took."""

    def __init__(self, inner: Any, method: str, sink: list[float]) -> None:
        self._inner, self._method, self._sink = inner, method, sink

    async def _run(self, *args: Any) -> Any:
        started = time.monotonic()
        try:
            return await getattr(self._inner, self._method)(*args)
        finally:
            self._sink.append(time.monotonic() - started)

    async def outline(self, ctx: TurnContext, inp: Any) -> Any:
        return await self._run(ctx, inp)

    async def draft(self, ctx: TurnContext, inp: Any) -> Any:
        return await self._run(ctx, inp)

    async def review(self, ctx: TurnContext, inp: Any) -> Any:
        return await self._run(ctx, inp)


def timed_ports(ports: Ports, times: CallTimes) -> Ports:
    return dataclasses.replace(
        ports,
        structure_outliner=_Timed(ports.structure_outliner, "outline", times.outline),  # type: ignore[arg-type]
        structure_drafter=_Timed(ports.structure_drafter, "draft", times.module),  # type: ignore[arg-type]
        structure_reviewer=_Timed(ports.structure_reviewer, "review", times.review),  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------------------------------- the replay
def structure_config(base: CompassConfig = DEFAULT_CONFIG) -> CompassConfig:
    return dataclasses.replace(
        base,
        interview=dataclasses.replace(base.interview, engine="planner", structure_enabled=True),
    )


def default_service(mode: Literal["mock", "live"]) -> Service:
    settings = load_settings(llm_mode="mock") if mode == "mock" else load_settings()
    clock = FakeClock() if mode == "mock" else SystemClock()
    return build_service(
        memory=True, settings=settings, clock=clock, compass_config=structure_config()
    )


async def run_replay(
    replay: Replay,
    band_id: BandId,
    *,
    svc: Service,
    instruction: str = "",
) -> dict[str, Any]:
    """One structure draft over the recorded Brief and facts; the report as plain data."""
    assert svc.ports is not None
    cfg = svc.config
    brief = replay.brief.model_copy(deep=True)
    slots = SlotStore(svc.catalog, clock=svc.clock)
    slots.load(slots_from_brief(brief, svc.catalog, svc.clock))
    digest = full_ledger(replay.facts)
    inp = draft.StructureInput(
        brief=brief,
        facts=replay.facts,
        language=replay.language,
        band=get_band(band_id),
        project_card=project_card(slots, svc.catalog),
        org=svc.structure._org(brief),
        fact_digest=digest[: cfg.context.fact_digest * cfg.context.chars_per_token],
        now=svc.clock.now(),
        instruction=instruction,
        force=True,
    )
    ctx = TurnContext(
        session_id=f"ses_replay_{replay.name[:12]}",
        tenant_id="replay",
        language=replay.language,
        mode=brief.meta.mode if hasattr(brief.meta, "mode") else "guided",  # type: ignore[arg-type]
        profile=brief.meta.situation_profile,
    )
    times = CallTimes()
    started = time.monotonic()
    result = await draft.compute_structure(inp, timed_ports(svc.ports, times), ctx)
    wall = time.monotonic() - started
    work = result.work
    band = get_band(band_id)
    counts = derive.counts(work)
    issues = checks.run_checks(work, band, facts=work.domain_facts)
    must = [f for f in work.scope if f.priority == "must" and f.scope_status == "in"]
    serving: dict[str, int] = {f.id: 0 for f in must}
    for s in work.screens:
        for fid in s.serves:
            if fid in serving:
                serving[fid] += 1
    return {
        "replay": replay.name,
        "band": band_id,
        "wall_seconds": round(wall, 1),
        "calls": {
            "outline": [round(x, 1) for x in times.outline],
            "module": [round(x, 1) for x in times.module],
            "review": [round(x, 1) for x in times.review],
            "total": result.calls,
        },
        "fallbacks": result.fallbacks,
        "counts": {
            "modules": counts.modules,
            "screens": counts.screens,
            "flows": counts.flows,
            "entities": counts.entities,
        },
        "target_screens": list(band.screens),
        "verdict": derive.size_verdict(work, band),
        "issues": [
            {"code": i.code, "severity": i.severity, "ids": list(i.ids), "detail": i.detail}
            for i in issues
        ],
        "errors_left": sum(1 for i in issues if i.severity == "error"),
        "leftover_after_repair": [i.code for i in result.issues],
        "must_features": dict(serving),
        "notes": result.notes,
        "reasons": result.reasons,
        "structure": {
            k: [r.model_dump(mode="json") for r in getattr(work, k)]
            for k in ("modules", "screens", "flows", "entities")
        },
        "research": derive.research_guidance(work, band_id),
        "text": render_structure(work),
    }


def render_structure(brief: Brief) -> str:
    """The structure as plain text, for the manual review of every module against the facts."""
    roles = {r.id: r.name_in_user_words for r in brief.roles}
    features = {f.id: f.name for f in brief.scope}
    entity = {e.id: e.name for e in brief.entities}
    screen = {s.id: s.name for s in brief.screens}
    lines: list[str] = []
    for m in brief.modules:
        lines.append(f"MODULE {m.id} {m.name}: {m.purpose}")
        for s in brief.screens:
            if s.module != m.id:
                continue
            uses = ", ".join(f"{entity.get(u.entity, u.entity)}:{u.use}" for u in s.entities)
            who = ", ".join(roles.get(r, r) for r in s.roles)
            serves = ", ".join(features.get(f, f) for f in s.serves)
            lines.append(
                f"  SCREEN {s.id} {s.name} [{s.pattern}] roles: {who}; serves: {serves}; data: {uses}"
            )
    for f in brief.flows:
        steps = " > ".join(screen.get(st.screen, st.screen) for st in f.steps)
        lines.append(f"FLOW {f.id} {f.name} ({roles.get(f.role, f.role)}): {steps}")
        lines.extend(f"  ACCEPT {a}" for a in f.acceptance)
    for e in brief.entities:
        fields = ", ".join(f"{x.key}:{x.type}{'*' if x.required else ''}" for x in e.fields)
        access = ", ".join(f"{roles.get(r, r)}={lv}" for r, lv in e.access.items())
        lines.append(
            f"ENTITY {e.id} {e.name}: {fields}; states: {', '.join(e.states)}; access: {access}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------- the CLI
def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def warn(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _usd(micros: int) -> str:
    return f"{Decimal(micros) / Decimal(1_000_000):.4f}"


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    ledger_path: Path | None = None,
    out_dir: Path | None = None,
) -> int:
    """Exit codes: 0 ran (or a dry run), 2 refused or bad usage, 1 the run itself failed."""
    environ = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        prog="python -m mde_compass.live_structure",
        description="Batch H: the structure drafted on the ledger of a recorded session.",
    )
    parser.add_argument(
        "--replay", required=True, help="delhi, pune, hospital or the path of an export JSON"
    )
    parser.add_argument("--band", choices=BANDS, default=None, help="default: the band of its tier")
    parser.add_argument("--instruction", default="", help="the person's own words for the draft")
    parser.add_argument("--allowance-usd", default=DEFAULT_ALLOWANCE_USD)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true", help="offline, free: the mock handlers")
    parser.add_argument("--quiet", action="store_true", help="do not print the structure text")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        allowance = usd_to_micros(Decimal(args.allowance_usd))
    except (InvalidOperation, ValueError):
        warn(f"--allowance-usd {args.allowance_usd!r} is not an amount")
        return 2
    try:
        replay = load_replay(args.replay)
    except (OSError, ValueError, KeyError) as exc:
        warn(f"cannot read the replay {args.replay!r}: {type(exc).__name__}: {str(exc)[:200]}")
        return 2
    band_id: BandId = args.band or band_for_tier(replay.tier).id
    mode: Literal["mock", "live"] = "mock" if args.mock else "live"
    settings = load_settings()
    ledger = ledger_path or Path(settings.llm_budget_ledger)
    policy = load_budget_policy(environ)
    spent = compass_spent_micros(ledger)
    say(
        f"batch H: {replay.name} ({len(replay.facts)} active facts, {len(replay.brief.scope)} features, "
        f"{len(replay.brief.roles)} roles, {len(replay.brief.apps)} apps) as a {band_id} app, mode {mode}"
    )
    say(
        f"  allowance USD {_usd(allowance)}; Compass has spent USD {_usd(spent)} of USD {_usd(policy.cap_micros)}"
    )
    reasons = preflight(
        environ,
        ledger,
        allowance,
        policy.cap_micros,
        dry_run=args.dry_run or mode == "mock",
        reserve_micros=int(BudgetPolicy().reserve_usd * 1_000_000),
        stop_fraction=policy.stop_fraction,
        api_key_present=None
        if (args.dry_run or mode == "mock")
        else settings.openai_api_key is not None,
    )
    if args.dry_run:
        for reason in reasons:
            say(f"  a live run would be refused: {reason}")
        say("dry run: nothing was called")
        return 0
    if reasons and mode == "live":
        warn("REFUSED: batch H will not run.")
        for reason in reasons:
            warn(f"  - {reason}")
        return 2
    try:
        svc = default_service(mode)
        before = compass_spent_micros(ledger) if mode == "live" else 0
        report = asyncio.run(run_replay(replay, band_id, svc=svc, instruction=args.instruction))
        after = compass_spent_micros(ledger) if mode == "live" else 0
    except Exception as exc:
        warn(f"the replay failed: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    report["mode"] = mode
    report["cost_micros"] = after - before
    out_dir = out_dir or REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"H-{replay.name}-{band_id}-{mode}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    c = report["counts"]
    say(
        f"  wall {report['wall_seconds']}s, calls {report['calls']['total']} "
        f"(outline {report['calls']['outline']}, modules {report['calls']['module']}, critic {report['calls']['review']}), "
        f"fallbacks {report['fallbacks']}, cost USD {_usd(report['cost_micros'])}"
    )
    say(
        f"  {c['modules']} modules, {c['screens']} screens (band {report['target_screens']}, {report['verdict']}), "
        f"{c['flows']} flows, {c['entities']} entities; errors left {report['errors_left']}, "
        f"issues {[i['code'] for i in report['issues']]}"
    )
    say(f"  must-have features and the screens that serve them: {report['must_features']}")
    if not args.quiet:
        say("")
        say(report["text"])
    say("")
    say(f"report: {path} (gitignored)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
