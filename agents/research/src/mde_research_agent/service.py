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
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from mde_core.clock import Clock, SystemClock
from mde_core.errors import BudgetExceeded, CassetteMissing, LiveCallForbidden, NotFound
from mde_core.hashing import hash_json
from mde_core.logging import get_logger
from mde_research_agent.assemble.output import assemble_output
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.config import ResearchSettings
from mde_research_agent.core.hashing import content_hash
from mde_research_agent.domain.events import RunSnapshot
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.engine.context import RunContext
from mde_research_agent.engine.director import Director, DirectorResult
from mde_research_agent.engine.factory import new_context
from mde_research_agent.engine.spend import GuardSpendBook, research_spent_micros
from mde_research_agent.events.reducer import fold
from mde_research_agent.ids import Ids, UlidIds
from mde_research_agent.intake import read_export
from mde_research_agent.ports.fetch import FetchPort
from mde_research_agent.ports.search import SearchPort
from mde_research_agent.store.ports import ACTIVE_STATUSES, Decision, RunRow, Store
from mde_research_agent.validators import ValidationContext, run_all
from mde_research_agent.validators.registry import must_refuse

log = get_logger(__name__)


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
        run_cap_micros: int | None = None,
        sleep: Any = None,
        language: str = "en",
        closers: list[Any] | None = None,
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
        self.run_cap_micros = run_cap_micros
        self.sleep = sleep
        self.language = language
        self.live: dict[str, LiveRun] = {}
        self._closers = closers or []

    # --- starting ------------------------------------------------------------------------------------
    async def start_run(
        self, tenant_id: str, program_id: str | None, export: dict[str, Any]
    ) -> RunRow:
        inp = read_export(
            export, self.catalog, program_id=program_id
        )  # raises ExportRefused (RV-13 and friends)
        pid = program_id or inp.program_id
        now = self.clock.now()
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
            created_at=now,
        )
        await self.store.create_run(row)  # RunActive -> 409
        live = LiveRun(run_id=row.run_id, tenant_id=tenant_id, program_id=pid, inp=inp)
        self.live[row.run_id] = live
        live.task = asyncio.create_task(self._execute(live), name=f"research-run-{row.run_id}")
        return row

    # --- the run ---------------------------------------------------------------------------------------
    def _remaining_budget(self) -> int | None:
        """What the configured Research budget still allows, from the shared ledger (live mode only)."""
        if self.mode != "live" or self.live_budget_micros is None:
            return self.run_cap_micros
        spent = research_spent_micros(self.ledger_path)
        left = max(0, self.live_budget_micros - spent)
        return min(left, self.run_cap_micros) if self.run_cap_micros else left

    async def _execute(self, live: LiveRun) -> None:
        rid = live.run_id
        started = self.clock.now()
        try:
            await self.store.update_run(live.tenant_id, rid, status="running", started_at=started)
            row_cap = self._remaining_budget()
            ctx = new_context(
                inp=live.inp,
                catalog=self.catalog,
                search=self.search or _NoSearch(),
                fetch=self.fetch,
                gateway=self.gateway,
                events=self.store,
                clock=self.clock,
                ids=self.ids,
                as_of=self.as_of or self.clock.now().date(),
                run_id=rid,
                run_cap_micros=row_cap,
                language=self.language,
                checker_vendor=self.checker_vendor,
                sleep=self.sleep,
            )
            live.ctx = ctx
            guard = getattr(getattr(self.gateway, "gateway", None), "guard", None)
            if self.mode == "live" and guard is not None:
                ctx.budget.on_search_cost = GuardSpendBook(guard, rid, self.ids)
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
        except (BudgetExceeded, CassetteMissing, LiveCallForbidden) as exc:
            await self._fail(live, f"{type(exc).__name__}", str(exc)[:160])
        except asyncio.CancelledError:
            await asyncio.shield(self._fail(live, "interrupted", "the run was cancelled"))
            raise
        except Exception as exc:  # a bug must not leave a run stuck: record it plainly and finish
            log.exception("research_run_failed", run_id=rid)
            await self._fail(live, "internal_error", f"{type(exc).__name__}")
        finally:
            self.live.pop(rid, None)

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
            slice_cap_tokens=cs.soft.slice_tokens,
            research_cap_tokens=cs.hard.research_tokens,
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
    async def recover(self) -> int:
        """At startup: runs that were active when the process stopped become ``interrupted``."""
        return await self.store.mark_interrupted()

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
