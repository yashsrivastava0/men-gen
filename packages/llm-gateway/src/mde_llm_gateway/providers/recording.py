"""RecordingProvider: wraps the live provider and writes sanitised cassettes (brief 7.4).

Only constructible when live calls are allowed (``LLM_MODE=live`` + ``ALLOW_LIVE_LLM=1`` and not
``MDE_FORBID_LIVE``). Calls are billed like any live call (it is a live call), and the ledger sees
them. With ``enabled=False`` it is a transparent pass-through.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mde_contracts.gateway import CallMode
from mde_core.settings import Settings
from mde_llm_gateway.gating import require_live
from mde_llm_gateway.providers.base import Provider, ProviderRequest, ProviderResponse
from mde_llm_gateway.providers.cassette import (
    DEFAULT_CASSETTE_DIR,
    build_cassette,
    cassette_key,
    write_cassette,
)


class RecordingProvider:
    name = "recording"
    billable = True

    def __init__(
        self,
        inner: Provider,
        *,
        settings: Settings,
        cassette_dir: str | Path | None = None,
        enabled: bool = True,
    ) -> None:
        require_live(settings)
        self.inner = inner
        self.enabled = enabled
        self.mode: CallMode = "live"
        self.cassette_dir = Path(cassette_dir) if cassette_dir is not None else DEFAULT_CASSETTE_DIR

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        response = await self.inner.complete(request)  # errors propagate: nothing is recorded
        if not self.enabled:
            return response
        key = cassette_key(request)
        write_cassette(self.cassette_dir, build_cassette(request, response, key))
        return replace(response, cassette_key=key)
