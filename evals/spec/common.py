"""Shared pieces of the proof tools: thresholds from data, verdicts, money, the guarded runner, report writing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mde_core.clock import SystemClock
from mde_core.settings import Settings, load_settings
from mde_spec_agent.calls.base import CallRunner, RunResult, SpecCallSpec
from mde_spec_agent.compose import build_gateway, prices_of
from mde_spec_agent.config import SpecSettings
from mde_spec_agent.spend import Prices, SpendGuard, SpendRecord
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.profiles import load_profiles
from mde_spec_core.verify_port import CallOutcome, PortCallSpec

VERDICTS = ("pass", "fail", "not_measured", "single_run")

DATA_DIR = Path(__file__).parent / "data"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------------------------------- thresholds


def thresholds(cfg: SpecConfig | None = None) -> dict[str, float]:
    """The numbers of doc 16 as configuration (`spec-config.yaml` `proof:`); never copied into code."""
    return dict((cfg or default_config()).proof.model_dump())


# ---------------------------------------------------------------------------------------------------- comparisons


@dataclass(frozen=True)
class Check:
    name: str
    value: float | int | bool | None
    op: str  # "<=", ">=", "==", "info"
    threshold: float | int | bool | None
    ok: bool | None  # None = could not be computed

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": _round(self.value),
            "op": self.op,
            "threshold": self.threshold,
            "ok": self.ok,
        }


def _round(v: Any) -> Any:
    return round(v, 4) if isinstance(v, float) else v


def compare(name: str, value: float | int | bool | None, op: str, threshold: Any) -> Check:
    if value is None:
        return Check(name, None, op, threshold, None)
    if op == "<=":
        ok = value <= threshold
    elif op == ">=":
        ok = value >= threshold
    elif op == "==":
        ok = value == threshold
    else:
        raise ValueError(f"unknown comparison {op!r}")
    return Check(name, value, op, threshold, bool(ok))


def checks_pass(checks: Sequence[Check]) -> bool | None:
    """True when every computable check passes; None when none could be computed or one could not be."""
    if not checks or any(c.ok is None for c in checks):
        return None
    return all(c.ok for c in checks)


def verdict_for(would_pass: bool | None, *, live: bool, runs: int = 1) -> str:
    """The only place a verdict is made. Offline stub results are NOT measurements ('not_measured'); a live single run
    is reported as 'single_run' (a single run decides nothing, doc 16 PS-5); pass or fail needs repeated live runs."""
    if would_pass is None or not live:
        return "not_measured"
    if runs < 2:
        return "single_run"
    return "pass" if would_pass else "fail"


# ---------------------------------------------------------------------------------------------------- money


def spent_micros(runner: CallRunner | None) -> int:
    """Integer micro-USD of every call this runner made (zero offline)."""
    if runner is None:
        return 0
    return sum(int(r.cost_micros) for _id, r in runner.calls)


def live_share(runner: CallRunner | None, since: int = 0) -> float:
    """Share of the calls made after index `since` that really went to a model (0.0 offline or without calls)."""
    calls = list(runner.calls)[since:] if runner else []
    return sum(1 for _i, r in calls if r.live) / len(calls) if calls else 0.0


def is_live(runner: CallRunner | None, since: int = 0) -> bool:
    calls = list(runner.calls)[since:] if runner else []
    return bool(calls) and all(r.live for _i, r in calls)


def fallback_calls(runner: CallRunner | None, since: int = 0) -> int:
    calls = list(runner.calls)[since:] if runner else []
    return sum(1 for _i, r in calls if r.used_fallback)


# ---------------------------------------------------------------------------------------------------- the guarded runner


def proof_call_specs() -> list[Any]:
    """Every gateway CallSpec the proof tools use: their own, plus Verify's three (a registry the gateway is built with)."""
    from evals.spec.calls.diversity.spec import SPEC as DIVERSITY
    from evals.spec.calls.freedom.spec import SPEC as FREEDOM
    from evals.spec.calls.lookup.spec import SPEC as LOOKUP
    from mde_verify_agent.calls.registry import ALL_SPECS

    return [FREEDOM.spec, LOOKUP.spec, DIVERSITY.spec, *(s.spec for s in ALL_SPECS)]


