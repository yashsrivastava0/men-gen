"""The composition root, the lazy state holder, policy choice and the process entry points."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn
from uvicorn.importer import import_from_string

from mde_context_api import __main__ as entry
from mde_context_api import composition, loop, worker
from mde_context_api.composition import SPEC_BUILDER_MISSING, AppState, build_app_state
from mde_context_api.holder import StateHolder
from mde_context_api.main import create_app
from mde_context_api.policies import PolicyCatalog
from mde_context_api.settings import ApiSettings, load_api_settings
from mde_context_api.testing import make_settings
from mde_contracts.gateway import GatewayClient
from mde_core.clock import FakeClock
from mde_core.errors import ConfigError, LiveCallForbidden, ValidationFailed
from mde_flow.handoff import LocalGitProjectRepo, NoopFrontendEngine
from mde_flow.testing import FakeSpecBuilder, InMemoryStore
from mde_llm_gateway.http_client import HttpGatewayClient
from mde_persistence.store import PostgresStore

# --------------------------------------------------------------------------- build_app_state


async def test_the_state_is_wired_from_settings_over_any_store(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "projects")
    store = InMemoryStore()
    state = await build_app_state(settings, store=store, fake_builder=True)
    try:
        assert state.store is store and state.settings is settings
        assert isinstance(state.flow.builder, FakeSpecBuilder) and state.builder_kind == "fake"
        assert isinstance(state.flow.project_repo, LocalGitProjectRepo)
        assert state.flow.project_repo.root == settings.projects_dir
        assert isinstance(state.flow.frontend, NoopFrontendEngine)
        assert state.flow.policies is state.policies
        assert state.worker is None  # started by the lifespan, not by building
        await state.ping()  # the in-memory store is always reachable
    finally:
        await state.aclose()


async def test_ports_and_collaborators_can_be_replaced(tmp_path: Path) -> None:
    class Gateway:
        async def run(self, spec: Any, input: Any, ctx: Any) -> Any:
            raise AssertionError("not called")

        async def budget_status(self) -> Any:
            raise AssertionError("not called")

    repo = SimpleNamespace()
    frontend = SimpleNamespace()
    clock = FakeClock()
    state = await build_app_state(
        make_settings(tmp_path),
        store=InMemoryStore(),
        gateway=Gateway(),  # type: ignore[arg-type]
        project_repo=repo,  # type: ignore[arg-type]
        frontend=frontend,  # type: ignore[arg-type]
        clock=clock,
        ids=lambda prefix: f"{prefix}_custom",
        fake_builder=True,
    )
    assert state.flow.project_repo is repo and state.flow.frontend is frontend
    assert state.flow.clock is clock and state.flow.ids("x") == "x_custom"
    assert isinstance(state.gateway, Gateway)
    await state.aclose()


async def test_building_never_connects_to_the_database(tmp_path: Path) -> None:
    """The pool is lazy: a state over an unreachable URL builds, and only ``ping`` notices."""
    settings = make_settings(tmp_path, database_url="postgresql+psycopg://mde:mde@127.0.0.1:1/none")
    state = await build_app_state(settings, fake_builder=True)
    try:
        assert isinstance(state.store, PostgresStore)
        with pytest.raises(Exception):  # noqa: B017 - any connection failure
            await state.ping()
    finally:
        await state.aclose()


async def test_the_real_spec_builder_is_imported_lazily_and_a_missing_one_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> Any:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(composition, "import_module", missing)
    settings = make_settings(tmp_path, spec_builder="real")
    with pytest.raises(ConfigError) as excinfo:
        await build_app_state(settings, store=InMemoryStore())
    assert excinfo.value.message == SPEC_BUILDER_MISSING
    assert "--fake-builder" in SPEC_BUILDER_MISSING and "SPEC_BUILDER=fake" in SPEC_BUILDER_MISSING


async def test_the_real_spec_builder_is_built_with_the_gateway_and_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[tuple[GatewayClient, object]] = []

    class Impl:
        def __init__(self, gateway: GatewayClient, *, clock: object) -> None:
            made.append((gateway, clock))

    def fake_import(name: str) -> Any:
        assert name == "mde_spec_builder.engine"
        return SimpleNamespace(SpecBuilderImpl=Impl)

    monkeypatch.setattr(composition, "import_module", fake_import)
    clock = FakeClock()
    state = await build_app_state(
        make_settings(tmp_path, spec_builder="real"), store=InMemoryStore(), clock=clock
    )
    assert state.builder_kind == "real" and isinstance(state.flow.builder, Impl)
    assert made == [(state.gateway, clock)]
    await state.aclose()


async def test_a_failed_build_releases_what_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real engine pool was opened before the builder failed: it must not leak."""
    disposed: list[bool] = []
    from mde_persistence.db import create_engine as real_create_engine

    class Tracking:
        """Forwards to the real engine and notes ``dispose`` (AsyncEngine has no __dict__)."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def dispose(self) -> None:
            disposed.append(True)
            await self._inner.dispose()

    def tracking_engine(*args: Any, **kwargs: Any) -> Any:
        return Tracking(real_create_engine(*args, **kwargs))

    def missing_builder(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(composition, "create_engine", tracking_engine)
    monkeypatch.setattr(composition, "import_module", missing_builder)
    with pytest.raises(ConfigError):
        await build_app_state(make_settings(tmp_path, spec_builder="real"))
    assert disposed == [True]


async def test_the_http_gateway_needs_a_url_and_a_token(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GATEWAY_URL"):
        await build_app_state(
            make_settings(tmp_path, gateway_mode="http"), store=InMemoryStore(), fake_builder=True
        )
    with pytest.raises(ConfigError, match="GATEWAY_SERVICE_TOKEN"):
        await build_app_state(
            make_settings(tmp_path, gateway_mode="http", gateway_url="http://gw.invalid"),
            store=InMemoryStore(),
            fake_builder=True,
        )


async def test_the_http_gateway_client_is_built_and_closed_with_the_state(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        gateway_mode="http",
        gateway_url="http://gateway.invalid",
        gateway_service_token="svc-token-123456",
    )
    state = await build_app_state(settings, store=InMemoryStore(), fake_builder=True)
    assert isinstance(state.gateway, HttpGatewayClient)
    client = state.gateway._client
    assert not client.is_closed
    await state.aclose()
    assert client.is_closed


async def test_live_mode_is_refused_outside_the_live_gate(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, llm_mode="live")
    with pytest.raises(LiveCallForbidden):
        await build_app_state(settings, store=InMemoryStore(), fake_builder=True)


async def test_the_settings_secrets_are_registered_for_log_scrubbing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(composition, "register_secrets", seen.append)
    state = await build_app_state(
        make_settings(tmp_path, api_dev_token="a-long-dev-token"),
        store=InMemoryStore(),
        fake_builder=True,
    )
    await state.aclose()
    assert seen and "a-long-dev-token" in seen[0]


# --------------------------------------------------------------------------- policies


def test_policy_defaults_and_overrides() -> None:
    catalog = PolicyCatalog(
        guided_default="guided-default", enterprise_default="enterprise-it-services-firm"
    )
    assert catalog.choose("guided", None) == "guided-default"
    assert catalog.choose("enterprise", None) == "enterprise-it-services-firm"
    assert catalog.choose("enterprise", "regulated-bank") == "regulated-bank"
    assert catalog("guided-default").mode == "guided"
    assert catalog("guided-default") is catalog("guided-default")  # cached
    with pytest.raises(ValidationFailed, match="unknown policy"):
        catalog.choose("guided", "nope")
    with pytest.raises(ValidationFailed, match="not guided"):
        catalog.choose("guided", "regulated-bank")
    with pytest.raises(ValidationFailed, match="unknown policy"):
        catalog("platform-default@99")


def test_a_default_policy_of_the_wrong_mode_fails_at_start_up() -> None:
    with pytest.raises(ValueError, match="not a enterprise policy"):
        PolicyCatalog(guided_default="guided-default", enterprise_default="guided-default")
    with pytest.raises(ValidationFailed):
        PolicyCatalog(guided_default="gone", enterprise_default="enterprise-it-services-firm")


# --------------------------------------------------------------------------- settings


def test_settings_have_safe_defaults_and_read_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = ApiSettings()
    assert defaults.default_enterprise_policy == "enterprise-it-services-firm"
    assert defaults.worker_enabled and defaults.spec_builder == "real"
    assert defaults.api_port == 8100 and defaults.api_host == "127.0.0.1"
    assert defaults.dev_token == "dev-token"
    monkeypatch.setenv("WORKER_ENABLED", "0")
    monkeypatch.setenv("DEFAULT_ENTERPRISE_POLICY", "regulated-bank")
    monkeypatch.setenv("API_MAX_BODY_BYTES", "2048")
    monkeypatch.setenv("MAX_BODY_BYTES", "2048")
    monkeypatch.setenv("SPEC_BUILDER", "fake")
    loaded = load_api_settings()
    assert not loaded.worker_enabled and loaded.spec_builder == "fake"
    assert loaded.default_enterprise_policy == "regulated-bank" and loaded.max_body_bytes == 2048


# --------------------------------------------------------------------------- the holder


async def test_the_holder_builds_once_even_under_concurrent_first_requests(
    tmp_path: Path,
) -> None:
    calls = 0
    built: list[AppState] = []

    async def factory(settings: ApiSettings) -> AppState:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        st = await build_app_state(settings, store=InMemoryStore(), fake_builder=True)
        built.append(st)
        return st

    holder = StateHolder(make_settings(tmp_path), factory=factory)
    assert not holder.built
    states = await asyncio.gather(*(holder.get() for _ in range(6)))
    assert calls == 1 and all(s is built[0] for s in states) and holder.built
    await holder.close()  # owned: closed and forgotten
    assert not holder.built


async def test_the_holder_leaves_an_injected_state_to_its_owner(tmp_path: Path) -> None:
    state = await build_app_state(
        make_settings(tmp_path, worker_enabled=True), store=InMemoryStore(), fake_builder=True
    )
    holder = StateHolder(state=state)
    assert holder.settings is state.settings
    assert await holder.start() is state and state.worker is not None and state.worker.running
    await holder.close()
    assert not state.worker.running and holder.built  # the worker stopped, the state stays
    await state.aclose()


async def test_the_holder_loads_settings_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_DEV_TOKEN", "from-the-environment")
    holder = StateHolder()
    assert holder.settings.dev_token == "from-the-environment"
    assert holder.settings is holder.settings


async def test_the_lifespan_builds_the_state_and_runs_the_worker(tmp_path: Path) -> None:
    async def factory(settings: ApiSettings) -> AppState:
        return await build_app_state(settings, store=InMemoryStore(), fake_builder=True)

    app = create_app(make_settings(tmp_path, worker_enabled=True), state_factory=factory)
    holder: StateHolder = app.state.holder
    assert not holder.built
    async with app.router.lifespan_context(app):
        state = await holder.get()
        assert state.worker is not None and state.worker.running
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.get("/readyz")).status_code == 200
    assert not holder.built and not state.worker.running


async def test_a_disabled_worker_is_not_started(tmp_path: Path) -> None:
    state = await build_app_state(
        make_settings(tmp_path, worker_enabled=False), store=InMemoryStore(), fake_builder=True
    )
    holder = StateHolder(state=state)
    await holder.start()
    assert state.worker is None
    await state.aclose()


def test_create_app_with_no_arguments_needs_nothing() -> None:
    app = create_app()
    assert app.openapi()["info"]["title"] == "Context Engine API"
    assert not app.state.holder.built  # no database, no state, at import or schema time


def test_the_module_level_app_is_lazy_too() -> None:
    from mde_context_api.main import app

    assert not app.state.holder.built


# --------------------------------------------------------------------------- entry points


def test_the_selector_loop_factory_serves_both_uvicorn_conventions() -> None:
    first = loop.selector_loop_factory()
    second = loop.selector_loop_factory(use_subprocess=True)
    try:
        assert isinstance(first, asyncio.SelectorEventLoop)
        assert isinstance(second, asyncio.SelectorEventLoop) and second is not first
    finally:
        first.close()
        second.close()
    assert import_from_string(loop.LOOP_FACTORY_PATH) is loop.selector_loop_factory


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows selector-loop policy")
def test_importing_the_loop_module_sets_the_selector_policy_on_windows() -> None:
    policy = asyncio.get_event_loop_policy()
    assert type(policy).__name__ == "WindowsSelectorEventLoopPolicy"


def test_python_m_runs_uvicorn_with_the_loop_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    monkeypatch.delenv("SPEC_BUILDER", raising=False)
    monkeypatch.setattr(entry, "configure_logging", lambda *a, **k: None)
    # a network address needs real identities (API_TOKENS); dev authentication would be refused
    monkeypatch.setenv(
        "API_TOKENS", '{"integration-token-0123456789": {"tenant_id": "t1", "user_id": "u1"}}'
    )
    assert entry.main(["--host", "0.0.0.0", "--port", "9123", "--reload", "--fake-builder"]) == 0  # noqa: S104
    assert captured == {
        "app": "mde_context_api.main:app",
        "host": "0.0.0.0",  # noqa: S104
        "port": 9123,
        "reload": True,
        "loop": "mde_context_api.loop:selector_loop_factory",
    }
    assert os.environ["SPEC_BUILDER"] == "fake"  # inherited by uvicorn's reload child
    monkeypatch.delenv("SPEC_BUILDER")
    monkeypatch.delenv("API_TOKENS")
    captured.clear()
    entry.main([])
    assert captured["host"] == "127.0.0.1" and captured["port"] == 8100 and not captured["reload"]
    monkeypatch.delenv("SPEC_BUILDER", raising=False)


def test_the_worker_entry_point_runs_the_serve_loop_on_a_selector_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(coro: Any, *, loop_factory: Any) -> None:
        seen["factory"] = loop_factory
        coro.close()

    monkeypatch.setattr(asyncio, "run", fake_run)
    assert worker.main(["--fake-builder"]) == 0
    assert seen["factory"] is loop.selector_loop_factory

    def interrupted(coro: Any, *, loop_factory: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupted)
    assert worker.main([]) == 0  # Ctrl-C is a clean exit


async def test_the_worker_process_loop_starts_a_worker_and_closes_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_build(settings: ApiSettings, **kwargs: Any) -> AppState:
        assert kwargs == {"fake_builder": True}
        return await build_app_state(settings, store=InMemoryStore(), fake_builder=True)

    monkeypatch.setattr(composition, "build_app_state", fake_build)
    task = asyncio.create_task(worker._serve(make_settings(tmp_path), fake_builder=True))
    await asyncio.sleep(0.2)  # the worker is polling an empty queue
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(10):
            await task
