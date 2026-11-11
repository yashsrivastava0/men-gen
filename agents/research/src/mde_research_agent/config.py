"""Research settings, key presence, and the gateway/budget configuration for a run.

Provider keys live in the environment (never in code); ``keys_present()`` returns booleans only. The
budget has two layers: the repository-wide ledger stays authoritative (the shared gateway guard), and
Research adds its own cap for live spend (``RESEARCH_LIVE_BUDGET_USD``, default USD 4.00, a config value
not a constant) plus a per-run cap, enforced through the gateway's scope mechanism.
"""

from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from mde_core.money import usd_to_micros
from mde_llm_gateway import BudgetConfig, GatewayConfig

SearchProviderName = Literal["auto", "tavily", "serper", "fake"]
RUN_SCOPE_KIND = "research_run"
USER_AGENT = "MobigenResearchBot/1.0 (product research)"


class ResearchSettings(BaseSettings):
    """Names match the environment variables (case-insensitive). Secrets are ``SecretStr``: never print them."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    tavily_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    research_search_provider: SearchProviderName = "auto"
    # Live spend for Research as a whole (the shared ledger can still stop earlier) and per run.
    research_live_budget_usd: Decimal = Decimal("4.00")
    research_run_cap_usd: Decimal = Decimal("2.50")
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
    if want in ("tavily", "serper", "fake"):
        return want
    keys = keys_present(settings)
    other = "serper" if default == "tavily" else "tavily"
    if keys.get(default):
        return default
    if keys.get(other):
        return other
    return "none"


def research_budget_config(core_cap_usd: Decimal | str, run_cap_usd: Decimal | str) -> BudgetConfig:
    """The shared budget config plus a ``research_run`` scope kind capped at ``run_cap_usd`` per run."""
    base = BudgetConfig.load(cap_usd=core_cap_usd)
    caps = {**base.scope_caps_micros, RUN_SCOPE_KIND: usd_to_micros(run_cap_usd)}
    return replace(base, scope_caps_micros=caps, scope_kinds=base.scope_kinds | {RUN_SCOPE_KIND})


def gateway_config(*, offline: bool, lanes_extract: int = 16) -> GatewayConfig:
    """Concurrency for the extraction lanes; offline (mock) the rate limits are lifted."""
    concurrency = {"fast": max(8, lanes_extract), "standard": 8, "deep": 4}
    if offline:
        return GatewayConfig(
            concurrency=concurrency, rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000}
        )
    return GatewayConfig(concurrency=concurrency, rpm={"fast": 600, "standard": 240, "deep": 120})
