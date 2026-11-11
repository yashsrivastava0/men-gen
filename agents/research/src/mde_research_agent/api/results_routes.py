"""Reading a committed run: overview, paged review, sources, report, metrics, decisions, compact view, and the spot-check.

All need the dev token. A run that is not committed yet answers 404. The page and the filters are served from an index
built once per committed run, so filters answer in memory.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict

from mde_core.errors import NotFound
from mde_research_agent import API_PREFIX
from mde_research_agent.api.deps import Auth, Svc
from mde_research_agent.spotcheck import export_markdown, summarize


class SpotCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[str]
    seed: int | None = None
    per_run: int = 20


class VerdictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str


def make_results_router() -> APIRouter:
    router = APIRouter()
    runs = f"{API_PREFIX}/runs"

    @router.get(f"{runs}/{{run_id}}/overview")
    async def overview(run_id: str, who: Auth, svc: Svc) -> Any:
        return JSONResponse(
            await svc.overview(who.tenant_id, run_id), headers={"Cache-Control": "no-store"}
        )

    @router.get(f"{runs}/{{run_id}}/review")
    async def review(
        run_id: str,
        who: Auth,
        svc: Svc,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        job: str = "",
        lane: str = "",
        evidence: str = "",
        flag: str = "",
        impact: str = "",
        kind: str = "",
        store: str = "",
        q: str = "",
    ) -> Any:
        return JSONResponse(
            await svc.review_items(
                who.tenant_id,
                run_id,
                limit=limit,
                offset=offset,
                job=job,
                lane=lane,
                evidence=evidence,
                flag=flag,
                impact=impact,
                kind=kind,
                store=store,
                q=q,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @router.get(f"{runs}/{{run_id}}/sources")
    async def sources(
        run_id: str,
        who: Auth,
        svc: Svc,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        q: str = "",
    ) -> Any:
        return JSONResponse(
            await svc.sources(who.tenant_id, run_id, limit=limit, offset=offset, q=q)
        )

    @router.get(f"{runs}/{{run_id}}/report")
    async def report(run_id: str, who: Auth, svc: Svc) -> Response:
        text = await svc.report(who.tenant_id, run_id)
        return PlainTextResponse(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="research-report-{run_id}.md"',
                "Cache-Control": "no-store",
            },
        )

    @router.get(f"{runs}/{{run_id}}/metrics")
    async def metrics(run_id: str, who: Auth, svc: Svc) -> Any:
        return JSONResponse(await svc.metrics(who.tenant_id, run_id))

    @router.get(f"{API_PREFIX}/programs/{{program_id}}/decisions")
    async def decisions(program_id: str, who: Auth, svc: Svc) -> Any:
        return {"decisions": await svc.decisions(who.tenant_id, program_id)}

    # --- spot-check ------------------------------------------------------------------------------------------------
    @router.post("/api/spot-checks", status_code=201)
    async def create(body: SpotCheckBody, who: Auth, svc: Svc) -> Any:
        return await svc.new_spot_check(
            who.tenant_id, body.run_ids, body.seed, max(1, min(body.per_run, 60))
        )

    @router.get("/api/spot-checks")
    async def listing(_: Auth, svc: Svc) -> Any:
        return {"checks": svc.spot_checks.list()}

    @router.get("/api/spot-checks/{check_id}")
    async def one(check_id: str, _: Auth, svc: Svc) -> Any:
        check = svc.spot_checks.get(check_id)
        if check is None:
            raise NotFound(f"spot-check {check_id}")
        return {**check, "summary": summarize(check)}

    @router.put("/api/spot-checks/{check_id}/verdicts/{card_key:path}")
    async def verdict(check_id: str, card_key: str, body: VerdictBody, _: Auth, svc: Svc) -> Any:
        return {"summary": await svc.set_verdict(check_id, card_key, body.verdict)}

    @router.get("/api/spot-checks/{check_id}/summary")
    async def summary(check_id: str, _: Auth, svc: Svc) -> Any:
        check = svc.spot_checks.get(check_id)
        if check is None:
            raise NotFound(f"spot-check {check_id}")
        return summarize(check)

    @router.get("/api/spot-checks/{check_id}/export")
    async def export(check_id: str, _: Auth, svc: Svc, format: str = "json") -> Response:
        check = svc.spot_checks.get(check_id)
        if check is None:
            raise NotFound(f"spot-check {check_id}")
        s = summarize(check)
        if format == "md":
            return PlainTextResponse(
                export_markdown(check, s),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="spot-check-{check_id}.md"'},
            )
        return JSONResponse(
            {**check, "summary": s},
            headers={"Content-Disposition": f'attachment; filename="spot-check-{check_id}.json"'},
        )

    return router
