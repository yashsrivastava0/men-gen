"""The real thing: ``python -m mde_context_api`` as a process, over real HTTP, on a real database.

This is what ``just dev`` runs. It proves the Windows event-loop wiring (psycopg needs a selector
loop, D-042), the in-process job worker started by the lifespan, and the whole conversation from
"hi" to a committed ``APP_SPEC.md`` over a socket. The process is started and stopped by the test;
every wait is bounded.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.engine import URL

from mde_context_api.testing import TEST_TOKEN, Api, eventually
from mde_core.hashing import sha256_hex

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(
    tmp: Path,
    database: URL,
    port: int,
    *,
    extra_env: dict[str, str] | None = None,
    extra_args: tuple[str, ...] = (),
) -> subprocess.Popen[bytes]:
    log = tmp / "server.log"
    env = {
        **os.environ,
        "DATABASE_URL": database.render_as_string(hide_password=False),
        "PROJECTS_DIR": str(tmp / "projects"),
        "API_DEV_TOKEN": TEST_TOKEN,
        "WORKER_POLL_SECONDS": "0.1",
        "PYTHONUNBUFFERED": "1",
        **(extra_env or {}),
    }
    with log.open("wb") as out:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mde_context_api",
                "--fake-builder",
                "--port",
                str(port),
                *extra_args,
            ],
            cwd=tmp,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    return proc


def stop_server(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)


@pytest.fixture
def server(pg_database: URL, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    from mde_persistence.testing_plugin import truncate_all

    truncate_all(pg_database)
    port = free_port()
    proc = start_server(tmp_path, pg_database, port)
    try:
        yield f"http://127.0.0.1:{port}", tmp_path
    finally:
        stop_server(proc)
    assert proc.returncode is not None


async def wait_until_up(base: str, tmp: Path) -> None:
    try:
        async with asyncio.timeout(45), httpx.AsyncClient(base_url=base, timeout=5) as client:
            while True:
                try:
                    if (await client.get("/healthz")).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.2)
    except TimeoutError:
        pytest.fail(
            "the server did not come up:\n" + (tmp / "server.log").read_text("utf-8")[-3000:]
        )


async def test_the_server_process_serves_a_whole_conversation_to_a_committed_spec(
    server: tuple[str, Path],
) -> None:
    base, tmp = server
    await wait_until_up(base, tmp)
    async with asyncio.timeout(120), httpx.AsyncClient(base_url=base, timeout=30) as client:
        ready = await client.get("/readyz")  # a real connection on the selector loop
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"] == {"database": "ok", "ledger": "ok"}

        api = Api(client)
        await api.create()
        await api.to_gate1()
        resp = await api.approve("brief")
        assert resp.status_code == 202

        async def at_plan_review() -> bool:
            return (await api.snapshot())["state"] == "PLAN_REVIEW"

        await eventually(at_plan_review)  # the in-process worker runs the job
        await api.refresh()
        gate2 = api.last_gate("plan")
        assert gate2 is not None
        done = await api.approve("plan")
        assert done.json()["state"] == "HANDED_OFF"

        spec = await api.get("/spec")
        assert spec.headers["x-spec-hash"] == sha256_hex(spec.text) == gate2.hash
        committed = next((tmp / "projects" / "tenant-a").glob("app_*/APP_SPEC.md"))
        assert committed.read_bytes() == spec.content

        budget = await client.get("/v1/budget", headers=api.headers)
        assert budget.status_code == 200 and budget.json()["spent_micros"] == 0

        # the stream replays everything stored, from a real server
        stored = await api.refresh()
        assert stored[-1].type == "spec_ready" and len(stored) > 10
        replay = await api.get("/stream", params={"follow": "false"})
        assert replay.text.count("event: ") == len(stored)


async def test_the_docs_are_off_in_a_default_process(server: tuple[str, Path]) -> None:
    """``just dev`` without ``API_DOCS``: nothing unauthenticated describes the API."""
    base, tmp = server
    await wait_until_up(base, tmp)
    async with asyncio.timeout(30), httpx.AsyncClient(base_url=base, timeout=10) as client:
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert (await client.get(path)).status_code == 404, path
        assert (await client.get("/healthz")).status_code == 200


@pytest.mark.parametrize("how", ["environment", "flag"])
async def test_the_openapi_document_and_docs_are_served_when_asked_for(
    how: str, pg_database: URL, tmp_path: Path
) -> None:
    from mde_persistence.testing_plugin import truncate_all

    truncate_all(pg_database)
    port = free_port()
    env = {"API_DOCS": "1"} if how == "environment" else {}
    args = ("--docs",) if how == "flag" else ()
    proc = start_server(tmp_path, pg_database, port, extra_env=env, extra_args=args)
    try:
        base = f"http://127.0.0.1:{port}"
        await wait_until_up(base, tmp_path)
        async with asyncio.timeout(30), httpx.AsyncClient(base_url=base, timeout=10) as client:
            doc = await client.get("/openapi.json")
            assert doc.status_code == 200 and "/v1/sessions" in doc.json()["paths"]
            assert (await client.get("/docs")).status_code == 200
    finally:
        stop_server(proc)
