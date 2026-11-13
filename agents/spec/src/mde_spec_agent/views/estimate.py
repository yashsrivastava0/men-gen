"""The ZERO-COST estimate of a run (doc 15 8.2, doc 18 1.2, doc 19 5): how many model calls, what they may cost, how long.

Pure and deterministic: calls come from the derived plan and the writer profile (items per call follow the writer's
``max_output_tokens``; an unset limit degrades conservatively to one item per call), token guesses per call kind come from
``views/data/estimate.yaml`` (every number there is a [J] estimate), prices come from the writer profile. Money is integer
micro-USD, priced like the spend guard prices a reservation: input x input price + max output x output price, plus the
long-prompt surcharge. ``low`` is the expected cost; ``high`` is the worst case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mde_spec_agent.run.concurrency import compute_k
from mde_spec_agent.spend.worst_case import Prices, worst_case_micros
from mde_spec_core.callkit.fusion import items_per_call, output_cap
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph
from mde_spec_core.profiles import ConsumerProfile
from mde_spec_core.verify_port import verify_enabled

from ._data import estimate_table

STEP_ORDER = (
    "plan",
    "skeleton",
    "modules",
    "integrations",
    "journeys",
    "design",
    "screens",
    "hero",
    "behav",
    "gloss",
    "locales",
    "g2_summary",
    "reasks",
    "canaries",
    "critics",
)


@dataclass(frozen=True)
class PlanCounts:
    """What the estimate counts. Build one by hand to ask 'what if the app had N screens'."""

    profile: str = "standard"
    release_screens: int = 0
    modules: int = 0
    entities: int = 0
    integrations: int = 0
    flows: int = 0
    apps: int = 1
    extra_locales: int = 0
    source_strings: int = 0
    task_cards: int | None = None


def prices_from_profile(profile: ConsumerProfile) -> Prices | None:
    """Worst-case prices from the writer profile (data); ``None`` when the profile has no verified price."""
    p = profile.price
    if not p.input_usd_per_million or not p.output_usd_per_million:
        return None
    return Prices(
        p.input_usd_per_million,
        p.output_usd_per_million,
        p.long_prompt_threshold_tokens,
        p.long_prompt_multiplier,
    )


def counts_of(source: Any) -> PlanCounts:
    """PlanCounts from a PlanCounts, a SpecGraph or a Derived (anything with ``.graph``)."""
    if isinstance(source, PlanCounts):
        return source
    graph: SpecGraph = source if isinstance(source, SpecGraph) else source.graph
    plan = getattr(source, "plan", None)
    if plan is not None and getattr(plan, "release_screens", None) is not None:
        screens = len(plan.release_screens)
    else:
        screens = sum(1 for s in graph.screens.values() if s.status == "in")
    decision = getattr(source, "decision", None)
    profile = decision.profile if decision is not None else graph.meta.profile
    src = graph.locale_copy.get(graph.meta.source_language, {})
    return PlanCounts(
        profile=profile,
        release_screens=screens,
        modules=len(graph.modules),
        entities=sum(1 for e in graph.entities.values() if not e.later and e.status == "in"),
        integrations=sum(1 for i in graph.integrations.values() if i.status == "in"),
        flows=sum(1 for f in graph.flows.values() if f.status == "in"),
        apps=max(1, len(graph.apps)),
        extra_locales=max(0, len(set(graph.meta.languages)) - 1),
        source_strings=len(src),
    )


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b) if b > 0 else 0


def _pct(value: int, percent: int) -> int:
    return -(-value * percent // 100)


def plan_calls(
    counts: PlanCounts, writer: ConsumerProfile, config: SpecConfig
) -> list[tuple[str, int, int, int]]:
    """Per step: ``(step, calls, input tokens per call, expected output tokens per call)``; steps with no call are left out."""
    t = estimate_table()
    kinds = t["calls"]
    prof = counts.profile
    fx = t["fixed_calls"]
    fus = config.fusion
    folded = set(t["folded_into_plan_for_compact"]) if prof == "compact" else set()
    rows: dict[str, tuple[int, int, int]] = {}

    def put(step: str, n: int, out: int | None = None, in_tokens: int | None = None) -> None:
        if n <= 0:
            return
        k = kinds[step]
        rows[step] = (
            n,
            in_tokens if in_tokens is not None else k["in"],
            out if out is not None else k["out"],
        )

    put("plan", fx["plan"][prof])
    put("skeleton", fx["skeleton"][prof])
    if "modules" not in folded:
        put("modules", counts.modules)
    if "integrations" not in folded and counts.integrations:
        put("integrations", _ceil_div(counts.integrations, fus.integrations_per_call))
    if "journeys" not in folded and counts.flows:
        put("journeys", _ceil_div(counts.flows, fus.flows_per_journey_call))
    directions = config.profiles.directions[prof]
    design_out = kinds["design"]["out"]
    cap = output_cap(writer, config)
    fused = (
        prof in t["design_fused_profiles"] and cap is not None and directions * design_out <= cap
    )
    if fused:
        put("design", 1, out=directions * design_out)
    else:
        put("design", directions)
    sc = kinds["screens"]
    per = items_per_call(writer, sc["out_per_item"], fus.cards_per_call_max, config)
    n_screen_calls = _ceil_div(counts.release_screens, per)
    if n_screen_calls:
        put("screens", n_screen_calls, out=sc["out_per_item"] * min(per, counts.release_screens))
    hk = kinds["hero"]
    hper = items_per_call(
        writer, t["hero"]["avg_out_tokens_per_entity"], fus.entities_per_call_max, config
    )
    n_hero = _ceil_div(counts.entities, hper)
    if n_hero:
        put("hero", n_hero, out=hk["out_per_item"] * min(hper, counts.entities))
    put("behav", fx["behav"][prof])
    put("gloss", fx["gloss"][prof])
    strings = max(
        counts.source_strings, counts.release_screens * t["screens"]["strings_per_screen"]
    )
    put("locales", counts.extra_locales * _ceil_div(strings, fus.strings_per_locale_call))
    put("g2_summary", fx["g2_summary"][prof])
    if verify_enabled(config, prof):
        tasks = counts.task_cards
        if tasks is None:
            pilots = config.profiles.pilot_screens_per_app[prof]
            rest = max(0, counts.release_screens - pilots * counts.apps)
            tasks = (
                counts.apps
                + pilots * counts.apps
                + _ceil_div(rest, t["screens"]["screens_per_task_card"])
            )
        share = config.verify.canary_share.get(prof, 0.0)
        put("canaries", math.ceil(tasks * share))
        put("critics", fx["critics"][prof])
    base = sum(n for n, _, _ in rows.values())
    put("reasks", _pct(base, t["reasks_share_percent"]))
    return [(s, *rows[s]) for s in STEP_ORDER if s in rows]


def estimate(
    graph_or_plan: Any,
    writer_profile: ConsumerProfile,
    config: SpecConfig,
    prices: Prices | None,
) -> dict[str, Any]:
    """``{low_micros, high_micros, calls_by_step, minutes_low, minutes_high, ...}``; integers for money and minutes."""
    t = estimate_table()
    counts = counts_of(graph_or_plan)
    rows = plan_calls(counts, writer_profile, config)
    wc = t["worst_case"]
    low = high = 0
    out_in = out_out = 0
    longest = 0
    by_step_micros: dict[str, int] = {}
    max_out_cap = writer_profile.max_output_tokens
    for step, n, tin, tout in rows:
        in_worst = _pct(tin, wc["input_factor_percent"])
        out_worst = _pct(tout, wc["max_output_factor_percent"])
        if max_out_cap:
            out_worst = min(out_worst, max_out_cap)
        longest = max(longest, in_worst)
        if prices is not None:
            lo = worst_case_micros(tin, tout, prices) * n
            hi = worst_case_micros(in_worst, out_worst, prices) * n
        else:
            lo = hi = 0
        low += lo
        high += max(hi, lo)
        by_step_micros[step] = max(hi, lo)
        out_in += tin * n
        out_out += tout * n
    threshold = prices.long_prompt_threshold_tokens if prices else None
    long_ok = threshold is None or longest <= threshold
    tm = t["time"]
    calls_by_step = {s: n for s, n, _, _ in rows}
    k_fast = max(
        1,
        min(
            compute_k(writer_profile, avg_input_tokens=20_000, call_seconds=50, cfg=config),
            tm["workers_cap_low"],
        ),
    )
    k_slow = max(1, config.concurrency.initial)

    def seconds(k: int, per_call: int) -> int:
        waves = sum(_ceil_div(n, k) for n in calls_by_step.values())
        tail = tm["serial_tail_seconds"] if calls_by_step else 0
        return waves * per_call + tail

    return {
        "low_micros": int(low),
        "high_micros": int(high),
        "calls_by_step": calls_by_step,
        "total_calls": sum(calls_by_step.values()),
        "minutes_low": _ceil_div(seconds(k_fast, tm["call_seconds_low"]), 60),
        "minutes_high": _ceil_div(seconds(k_slow, tm["call_seconds_high"]), 60),
        "tokens_in": int(out_in),
        "tokens_out": int(out_out),
        "by_step_high_micros": by_step_micros,
        "long_prompt_ok": bool(long_ok),
        "max_call_input_tokens": int(longest),
        "priced": prices is not None,
        "estimates_are": "judgement [J]: replace with measured values",
    }
