"""The job worker: plan jobs run in the background, from the app's lifespan or a separate state.

Every wait is bounded (``eventually``), so a broken worker fails the test instead of hanging it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from mde_context_api.composition import AppState
from mde_context_api.main import create_app
from mde_context_api.testing import Api, build_state, eventually, make_client, make_settings
from mde_context_api.worker import BackgroundWorker, make_job_handler
from mde_contracts.events import PlanReady, StateChanged
from mde_contracts.records import JobRecord, PlanJobPayload
from mde_core.cancel import CancellationToken
from mde_flow.runs import RunManager
from mde_flow.service import FlowService
from mde_flow.testing import FakeResearchEngine

TIMEOUT = 20.0


async def reach_planning(api: Api) -> None:
    await api.create()
    await api.to_gate1()
    resp = await api.approve("brief")
    assert resp.status_code == 202


async def test_the_lifespan_starts_a_worker_that_runs_the_plan_job(
    state: AppState, tmp_path_factory: pytest.TempPathFactory
) -> None:
    live = await build_state(
        make_settings(tmp_path_factory.mktemp("w"), worker_enabled=True), state.store
    )
    app = create_app(state=live)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        assert live.worker is not None and live.worker.running
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            api = Api(client)
            await reach_planning(api)

            async def at_plan_review() -> bool:
                return (await api.snapshot())["state"] == "PLAN_REVIEW"

            await eventually(at_plan_review)
            await api.refresh()
            assert api.of_type(PlanReady)
            done = await api.approve("plan")
            assert done.json()["state"] == "HANDED_OFF"
            assert (await api.get("/spec")).status_code == 200
    assert live.worker is not None and not live.worker.running  # stopped with the app


async def test_with_the_worker_disabled_the_job_waits_for_a_separate_worker(
    state: AppState, client: httpx.AsyncClient, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """``WORKER_ENABLED=0``: the API only enqueues; another process (here: another state on the
    same store) claims and runs the job."""
    assert not state.settings.worker_enabled
    api = Api(client)
    await reach_planning(api)
    await asyncio.sleep(0.3)  # nothing is running it
    snap = await api.snapshot()
    assert snap["state"] == "PLANNING" and snap["active_job"]["state"] == "queued"

    other = await build_state(make_settings(tmp_path_factory.mktemp("w2")), state.store)
    worker = other.start_worker()
    try:

        async def at_plan_review() -> bool:
            return (await api.snapshot())["state"] == "PLAN_REVIEW"

        await eventually(at_plan_review)
    finally:
        await worker.stop()
    assert not worker.running


async def test_stop_reaches_a_job_that_the_worker_is_running(
    state: AppState, client: httpx.AsyncClient
) -> None:
    research = FakeResearchEngine()
    hold = research.pause_inside()
    state.flow.research = research  # deterministic: the research step waits until released
    api = Api(client)
    await reach_planning(api)
    worker = state.start_worker()
    try:
        async with asyncio.timeout(TIMEOUT):
            await hold.entered.wait()  # the worker is inside the research step
        snap = await api.snapshot()
        assert snap["state"] == "PLANNING" and snap["active_job"]["state"] == "running"

        stopped = await api.ui("stop")
        assert any(isinstance(e, StateChanged) and e.to_state == "BRIEF_REVIEW" for e in stopped)
        hold.release()

        async def job_gone() -> bool:
            return (await api.snapshot())["active_job"] is None

        await eventually(job_gone)
    finally:
        hold.release()
        await worker.stop()
    snap = await api.snapshot()
    assert snap["state"] == "BRIEF_REVIEW" and snap["spec_version"] is None
    await api.refresh()
    assert not api.of_type(PlanReady)  # nothing the stopped run produced was kept
    async with state.store.begin("tenant-a") as uow:
        assert await uow.specs.list_versions(api.session_id) == []


async def test_the_handler_joins_the_worker_token_and_the_flow_token() -> None:
    seen: list[CancellationToken] = []

    async def run_plan_job(job: JobRecord, worker_id: str, cancel: CancellationToken) -> None:
        seen.append(cancel)
        await cancel.wait()  # finishes only when something cancels it

    flow = cast("FlowService", SimpleNamespace(runs=RunManager(), run_plan_job=run_plan_job))
    job = JobRecord(
        id="job_1",
        session_id="ses_1",
        tenant_id="t",
        payload=PlanJobPayload(origin="BRIEF_REVIEW", spec_version=1, brief_version=1),
    )
    handler = make_job_handler(flow, "w-1")

    worker_token = CancellationToken()  # cancelled by the persistence worker's watcher
    task = asyncio.ensure_future(handler(job, worker_token))
    await asyncio.sleep(0)
    assert not task.done()
    worker_token.cancel("lost_lock")
    async with asyncio.timeout(5):
        await task
    assert seen[0].cancelled and seen[0].reason == "lost_lock"

    flow_token = flow.runs.token(job.id)  # the in-process Stop path
    flow_token.cancel("stopped")
    task = asyncio.ensure_future(handler(job, CancellationToken()))
    async with asyncio.timeout(5):
        await task  # returned at once: the flow token was already cancelled


async def test_stopping_a_worker_that_will_not_finish_cancels_it_after_the_timeout(
    state: AppState, client: httpx.AsyncClient, tmp_path_factory: pytest.TempPathFactory
) -> None:
    started = asyncio.Event()

    async def never_ends(job: JobRecord, worker_id: str, cancel: CancellationToken) -> None:
        started.set()
        await asyncio.sleep(3600)

    api = Api(client)
    await reach_planning(api)
    impatient = await build_state(
        make_settings(tmp_path_factory.mktemp("w3"), worker_stop_timeout_seconds=0.2), state.store
    )
    impatient.flow.run_plan_job = never_ends  # type: ignore[method-assign]
    worker = BackgroundWorker(impatient.store, impatient.flow, impatient.settings)
    worker.start()
    async with asyncio.timeout(TIMEOUT):
        await started.wait()
    async with asyncio.timeout(5):
        await worker.stop()  # waits 0.2 s for the job, then cancels the task
    assert not worker.running
    await worker.stop()  # idempotent


async def test_starting_a_running_worker_twice_is_harmless(state: AppState) -> None:
    worker = state.start_worker()
    try:
        again = state.start_worker()
        assert again is worker and worker.running
    finally:
        await worker.stop()


async def test_aclose_stops_the_worker(tmp_path_factory: pytest.TempPathFactory) -> None:
    st = await build_state(make_settings(tmp_path_factory.mktemp("w4")))
    worker = st.start_worker()
    assert worker.running
    await st.aclose()
    assert not worker.running
    async with make_client(st) as c:  # the app object is still usable for reads after close
        assert (await c.get("/healthz")).status_code == 200
