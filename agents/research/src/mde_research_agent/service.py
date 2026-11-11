"""The run service: the lifecycle of a Research run, independent of HTTP.

``start_run`` checks the approved export (hash binding, RV-13), creates the run row (one active run per program) and
starts the run as a background task held by this service (no ``BackgroundTasks``, so shutdown can cancel it and a
restart can mark orphans interrupted). The task: plan -> rounds -> checker -> assemble -> validate -> COMMIT. The commit
is shielded and ordered: the immutable, hashed ``research.json`` is stored FIRST, then the status flips, then the final
event is emitted, so a consumer never sees ``run.finished`` before the data. Skip, per-topic cancel and owner
rejections are commands against a live run or a committed result; rejections are an overlay and never change the
hashed file.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from mde_core.clock import Clock, SystemClock
from mde_core.errors import (
    BudgetExceeded,
    CassetteMissing,
    Conflict,
    LiveCallForbidden,
    NotFound,
)
from mde_core.hashing import hash_json
from mde_core.logging import get_logger
from mde_research_agent.assemble.output import assemble_output
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.config import ResearchSettings
from mde_research_agent.core.hashing import content_hash
from mde_research_agent.domain.events import Counters, RunSnapshot
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.engine.context import RunContext
from mde_research_agent.engine.director import Director, DirectorResult
from mde_research_agent.engine.factory import new_context
from mde_research_agent.engine.spend import GuardSpendBook
from mde_research_agent.events.reducer import fold
from mde_research_agent.events.writer import EventWriter
from mde_research_agent.ids import Ids, UlidIds
from mde_research_agent.intake import read_export
from mde_research_agent.ports.fetch import FetchPort
from mde_research_agent.ports.search import SearchPort
from mde_research_agent.spend import BudgetControl, SpendRecord, ledger_run_spend
from mde_research_agent.store.ports import ACTIVE_STATUSES, Decision, RunRow, Store
from mde_research_agent.validators import ValidationContext, run_all
from mde_research_agent.validators.registry import must_refuse

log = get_logger(__name__)


class NotReady(Conflict):
    """The app cannot start a run yet. ``missing`` names what to set (variable NAMES only, never values)."""

    code = "not_ready"

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "Research cannot start yet. Set: " + ", ".join(missing) + ".",
            details={"missing": list(missing)},
        )
        self.missing = list(missing)


class BudgetBlocked(Conflict):
    """No new run may start: the budget tripwire was reached or the budget is used up."""

    def __init__(self, reason: str) -> None:
        text = (
            "The budget tripwire was reached, so no new research starts. The run in progress finishes."
            if reason == "budget_tripwire"
            else "The budget is used up."
        )
        super().__init__(text, details={"reason": reason})
        self.code = reason
        self.reason = reason


@dataclass
class LiveRun:
    run_id: str
    tenant_id: str
    program_id: str
    inp: ResearchInput
    task: asyncio.Task[None] | None = None
    ctx: RunContext | None = None
    director: Director | None = None
    pending_skip: bool = False
    pending_cancels: list[str] = field(default_factory=list)


class ResearchService:
    def __init__(
        self,
        *,
        store: Store,
        catalog: Catalog,
        settings: ResearchSettings,
        gateway: Any,
        search: SearchPort | None,
        fetch: FetchPort,
        clock: Clock | None = None,
        ids: Ids | None = None,
        as_of: date | None = None,
        mode: str = "mock",
        checker_vendor: str = "same",
        ledger_path: Any = None,
        live_budget_micros: int | None = None,
        sleep: Any = None,
        language: str = "en",
        closers: list[Any] | None = None,
        missing: list[str] | None = None,
        control: BudgetControl | None = None,
        record: SpendRecord | None = None,
        data_dir: Path | None = None,
        inputs: Any = None,
        estimates: Any = None,
        max_concurrent: int = 4,
        gateway_factory: Callable[..., Any] | None = None,
        stale_after_s: int = 600,
        own_store: bool = False,
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.settings = settings
        self.gateway = gateway
        self.search = search
        self.fetch = fetch
        self.clock = clock or SystemClock()
        self.ids = ids or UlidIds()
        self.as_of = as_of
        self.mode = mode
        self.checker_vendor = checker_vendor
        self.ledger_path = ledger_path
        self.live_budget_micros = live_budget_micros
        self.sleep = sleep
        self.language = language
        self.live: dict[str, LiveRun] = {}
        self._closers = closers or []
        self.missing = list(missing or [])
        self.control = control
        self.record = record
        self.data_dir = data_dir
        self.inputs = inputs
        self.estimates = estimates
        self.max_concurrent = max(1, max_concurrent)
        self.gateway_factory = gateway_factory
        self.stale_after_s = stale_after_s
        self.own_store = (
            own_store  # a single-process store (file): every active run at startup is an orphan
        )
        self.queue: list[str] = []
        self._running: set[str] = set()
        self._pump_lock = asyncio.Lock()

    # --- readiness ------------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.gateway is not None and not self.missing

    def _require_ready(self) -> None:
        if not self.ready:
            raise NotReady(self.missing or ["the live switches"])
        if self.search is None:
            raise NotReady(["TAVILY_API_KEY or SERPER_API_KEY"])
        self._require_budget()

    def _require_budget(self) -> None:
        if self.control is not None:
            ok, reason = self.control.can_start()
            if not ok:
                raise BudgetBlocked(reason)

    # --- starting ------------------------------------------------------------------------------------
    async def start_run(
        self,
        tenant_id: str,
        program_id: str | None,
        export: dict[str, Any],
        *,
        input_id: str | None = None,
        input_name: str | None = None,
        raw: bytes | None = None,
    ) -> RunRow:
        if self.mode == "live" or self.gateway is None:
            self._require_ready()
        else:
            self._require_budget()
        inp = read_export(
            export, self.catalog, program_id=program_id
        )  # raises ExportRefused (RV-13 and friends)
        pid = program_id or inp.program_id
        now = self.clock.now()
        size = (
            self.estimates.size_class(inp.load.load_class if inp.load else "standard", light=False)
            if self.estimates is not None
            else None
        )
        row = RunRow(
            run_id=self.ids.new("rrn"),
            tenant_id=tenant_id,
            program_id=pid,
            status="queued",
            brief_hash=inp.brief_hash,
            brief_version=inp.brief_version,
            load_class=inp.load.load_class if inp.load else None,
            load_score=inp.load.score if inp.load else None,
            plan_source=inp.quality.plan_source,
            export_sha256=hash_json(export),
            input_json=inp.model_dump(mode="json"),
            meta={
                "input_id": input_id,
                "input_name": input_name,
                "size_class": size,
                "idea": inp.idea.summary[:200],
            },
            created_at=now,
        )
        await self.store.create_run(row)  # RunActive -> 409
        self._save_input_copy(row.run_id, raw)
        live = LiveRun(run_id=row.run_id, tenant_id=tenant_id, program_id=pid, inp=inp)
        self.live[row.run_id] = live
        self.queue.append(row.run_id)
        await self._pump()
        return await self.get_row(tenant_id, row.run_id)

    def _save_input_copy(self, run_id: str, raw: bytes | None) -> None:
        """The run keeps its own copy of the input: later edits to the file do not change the run."""
        if self.data_dir is None or raw is None:
            return
        try:
            d = self.data_dir / "runs" / run_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "input.json.gz").write_bytes(gzip.compress(raw, 5))
        except OSError:
            log.exception("research_input_copy_failed", run_id=run_id)

    async def start_from_input(self, tenant_id: str, input_id: str) -> tuple[RunRow, bool]:
        """Start a run on a listed input. A second start for an input that is already running returns that run."""
        if self.inputs is None:
            raise NotFound("inputs")
        for r in await self.store.active_runs():
            if r.tenant_id == tenant_id and (r.meta or {}).get("input_id") == input_id:
                return r, False
        loaded = self.inputs.load_export(input_id)
        if loaded is None:
            raise NotFound(f"input {input_id}")
        entry, export, raw = loaded
        row = await self.start_run(
            tenant_id, None, export, input_id=entry.id, input_name=entry.name, raw=raw
        )
        return row, True

    # --- the queue ---------------------------------------------------------------------------------------
    def queue_position(self, run_id: str) -> int | None:
        """1 for the next run to start; None when the run is not waiting."""
        try:
            return self.queue.index(run_id) + 1
        except ValueError:
            return None

    async def _pump(self) -> None:
        """Start queued runs while there is room (``max_concurrent``). Never raises."""
        async with self._pump_lock:
            while self.queue and len(self._running) < self.max_concurrent:
                rid = self.queue.pop(0)
                live = self.live.get(rid)
                if live is None:
                    continue
                self._running.add(rid)
                live.task = asyncio.create_task(self._execute(live), name=f"research-run-{rid}")

    def _pump_later(self) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._pump())

    async def cancel_queued(self, tenant_id: str, run_id: str) -> RunRow:
        row = await self.get_row(tenant_id, run_id)
        if run_id not in self.queue:
            return row
        self.queue.remove(run_id)
        self.live.pop(run_id, None)
        await self._terminal(
            row,
            "run.failed",
            "cancelled",
            "The run was cancelled before it started.",
            status="cancelled",
        )
        return await self.get_row(tenant_id, run_id)

    # --- the run ---------------------------------------------------------------------------------------
    def _remaining_budget(self) -> int | None:
        """What the budget still allows (the only limit is money): the period total minus what it has spent."""
        if self.control is not None:
            return self.control.state().remaining_micros
        if (
            self.mode == "live" and self.live_budget_micros is not None
        ):  # developer scripts: the shared ledger
            from mde_research_agent.engine.spend import research_spent_micros

            return max(0, self.live_budget_micros - research_spent_micros(self.ledger_path))
        return None

    async def _execute(self, live: LiveRun) -> None:
        rid = live.run_id
        started = self.clock.now()
        entry_id: str | None = None
        status_for_record = "failed"
        try:
            await self.store.update_run(live.tenant_id, rid, status="running", started_at=started)
            row_cap = self._remaining_budget()
            period_scope = self.control.period_scope if self.control is not None else None
            search: Any = self.search or cast("SearchPort", _NoSearch())
            fetch: Any = self.fetch
            if self.data_dir is not None and self.search is not None and self.mode == "live":
                from mde_research_agent.adapters.recording import RecordingFetch, RecordingSearch

                cassettes = self.data_dir / "runs" / rid / "cassettes"
                search = RecordingSearch(search, cassettes)
                fetch = RecordingFetch(fetch, cassettes)
            run_gateway = self.gateway
            if (
                self.gateway_factory is not None
                and self.control is not None
                and self.data_dir is not None
                and self.mode == "live"
            ):
                run_gateway = self.gateway_factory(
                    self.control, cassette_dir=self.data_dir / "runs" / rid / "cassettes" / "llm"
                )
            ctx = new_context(
                inp=live.inp,
                catalog=self.catalog,
                search=search,
                fetch=fetch,
                gateway=run_gateway,
                events=self.store,
                clock=self.clock,
                ids=self.ids,
                as_of=self.as_of or self.clock.now().date(),
                run_id=rid,
                run_cap_micros=row_cap,
                period_scope=period_scope,
                language=self.language,
                checker_vendor=self.checker_vendor,
                sleep=self.sleep,
            )
            live.ctx = ctx
            if self.record is not None and self.mode == "live":
                row0 = await self.get_row(live.tenant_id, rid)
                meta = row0.meta or {}
                entry_id = self.record.start_entry(
                    "run",
                    run_id=rid,
                    input_id=meta.get("input_id"),
                    label=str(meta.get("input_name") or meta.get("idea") or rid),
                )
                self.record.set_meta(
                    entry_id,
                    {"size_class": meta.get("size_class"), "load_class": row0.load_class},
                )
                eid = entry_id
                rec = self.record
                ctx.budget.on_cost = lambda step, micros: rec.add_cost(eid, step, micros)
            guard = getattr(getattr(run_gateway, "gateway", None), "guard", None)
            if self.mode == "live" and guard is not None:
                ctx.budget.on_search_cost = GuardSpendBook(guard, rid, self.ids, period_scope)
            director = Director(ctx)
            live.director = director
            load = live.inp.load
            klass = load.load_class if load else "standard"
            await ctx.events.emit(
                "run.started",
                phase="intake",
                params={
                    "depth_word": ctx.events.labels.depth_word(klass),
                    "minutes": max(1, round(ctx.budget.soft.seconds / 60)),
                },
                detail={
                    "load_class": klass,
                    "load_score": load.score if load else 0,
                    "minutes": max(1, round(ctx.budget.soft.seconds / 60)),
                    "plan_source": live.inp.quality.plan_source,
                    "degraded": live.inp.quality.degraded,
                    "depth_word": ctx.events.labels.depth_word(klass),
                },
            )
            no_search = self.search is None
            if no_search or (row_cap is not None and row_cap <= 0):
                await ctx.warn("no_keys" if no_search else "budget_stop")
                director.skip()
            if live.pending_skip:
                director.skip()
            for tid in live.pending_cancels:
                await director.cancel_target(tid)
            result = await director.execute()
            await asyncio.shield(self._commit(live, ctx, result, started))
            status_for_record = result.status
        except (BudgetExceeded, CassetteMissing, LiveCallForbidden) as exc:
            await self._fail(live, f"{type(exc).__name__}", str(exc)[:160])
        except asyncio.CancelledError:
            await asyncio.shield(self._fail(live, "interrupted", "the run was cancelled"))
            raise
        except Exception as exc:  # a bug must not leave a run stuck: record it plainly and finish
            log.exception("research_run_failed", run_id=rid)
            await self._fail(live, "internal_error", f"{type(exc).__name__}")
        finally:
            self._finish_record(entry_id, status_for_record, rid)
            self.live.pop(rid, None)
            self._running.discard(rid)
            self._pump_later()

    def _finish_record(self, entry_id: str | None, status: str, run_id: str) -> None:
        if entry_id is None or self.record is None:
            return
        try:
            has_ledger = self.control is not None and self.control.ledger_path.is_file()
            ledger = (
                ledger_run_spend(self.control.ledger_path, run_id)
                if has_ledger and self.control
                else None
            )
            self.record.finish_entry(entry_id, status, ledger_micros=ledger)
        except Exception:  # the record must never break a run
            log.exception("research_spend_finish_failed", run_id=run_id)

    async def _commit(
        self, live: LiveRun, ctx: RunContext, result: DirectorResult, started: datetime
    ) -> None:
        rid = live.run_id
        await self.store.update_run(live.tenant_id, rid, status="committing")
        finished = self.clock.now()
        use_model = result.status != "skipped" and not ctx.run_cancel.cancelled
        output, evidence = await assemble_output(
            ctx, result, use_model=use_model, started_at=started, finished_at=finished
        )
        dumped = output.to_json_dict()
        events = await self.store.events_after(rid, 0, limit=100_000)
        cs = self.catalog.ceilings.for_class(
            live.inp.load.load_class if live.inp.load else "standard"
        )
        vctx = ValidationContext(
            output=dumped,
            evidence=evidence,
            inp=live.inp,
            catalog=self.catalog,
            plan=ctx.plan,
            events=events,
            ceilings_hard=cs.hard.model_dump(),
            store_tokens=cs.store_tokens,
            chunk_items=cs.chunk_items,
            digest_tokens=cs.digest_tokens,
        )
        violations = run_all(vctx)
        refused = must_refuse(violations)
        if refused:
            raise RuntimeError(
                "commit refused: " + "; ".join(f"{v.rule} {v.message}" for v in refused)
            )
        evidence["validation"] = [
            {"rule": v.rule, "message": v.message, "item_id": v.item_id, "severity": v.severity}
            for v in violations
        ]
        evidence["events"] = [e.model_dump(mode="json") for e in events]
        digest = content_hash(dumped)
        await self.store.save_result(rid, dumped, evidence, digest)
        status = output.status
        await self.store.update_run(
            live.tenant_id,
            rid,
            status=status,
            finished_at=finished,
            result_status=status,
            content_hash=digest,
            reason=result.reason,
        )
        counts = {
            "status": status,
            "facts": ctx.verified_count(),
            "pages": ctx.budget.pages_read,
            "searches": ctx.budget.searches_used,
            "usd": output.budget.usd,
            "elapsed_s": int(ctx.budget.elapsed()),
        }
        terminal = {
            "complete": "run.finished",
            "partial": "run.finished",
            "skipped": "run.skipped",
            "failed": "run.failed",
        }[status]
        detail = (
            counts
            if terminal != "run.failed"
            else {
                "status": status,
                "reason": result.reason or "failed",
                "code": result.reason or "failed",
            }
        )
        await ctx.events.emit(
            terminal,
            phase="commit",
            params={
                **counts,
                "reason": ctx.events.labels.warning(result.reason or "model_failed")
                if terminal == "run.failed"
                else "",
            },
            detail=detail,
        )

    async def _fail(self, live: LiveRun, code: str, message: str) -> None:
        """A run that could not finish: say so plainly (event + status); never raise."""
        rid = live.run_id
        try:
            if live.ctx is not None:
                await live.ctx.events.emit(
                    "run.failed",
                    phase="commit",
                    params={"reason": message or code},
                    detail={"status": "failed", "reason": message or code, "code": code},
                )
            await self.store.update_run(
                live.tenant_id,
                rid,
                status="failed" if code != "interrupted" else "interrupted",
                finished_at=self.clock.now(),
                reason=message or code,
                result_status="failed",
            )
        except Exception:  # pragma: no cover - last resort
            log.exception("research_fail_record_failed", run_id=rid)

    # --- commands -------------------------------------------------------------------------------------------
    async def get_row(self, tenant_id: str, run_id: str) -> RunRow:
        row = await self.store.get_run(tenant_id, run_id)
        if row is None:
            raise NotFound(f"run {run_id}")
        return row

    async def skip(self, tenant_id: str, run_id: str) -> RunRow:
        row = await self.get_row(tenant_id, run_id)
        if row.status not in ACTIVE_STATUSES:
            return row
        live = self.live.get(run_id)
        if live is not None:
            if live.director is not None:
                live.director.skip()
            else:
                live.pending_skip = True
        await self.store.update_run(tenant_id, run_id, skip_requested=True)
        return await self.get_row(tenant_id, run_id)

    async def cancel_target(self, tenant_id: str, run_id: str, target_id: str) -> bool:
        await self.get_row(tenant_id, run_id)
        live = self.live.get(run_id)
        if live is None:
            return False
        if live.director is None:
            live.pending_cancels.append(target_id)
            return True
        return await live.director.cancel_target(target_id)

    async def reject_item(
        self, tenant_id: str, run_id: str, item_id: str, reason: str = ""
    ) -> None:
        await self.get_row(tenant_id, run_id)
        result = await self.store.get_result(run_id)
        if result is None or not any(i["id"] == item_id for i in result.get("items", [])):
            raise NotFound(f"item {item_id}")
        await self.store.add_decision(
            Decision(
                run_id=run_id,
                item_id=item_id,
                kind="reject",
                reason=reason[:300],
                at=self.clock.now(),
            )
        )

    # --- reading -------------------------------------------------------------------------------------------
    async def snapshot(self, tenant_id: str, run_id: str) -> RunSnapshot:
        row = await self.get_row(tenant_id, run_id)
        events = await self.store.events_after(run_id, 0, limit=100_000)
        view = fold(events)
        started = view["started"]
        return RunSnapshot(
            run_id=row.run_id,
            program_id=row.program_id,
            status=row.status,  # type: ignore[arg-type]
            phase=view["phase"],
            round=view["round"],
            started_at=row.started_at,
            finished_at=row.finished_at,
            load_class=row.load_class,
            load_score=row.load_score,
            depth_word=started.get("depth_word"),
            plan_source=row.plan_source,
            input_quality=(row.input_json or {}).get("quality", {}),
            counters=view["counters"],
            targets=view["targets"],
            declined=view["declined"],
            entities=view["entities"],
            feed=view["feed"],
            last_seq=view["last_seq"],
            result_status=row.result_status,
            content_hash=row.content_hash,
            reason=row.reason,
        )

    async def result(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        await self.get_row(tenant_id, run_id)
        res = await self.store.get_result(run_id)
        if res is None:
            raise NotFound("result not committed yet")
        return res

    async def latest(self, tenant_id: str, program_id: str) -> dict[str, Any]:
        row = await self.store.latest_committed(tenant_id, program_id)
        res = await self.store.get_result(row.run_id) if row else None
        if res is None:
            raise NotFound(f"research for program {program_id}")
        return res

    async def review(self, tenant_id: str, program_id: str) -> dict[str, Any]:
        row = await self.store.latest_committed(tenant_id, program_id)
        res = await self.store.get_result(row.run_id) if row else None
        if row is None or res is None:
            raise NotFound(f"research for program {program_id}")
        rejected = {
            d.item_id for d in await self.store.list_decisions(row.run_id) if d.kind == "reject"
        }
        lanes = self.catalog.lanes.kinds
        items = []
        for it in res.get("items", []):
            flags = sorted({*it.get("flags", []), *(["withdrawn"] if it["id"] in rejected else [])})
            items.append(
                {
                    **it,
                    "flags": flags,
                    "lane": (lanes[it["kind"]].lanes[0] if it["kind"] in lanes else "blueprint"),
                    "rejected": it["id"] in rejected,
                }
            )
        body = {
            k: res.get(k)
            for k in (
                "profile",
                "coverage",
                "budget",
                "input_quality",
                "norms",
                "suggestions",
                "confirm",
                "gaps",
                "conflicts",
                "slices",
            )
        }
        return {
            "run_id": row.run_id,
            "status": res.get("status"),
            "content_hash": row.content_hash,
            **body,
            "items": items,
        }

    # --- lifecycle ----------------------------------------------------------------------------------------------
    async def _terminal(
        self, row: RunRow, type_: str, code: str, message: str, *, status: str
    ) -> None:
        """Give a run that ended outside its own task a terminal event, then set its status. Never raises."""
        try:
            writer = EventWriter(
                row.run_id,
                self.store,
                self.catalog,
                clock=self.clock,
                ids=self.ids,
                counters=lambda: Counters(),
                language=self.language,
            )
            writer.seq = await self.store.last_seq(row.run_id)
            await writer.emit(
                type_,
                phase="commit",
                params={"reason": message},
                detail={"status": "failed", "reason": message, "code": code},
            )
        except Exception:
            log.exception("research_terminal_event_failed", run_id=row.run_id)
        try:
            await self.store.update_run(
                row.tenant_id,
                row.run_id,
                status=status,
                finished_at=self.clock.now(),
                reason=message,
                result_status="failed",
            )
        except Exception:
            log.exception("research_terminal_status_failed", run_id=row.run_id)

    async def _last_activity(self, row: RunRow) -> datetime:
        seq = await self.store.last_seq(row.run_id)
        if seq:
            ev = await self.store.events_after(row.run_id, seq - 1, limit=1)
            if ev:
                return ev[-1].ts
        return row.started_at or row.created_at

    async def recover(self) -> int:
        """At startup: runs that were active when the process stopped become ``interrupted`` WITH a terminal event.

        A single-process store (the file store) owns every run in it, so all active runs are orphans. A shared store
        (PostgreSQL) may hold a live run of another process: only runs with no event for ``stale_after_s`` count.
        """
        n = 0
        now = self.clock.now()
        for row in await self.store.active_runs():
            if row.run_id in self.live:
                continue
            if not self.own_store and (now - await self._last_activity(row)) < timedelta(
                seconds=self.stale_after_s
            ):
                continue
            await self._terminal(
                row,
                "run.failed",
                "interrupted",
                "The server restarted while this run was in progress. What it spent is recorded.",
                status="interrupted",
            )
            n += 1
        return n

    async def sweep_stale(self) -> int:
        """A run (not owned by this process) with no event for ``stale_after_s`` becomes ``failed: stalled``."""
        n = 0
        now = self.clock.now()
        for row in await self.store.active_runs():
            if row.run_id in self.live:
                continue
            if (now - await self._last_activity(row)) >= timedelta(seconds=self.stale_after_s):
                await self._terminal(
                    row,
                    "run.failed",
                    "stalled",
                    "This run stopped reporting and was marked as failed so its input is free again.",
                    status="failed",
                )
                n += 1
        return n

    # --- listing and budget ----------------------------------------------------------------------------------
    async def list_runs(self, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self.store.list_runs(tenant_id, limit)
        spent = self._spend_by_run()
        out = []
        for r in rows:
            out.append(
                {
                    "run_id": r.run_id,
                    "program_id": r.program_id,
                    "status": r.status,
                    "result_status": r.result_status,
                    "load_class": r.load_class,
                    "load_score": r.load_score,
                    "input_id": (r.meta or {}).get("input_id"),
                    "input_name": (r.meta or {}).get("input_name"),
                    "size_class": (r.meta or {}).get("size_class"),
                    "idea": (r.meta or {}).get("idea"),
                    "created_at": r.created_at.isoformat(),
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "reason": r.reason,
                    "content_hash": r.content_hash,
                    "queue_position": self.queue_position(r.run_id),
                    "spend_micros": spent.get(r.run_id, 0),
                }
            )
        return out

    def _spend_by_run(self) -> dict[str, int]:
        if self.record is None:
            return {}
        out: dict[str, int] = {}
        for e in self.record.data["entries"]:
            if e.get("run_id"):
                out[e["run_id"]] = out.get(e["run_id"], 0) + int(e["total_micros"])
        return out

    async def ping(self) -> dict[str, Any]:
        """Preflight ping (about USD 0.01): one search and one tiny model call, booked in the spend record."""
        from mde_research_agent.calls import check as check_call
        from mde_research_agent.calls.base import CallRunner
        from mde_research_agent.ports.search import SearchRequest

        self._require_ready()
        assert self.search is not None
        entry = self.record.start_entry("ping", label="preflight ping") if self.record else None
        period = self.control.period_scope if self.control else None
        out: dict[str, Any] = {"search": {"ok": False}, "model": {"ok": False}}
        try:
            try:
                res = await self.search.search(
                    SearchRequest(query="mobile app onboarding", max_results=3)
                )
                out["search"] = {"ok": True, "provider": res.provider, "results": len(res.hits)}
                if entry and self.record:
                    self.record.add_cost(entry, f"search.{res.provider}", res.cost_micros)
                if res.cost_micros and self.mode == "live":
                    guard = getattr(getattr(self.gateway, "gateway", None), "guard", None)
                    if guard is not None:
                        GuardSpendBook(guard, "ping", self.ids, period)(
                            res.cost_micros, res.provider
                        )
            except Exception as exc:
                out["search"] = {"ok": False, "error": type(exc).__name__}
            try:
                runner = CallRunner(
                    self.gateway,
                    run_id="ping",
                    ids=self.ids,
                    extra_scopes=(period,) if period else (),
                )
                item = check_call.CheckItem(
                    id="P1", claim="Water boils at 100 C.", quote="Water boils at 100 C."
                )
                r = await runner.run(check_call.SPEC, check_call.CheckIn(items=[item]))
                out["model"] = {"ok": not r.failed, "cost_micros": r.cost_micros}
                if entry and self.record:
                    self.record.add_cost(entry, "probe", r.cost_micros)
            except Exception as exc:
                out["model"] = {"ok": False, "error": type(exc).__name__}
        finally:
            if entry and self.record:
                self.record.finish_entry(entry, "done")
        return out

    def budget_status(self) -> dict[str, Any]:
        if self.control is None:
            return {"enabled": False}
        s = self.control.state().as_dict()
        s["enabled"] = True
        s["write_failed"] = bool(self.record and self.record.write_failed)
        s["active_runs"] = len(self.live)
        return s

    async def restart_budget(self) -> dict[str, Any]:
        """Close the budget period and start the next at 0 (history kept). Refused while any run is active."""
        if self.control is None:
            raise NotFound("budget")
        if self.live or self.queue:
            raise Conflict(
                "A run is in progress: wait for it to finish before restarting the budget."
            )
        self.control.restart()
        if self.gateway_factory is not None:
            self.gateway = self.gateway_factory(self.control)
        return self.budget_status()

    async def wait(self, run_id: str, timeout_s: float = 30.0) -> None:
        live = self.live.get(run_id)
        if live is not None and live.task is not None:
            await asyncio.wait_for(asyncio.shield(live.task), timeout=timeout_s)

    async def aclose(self) -> None:
        for live in list(self.live.values()):
            if live.task is not None and not live.task.done():
                live.task.cancel()
        for live in list(self.live.values()):
            if live.task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await live.task
        for closer in self._closers:
            try:
                res = closer()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: S110
                pass
        await self.store.aclose()


class _NoSearch:
    """Stands in when no search provider is configured: the run is skipped before it is ever called."""

    name = "none"

    class capabilities:
        page_content = False

    async def search(self, request: Any) -> Any:  # pragma: no cover - never called
        raise RuntimeError("no search provider configured")
