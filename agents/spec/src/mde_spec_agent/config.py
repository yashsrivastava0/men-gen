"""Spec settings: the money cap comes from the LAUNCH ENVIRONMENT only, never from `.env` (doc 19 5).

``SPEC_LIVE_BUDGET_USD`` (default 5.00) and ``SPEC_TRIPWIRE_USD`` (default 4.50) are read with no dotenv file. The
gateway keeps its own guard; ``guard_cap_usd`` sets that cap high enough that the gateway's must-run tripwire (80% of
its cap) never binds before Spec's own limits (the same arithmetic as Research, D-177).
"""

from __future__ import annotations

import os
from dataclasses import replace
from decimal import ROUND_UP, Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mde_core.money import usd_to_micros
from mde_llm_gateway import BudgetConfig

RUN_SCOPE_KIND = "spec_run"
VERIFY_SCOPE_KIND = "spec_verify"
PERIOD_SCOPE_KIND = "spec_period"
DEFAULT_PORT = 8130
FAKE_PORT = 8150


class SpecSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    spec_live_budget_usd: Decimal = Decimal("5.00")
    spec_tripwire_usd: Decimal = Decimal("4.50")
    spec_data_dir: Path | None = None
    spec_input_dirs: str = ""  # extra directories, separated by os.pathsep (SPEC_INPUT_DIRS)
    spec_ui_dir: Path | None = None
    spec_port: int = Field(default=DEFAULT_PORT, ge=1024, le=65535)
    spec_max_concurrent_runs: int = Field(default=1, ge=1, le=8)

    @property
    def cap_micros(self) -> int:
        return usd_to_micros(self.spec_live_budget_usd)

    @property
    def tripwire_micros(self) -> int:
        return min(usd_to_micros(self.spec_tripwire_usd), self.cap_micros)

    @property
    def input_dirs(self) -> list[Path]:
        return [Path(p) for p in self.spec_input_dirs.split(os.pathsep) if p.strip()]


def load_spec_settings(**overrides: object) -> SpecSettings:
    return SpecSettings(**overrides)  # type: ignore[arg-type]


def repo_root() -> Path:
    from mde_core.paths import main_repo_root

    return main_repo_root() or Path(__file__).resolve().parents[4]


def data_dir(settings: SpecSettings | None = None) -> Path:
    s = settings or load_spec_settings()
    return s.spec_data_dir or (repo_root() / "agents" / "spec" / ".runtime")


def ui_dir(settings: SpecSettings | None = None) -> Path:
    s = settings or load_spec_settings()
    return s.spec_ui_dir or (repo_root() / "apps" / "spec-bench")


def default_input_dirs(settings: SpecSettings | None = None) -> list[Path]:
    s = settings or load_spec_settings()
    root = repo_root()
    return [root / "docs" / "compass" / "research-inputs", data_dir(s) / "inputs", *s.input_dirs]


def guard_cap_usd(llm_budget_usd: Decimal, total_usd: Decimal) -> Decimal:
    """The gateway cap this app needs so ``total_usd`` is the real ceiling (must-run tripwire at 80%, 4% margin)."""
    need = (total_usd / Decimal("0.8") * Decimal("1.04")).quantize(
        Decimal("0.01"), rounding=ROUND_UP
    )
    return max(llm_budget_usd, need)


def spec_budget_config(core_cap_usd: Decimal | str, total_usd: Decimal | str) -> BudgetConfig:
    """The shared budget config plus Spec's scope kinds, each capped at the whole total (money is the only limit)."""
    base = BudgetConfig.load(cap_usd=core_cap_usd)
    total = usd_to_micros(total_usd)
    caps = {
        **base.scope_caps_micros,
        RUN_SCOPE_KIND: total,
        VERIFY_SCOPE_KIND: total,
        PERIOD_SCOPE_KIND: total,
    }
    return replace(
        base,
        scope_caps_micros=caps,
        scope_kinds=base.scope_kinds | {RUN_SCOPE_KIND, VERIFY_SCOPE_KIND, PERIOD_SCOPE_KIND},
    )
