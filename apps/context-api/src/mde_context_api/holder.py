"""``StateHolder``: the lazily built ``AppState`` behind the application.

Why lazy: ``create_app()`` must work offline with no settings file, no database and no key (the
OpenAPI export, ``uvicorn mde_context_api.main:app`` import, tests). The state is built

* eagerly in the lifespan (fail fast at start-up, and the worker starts there), or
* on the first request that needs it, when the application runs without a lifespan,

never at import time. Settings are lazy too, so authentication can reject a request before anything
else is built.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from mde_context_api.composition import AppState, build_app_state
from mde_context_api.settings import ApiSettings, load_api_settings

StateFactory = Callable[[ApiSettings], Awaitable[AppState]]


class StateHolder:
    def __init__(
        self,
        settings: ApiSettings | None = None,
        *,
        state: AppState | None = None,
        factory: StateFactory | None = None,
    ) -> None:
        self._settings = settings if settings is not None else (state.settings if state else None)
        self._state = state
        self._owned = state is None  # a state passed in is closed by whoever built it
        self._factory = factory or build_app_state
        self._lock: asyncio.Lock | None = None

    @property
    def settings(self) -> ApiSettings:
        if self._settings is None:
            self._settings = load_api_settings()
        return self._settings

    @property
    def built(self) -> bool:
        return self._state is not None

    async def get(self) -> AppState:
        if self._state is not None:
            return self._state
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._state is None:
                self._state = await self._factory(self.settings)
        return self._state

    async def start(self) -> AppState:
        """Lifespan start: build the state and (unless disabled) start the in-process worker."""
        state = await self.get()
        if state.settings.worker_enabled:
            state.start_worker()
        return state

    async def close(self) -> None:
        """Lifespan stop: always stop the worker; release the pool only if this holder built it."""
        state = self._state
        if state is None:
            return
        if self._owned:
            await state.aclose()
            self._state = None
        else:
            await state.stop_worker()