def make_proof_runner(
    workdir: Path,
    *,
    core: Settings | None = None,
    run_id: str = "proof",
) -> CallRunner:
    """The ONE way a proof tool reaches a model: Spec's own `CallRunner` behind a `SpendGuard` and its own spend record
    (separate from every other budget; restartable). Mode follows `core.llm_mode`: the default is mock (offline). A live
    run is the lead's: `make_proof_runner(dir, core=load_settings())` in an environment where `LLM_MODE=live` is set.
    Nothing here sets that variable or reads a key."""
    core = core or load_settings(llm_mode="mock")
    offline = core.llm_mode != "live"
    settings = SpecSettings()
    workdir.mkdir(parents=True, exist_ok=True)
    record = SpendRecord(
        workdir / "spend-record.json",
        cap_micros=settings.cap_micros,
        tripwire_micros=settings.tripwire_micros,
    )
    kw: dict[str, Any] = {}
    if (
        not offline
    ):  # a live run books to Spec's own period ledger, like the service (never the shared one)
        from mde_spec_agent.compose import period_ledger_path

        kw["ledger_path"] = period_ledger_path(record)
    gateway = build_gateway(
        core, settings, SystemClock(), offline=offline, extra_specs=proof_call_specs(), **kw
    )
    prices = Prices("0", "0") if offline else prices_of(load_profiles().get("weak-default"))
    return CallRunner(gateway, SpendGuard(record, prices), run_id=run_id)


class RunnerCall:
    """The `ModelCall` the Verify port needs, standing on the SAME guarded runner (exactly what Spec's service does)."""

    def __init__(self, runner: CallRunner) -> None:
        self.runner = runner

    async def __call__(
        self, spec: PortCallSpec, inp: Any, *, node_ids: Sequence[str] = (), step: str = ""
    ) -> CallOutcome:
        res: RunResult[Any] = await self.runner.run(
            as_spec_call(spec), inp, node_ids=node_ids, step=step
        )
        return CallOutcome(
            output=res.output,
            used_fallback=res.used_fallback,
            flagged=res.flagged,
            cost_micros=res.cost_micros,
        )


def as_spec_call(spec: PortCallSpec | SpecCallSpec[Any, Any]) -> SpecCallSpec[Any, Any]:
    if isinstance(spec, SpecCallSpec):
        return spec
    return SpecCallSpec(
        spec=spec.spec,
        kind=spec.kind,
        timeout_s=spec.timeout_s,
        fallback=spec.fallback,
        validators=spec.validators,
    )


# ---------------------------------------------------------------------------------------------------- reports


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_int(*parts: object) -> int:
    """A deterministic integer from its parts (never Python's salted hash)."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def stable_order(items: Sequence[str], salt: str) -> list[str]:
    return sorted(items, key=lambda x: stable_int(salt, x))


@dataclass
class Tally:
    """Counts of calls and money for one tool, so every report states what it spent."""

    calls: int = 0
    fallbacks: int = 0
    micros: int = 0
    live: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def since(cls, runner: CallRunner | None, start: int) -> Tally:
        calls = list(runner.calls)[start:] if runner else []
        return cls(
            calls=len(calls),
            fallbacks=sum(1 for _i, r in calls if r.used_fallback),
            micros=sum(int(r.cost_micros) for _i, r in calls),
            live=bool(calls) and all(r.live for _i, r in calls),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "fallback_calls": self.fallbacks,
            "cost_micro_usd": self.micros,
            "live": self.live,
        }


def call_count(runner: CallRunner | None) -> int:
    return len(runner.calls) if runner else 0


Handler = Callable[..., Any]
