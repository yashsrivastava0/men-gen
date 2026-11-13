"""One report for the proof gates: `run_all(...)` runs what applies to the packs it is given, writes `proof-report.json`
(every gate's numbers against its threshold, a verdict per gate, the money spent) and `proof-report.txt` (plain words
for the owner). NOTHING is called Proven by this tool: it only reports; the planning chat applies the decision table.

Verdicts: 'pass' or 'fail' (a live measurement repeated at least twice, or a deterministic code measurement),
'single_run' (a live measurement from one run: a single run decides nothing), 'not_measured' (an offline stub result,
or a gate with nothing to measure). Mode is the runner's: no runner means an offline mock runner, never a live one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evals.spec import (
    ablation,
    consumer_probe,
    diversity,
    freedom_probe,
    lookup_probe,
    planted_defects,
)
from evals.spec.common import (
    make_proof_runner,
    spent_micros,
    thresholds,
    write_json,
)
from evals.spec.packs import SIZES, ProofPack, baseline, from_dir, from_graph
from mde_spec_agent.calls.base import PROPAGATE, CallRunner
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.graph import SpecGraph
from mde_spec_core.verify_port import CriticVendor

SCHEMA = "spec.proof-report.v1"
TITLES = {
    "PS-4": "Freedom: the same card built twice, card schema and lint",
    "PS-6": "Consumer probe: can the weakest consumer build a screen from its reads",
    "PS-7": "Planted defects: 22 structural and 8 meaning, self-check and critic arms",
    "PS-10.lookup": "Library lookup on two public catalogues, with planted gaps",
    "PS-10.ablation": "Ablation: does each content bundle earn its place",
    "PS-10.diversity": "Diversity between apps and consistency within one, COULD off and on",
}
_PROFILE_SIZE = {"compact": "quick", "standard": "standard", "deep": "thorough"}
_ORDER = ("not_measured", "fail", "single_run", "pass")


# ---------------------------------------------------------------------------------------------------- inputs


def resolve_packs(source: Any, sizes: tuple[str, ...] = SIZES) -> dict[str, ProofPack]:
    """size -> pack. `source` is None (the real baselines through the derive pipeline), a pack folder, a folder of pack
    folders, or a mapping of name -> ProofPack, SpecGraph or folder."""
    if source is None:
        return {s: baseline(s) for s in sizes}
    if isinstance(source, (str, Path)):
        root = Path(source)
        if (root / "MANIFEST.json").exists():
            p = from_dir(root)
            return {_PROFILE_SIZE.get(p.profile, "standard"): p}
        out: dict[str, ProofPack] = {}
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "MANIFEST.json").exists():
                p = from_dir(child)
                key = next(
                    (s for s in SIZES if s in child.name), _PROFILE_SIZE.get(p.profile, "standard")
                )
                out.setdefault(key, p)
        return out
    out = {}
    for name, v in source.items():
        p = (
            v
            if isinstance(v, ProofPack)
            else from_graph(name, v)
            if isinstance(v, SpecGraph)
            else from_dir(v, name)
        )
        out[name if name in SIZES else (_PROFILE_SIZE.get(p.profile) or name)] = p
    return out


# ---------------------------------------------------------------------------------------------------- gates


def _worst(verdicts: list[str]) -> str:
    if not verdicts:
        return "not_measured"
    for v in _ORDER:
        if v in verdicts:
            return v
    return "not_measured"


def _gate(
    gate_id: str, verdict: str, checks: list[dict[str, Any]], details: Any, **extra: Any
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "title": TITLES[gate_id],
        "verdict": verdict,
        "checks": checks,
        "details": details,
        **extra,
    }


def _skipped(gate_id: str, why: str) -> dict[str, Any]:
    return _gate(gate_id, "not_measured", [], {}, skipped=why)


async def _guard(gate_id: str, coro: Any) -> Any:
    try:
        return await coro
    except PROPAGATE:
        raise
    except Exception as exc:  # a tool failing is a result, never a silent hole
        return _gate(gate_id, "not_measured", [], {}, error=f"{type(exc).__name__}: {exc}")


async def arun_all(
    source: Any,
    out_dir: str | Path,
    runner: CallRunner | None = None,
    *,
    sizes: tuple[str, ...] = SIZES,
    runs: int = 1,
    critic_vendor: CriticVendor = "same",
    planted_graph: SpecGraph | None = None,
    packs_live: bool = False,
    canary_scope: str = "changed",
    cfg: SpecConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or default_config()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    runner = runner or make_proof_runner(out / "_offline-runner")
    packs = resolve_packs(source, sizes)
    gates: dict[str, Any] = {}
    start_calls = len(runner.calls)

    # PS-6 per size
    ps6: list[dict[str, Any]] = []
    for size, pack in packs.items():
        rep = await consumer_probe.run_consumer_probe(runner, pack, size, cfg=cfg)
        consumer_probe.write_report(rep, out / "ps-6" / size)
        ps6.append(
            {
                "size": size,
                "verdict": rep.verdict(runs),
                "would_pass": rep.would_pass(),
                "report": rep.as_dict(),
            }
        )
    gates["PS-6"] = (
        _gate(
            "PS-6",
            _worst([r["verdict"] for r in ps6]),
            [c for r in ps6 for c in r["report"]["checks"]],
            {r["size"]: r["report"] for r in ps6},
        )
        if ps6
        else _skipped("PS-6", "no pack")
    )

    # PS-4 on quick and standard cards (else the first pack)
    ps4_packs = [p for s, p in packs.items() if s in ("quick", "standard")] or list(packs.values())[
        :1
    ]
    ps4: list[dict[str, Any]] = []
    for pack in ps4_packs:
        rep4 = await freedom_probe.run_freedom_probe(
            runner, pack, "standard" if pack.profile != "compact" else "quick", cfg=cfg
        )
        freedom_probe.write_report(rep4, pack, out / "ps-4" / pack.name)
        ps4.append({"pack": pack.name, "verdict": rep4.verdict(runs), "report": rep4.as_dict()})
    gates["PS-4"] = (
        _gate(
            "PS-4",
            _worst([r["verdict"] for r in ps4]),
            [c for r in ps4 for c in r["report"]["checks"]],
            {r["pack"]: r["report"] for r in ps4},
        )
        if ps4
        else _skipped("PS-4", "no pack")
    )

    # PS-7 on a deep pack's graph
    pg = planted_graph
    pname = "given graph"
    if pg is None:
        for key in ("thorough", "standard", "quick"):
            if key in packs and packs[key].graph is not None:
                pg, pname = packs[key].graph, packs[key].name
                break
    if pg is None:
        gates["PS-7"] = _skipped("PS-7", "no graph: planting needs the graph a pack came from")
    else:

        async def _ps7() -> dict[str, Any]:
            prep = await planted_defects.run_planted_defects(
                runner,
                pg,
                name=pname,
                critic_vendor=critic_vendor,
                canary_scope=canary_scope,
                cfg=cfg,
            )
            planted_defects.write_report(prep, out / "ps-7")
            d = prep.as_dict()
            checks = [c for a in prep.arms for c in d["arms"][a]["checks"]]
            return _gate(
                "PS-7",
                prep.verdict(),
                checks,
                d,
                critic_vendor=critic_vendor,
                rounds_used_max=prep.rounds_used_max,
            )

        gates["PS-7"] = await _guard("PS-7", _ps7())

    # PS-10: lookup and ablation on the richest pack, diversity on two packs
    rich = packs.get("standard") or (list(packs.values())[-1] if packs else None)
    if rich is None:
        for g in ("PS-10.lookup", "PS-10.ablation"):
            gates[g] = _skipped(g, "no pack")
    else:

        async def _lookup() -> dict[str, Any]:
            r = await lookup_probe.run_lookup_probe(runner, rich, cfg=cfg)
            lookup_probe.write_report(r, out / "ps-10")
            return _gate(
                "PS-10.lookup", r.verdict(runs), [c.as_dict() for c in r.checks()], r.as_dict()
            )

        async def _ablate() -> dict[str, Any]:
            r = await ablation.run_ablation(
                runner, rich, "standard" if rich.profile != "compact" else "quick", cfg=cfg
            )
            ablation.write_report(r, out / "ps-10")
            return _gate(
                "PS-10.ablation", r.verdict(runs), [c.as_dict() for c in r.checks()], r.as_dict()
            )

        gates["PS-10.lookup"] = await _guard("PS-10.lookup", _lookup())
        gates["PS-10.ablation"] = await _guard("PS-10.ablation", _ablate())
    plist = list(packs.values())
    if len(plist) < 2:
        gates["PS-10.diversity"] = _skipped(
            "PS-10.diversity", "needs two packs from different inputs"
        )
    else:

        async def _div() -> dict[str, Any]:
            r = await diversity.run_diversity(
                plist[0], plist[1], runner, packs_live=packs_live, cfg=cfg
            )
            diversity.write_report(r, out / "ps-10")
            return _gate(
                "PS-10.diversity", r.verdict(), [c.as_dict() for c in r.checks()], r.as_dict()
            )

        gates["PS-10.diversity"] = await _guard("PS-10.diversity", _div())

    calls = list(runner.calls)[start_calls:]
    live = bool(calls) and all(r.live for _i, r in calls)
    money = {
        "cost_micro_usd": sum(int(r.cost_micros) for _i, r in calls),
        "total_in_runner_micro_usd": spent_micros(runner),
        "calls": len(calls),
        "live_calls": sum(1 for _i, r in calls if r.live),
        "fallback_calls": sum(1 for _i, r in calls if r.used_fallback),
        "cap_micro_usd": cfg.money.cap_micro_usd,
    }
    report = {
        "schema": SCHEMA,
        "mode": "live" if live else "offline_stub",
        "runs": runs,
        "inputs": {
            size: {
                "pack": p.name,
                "profile": p.profile,
                "screens": len(p.screen_ids()),
                "has_graph": p.graph is not None,
            }
            for size, p in packs.items()
        },
        "thresholds": thresholds(cfg),
        "gates": gates,
        "verdicts": {k: v["verdict"] for k, v in gates.items()},
        "money": money,
        "note": "This tool only reports numbers against thresholds. It declares nothing decided: the decision table (doc 16 section 7) is applied by people.",
    }
    write_json(out / "proof-report.json", report)
    (out / "proof-report.txt").write_text(plain_summary(report), encoding="utf-8")
    return report


def run_all(
    source: Any,
    out_dir: str | Path,
    runner: CallRunner | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """`run_all(pack_dir_or_runs, out_dir, runner|None)`: see `arun_all` for the keyword options."""
    return asyncio.run(arun_all(source, out_dir, runner, **kw))


# ---------------------------------------------------------------------------------------------------- plain words

_VERDICT_WORDS = {
    "pass": "passed",
    "fail": "did not pass",
    "single_run": "measured once (a single run decides nothing)",
    "not_measured": "not measured",
}


def _fmt(c: Mapping[str, Any]) -> str:
    v = c["value"]
    if v is None:
        return f"{c['name']}: could not be computed"
    shown = (
        f"{v:.0%}"
        if isinstance(v, float) and v <= 1.0 and "per screen" not in c["name"]
        else str(v)
    )
    ok = {True: "ok", False: "NOT ok", None: "?"}[c["ok"]]
    return f"{c['name']}: {shown} (needs {c['op']} {c['threshold']}) {ok}"


def plain_summary(report: Mapping[str, Any]) -> str:
    live = report["mode"] == "live"
    lines = [
        "SPEC PROOF REPORT",
        "",
        (
            "This run called a real model."
            if live
            else "This run used the OFFLINE MOCK reader: the numbers prove that the tools work, not how well the real model does."
        )
        + f" Runs: {report['runs']}.",
        "Nothing here says a decision is settled. These are numbers against the thresholds of the proof plan; people read them and apply its decision table.",
        "",
    ]
    for gid, g in report["gates"].items():
        lines.append(f"{gid}  {g['title']}")
        lines.append(f"  Result: {_VERDICT_WORDS[g['verdict']]}.")
        if g.get("skipped"):
            lines.append(f"  Skipped: {g['skipped']}.")
        if g.get("error"):
            lines.append(f"  The tool stopped with an error: {g['error']}.")
        for c in g["checks"][:14]:
            lines.append("  - " + _fmt(c))
        if len(g["checks"]) > 14:
            lines.append(f"  - and {len(g['checks']) - 14} more in proof-report.json")
        lines.append("")
    m = report["money"]
    lines += [
        f"Model calls: {m['calls']} ({m['live_calls']} live, {m['fallback_calls']} fell back).",
        f"Money spent by this run: {m['cost_micro_usd']} micro-USD (cap {m['cap_micro_usd']} micro-USD).",
        "",
        "For the owner to read: owner-reading/probe-outputs.json (ten consumer outputs) and owner-reading/cards.json (five cards), inside the ps-6 and ps-4 folders.",
        "",
    ]
    return "\n".join(lines)
