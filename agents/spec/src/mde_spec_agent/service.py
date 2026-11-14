"""The Spec run service: one object that owns the run store, the spend record, the settings, the profiles and the
background tasks of every active run. The HTTP API (``mde_spec_agent.api``) and the headless ``bench run`` both use it.

Rules this module keeps:

* A run is either live (the gateway in live mode, inside the USD cap) or offline (the gateway's own mock mode). There is
  NO switch that selects a test double: tests and the fake app drive the same class through ``offline=True``.
* The zero-cost PREVIEW (code only) runs before anything is created. A live run is refused when the estimate's upper
  bound exceeds the money that is left (``refused_estimate``), when the budget is at the tripwire or exhausted
  (``budget_blocked``) or when a route's key is missing (``not_ready``, the setting named by NAME only).
* Every model call, Verify's included, passes through the one ``CallRunner`` of the run, so the cap cannot be bypassed.
* Gates are decisions of a person: G1.5 records the consent, G2 binds the content hash and is the only way to
  ``approved``. ``run_to_end`` auto-approves them ONLY because it is the headless helper of the terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mde_compass_intake import intake as read_intake
from mde_compass_intake import read_research
from mde_core.clock import Clock, SystemClock
from mde_core.errors import CircuitOpen
from mde_core.logging import get_logger
from mde_core.settings import Settings, load_settings
from mde_llm_gateway.factory import route_problems, routes_share_vendor
from mde_spec_agent.bench.autoselect import SIZES, auto_select, research_matches
from mde_spec_agent.bench.inputs import (
    CompassEntry,
    ResearchEntry,
    classify_file,
    default_research_runs_dir,
    discover_compass_report,
    discover_research,
    synth_name,
)
from mde_spec_agent.calls.base import CallRunner, NoCredit, RunResult, SpecCallSpec
from mde_spec_agent.compose import (
    build_context,
    build_gateway,
    period_ledger_path,
    prices_of,
    spend_record,
)
from mde_spec_agent.config import (
    SpecSettings,
    data_dir,
    default_input_dirs,
    load_spec_settings,
)
from mde_spec_agent.derive.choices import (
    everything_in_one_release,
    moves_in_force,
)
from mde_spec_agent.derive.pipeline import derive_graph
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.orchestrator import Orchestrator, Outcome, Stage, StepDef
from mde_spec_agent.run.pipeline import build_pipeline
from mde_spec_agent.run.store import ACTIVE, RunStore
from mde_spec_agent.run.store import NotFound as StoreNotFound
from mde_spec_agent.run.store import RunActive as StoreRunActive
from mde_spec_agent.spend import SpendRecord, StopRequested
from mde_spec_agent.views.load import LoadedRun, load_run
from mde_spec_agent.views.preview import preview as build_preview
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.graph import SpecGraph
from mde_spec_core.profiles import ConsumerProfile, ProfileSet, load_profiles
from mde_spec_core.synth import PRESETS, make_graph
from mde_spec_core.verify_port import (
    BriefView,
    CallOutcome,
    CanaryResult,
    FinalInput,
    PortCallSpec,
    VerifyPort,
    canary_cards,
    load_verify_port,
    merge_canaries,
    verify_enabled,
)

log = get_logger(__name__)

RUN_ID = re.compile(r"^run_[0-9A-Za-z]{10,40}$")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
STOP_GRACE_S = 10.0

PLANNING_IN_TOKENS = (
    20_000  # the average writer call the concurrency (k) is computed for, as in compose
)


# --------------------------------------------------------------------------------------------------- errors


class SpecServiceError(Exception):
    """A refusal with a plain message and (often) a fix. ``code`` is one of the documented API error codes."""

    code = "bad_request"
    status = 400

    def __init__(self, message: str, fix: str = "", **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix
        self.extra = extra

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fix:
            err["fix"] = self.fix
        err.update(self.extra)
        return {"error": err}


class BadRequest(SpecServiceError):
    code, status = "bad_request", 400


class NotFound(SpecServiceError):
    code, status = "not_found", 404


class Conflict(SpecServiceError):
    code, status = "conflict", 409


class RunActive(SpecServiceError):
    code, status = "run_active", 409


class NotReady(SpecServiceError):
    code, status = "not_ready", 503


class BudgetBlocked(SpecServiceError):
    """409 (like Research): the request conflicts with the state of the money budget. Documented in the API page."""

    code, status = "budget_blocked", 409


class RefusedEstimate(SpecServiceError):
    code, status = "refused_estimate", 422


class GateRequired(SpecServiceError):
    code, status = "gate_required", 409


# --------------------------------------------------------------------------------------------------- small helpers


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_text(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def _entry_dict(e: CompassEntry, research: list[ResearchEntry]) -> dict[str, Any]:
    return {
        "id": e.id,
        "path": e.path,
        "format": e.format,
        "tier": e.tier,
        "size_band": e.size_band,
        "profile": e.profile,
        "readiness": {"ready": e.readiness.ready, "missing": list(e.readiness.missing)},
        "drift_flags": list(e.drift_flags),
        "research_matches": [r.id for r in research_matches(e, research)],
        "warnings": list(e.warnings),
        "idea": e.idea,
        "features": e.features,
        "screens": e.screens,
        "fixture": e.fixture,
        "synthetic": False,
    }


def _research_dict(r: ResearchEntry) -> dict[str, Any]:
    return {
        "id": r.id,
        "path": r.path,
        "status": r.status,
        "input_hash": r.input_hash,
        "usable": r.usable,
        "created_at": r.created_at,
        "note": r.note,
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@dataclass
class StartResult:
    run_id: str
    joined: bool = False


@dataclass
class Plan:
    """What the zero-cost preview learned about an input (nothing here called a model)."""

    program_id: str
    input_hash: str
    profile: str
    preview: dict[str, Any]
    estimate: dict[str, int | None]
    fields: dict[str, Any] = field(default_factory=dict)
    compass_bytes: bytes = b""


class _RunnerCall:
    """The ``ModelCall`` Verify receives: it wraps the SAME ``CallRunner`` of the run (reserve, reconcile, one re-ask,
    flagged fallback). Verify has no other way to a model, so it cannot bypass the money cap."""

    def __init__(self, runner: CallRunner) -> None:
        self._runner = runner

    async def __call__(
        self, spec: PortCallSpec, inp: Any, *, node_ids: Sequence[str] = (), step: str = ""
    ) -> CallOutcome:
        cs: SpecCallSpec[Any, Any] = SpecCallSpec(
            spec=spec.spec,
            kind=spec.kind,
            timeout_s=spec.timeout_s,
            fallback=spec.fallback,
            validators=spec.validators,
        )
        res: RunResult[Any] = await self._runner.run(cs, inp, node_ids=node_ids, step=step)
        return CallOutcome(
            output=res.output,
            used_fallback=res.used_fallback,
            flagged=res.flagged,
            cost_micros=res.cost_micros,
        )


# --------------------------------------------------------------------------------------------------- the service


class SpecService:
    def __init__(
        self,
        settings: SpecSettings | None = None,
        core: Settings | None = None,
        *,
        data: Path | None = None,
        profiles: ProfileSet | None = None,
        config: SpecConfig | None = None,
        research_runs_dir: Path | None = None,
        readiness: Callable[[], list[str]] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings or load_spec_settings()
        self.core = core or load_settings()
        self.data = Path(data) if data is not None else data_dir(self.settings)
        self.clock = clock or SystemClock()
        self.cfg = config or default_config()
        self.profiles = profiles or load_profiles()
        self.research_runs_dir = research_runs_dir
        self._readiness = readiness  # a seam for the not-ready case; the real app never passes it
        self.fake = False  # only the test fake app sets this (health reports it)
        self.store = RunStore(self.data, clock=self.clock)
        self.record: SpendRecord = spend_record(self.settings, self.data, self.clock)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._ctx: dict[str, RunContext] = {}
        self._gateways: dict[bool, Any] = {}
        self._sem = asyncio.Semaphore(self.settings.spec_max_concurrent_runs)

    # ------------------------------------------------------------------------------------ health, budget, config
    def readiness_problems(self) -> list[str]:
        """Names only (a setting's NAME, never a value) of what a live run still needs; empty = ready."""
        if self._readiness is not None:
            return list(self._readiness())
        problems = route_problems(self.core)
        if getattr(self.core, "llm_mode", "mock") != "live":
            problems.insert(0, "LLM_MODE=live in the launch environment")
        try:
            prices_of(self.profiles.get("weak-default"))
        except (ValueError, KeyError) as exc:
            problems.append(f"verified writer prices in the consumer profiles ({exc})")
        return problems

    def health(self) -> dict[str, Any]:
        problems = self.readiness_problems()
        return {
            "ready": not problems,
            "mode": "live" if not problems else "offline_only",
            "reasons": problems,
            "fake": self.fake,
            "version": _version(),
        }

    def budget(self) -> dict[str, Any]:
        snap = self.record.snapshot()
        snap.pop("entries", None)
        return snap

    def config_view(self) -> dict[str, Any]:
        return {
            "numbers": self.cfg.model_dump(mode="json"),
            "money": {
                "cap_micros": self.settings.cap_micros,
                "tripwire_micros": self.settings.tripwire_micros,
                "source": "SPEC_LIVE_BUDGET_USD and SPEC_TRIPWIRE_USD in the launch environment (never .env)",
            },
            "max_concurrent_runs": self.settings.spec_max_concurrent_runs,
        }

    def profiles_view(self) -> dict[str, Any]:
        from mde_spec_agent.run.concurrency import compute_k

        out = []
        for p in self.profiles.profiles:
            out.append(
                {
                    "id": p.id,
                    "label": p.id.replace("-", " "),
                    "capability_class": p.capability_class,
                    "effective_window": p.effective_context(),
                    "whole_pack_tokens": self.cfg.budgets.whole_pack_tokens,
                    "k": compute_k(p, PLANNING_IN_TOKENS, 50, self.cfg),
                    "unset": list(p.unset_fields),
                }
            )
        return {"profiles": out}

    def consumer(self, profile_id: str | None) -> ConsumerProfile:
        try:
            return self.profiles.get(profile_id or "weak-default")
        except KeyError:
            raise BadRequest(
                f"There is no consumer profile called {profile_id!r}.",
                "Pick one from GET /api/profiles.",
            ) from None

    # ------------------------------------------------------------------------------------ inputs
    def _discover(self) -> tuple[list[CompassEntry], list[ResearchEntry], list[Any]]:
        found = discover_compass_report(default_input_dirs(self.settings))
        runs_dir = self.research_runs_dir or default_research_runs_dir()
        return list(found.entries), discover_research(runs_dir), list(found.skipped)

    def inputs(self) -> dict[str, Any]:
        entries, research, skipped = self._discover()
        return {
            "compass": [_entry_dict(e, research) for e in entries],
            "research": [_research_dict(r) for r in research],
            "skipped": [s.model_dump(mode="json") for s in skipped],
            "synthetic": [f"synth:{name}" for name in sorted(PRESETS)],
        }

    def auto(self, size: str) -> dict[str, Any]:
        if size not in SIZES:
            raise BadRequest(f"size must be one of {', '.join(SIZES)}.")
        entries, research, _ = self._discover()
        props = auto_select(entries, research, size)
        if not props:
            raise NotFound(f"There is nothing to propose for the size {size}.")
        p = props[0]
        return {
            "compass_id": p.compass_id,
            "research_id": p.research_id,
            "reason": p.reason,
            "gaps": list(p.gaps),
            "label": p.label,
            "size": p.size,
            "profile": p.profile,
            "synthetic": p.synthetic,
        }

    def upload(self, raw: bytes) -> dict[str, Any]:
        if len(raw) > MAX_UPLOAD_BYTES:
            raise BadRequest("That file is too large (the limit is 8 MB).")
        try:
            data = json.loads(raw)
        except ValueError:
            raise BadRequest(
                "That file is not JSON.", "Upload the export file Compass wrote."
            ) from None
        if not isinstance(data, dict):
            raise BadRequest("That JSON is not a Compass export (it is not an object).")
        folder = self.data / "inputs"
        folder.mkdir(parents=True, exist_ok=True)
        path = (
            folder / f"upload-{_sha(raw)[:12]}.json"
        )  # a name the server makes: never the client's
        path.write_bytes(raw)
        entry, reason = classify_file(path)
        if entry is None:
            path.unlink(missing_ok=True)
            raise BadRequest(f"That file cannot be used: {reason}.")
        _, research, _ = self._discover()
        return _entry_dict(entry, research)

    # ------------------------------------------------------------------------------------ the zero-cost preview
    def _resolve_compass(
        self, compass_id: str | None, compass_path: Path | None, synth: str | None
    ) -> tuple[CompassEntry | None, Path | None, str | None]:
        if synth is None and compass_id and synth_name(compass_id) is not None:
            synth = synth_name(compass_id)
        if synth is not None:
            if synth not in PRESETS:
                raise BadRequest(
                    f"There is no synthetic input called {synth!r}.",
                    f"Use one of: {', '.join(sorted(PRESETS))}.",
                )
            return None, None, synth
        entries, _, _ = self._discover()
        if compass_path is not None:
            path = Path(compass_path)
            entry = next((e for e in entries if Path(e.path).resolve() == path.resolve()), None)
            if entry is None:
                entry, reason = classify_file(path)
                if entry is None:
                    raise BadRequest(f"{path} cannot be used: {reason}.")
            return entry, path, None
        if not compass_id:
            raise BadRequest(
                "Say which Compass export to use.", "Send compass_id (an id from GET /api/inputs)."
            )
        entry = next((e for e in entries if e.id == compass_id), None)
        if entry is None:
            raise NotFound(
                f"There is no Compass export called {compass_id!r}.",
                "Pick one from GET /api/inputs.",
            )
        return entry, Path(entry.path), None

    def _resolve_research(
        self, research_id: str | None, research_path: Path | None
    ) -> tuple[str | None, Path | None]:
        if research_path is not None:
            path = Path(research_path)
            if path.is_dir():
                path = path / "research.json"
            if not path.is_file():
                raise BadRequest(f"The research file {research_path} was not found.")
            return research_id, path
        if not research_id:
            return None, None
        _, research, _ = self._discover()
        run = next((r for r in research if r.id == research_id), None)
        if run is None or not run.research_path:
            raise NotFound(
                f"There is no usable Research run called {research_id!r}.",
                "Pick one from GET /api/inputs, or run Compass-only.",
            )
        return run.id, Path(run.research_path)

    def plan_input(
        self,
        *,
        compass_id: str | None = None,
        compass_path: Path | None = None,
        synth: str | None = None,
        research_id: str | None = None,
        research_path: Path | None = None,
        consumer_profile: str | None = None,
        offline: bool = True,
    ) -> Plan:
        entry, path, synth = self._resolve_compass(compass_id, compass_path, synth)
        self.consumer(consumer_profile)  # validates the id
        if synth is not None and (research_id or research_path):
            raise BadRequest(
                "A synthetic input has no Research run to pair with.",
                "Leave research_id out, or pick a real Compass export.",
            )
        compass_bytes = b""
        writer = self.profiles.get("weak-default")
        remaining = self.record.snapshot()["remaining_micros"]
        extra: dict[str, Any] = {}
        if synth is not None:
            graph = make_graph(synth)
            pv = build_preview(graph, None, writer, self.cfg, remaining)
            extra["synthetic"] = synth
            fields: dict[str, Any] = {"synth": synth, "compass_id": f"synth:{synth}"}
            program = graph.meta.program_id or f"prog-{synth}"
            comp_key: Any = f"synth:{synth}"
        else:
            assert entry is not None
            assert path is not None
            if not entry.readiness.ready:
                raise BadRequest(
                    "Compass must still ask a few things before Spec can run on this export: "
                    + "; ".join(entry.readiness.missing)
                    + ".",
                    "Finish the interview in Compass, then export again.",
                )
            compass_bytes = path.read_bytes()
            got = read_intake(json.loads(compass_bytes))
            if got.spec_input is None:
                raise BadRequest(
                    "That export has no Brief Spec can read.", "Export it again from Compass."
                )
            research_view = None
            rid, rpath = self._resolve_research(research_id, research_path)
            if rpath is not None:
                research_view = read_research(json.loads(rpath.read_text(encoding="utf-8")))
            dec = derive_graph(got.spec_input, research_view, self.cfg)
            pv = build_preview(dec, dec.decision, writer, self.cfg, remaining)
            plan = dec.plan
            extra.update(
                {
                    "consent_required": plan.consent_required,
                    "consents": [
                        {
                            "kind": c.kind,
                            "app": c.app,
                            "features": list(c.feature_ids),
                            "why": c.why,
                        }
                        for c in plan.needs_consent
                    ],
                    "thin_input": bool(getattr(got.spec_input, "thin_input", False)),
                    "research": rpath is not None,
                }
            )
            fields = {
                "compass_id": entry.id,
                "compass_path": str(path),
                "research_id": rid,
                "research_path": str(rpath) if rpath else None,
            }
            program = entry.brief_id or f"prog-{entry.id}"
            comp_key = [_sha(compass_bytes), _sha(rpath.read_bytes()) if rpath else None]
        preview: dict[str, Any] = {**pv, **extra}
        if offline:  # an offline run costs nothing: the estimate is what a LIVE run would cost
            preview["refused"] = False
            preview.pop("refusal_message", None)
        profile = str(preview["profile"]["id"])
        est = preview["estimate"]
        input_hash = _sha(
            json.dumps(
                [comp_key, consumer_profile or "weak-default", bool(offline)], sort_keys=True
            ).encode()
        )
        fields.update(
            {
                "offline": bool(offline),
                "consumer_profile": consumer_profile or "weak-default",
                "profile": profile,
                "estimate": est,
                "preview": preview,
            }
        )
        return Plan(program, input_hash, profile, preview, est, fields, compass_bytes)

    # ------------------------------------------------------------------------------------ refusals
    def _live_gate(self, preview: dict[str, Any] | None) -> None:
        """The three refusals of a live run, in the order the owner would fix them: money state, estimate, keys."""
        snap = self.record.snapshot()
        if snap["state"] in ("tripwire", "exhausted"):
            raise BudgetBlocked(
                "The money budget is "
                + ("used up" if snap["state"] == "exhausted" else "at its tripwire")
                + ": no new live run starts.",
                "Run offline, or ask the owner to start a new budget period.",
            )
        if preview is not None and preview.get("refused"):
            high = int(preview["estimate"]["high_micros"])
            raise RefusedEstimate(
                str(
                    preview.get("refusal_message")
                    or "This run was not started: its highest estimated cost is more than the money that is left."
                ),
                "Run offline, or ask the owner to raise SPEC_LIVE_BUDGET_USD and restart the app.",
                high_micros=high,
                remaining_micros=snap["remaining_micros"],
            )
        problems = self.readiness_problems()
        if problems:
            raise NotReady(
                "Live runs are not ready: " + "; ".join(problems) + ".",
                "Set the missing settings (names above) in the launch environment, or run offline.",
                missing=problems,
            )

    # ------------------------------------------------------------------------------------ the preview BEFORE a run
    def preview_input(
        self,
        *,
        compass_id: str | None = None,
        synth: str | None = None,
        research_id: str | None = None,
        consumer_profile: str | None = None,
        offline: bool = True,
    ) -> dict[str, Any]:
        """The zero-cost preview of an input before any run exists (the owner sees the cost and says yes).

        Nothing is created, reserved or called: no run folder, no gateway, no money. ``live`` says whether a live
        run could start right now and, when it could not, why in plain words (``blocked``: the same code, message
        and fix the start would answer with); ``mode`` is what the start would do with the ``offline`` flag given.
        """
        plan = self.plan_input(
            compass_id=compass_id,
            synth=synth,
            research_id=research_id,
            consumer_profile=consumer_profile,
            offline=offline,
        )
        live: dict[str, Any] = {"allowed": True, "blocked": None}
        try:
            self._live_gate(plan.preview)
        except SpecServiceError as exc:
            live = {
                "allowed": False,
                "blocked": {"code": exc.code, "message": exc.message, "fix": exc.fix, **exc.extra},
            }
        snap = self.record.snapshot()
        out = dict(plan.preview)
        out.setdefault("estimate", plan.estimate)
        out.update(
            {
                "profile_id": plan.profile,
                "mode": "offline" if offline else "live",
                "live": live,
                "remaining_micros": snap["remaining_micros"],
                "budget_state": snap["state"],
            }
        )
        return out

    # ------------------------------------------------------------------------------------ runs: start, list, read
    async def start_run(
        self,
        *,
        compass_id: str | None = None,
        compass_path: Path | None = None,
        synth: str | None = None,
        research_id: str | None = None,
        research_path: Path | None = None,
        consumer_profile: str | None = None,
        offline: bool = True,
    ) -> StartResult:
        plan = self.plan_input(
            compass_id=compass_id,
            compass_path=compass_path,
            synth=synth,
            research_id=research_id,
            research_path=research_path,
            consumer_profile=consumer_profile,
            offline=offline,
        )
        if not offline:
            self._live_gate(plan.preview)
        try:
            joined = self.store.create_run(plan.program_id, plan.input_hash, plan.fields)
        except StoreRunActive as exc:
            raise RunActive(
                f"A different run is already active for this program ({exc.run_id}).",
                "Wait for it, stop it, or approve it before starting another.",
                run_id=exc.run_id,
            ) from None
        if joined.joined:
            return StartResult(joined.run_id, True)
        rid = joined.run_id
        if plan.compass_bytes:
            (self.store.run_dir(rid) / "input.json.gz").write_bytes(
                gzip.compress(plan.compass_bytes, mtime=0)
            )
        self.store.append_event(
            rid,
            "run.queued",
            data={"profile": plan.profile, "offline": bool(offline), "estimate": plan.estimate},
        )
        self._spawn(rid)
        return StartResult(rid, False)

    def _check_id(self, run_id: str) -> None:
        if not RUN_ID.match(run_id):
            raise NotFound("There is no such run.")

    def get_record(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        try:
            return self.store.get_run(run_id)
        except StoreNotFound:
            raise NotFound("There is no such run.") from None

    def _spend_by_run(self) -> dict[str, int]:
        try:
            data = json.loads(self.record.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out: dict[str, int] = {}
        for e in data.get("entries", []):
            rid = e.get("run_id")
            if rid:
                out[rid] = out.get(rid, 0) + int(e.get("actual_micros", 0))
        return out

    def _snapshot_of(self, rec: dict[str, Any], spend: dict[str, int]) -> dict[str, Any]:
        status = rec["status"]
        events = self.store.events(rec["id"], after=0) if status in ACTIVE else []
        last = events[-1].seq if events else self._last_seq(rec["id"])
        return {
            "id": rec["id"],
            "run_id": rec["id"],
            "status": status,
            "step": rec.get("step"),
            "profile": rec.get("profile"),
            "program_id": rec.get("program_id"),
            "offline": bool(rec.get("offline", True)),
            "gates": {
                "g15": "open" if status == "paused_g15" else "closed",
                "g2": "open" if status == "awaiting_g2" else "closed",
            },
            "gate_records": rec.get("gates", {}),
            "spend_micros": spend.get(rec["id"], 0),
            "estimate": rec.get("estimate") or {"low_micros": None, "high_micros": None},
            "banners": rec.get("banners", []),
            "checked_by": rec.get("checked_by", ["self_check"]),
            "critic_vendor": rec.get("critic_vendor"),
            "last_seq": last,
            "partial_reason": rec.get("partial_reason"),
            "content_hash": rec.get("content_hash"),
            "pack_hash": rec.get("package_hash"),
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
            "note": rec.get("note"),
            "compass_id": rec.get("compass_id"),
            "research_id": rec.get("research_id"),
            "consumer_profile": rec.get("consumer_profile"),
            "approved_hash": rec.get("approved_hash"),
        }

    def _last_seq(self, run_id: str) -> int:
        path = self.store.run_dir(run_id) / "events.jsonl"
        if not path.is_file():
            return 0
        with path.open("rb") as fh:
            return sum(1 for line in fh if line.strip())

    def snapshot(self, run_id: str) -> dict[str, Any]:
        return self._snapshot_of(self.get_record(run_id), self._spend_by_run())

    def list_runs(self) -> dict[str, Any]:
        spend = self._spend_by_run()
        runs = []
        for rec in self.store.list_runs():
            runs.append(
                {
                    "id": rec["id"],
                    "status": rec["status"],
                    "profile": rec.get("profile"),
                    "spend_micros": spend.get(rec["id"], 0),
                    "created_at": rec.get("created_at"),
                    "pack_hash": rec.get("package_hash"),
                    "content_hash": rec.get("content_hash"),
                    "compass_id": rec.get("compass_id"),
                    "offline": bool(rec.get("offline", True)),
                }
            )
        return {"runs": runs}

    def preview(self, run_id: str) -> dict[str, Any]:
        rec = self.get_record(run_id)
        pv = dict(rec.get("preview") or {})
        pv.setdefault("estimate", rec.get("estimate"))
        return pv

    def is_active(self, run_id: str) -> bool:
        try:
            return self.store.get_run(run_id)["status"] in ACTIVE
        except StoreNotFound:
            return False

    # ------------------------------------------------------------------------------------ driving a run
    def _spawn(self, run_id: str) -> None:
        if run_id in self._tasks and not self._tasks[run_id].done():
            return
        task = asyncio.get_running_loop().create_task(self._drive(run_id), name=f"spec-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda t, rid=run_id: self._tasks.pop(rid, None))

    def _gateway(self, offline: bool) -> Any:
        if offline not in self._gateways:
            self._gateways[offline] = build_gateway(
                self.core,
                self.settings,
                self.clock,
                offline=offline,
                ledger_path=None if offline else period_ledger_path(self.record),
            )
        return self._gateways[offline]

    def _on_call(self, run_id: str) -> Callable[[str, str, RunResult[Any]], None]:
        def emit(step: str, kind: str, res: RunResult[Any]) -> None:
            self.store.append_event(
                run_id,
                "call.finished",
                step=step or None,
                data={
                    "kind": kind,
                    "tokens": {"in": res.tokens_in, "out": res.tokens_out},
                    "cost_micros": res.cost_micros,
                    "repaired": bool(res.repaired or res.reasked),
                    "fallback": res.used_fallback,
                    "reason": (res.reason or "")[:300],
                    "first_errors": [e[:200] for e in res.first_errors[:4]],
                },
            )

        return emit

    def _make_ctx(self, run_id: str, rec: dict[str, Any]) -> RunContext:
        offline = bool(rec.get("offline", True))
        ctx = build_context(
            store=self.store,
            run_id=run_id,
            program_id=rec["program_id"],
            input_hash=rec["input_hash"],
            graph=SpecGraph(),
            core=self.core,
            settings=self.settings,
            record=self.record,
            offline=offline,
            gateway=self._gateway(offline),
            profiles=self.profiles,
            config=self.cfg,
            consumer_id=rec.get("consumer_profile") or "weak-default",
            on_call=self._on_call(run_id),
            clock=self.clock,
        )
        profile = rec.get("profile") or "standard"
        if ctx.runner is not None and verify_enabled(self.cfg, profile):
            ctx.verify = load_verify_port(
                _RunnerCall(ctx.runner),
                self.cfg,
                profile=profile,
                critic_vendor=self.critic_vendor(),
            )
        return ctx

    # The routes Verify's critic calls name (their call specs) and the routes the writer lanes name. Names of routes
    # only: the gateway says whether two routes share a vendor (rule 14: this code never sees a model or vendor name).
    CRITIC_ROUTES = ("standard",)
    WRITER_ROUTES = ("deep", "standard", "fast")

    def critic_vendor(self) -> str:
        """``different`` only when NO writer route shares a vendor with a critic route; otherwise ``same``. Independence
        is never claimed falsely (a bigger or dearer route is not assumed to be a better critic, D-172). With the
        calls as built, the critic and the module writers both use the ``standard`` route, so this is ``same`` by
        construction until a distinct critic route exists; it is computed, not typed."""
        try:
            shared = any(
                routes_share_vendor(self.core, c, w)
                for c in self.CRITIC_ROUTES
                for w in self.WRITER_ROUTES
            )
        except Exception:  # a route the gateway cannot resolve: say the cautious thing
            shared = True
        return "same" if shared else "different"

    def _rehydrate(self, ctx: RunContext, rec: dict[str, Any]) -> None:
        """After a restart the run is rebuilt from its files: the input, the research and the derivation are
        deterministic, so what intake would have set is set again (the graph itself comes from graph.json)."""
        if rec.get("synth"):
            ctx.facts = set(make_graph(rec["synth"]).meta.facts)
            return
        export = json.loads(Path(rec["compass_path"]).read_text(encoding="utf-8"))
        got = read_intake(export)
        ctx.spec_input = got.spec_input
        rpath = rec.get("research_path")
        ctx.research = (
            read_research(json.loads(Path(rpath).read_text(encoding="utf-8"))) if rpath else None
        )
        ctx.derived = derive_graph(
            ctx.spec_input,
            ctx.research,
            ctx.config,
            everything_in_one_release=everything_in_one_release(rec),
            moves=moves_in_force(rec),
        )
        ctx.facts = set(ctx.derived.facts)

    def _guarded(self, name: str, fn: Callable[[RunContext], Awaitable[None]]) -> Any:
        async def step(ctx: RunContext) -> None:
            if ctx.guard.stopping:
                raise StopRequested("stop requested")
            await self._before_step(ctx, name)
            await fn(ctx)

        return step

    async def _before_step(self, ctx: RunContext, name: str) -> None:
        """A hook before every step (a subclass may pace a run so it can be watched). Does nothing here."""

    def _pipeline(self, ctx: RunContext) -> list[Stage]:
        profile = ctx.graph.meta.profile if ctx.graph.screens else None
        profile = profile or self.store.get_run(ctx.run_id).get("profile") or "standard"
        wants_verify = verify_enabled(self.cfg, profile)
        stages = build_pipeline(
            verify=(self._verify_step if ctx.verify is not None else self._verify_not_run)
            if wants_verify
            else None
        )
        return [
            Stage(
                tuple(StepDef(s.name, self._guarded(s.name, s.fn)) for s in st.steps),
                gate_before=st.gate_before,
                gate_when=st.gate_when,
            )
            for st in stages
        ]

    async def _drive(self, run_id: str) -> None:
        async with self._sem:
            rec = self.store.get_run(run_id)
            if rec["status"] not in ACTIVE and rec["status"] != "partial":
                return
            try:
                ctx = self._make_ctx(run_id, rec)
                self._ctx[run_id] = ctx
                if rec.get("done_steps"):
                    self._rehydrate(ctx, rec)
                orch = Orchestrator(self.store, self._pipeline(ctx))
                out = await orch.run(ctx)
                self._after_run(ctx, out)
            except asyncio.CancelledError:
                self._mark_partial(run_id, "stop", "the app was stopped; resume continues")
                raise
            except Exception as exc:
                log.exception("spec_run_crashed", run_id=run_id, error=type(exc).__name__)
                self._fail(run_id, f"{type(exc).__name__}: {exc}"[:300])
            finally:
                ctx2 = self._ctx.get(run_id)
                if ctx2 is not None:
                    with contextlib.suppress(Exception):
                        self.store.update_run(run_id, banners=ctx2.banners, flags=ctx2.flags)

    def _after_run(self, ctx: RunContext, out: Outcome) -> None:
        if out.status == "failed" and out.reason == "MissingNeed":
            log.info("spec_run_missing_needs", run_id=ctx.run_id)

    def _fail(self, run_id: str, note: str) -> None:
        with contextlib.suppress(StoreNotFound):
            self.store.update_run(run_id, status="failed", note=note)
            self.store.append_event(run_id, "run.failed", data={"error": note})

    def _mark_partial(self, run_id: str, reason: str, message: str) -> None:
        with contextlib.suppress(StoreNotFound):
            self.store.update_run(run_id, status="partial", partial_reason=reason, note=message)
            self.store.append_event(
                run_id, "run.partial", data={"reason": reason, "message": message}
            )

    # ------------------------------------------------------------------------------------ the verify step
    async def _verify_not_run(self, ctx: RunContext) -> None:
        """A deep pack with no Verify implementation installed: say so, never claim a check that did not happen."""
        ctx.store.write_text(
            ctx.run_id,
            "evidence/verify.json",
            _json_text(
                {
                    "enabled": False,
                    "verify": "not_run",
                    "reason": "no Verify implementation is installed (entry point mde.spec.verify)",
                }
            ),
        )
        ctx.emit("banner", step="verify", code="verify_not_run", message="Verify did not run.")
        ctx.banners.append(
            {"code": "verify_not_run", "message": "Verify did not run: it is not installed."}
        )

    async def _verify_step(self, ctx: RunContext) -> None:
        """The final stage of Verify for a deep pack: a canary per task card, then the final critic. Class A patches
        are PROPOSED and recorded (there is no applier in spec-core yet), never applied or claimed as applied."""
        port: VerifyPort | None = ctx.verify
        pack = ctx.pack
        if port is None or pack is None:
            await self._verify_not_run(ctx)
            return
        by_id = {str(t["id"]): t for t in pack.tasks}
        ids = canary_cards(pack.tasks, ctx.graph.meta.profile, ctx.config)
        canaries: list[CanaryResult] = []

        async def one(task_id: str) -> CanaryResult:
            card = by_id[task_id]
            reads = card.get("reads", {}) or {}
            paths = [reads.get("prefix"), *(reads.get("global") or []), reads.get("module")]
            paths += list(reads.get("screens") or [])
            wanted = [p for p in dict.fromkeys(str(p) for p in paths if p) if p in pack.files]
            async with ctx.limiter:
                ctx.guard.start_wave()
                try:
                    return await port.canary(card, {p: pack.files[p] for p in wanted}, run_index=0)
                except CircuitOpen as exc:
                    # Verify is a check, never a reason to lose the pack: a canary the provider would not run is
                    # recorded as NOT RUN (flagged, falls back to the code reading); no credit still stops the run.
                    if isinstance(exc, NoCredit):
                        raise
                    ctx.emit("notice", step="verify", what="canary.not_run", task=task_id)
                    return CanaryResult(task_id=task_id, used_fallback=True, flagged=True)

        canaries = list(await asyncio.gather(*(one(t) for t in ids)))
        merged = [merge_canaries([c]) for c in canaries]
        brief = BriefView(
            summary=str(getattr(getattr(ctx.spec_input, "idea", None), "summary", ""))
        )
        ctx.guard.start_wave()
        report = await port.final_critic(FinalInput(graph=ctx.graph, brief=brief, check=ctx.check))
        report.canaries = merged
        dumper = getattr(port, "evidence_json", None)
        try:
            evidence = (
                json.loads(dumper(report, merged)) if dumper else report.model_dump(mode="json")
            )
        except Exception:  # an implementation's dump must not lose the run
            evidence = report.model_dump(mode="json")
        from mde_spec_agent.run import steps_code
        from mde_spec_agent.run.verify_apply import apply_patches

        applied = apply_patches(
            ctx.graph, list(report.patches), max_passes=ctx.config.repair.class_a_max_passes
        )
        for nid in applied.degraded_nodes:
            ctx.flags.append({"kind": "degraded_node", "node": nid, "by": "verify"})
        if applied.applied:  # the single writer changed the graph: rebuild the pack and re-check (touched nodes by hash)
            await steps_code.assemble_and_check(ctx)
        evidence.update(
            {
                "enabled": True,
                "verify": "ran",
                "patches_proposed": len(report.patches),
                "patches_applied": len(applied.applied),
                "patches_skipped": applied.skipped,
                "applied_nodes": applied.touched,
                "class_a_passes": applied.passes,
            }
        )
        ctx.store.write_text(ctx.run_id, "evidence/verify.json", _json_text(evidence))
        vendor = str(getattr(port, "critic_vendor", "same"))
        ctx.store.update_run(ctx.run_id, checked_by=["self_check", "verify"], critic_vendor=vendor)
        manifest_path = "pack/MANIFEST.json"
        manifest = json.loads(ctx.store.read_text(ctx.run_id, manifest_path))
        manifest["checked_by"] = ["self_check", "verify"]
        manifest["critic_vendor"] = vendor
        ctx.store.write_text(
            ctx.run_id,
            manifest_path,
            json.dumps(manifest, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
        )
        pack.manifest["checked_by"] = ["self_check", "verify"]
        pack.manifest["critic_vendor"] = vendor
        ctx.emit(
            "validators.done",
            step="verify",
            gate="verify",
            blockers=len(report.blockers),
            warnings=sum(1 for d in report.findings if d.severity == "warning"),
            canaries=len(merged),
        )

    # ------------------------------------------------------------------------------------ stop, resume
    async def stop_run(self, run_id: str) -> dict[str, Any]:
        rec = self.get_record(run_id)
        if rec["status"] not in ACTIVE:
            raise Conflict(f"This run is {rec['status']}: there is nothing to stop.")
        self._stop_one(run_id, rec)
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=STOP_GRACE_S)
        return self.snapshot(run_id)

    def _stop_one(self, run_id: str, rec: dict[str, Any]) -> None:
        ctx = self._ctx.get(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            if ctx is not None:
                ctx.guard.request_stop()
            return
        # waiting at a gate (or queued with no task): checkpoint is the saved graph; end partial
        self._mark_partial(
            run_id, "stop", "stopped on request; resume continues from the last checkpoint"
        )

    async def stop_all(self) -> int:
        n = 0
        for rec in self.store.list_runs():
            if rec["status"] in ACTIVE:
                self._stop_one(rec["id"], rec)
                n += 1
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            _, still = await asyncio.wait(pending, timeout=STOP_GRACE_S)
            for t in still:
                t.cancel()
            if still:
                await asyncio.gather(*still, return_exceptions=True)
        return n

    async def resume_run(self, run_id: str) -> dict[str, Any]:
        rec = self.get_record(run_id)
        if rec["status"] != "partial":
            raise Conflict(
                f"This run is {rec['status']}: only a partial run can be resumed.",
                "Start a new run instead." if rec["status"] in ("failed", "approved") else "",
            )
        if not rec.get("offline", True):
            self._live_gate(None)
        self.store.update_run(run_id, status="queued", partial_reason=None)
        self.store.append_event(run_id, "run.queued", data={"resume": True})
        self._ctx.pop(run_id, None)
        self._spawn(run_id)
        return self.snapshot(run_id)

    # ------------------------------------------------------------------------------------ gates
    @staticmethod
    def _move_target(raw: Any) -> str:
        """``in`` or ``later`` from what the page sends: a place ('Phase 1 (first release)', 'Phase 2 (later)') or the
        short words themselves."""
        text = str(raw).strip().lower()
        if text in ("in", "phase 1", "first release") or text.startswith(("phase 1", "first")):
            return "in"
        if text in ("later", "phase 2") or text.startswith(("phase 2", "later")):
            return "later"
        raise BadRequest(
            f"A feature can only be moved to the first release or to later, not {raw!r}.",
            "Choose the first release (Phase 1) or later (Phase 2) for each item you move.",
        )

    def _check_moves(self, run_id: str, moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The owner's requested moves, checked and put in terms the release planner works in: FEATURES. The page moves
        what it lists (screens, with the part they belong to); a screen stands for the features it serves, so moving it
        moves those features (and so every screen of theirs). A system screen cannot be moved (the stores require it).
        A refusal names what it did not recognise; nothing is half applied."""
        loaded = self.load(run_id)
        features = set(loaded.graph.features)
        derived = getattr(loaded, "derived", None)
        proposals = (
            {p.id: p for p in derived.inventory.screens}
            if derived is not None and getattr(derived, "inventory", None) is not None
            else {}
        )
        clean: list[dict[str, Any]] = []
        for m in moves:
            item, to = str(m["item"]), self._move_target(m["to"])
            if item in features:
                fids = [item]
            elif item in proposals or item in loaded.graph.screens:
                prop = proposals.get(item)
                node = loaded.graph.screens.get(item)
                if (prop is not None and prop.system) or (node is not None and node.system):
                    raise BadRequest(
                        "This screen is part of the set every app with accounts needs, so it cannot be moved.",
                        "Leave it where it is; the app stores require it.",
                    )
                fids = list(prop.feature_ids) if prop is not None else []
                if not fids:
                    raise BadRequest(
                        "This screen does not serve a feature of its own, so there is nothing to move.",
                        "Move the feature it belongs to, or leave it.",
                    )
                fids = [f for f in fids if f in features]
            else:
                raise BadRequest(
                    f"This run has nothing called {item!r}.",
                    "Pick the item from the list on the structure page.",
                )
            for fid in fids:
                clean.append({"item": fid, "to": to, "asked": item})
        return clean

    def approve_g15(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        rec = self.get_record(run_id)
        decision = payload.get("decision")
        if decision not in ("approve", "change"):
            raise BadRequest("decision must be 'approve' or 'change'.")
        if rec["status"] != "paused_g15":
            raise Conflict(
                f"This run is {rec['status']}, not waiting at the structure gate (G1.5)."
            )
        moves = payload.get("moves") or []
        if not isinstance(moves, list) or not all(
            isinstance(m, dict) and "item" in m and "to" in m for m in moves
        ):
            raise BadRequest("moves must be a list of {item, to}.")
        asked = len(moves)
        if moves:
            moves = self._check_moves(run_id, moves)
        everything = bool(payload.get("everything_in_one_release"))
        consents = [
            {"kind": c["kind"], "app": c["app"], "features": c["features"]}
            for c in (rec.get("preview") or {}).get("consents", [])
        ]
        stamp = self.clock.now().isoformat()
        if decision == "change":
            # The I-4 override record (who asked to move what, when) is kept, and the moves are APPLIED: the release
            # plan is derived again with them and the run stops at this gate once more with the new split. The work the
            # lanes already did is kept wherever its inputs did not change (checkpoints are keyed by input hash).
            if not moves:
                raise BadRequest(
                    "Nothing was asked to change.",
                    "Move at least one feature, or approve the structure as it is.",
                )
            changes = [*rec.get("g15_changes", []), {"moves": moves, "at": stamp}]
            self.store.update_run(run_id, g15_changes=changes, done_steps=[], status="queued")
            self.store.append_event(
                run_id,
                "notice",
                step="freeze",
                data={"what": "g15.changed", "moves": len(moves), "at": stamp},
            )
            self._ctx.pop(run_id, None)
            self._spawn(run_id)
            return {
                "run_id": run_id,
                "gate": "open",
                "recorded": True,
                "moves": asked,
                "applied": True,
            }
        in_force = [
            {"item": a, "to": b}
            for a, b in moves_in_force({"g15_changes": rec.get("g15_changes", []), "gates": {}})
        ] + moves
        record = {
            "moves": in_force,
            "everything_in_one_release": everything
            or bool(((rec.get("gates") or {}).get("g15") or {}).get("everything_in_one_release")),
            "consents": consents,
            "at": stamp,
        }
        Orchestrator(self.store, []).approve_gate(run_id, "g15", record)
        if in_force or record["everything_in_one_release"]:
            # the release split differs from the one derived: derive again from the start (the consent is on file)
            self.store.update_run(run_id, done_steps=[])
        self.store.update_run(run_id, status="queued")
        self._ctx.pop(run_id, None)
        self._spawn(run_id)
        return {"run_id": run_id, "gate": "closed", "recorded": True}

    def missing_needs(self, run_id: str) -> list[str]:
        """Builder needs still missing in the run's gate-3 report (a pack with one never reaches G2)."""
        try:
            raw = json.loads(self.store.read_text(run_id, "evidence/self-check.json"))
        except (StoreNotFound, ValueError):
            return []
        needs = (raw.get("builder_needs") or {}).get("needs", [])
        return [str(n.get("id")) for n in needs if n.get("status") == "missing"]

    def blocking_checks(self, run_id: str) -> list[dict[str, str]]:
        """Checks of the gate-3 report that failed with blocker status (code, where, why, fix), in report order."""
        try:
            raw = json.loads(self.store.read_text(run_id, "evidence/self-check.json"))
        except (StoreNotFound, ValueError):
            return []
        return [
            {k: str(e.get(k, "")) for k in ("code", "where", "why", "fix")}
            for e in raw.get("errors", [])
            if isinstance(e, dict) and e.get("severity") == "blocker"
        ]

    def approve_g2(
        self, run_id: str, content_hash: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        rec = self.get_record(run_id)
        if rec["status"] == "paused_g15":
            raise GateRequired(
                "The structure (G1.5) has not been approved yet.",
                "Approve the structure first, then the spec.",
            )
        if rec["status"] != "awaiting_g2":
            raise Conflict(f"This run is {rec['status']}, not waiting for the final approval (G2).")
        needs = self.missing_needs(run_id)
        if needs:
            raise GateRequired(
                "The pack still has builder needs that are missing: " + ", ".join(needs) + ".",
                "Fix them in Compass or Research, then run again.",
                blocking_needs=needs,
            )
        if not content_hash or content_hash != rec.get("content_hash"):
            raise Conflict(
                "The spec changed since you looked at it, or the hash was not sent.",
                "Reload the page and approve the version you see.",
                content_hash=rec.get("content_hash"),
            )
        blockers = self.blocking_checks(run_id)
        acknowledged = bool((payload or {}).get("acknowledge_blockers"))
        if blockers and not acknowledged:
            codes = sorted({b["code"] for b in blockers if b["code"]})
            raise GateRequired(
                f"{len(blockers)} check(s) failed with blocker status ({', '.join(codes) or 'see the list'}).",
                "Read them on the spec review page. If you accept them, tick the box to approve anyway: "
                "your acknowledgement is recorded with the approval.",
                blocking_checks=blockers[:20],
                blocking_checks_total=len(blockers),
            )
        record: dict[str, Any] = {"content_hash": content_hash, "at": self.clock.now().isoformat()}
        if blockers:
            record["acknowledged_blockers"] = [
                {"code": b["code"], "where": b["where"]} for b in blockers
            ]
        choices = (payload or {}).get("suggestion_choices")
        if isinstance(choices, dict):
            record["suggestion_choices"] = {str(k): bool(v) for k, v in choices.items()}
        Orchestrator(self.store, []).approve_gate(run_id, "g2", record)
        self.store.update_run(run_id, status="approved", approved_hash=content_hash)
        self.store.append_event(
            run_id,
            "run.finished",
            data={"status": "approved", "content_hash": content_hash},
        )
        return {
            "run_id": run_id,
            "status": "approved",
            "content_hash": content_hash,
            "gate": "closed",
        }

    # ------------------------------------------------------------------------------------ views support
    def load(self, run_id: str) -> LoadedRun:
        """Graph, derivation, pack and gate-3 report of a run, read back from its folder for the view functions (the
        derivation is deterministic, so what the run saw is what the views see)."""
        rec = self.get_record(run_id)
        try:
            loaded = load_run(self.store, run_id)
        except StoreNotFound:
            raise Conflict(
                "The structure of this run is not built yet.", "Wait for the run to reach G1.5."
            ) from None
        if not loaded.banners:
            loaded.banners = list(rec.get("banners", []))
        if not loaded.flags:
            loaded.flags = list(rec.get("flags", []))
        return loaded

    # ------------------------------------------------------------------------------------ the probe
    async def probe(self, run_id: str, task_id: str, offline: bool) -> dict[str, Any]:
        """The weakest consumer reads one task card and says what it would build, ask and assume. Offline: the
        canary through the gateway's mock mode (deterministic); live: the same call inside the money cap."""
        from mde_spec_agent.api.packdata import PackFolder

        rec = self.get_record(run_id)
        if not rec.get("content_hash"):
            raise Conflict("This run has no pack yet.", "Wait for the run to reach G2.")
        folder = PackFolder(self.store, run_id)
        card = folder.task(task_id)
        if card is None:
            raise NotFound(f"There is no task card called {task_id!r} in this pack.")
        reads = card.get("reads", {}) or {}
        paths = [reads.get("prefix"), *(reads.get("global") or []), reads.get("module")]
        paths += list(reads.get("screens") or [])
        files = folder.files()
        wanted = [p for p in dict.fromkeys(str(p) for p in paths if p) if p in files]
        if not offline:
            self._live_gate(None)
        probe_id = f"probe_{run_id}"
        probe_rec = dict(rec)
        probe_rec["offline"] = bool(offline)
        ctx = build_context(
            store=self.store,
            run_id=run_id,
            program_id=rec["program_id"],
            input_hash=rec["input_hash"],
            graph=SpecGraph(),
            core=self.core,
            settings=self.settings,
            record=self.record,
            offline=bool(offline),
            gateway=self._gateway(bool(offline)),
            profiles=self.profiles,
            config=self.cfg,
            consumer_id="weak-default",
            clock=self.clock,
        )
        port = (
            load_verify_port(
                _RunnerCall(ctx.runner),
                self.cfg,
                profile="deep",
                critic_vendor=self.critic_vendor(),
            )
            if ctx.runner is not None
            else None
        )
        if port is None:
            if not offline:
                raise NotReady(
                    "A live probe needs Verify, and it is not installed.",
                    "Run the probe offline.",
                    missing=["Verify (entry point mde.spec.verify)"],
                )
            return _offline_stub(card, task_id)
        del probe_id
        try:
            res = await port.canary(card, {p: files[p] for p in wanted}, run_index=0)
        except StopRequested:
            raise Conflict("The probe was stopped.") from None
        return {
            "task_id": task_id,
            "offline": bool(offline),
            "outline": list(res.outline),
            "blocking_questions": list(res.blocking_questions),
            "assumptions": list(res.assumptions),
            "acceptance_coverage": [c.model_dump(mode="json") for c in res.acceptance_coverage],
            "flagged": res.flagged,
        }

    # ------------------------------------------------------------------------------------ lifecycle
    def recover(self) -> list[str]:
        """At start: runs a dead process left running become partial (resumable); orphan spend reservations of
        dead processes are booked at their worst case."""
        ids = self.store.recover_interrupted()
        self.record.recover_orphans(is_alive=_pid_alive)
        return ids

    async def aclose(self) -> None:
        await self.stop_all()

    def wait_sync(self) -> None:  # pragma: no cover - convenience for scripts
        pass


def _offline_stub(card: dict[str, Any], task_id: str) -> dict[str, Any]:
    reads = card.get("reads", {}) or {}
    screens = [str(s) for s in reads.get("screens") or []]
    return {
        "task_id": task_id,
        "offline": True,
        "outline": [f"read {s}" for s in screens] or ["read the prefix"],
        "blocking_questions": [],
        "assumptions": ["offline stub: no model read this card"],
        "acceptance_coverage": [],
        "flagged": False,
    }


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("mde-spec-agent")
    except Exception:
        return "0"


def build_service(
    settings: SpecSettings | None = None, core: Settings | None = None, **kw: Any
) -> SpecService:
    """The composition root of the app: always the real service (live or the gateway's own mock mode)."""
    return SpecService(settings, core, **kw)


# --------------------------------------------------------------------------------------------------- headless


async def _run_to_end(
    service: SpecService,
    *,
    compass_path: Path | None,
    synth: str | None,
    research_path: Path | None,
    offline: bool,
) -> dict[str, Any]:
    started = await service.start_run(
        compass_path=compass_path, synth=synth, research_path=research_path, offline=offline
    )
    rid = started.run_id
    gates: dict[str, str] = {}
    while True:
        task = service._tasks.get(rid)
        if task is not None:
            await asyncio.shield(task)
        rec = service.store.get_run(rid)
        status = rec["status"]
        if status == "paused_g15":
            # headless only: the terminal helper stands in for the person at G1.5 and G2
            service.approve_g15(rid, {"decision": "approve"})
            gates["g15"] = "approved (headless)"
            continue
        if status == "awaiting_g2":
            service.approve_g2(rid, str(rec.get("content_hash")))
            gates["g2"] = "approved (headless)"
            continue
        break
    rec = service.store.get_run(rid)
    return {
        "run_id": rid,
        "run_dir": str(service.store.run_dir(rid)),
        "status": rec["status"],
        "gates": gates,
        "pack_hash": rec.get("package_hash"),
        "content_hash": rec.get("content_hash"),
        "note": rec.get("note"),
    }


def run_to_end(
    compass_path: Path | None = None,
    synth: str | None = None,
    research_path: Path | None = None,
    offline: bool = True,
) -> dict[str, Any]:
    """The whole flow, synchronously, for ``bench run``. Prints nothing and returns
    ``{run_id, run_dir, status, gates, pack_hash}``. A live run is refused like in the app (budget, estimate, keys)."""

    async def go() -> dict[str, Any]:
        service = build_service()
        service.recover()
        try:
            return await _run_to_end(
                service,
                compass_path=compass_path,
                synth=synth,
                research_path=research_path,
                offline=offline,
            )
        finally:
            await service.aclose()

    return asyncio.run(go())


__all__ = [
    "BadRequest",
    "BudgetBlocked",
    "Conflict",
    "GateRequired",
    "NotFound",
    "NotReady",
    "RefusedEstimate",
    "RunActive",
    "SpecService",
    "SpecServiceError",
    "StartResult",
    "build_service",
    "run_to_end",
]
