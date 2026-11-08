"""Session metrics (spec section 12, brief 9.3), computed from what a session left behind.

Per session (``collect_metrics``) and over a batch (``aggregate``):

* cards asked per Guided session (target 7, cap 10);
* assumption rate: assumed slots over filled slots;
* first-pass validator rate: sessions whose build needed no repair round and no question;
* valid proposals accepted vs dropped (the builder's own tally);
* screens per app; tokens per slice (the consumer simulator's estimate);
* cost and latency per session (mock mode: cost is 0 and latency is the fake clock, honest zeros);
* research additions accepted (included in the Plan view over all additions);
* injection pass rate (``evals.graders.injection``) and the sufficiency probe accuracy.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from evals.graders.sufficiency import ProbeReport
from evals.harness.checks import check_spec
from evals.harness.driver import FlowDriver
from evals.harness.stack import Stack
from mde_contracts.events import CardEvent, SpecReady

CARDS_TARGET = 7
CARDS_CAP = 10


@dataclass(frozen=True)
class SessionMetrics:
    name: str
    mode: str
    cards_asked: int
    cards_shown: int
    assumed_slots: int
    filled_slots: int
    first_pass: bool
    repair_rounds: int
    proposals_applied: int
    proposals_dropped: int
    screens: int
    tokens_per_slice_mean: float
    tokens_per_slice_max: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_micros: int
    latency_ms: int
    research_additions: int
    research_accepted: int
    validators_clean: bool
    spec_ready: bool
    probe_accuracy: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


async def collect_metrics(
    name: str, driver: FlowDriver, stack: Stack, *, probe: ProbeReport | None = None
) -> SessionMetrics:
    record = await driver.session_record()
    events = await driver.events()
    specs = await driver.specs()
    slots = [s for s in record.discovery.slots.values() if s.value not in (None, "", [])]
    results = stack.build_results
    first_pass = bool(results) and all(r.status == "ok" and r.repair_rounds == 0 for r in results)
    usage = stack.calls.total_usage()
    screens = 0
    tokens_mean, tokens_max = 0.0, 0
    additions = accepted = 0
    clean = False
    if specs:
        spec = specs[-1]
        screens = len(spec.spec.screens)
        checks = check_spec(spec, stack.policies(record.policy_id))
        clean = not checks.errors
        tokens = [s.tokens for s in checks.consumer.slices]
        tokens_mean = statistics.fmean(tokens) if tokens else 0.0
        tokens_max = max(tokens, default=0)
        if spec.plan is not None:
            additions = len(spec.plan.research_added)
            accepted = sum(1 for a in spec.plan.research_added if a.included)
    return SessionMetrics(
        name=name,
        mode=record.mode,
        cards_asked=record.discovery.card_count,
        cards_shown=sum(1 for e in events if isinstance(e, CardEvent)),
        assumed_slots=sum(1 for s in slots if s.state == "assumed"),
        filled_slots=len(slots),
        first_pass=first_pass,
        repair_rounds=sum(r.repair_rounds for r in results),
        proposals_applied=sum(r.proposals_applied for r in results),
        proposals_dropped=sum(r.proposals_dropped for r in results),
        screens=screens,
        tokens_per_slice_mean=round(tokens_mean, 1),
        tokens_per_slice_max=tokens_max,
        llm_calls=len(stack.calls.records),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_micros=usage.cost_micros,
        latency_ms=stack.calls.latency_ms(),
        research_additions=additions,
        research_accepted=accepted,
        validators_clean=clean,
        spec_ready=any(isinstance(e, SpecReady) for e in events),
        probe_accuracy=None if probe is None else round(probe.accuracy, 4),
    )


def _rate(n: int, d: int) -> float | None:
    return None if d == 0 else round(n / d, 4)


def aggregate(sessions: Sequence[SessionMetrics]) -> dict[str, object]:
    """The batch-level numbers the report prints (``None`` where a denominator is zero)."""
    n = len(sessions)
    guided = [s for s in sessions if s.mode == "guided" and s.screens]
    built = [s for s in sessions if s.screens]
    cards = [s.cards_asked for s in guided]
    applied = sum(s.proposals_applied for s in built)
    dropped = sum(s.proposals_dropped for s in built)
    probes = [s.probe_accuracy for s in sessions if s.probe_accuracy is not None]
    return {
        "sessions": n,
        "reached_spec_ready": _rate(sum(1 for s in sessions if s.spec_ready), n),
        "cards_per_guided_session": {
            "mean": round(statistics.fmean(cards), 2) if cards else None,
            "max": max(cards, default=None),
            "target": CARDS_TARGET,
            "cap": CARDS_CAP,
            "within_cap": _rate(sum(1 for c in cards if c <= CARDS_CAP), len(cards)),
            "at_or_under_target": _rate(sum(1 for c in cards if c <= CARDS_TARGET), len(cards)),
        },
        "assumption_rate": _rate(
            sum(s.assumed_slots for s in sessions), sum(s.filled_slots for s in sessions)
        ),
        "first_pass_validator_rate": _rate(sum(1 for s in built if s.first_pass), len(built)),
        "validators_clean_rate": _rate(sum(1 for s in built if s.validators_clean), len(built)),
        "proposals": {
            "applied": applied,
            "dropped": dropped,
            "accepted_share": _rate(applied, applied + dropped),
        },
        "screens_per_app": {
            "mean": round(statistics.fmean(s.screens for s in built), 1) if built else None,
            "min": min((s.screens for s in built), default=None),
            "max": max((s.screens for s in built), default=None),
        },
        "tokens_per_slice": {
            "mean": round(statistics.fmean(s.tokens_per_slice_mean for s in built), 1)
            if built
            else None,
            "max": max((s.tokens_per_slice_max for s in built), default=None),
        },
        "per_session": {
            "llm_calls_mean": round(statistics.fmean(s.llm_calls for s in sessions), 1)
            if n
            else None,
            "cost_micros_mean": round(statistics.fmean(s.cost_micros for s in sessions), 1)
            if n
            else None,
            "latency_ms_mean": round(statistics.fmean(s.latency_ms for s in sessions), 1)
            if n
            else None,
        },
        "research_additions_accepted": _rate(
            sum(s.research_accepted for s in built), sum(s.research_additions for s in built)
        ),
        "sufficiency_probe_accuracy": round(statistics.fmean(probes), 4) if probes else None,
    }
