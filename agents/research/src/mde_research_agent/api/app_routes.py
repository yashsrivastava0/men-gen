"""The standalone app's own endpoints (``/api/*``): inputs, runs, budget, preflight, settings, health.

All but ``/api/session`` and ``/api/health`` need the dev token (``Authorization: Bearer``). The page gets the token
from ``/api/session`` (same origin only; the app has no CORS). No path ever comes from a client: an input is named by its
content hash and mapped to a file by the server.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from mde_core.errors import Conflict, NotFound
from mde_research_agent import AGENT_NAME
from mde_research_agent.api.deps import Auth, Svc
from mde_research_agent.config import data_dir
from mde_research_agent.preflight import run_preflight

MAX_UPLOAD = 25 * 1024 * 1024
PROVIDERS = ("auto", "tavily", "serper")


class StartRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_id: str


class RestartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_provider: str


def settings_path(request: Request) -> Path:
    return data_dir(request.app.state.settings) / "settings.json"


def read_settings(request: Request) -> dict[str, Any]:
    try:
        raw = json.loads(settings_path(request).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _micros_to_text(micros: int) -> str:
    return f"{micros / 1_000_000:.2f}"


def make_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        svc = request.app.state.service
        return {
            "ok": True,
            "ready": svc.ready,
            "app": AGENT_NAME,
            "time": datetime.now(UTC).isoformat(),
        }

    @router.get("/session")
    async def session(request: Request) -> dict[str, Any]:
        cfg = request.app.state.settings
        svc = request.app.state.service
        return {
            "token": cfg.research_dev_token.get_secret_value(),
            "tenant_id": "demo",
            "app": AGENT_NAME,
            "live": svc.ready,
        }

    # ---- inputs ---------------------------------------------------------------------------------------------
    def _with_estimate(svc: Any, entry: dict[str, Any]) -> dict[str, Any]:
        prev = entry["preview"]
        if svc.estimates is not None:
            entries = svc.record.data["entries"] if svc.record else []
            prev["estimate"] = svc.estimates.for_class(prev["size_class"], entries)
        return entry

    async def _all_runs(svc: Any, tenant: str) -> list[dict[str, Any]]:
        return await svc.list_runs(tenant, 500)

    def _runs_for(entry: Any, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Past runs of this input: the same content, or the same file name (an earlier version of it)."""
        return [r for r in runs if r["input_id"] == entry.id or r["input_name"] == entry.name]

    @router.get("/inputs")
    async def inputs(who: Auth, svc: Svc) -> dict[str, Any]:
        if svc.inputs is None:
            raise NotFound("inputs")
        scan = svc.inputs.scan()
        every = await _all_runs(svc, who.tenant_id)
        cards = []
        for e in scan.valid:
            pub = _with_estimate(svc, e.public())
            runs = _runs_for(e, every)
            pub["runs"] = runs[:5]
            pub["active_run"] = next(
                (r for r in runs if r["status"] in ("queued", "running", "committing")), None
            )
            pub["changed_since_last_run"] = _changed(e, runs)
            cards.append(pub)
        return {
            "folder": scan.folder,
            "folder_exists": scan.folder_exists,
            "inputs": cards,
            "skipped": [
                {"name": s.name, "reason": s.reason, "text": s.text, "size": s.size}
                for s in scan.skipped
            ],
        }

    def _changed(entry: Any, runs: list[dict[str, Any]]) -> bool:
        """The file now differs from what the newest run of this name used (a run keeps its own copy)."""
        same_name = [r for r in runs if r["input_name"] == entry.name]
        return bool(same_name and same_name[0]["input_id"] != entry.id)

    @router.get("/inputs/{input_id}")
    async def input_detail(input_id: str, who: Auth, svc: Svc) -> dict[str, Any]:
        if svc.inputs is None:
            raise NotFound("inputs")
        entry = svc.inputs.entry(input_id)
        if entry is None:
            raise NotFound(f"input {input_id}")
        runs = _runs_for(entry, await _all_runs(svc, who.tenant_id))
        pub = _with_estimate(svc, entry.public())
        pub["runs"] = runs
        pub["changed_since_last_run"] = _changed(entry, runs)
        return pub

    @router.post("/inputs/upload", status_code=201)
    async def upload(request: Request, who: Auth, svc: Svc) -> dict[str, Any]:
        """The body is the export JSON itself (up to 25 MB). It is checked like any listed file, then stored."""
        if svc.inputs is None or svc.inputs.uploads is None:
            raise NotFound("uploads")
        body = await request.body()
        if len(body) > MAX_UPLOAD:
            raise Conflict("The file is over 25 MB.")
        sha = hashlib.sha256(body).hexdigest()[:12]
        folder = svc.inputs.uploads
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{sha}.json"
        target.write_bytes(body)
        scan = svc.inputs.scan()
        found = next((e for e in scan.valid if e.id == sha), None)
        if found is None:
            reason = next((s for s in scan.skipped if s.name.endswith(f"{sha}.json")), None)
            target.unlink(missing_ok=True)
            svc.inputs.scan()
            return {
                "accepted": False,
                "reason": reason.reason if reason else "not_a_compass_export",
                "text": reason.text if reason else "This is not a usable Compass export.",
            }
        return {"accepted": True, "input": _with_estimate(svc, found.public())}

    # ---- runs -------------------------------------------------------------------------------------------------
    @router.post("/runs", status_code=201)
    async def start(body: StartRunBody, who: Auth, svc: Svc, response: Response) -> dict[str, Any]:
        row, created = await svc.start_from_input(who.tenant_id, body.input_id)
        if not created:
            response.status_code = 200
        return {
            "run_id": row.run_id,
            "program_id": row.program_id,
            "status": row.status,
            "created": created,
            "queue_position": svc.queue_position(row.run_id),
            "events_url": f"/v1/research/runs/{row.run_id}/events",
        }

    @router.get("/runs")
    async def runs(who: Auth, svc: Svc) -> dict[str, Any]:
        return {"runs": await svc.list_runs(who.tenant_id)}

    @router.post("/runs/{run_id}/cancel-queued")
    async def cancel_queued(run_id: str, who: Auth, svc: Svc) -> dict[str, Any]:
        row = await svc.cancel_queued(who.tenant_id, run_id)
        return {"run_id": run_id, "status": row.status}

    # ---- budget -------------------------------------------------------------------------------------------------
    @router.get("/budget")
    async def budget(_: Auth, svc: Svc) -> dict[str, Any]:
        return svc.budget_status()

    @router.get("/spend")
    async def spend(_: Auth, svc: Svc) -> dict[str, Any]:
        if svc.record is None:
            return {"enabled": False}
        return {"enabled": True, "budget": svc.budget_status(), "record": svc.record.snapshot()}

    @router.post("/budget/restart")
    async def restart(body: RestartBody, _: Auth, svc: Svc) -> dict[str, Any]:
        if body.confirm != "RESTART":
            raise Conflict("Type the word RESTART to confirm.")
        return await svc.restart_budget()

    # ---- preflight, settings ---------------------------------------------------------------------------------
    @router.get("/preflight")
    async def preflight(request: Request, _: Auth, svc: Svc) -> dict[str, Any]:
        return run_preflight(svc, request.app.state.core, request.app.state.settings)

    @router.post("/preflight/ping")
    async def ping(_: Auth, svc: Svc) -> dict[str, Any]:
        """About USD 0.01: one search and one tiny model call, booked in the spend record."""
        return await svc.ping()

    @router.get("/tools/bakeoff")
    async def bakeoff_status(_: Auth, svc: Svc) -> dict[str, Any]:
        if svc.bakeoff is None:
            raise NotFound("bakeoff")
        return {**svc.bakeoff.status(), "plan": svc.bakeoff.plan()}

    @router.post("/tools/bakeoff", status_code=202)
    async def bakeoff_start(body: ConfirmBody, _: Auth, svc: Svc) -> dict[str, Any]:
        """Starts the provider bake-off (about USD 0.40: it needs the word ``RUN`` and enough budget left)."""
        if svc.bakeoff is None:
            raise NotFound("bakeoff")
        if body.confirm != "RUN":
            raise Conflict("Confirm to start: this costs real money.")
        svc._require_ready()
        return svc.bakeoff.start()  # type: ignore[no-any-return]

    @router.get("/settings")
    async def get_settings(request: Request, _: Auth) -> dict[str, Any]:
        s = read_settings(request)
        return {
            "primary_provider": s.get("primary_provider", "auto"),
            "providers": list(PROVIDERS),
            "applies": "after the app is restarted",
        }

    @router.post("/settings")
    async def set_settings(
        body: SettingsBody, request: Request, _: Auth, svc: Svc
    ) -> dict[str, Any]:
        if body.primary_provider not in PROVIDERS:
            raise Conflict("Unknown provider.")
        path = settings_path(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        s = read_settings(request)
        s["primary_provider"] = body.primary_provider
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
        tmp.replace(path)
        return {"primary_provider": body.primary_provider, "applies": "after the app is restarted"}

    return router


def format_usd(micros: int) -> str:
    return _micros_to_text(micros)
