"""The app's Tools: the search-provider bake-off, run from the front end.

Sends about 40 generic queries (derived from the inputs by the planner, never hand-written) to each provider that has a key,
measures local share, on-topic share (a cheap model judge), page-text availability, latency and cost, and proposes a default
by the rule of the design (higher local share wins; within 5 points the cheaper one). Every cent is booked in the spend record
(kind ``bakeoff``) and into the period ledger, and nothing starts without the budget room for it. The result is kept in the
data folder; "Use this provider" is the settings endpoint. One bake-off at a time.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from mde_core.money import micros_to_usd
from mde_research_agent.calls.base import CallRunner
from mde_research_agent.engine.spend import GuardSpendBook
from mde_research_agent.live_bakeoff import (
    build_probes,
    choose,
    estimate,
    judge,
    measure_page_text,
    run_provider,
    summarize,
)
from mde_research_agent.ports.search import SearchPort

N_QUERIES = 40
JUDGE_ESTIMATE_MICROS = 20_000


class BakeoffTool:
    def __init__(
        self, service: Any, providers: Callable[[httpx.AsyncClient], list[SearchPort]]
    ) -> None:
        self.svc = service
        self.providers = providers
        self.state: dict[str, Any] = {"state": "idle"}
        self._task: asyncio.Task[None] | None = None

    @property
    def result_path(self) -> Path | None:
        return (self.svc.data_dir / "bakeoff.json") if self.svc.data_dir else None

    def _probes(self) -> list[Any]:
        folder = Path(self.svc.inputs.folder)
        return build_probes(folder, N_QUERIES, None)

    def plan(self) -> dict[str, Any]:
        """What a run would do and cost, before it starts (no spend)."""
        names = [p.name for p in self.providers(httpx.AsyncClient())]
        probes = self._probes()
        est = estimate(probes, names)
        est["queries_sent"] = len(probes) * len(names)
        return est

    def status(self) -> dict[str, Any]:
        out = dict(self.state)
        if out["state"] == "idle" and self.result_path and self.result_path.is_file():
            try:
                out["last"] = json.loads(self.result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return out

    def start(self) -> dict[str, Any]:
        if self._task and not self._task.done():
            return self.status()
        control = self.svc.control
        if control is None or self.svc.record is None:
            raise RuntimeError("the spend record is not available")
        ok, reason = control.can_start()
        if not ok:
            from mde_research_agent.service import BudgetBlocked

            raise BudgetBlocked(reason)
        est = self.plan()
        need = int((Decimal(est["search_usd"]) + Decimal(est["judge_usd"])) * 1_000_000 * 2)
        if control.state().remaining_micros < need:
            from mde_core.errors import Conflict

            raise Conflict(
                "There is not enough budget left for the bake-off (it needs about twice its estimate)."
            )
        self.state = {
            "state": "running",
            "estimate": est,
            "started_at": datetime.now(UTC).isoformat(),
            "progress": "starting",
        }
        self._task = asyncio.create_task(self._run(est), name="research-bakeoff")
        return self.status()

    async def _run(self, est: dict[str, Any]) -> None:
        svc = self.svc
        record, control = svc.record, svc.control
        entry = record.start_entry("bakeoff", label="provider bake-off")
        spent = 0
        try:
            probes = self._probes()
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                ports = self.providers(client)
                guard = getattr(getattr(svc.gateway, "gateway", None), "guard", None)
                stats = []
                for port in ports:
                    self.state["progress"] = f"asking {port.name}"
                    st = await run_provider(port, probes)
                    cost = sum(r.cost_micros for r in st.results.values() if r is not None)
                    spent += cost
                    record.add_cost(entry, f"search.{port.name}", cost)
                    if guard is not None and cost:
                        GuardSpendBook(guard, "bakeoff", svc.ids, control.period_scope)(
                            cost, port.name
                        )
                    stats.append(st)
                self.state["progress"] = "measuring page text"
                for st in stats:
                    await measure_page_text(st, client)
                self.state["progress"] = "judging"
                runner = CallRunner(
                    svc.gateway, run_id="bakeoff", ids=svc.ids, extra_scopes=(control.period_scope,)
                )
                await judge(stats, probes, runner)
                jc = sum(r.cost_micros for _, r in runner.calls)
                spent += jc
                record.add_cost(entry, "probe", jc)
            from mde_research_agent.catalog import load_catalog

            cat = load_catalog()
            summaries = [summarize(s, probes, cat) for s in stats]
            chosen = choose(summaries)
            result = {
                "finished_at": datetime.now(UTC).isoformat(),
                "queries": len(probes),
                "summaries": summaries,
                "recommended": chosen[0],
                "why": chosen[1],
                "cost_usd": str(micros_to_usd(spent)),
            }
            if self.result_path:
                self.result_path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
            record.finish_entry(entry, "done")
            self.state = {"state": "done", "result": result}
        except Exception as exc:  # the tool never takes the app down; the owner sees why
            record.finish_entry(entry, "failed")
            self.state = {"state": "failed", "error": type(exc).__name__, "message": str(exc)[:200]}
