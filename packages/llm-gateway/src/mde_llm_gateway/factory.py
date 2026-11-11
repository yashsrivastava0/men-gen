"""``create_gateway``: assemble a :class:`Gateway` from settings (brief 7.4, 7.9).

The provider follows ``settings.llm_mode``:

* ``mock``   -> :class:`MockProvider` (default; no key, no network)
* ``replay`` -> :class:`ReplayProvider` over ``settings.cassette_dir`` (env ``CASSETTE_DIR``,
  default ``tests/cassettes`` in the MAIN repository, anchored like the ledger)
* ``live``   -> :class:`OpenAIResponsesProvider`, ONLY if ``LLM_MODE=live``
  and not ``MDE_FORBID_LIVE=1`` (else :class:`LiveCallForbidden`), optionally wrapped in a
  :class:`RecordingProvider` (``settings.record_cassettes``, env ``MDE_RECORD_CASSETTES=1``).

The two cassette switches are ``mde_core.Settings`` fields (D-034); the ``cassette_dir`` and
``record_cassettes`` arguments of :func:`create_gateway` override them for one gateway.

The ledger follows billing: live (and recorded-live) calls use the shared file ledger
(``LLM_BUDGET_LEDGER``, a relative path is anchored to the MAIN repository root, never the working
directory); mock, replay and scripted providers use an in-memory ledger, so tests never pollute
``data/.budget``.

A billing gateway refuses to start when the ledger contradicts its guard file (deleted, truncated:
see ``ledger.py``) unless the operator resets on purpose with ``MDE_BUDGET_LEDGER_RESET=1`` (or
``python -m mde_llm_gateway.budget_cli init --force``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from mde_contracts.gateway import LlmCallSink
from mde_core.clock import Clock, SystemClock
from mde_core.errors import ConfigError
from mde_core.paths import anchor_to_main_repo
from mde_core.settings import Settings
from mde_llm_gateway.budget import BudgetConfig, BudgetGuard
from mde_llm_gateway.gateway import Gateway, GatewayConfig
from mde_llm_gateway.gating import require_api_key, require_live
from mde_llm_gateway.ledger import FileLedger, Ledger, MemoryLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers.base import Provider
from mde_llm_gateway.providers.mock import MockProvider
from mde_llm_gateway.providers.replay import ReplayProvider
from mde_llm_gateway.registry import SpecRegistry

ENV_CASSETTE_DIR = "CASSETTE_DIR"  # read by ``Settings.cassette_dir``
ENV_RECORD = "MDE_RECORD_CASSETTES"  # read by ``Settings.record_cassettes``
ENV_LEDGER_RESET = "MDE_BUDGET_LEDGER_RESET"
OPENAI_CLIENT_TIMEOUT_S = 600.0  # the per-call timeout is set on every request instead


def _build_provider(
    settings: Settings,
    *,
    cassette_dir: str | Path | None,
    record_cassettes: bool | None,
    openai_client: object | None,
) -> Provider:
    mode = settings.llm_mode
    directory = Path(cassette_dir) if cassette_dir is not None else settings.cassette_dir
    if mode == "mock":
        return MockProvider()
    if mode == "replay":
        return ReplayProvider(directory)
    if mode != "live":  # pragma: no cover - Settings validates the literal
        raise ConfigError(f"unknown LLM_MODE {mode!r}")
    require_live(settings)  # raises LiveCallForbidden before anything else happens
    from mde_llm_gateway.providers.openai_responses import (  # openai is only imported when live
        OpenAIClientLike,
        OpenAIResponsesProvider,
    )
    from mde_llm_gateway.providers.recording import RecordingProvider

    client = openai_client
    if client is None:
        import openai

        client = openai.AsyncOpenAI(
            api_key=require_api_key(settings),
            max_retries=0,  # the gateway owns retries: every attempt must be metered
            timeout=OPENAI_CLIENT_TIMEOUT_S,
        )
    live: Provider = OpenAIResponsesProvider(cast("OpenAIClientLike", client), settings=settings)
    record = record_cassettes if record_cassettes is not None else settings.record_cassettes
    if record:
        return RecordingProvider(live, settings=settings, cassette_dir=directory)
    return live


def create_gateway(
    settings: Settings,
    registry: SpecRegistry,
    *,
    provider: Provider | None = None,
    ledger: Ledger | None = None,
    sink: LlmCallSink | None = None,
    clock: Clock | None = None,
    config: GatewayConfig | None = None,
    pricing: Pricing | None = None,
    budget_config: BudgetConfig | None = None,
    cassette_dir: str | Path | None = None,
    record_cassettes: bool | None = None,
    openai_client: object | None = None,
) -> Gateway:
    """Build the gateway for ``settings``. Pass ``provider`` to override the mode's default."""
    the_clock: Clock = clock or SystemClock()
    the_provider = provider or _build_provider(
        settings,
        cassette_dir=cassette_dir,
        record_cassettes=record_cassettes,
        openai_client=openai_client,
    )
    the_ledger = ledger
    if the_ledger is None:
        the_ledger = (
            FileLedger(anchor_to_main_repo(settings.llm_budget_ledger), clock=the_clock)
            if the_provider.billable
            else MemoryLedger(clock=the_clock)
        )
    if the_provider.billable and isinstance(the_ledger, FileLedger):
        # Refuses to start on a deleted or truncated ledger unless the operator resets on purpose.
        the_ledger.verify_integrity(reset=os.environ.get(ENV_LEDGER_RESET, "") in {"1", "true"})
    guard = BudgetGuard(
        budget_config or BudgetConfig.load(cap_usd=settings.llm_budget_usd),
        pricing or Pricing.load(),
        the_ledger,
        bills=the_provider.billable,
        clock=the_clock,
    )
    return Gateway(
        registry=registry,
        provider=the_provider,
        guard=guard,
        settings=settings,
        sink=sink,
        clock=the_clock,
        config=config,
    )
