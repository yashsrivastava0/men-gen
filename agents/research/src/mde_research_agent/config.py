"""Research settings, key presence, and the gateway/budget configuration for a run.

Provider keys live in the environment (never in code); ``keys_present()`` returns booleans only. The
budget has two layers: the gateway guard (a ledger file per budget period, see ``spend/``) and Research's own
total (``RESEARCH_LIVE_BUDGET_USD``, default USD 5.00, with a tripwire at ``RESEARCH_TRIPWIRE_USD``, default 4.50).
There is NO per-run cap and no call-count cap: the only limit is money (owner direction, prompt 06).
"""

from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from mde_core.money import usd_to_micros
from mde_llm_gateway import BudgetConfig, GatewayConfig

SearchProviderName = Literal["auto", "tavily", "serper"]
StoreName = Literal["file", "postgres"]
RUN_SCOPE_KIND = "research_run"
PERIOD_SCOPE_KIND = "research_period"
USER_AGENT = "MobigenResearchBot/1.0 (product research)"
UNTRUSTED_CHARS = 120_000


class ResearchSettings(BaseSettings):
    """Names match the environment variables (case-insensitive). Secrets are ``SecretStr``: never print them."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    tavily_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    research_search_provider: SearchProviderName = "auto"
    # The total live budget for the app (every cent is recorded; never exceeded) and the tripwire: at or above it
    # the run in progress finishes and no new run starts.
    research_live_budget_usd: Decimal = Decimal("5.00")
    research_tripwire_usd: Decimal = Decimal("4.50")
    research_data_dir: Path | None = None
    research_inputs_dir: Path | None = None
    research_ui_dir: Path | None = None
    research_store: StoreName = "file"
    research_max_concurrent_runs: int = Field(default=1, ge=1, le=8)
    research_stale_after_s: int = Field(default=600, ge=60)
    research_dev_token: SecretStr = SecretStr("dev-token")
    research_cors_origins: str = (
        "http://127.0.0.1:8110,http://localhost:8110,http://127.0.0.1:8120,http://localhost:8120"
    )
    research_user_agent: str = USER_AGENT

    def secret_values(self) -> list[str]:
        """Secret values for log scrubbing. Never log the return value."""
        out = []
        for s in (self.tavily_api_key, self.serper_api_key, self.research_dev_token):
            if s is not None and s.get_secret_value():
                out.append(s.get_secret_value())
        return out

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.research_cors_origins.split(",") if o.strip()]


def load_research_settings(**overrides: object) -> ResearchSettings:
    """From the process environment and the dotenv file (``MDE_ENV_FILE`` selects it; empty disables it)."""
    env_file = os.environ.get("MDE_ENV_FILE", ".env")
    return ResearchSettings(_env_file=env_file or None, **overrides)  # type: ignore[arg-type]


def keys_present(settings: ResearchSettings | None = None) -> dict[str, bool]:
    """Which search keys are set. Booleans only, never values."""
    s = settings or load_research_settings()
    return {
        "tavily": bool(s.tavily_api_key and s.tavily_api_key.get_secret_value()),
        "serper": bool(s.serper_api_key and s.serper_api_key.get_secret_value()),
    }


def choose_provider(settings: ResearchSettings, default: str = "tavily") -> str:
    """The search provider for a run: an explicit choice, else the measured default if its key is set, else the other."""
    want = settings.research_search_provider
    if want in ("tavily", "serper"):
        return want
    keys = keys_present(settings)
    other = "serper" if default == "tavily" else "tavily"
    if keys.get(default):
        return default
    if keys.get(other):
        return other
    return "none"


def research_budget_config(core_cap_usd: Decimal | str, total_usd: Decimal | str) -> BudgetConfig:
    """The shared budget config plus a ``research_run`` scope kind. A run's scope is capped at the whole budget,
    so no per-run cap ever binds (the total is the only limit)."""
    base = BudgetConfig.load(cap_usd=core_cap_usd)
    total = usd_to_micros(total_usd)
    # ``research_run:<id>`` per run and ``research_period:<id>`` for the whole budget period: the second is the
    # hard stop. The guard refuses (atomically) any reservation that would take the period past the total.
    caps = {**base.scope_caps_micros, RUN_SCOPE_KIND: total, PERIOD_SCOPE_KIND: total}
    return replace(
        base,
        scope_caps_micros=caps,
        scope_kinds=base.scope_kinds | {RUN_SCOPE_KIND, PERIOD_SCOPE_KIND},
    )


def gateway_config(*, offline: bool, lanes_extract: int = 16) -> GatewayConfig:
    """Concurrency for the extraction lanes; offline (mock) the rate limits are lifted."""
    concurrency = {"fast": max(8, lanes_extract), "standard": 8, "deep": 4}
    # The gateway cuts every untrusted field at 20,000 characters by default; a checker batch (15 items with their passages
    # and data) is longer, and a silent cut would drop the LAST items of the batch. 120,000 keeps a whole batch.
    if offline:
        return GatewayConfig(
            concurrency=concurrency,
            rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000},
            max_untrusted_chars=UNTRUSTED_CHARS,
        )
    return GatewayConfig(
        concurrency=concurrency,
        rpm={"fast": 600, "standard": 240, "deep": 120},
        max_untrusted_chars=UNTRUSTED_CHARS,
    )


def repo_root() -> Path:
    """The main checkout (worktrees share it); falls back to the source tree location."""
    from mde_core.paths import main_repo_root

    return main_repo_root() or Path(__file__).resolve().parents[4]


def data_dir(settings: ResearchSettings) -> Path:
    """``RESEARCH_DATA_DIR`` (default ``agents/research/.runtime``, git-ignored)."""
    return (
        Path(settings.research_data_dir)
        if settings.research_data_dir
        else (repo_root() / "agents" / "research" / ".runtime")
    )


def inputs_dir(settings: ResearchSettings) -> Path:
    """``RESEARCH_INPUTS_DIR`` (default ``docs/compass/research-inputs``; read-only for the app)."""
    return (
        Path(settings.research_inputs_dir)
        if settings.research_inputs_dir
        else (repo_root() / "docs" / "compass" / "research-inputs")
    )


def ui_dir(settings: ResearchSettings) -> Path:
    """The static front end (``apps/research-console`` unless ``RESEARCH_UI_DIR`` says otherwise)."""
    return (
        Path(settings.research_ui_dir)
        if settings.research_ui_dir
        else (repo_root() / "apps" / "research-console")
    )
