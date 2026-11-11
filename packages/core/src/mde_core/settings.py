"""Settings (brief 6.1). Names match the environment variables exactly (case-insensitive).

Never print a Settings object's secret fields: they are ``SecretStr``. Use :func:`describe_presence`
for a safe "is it set?" report (used by ``scripts/doctor.py`` and ``mde doctor``).
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mde_core.paths import anchor_to_main_repo

LlmMode = Literal["mock", "replay", "live"]
Route = Literal["fast", "standard", "deep"]
ResearchDepth = Literal["light", "standard", "deep"]

DEFAULT_MODEL = "gpt-6-luna"


def _default_routes() -> dict[str, str]:
    return {"fast": DEFAULT_MODEL, "standard": DEFAULT_MODEL, "deep": DEFAULT_MODEL}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, env_file=None)

    openai_api_key: SecretStr | None = None
    openai_model: str = DEFAULT_MODEL
    llm_mode: LlmMode = "mock"
    llm_budget_usd: Decimal = Decimal("6.00")
    # Relative paths are anchored to the MAIN repository root (see ``_anchor_shared_path``).
    llm_budget_ledger: Path = Field(
        default=Path("data/.budget/ledger.jsonl"), validate_default=True
    )
    model_routes: dict[str, str] = Field(default_factory=_default_routes)

    # The URL carries the database password: keep it out of repr() and model_dump().
    database_url: str = Field(
        default="postgresql+psycopg://mde:mde@127.0.0.1:54329/mde", repr=False, exclude=True
    )

    gateway_mode: Literal["inprocess", "http"] = "inprocess"
    gateway_url: str | None = None
    gateway_service_token: SecretStr | None = None

    research_depth: ResearchDepth = "light"
    research_concurrency: int = 1

    api_dev_token: SecretStr = SecretStr("dev-token")
    # Hand-off git repositories (``PROJECTS_DIR``): a relative value is anchored to the main
    # repository root, so every worktree and working directory shares one hand-off location.
    projects_dir: Path = Field(default=Path("data/projects"), validate_default=True)

    # LLM cassettes (D-034): ``CASSETTE_DIR`` (anchored like the ledger) and
    # ``MDE_RECORD_CASSETTES=1`` (record a cassette after every successful live call).
    cassette_dir: Path = Field(default=Path("tests/cassettes"), validate_default=True)
    record_cassettes: bool = Field(
        default=False, validation_alias=AliasChoices("MDE_RECORD_CASSETTES", "record_cassettes")
    )

    log_level: str = "INFO"
    log_prompts: bool = False

    # Set to 1 by the pytest autouse fixture: any live provider construction must raise.
    forbid_live: bool = Field(
        default=False, validation_alias=AliasChoices("MDE_FORBID_LIVE", "forbid_live")
    )

    @field_validator("llm_budget_ledger", "projects_dir", "cassette_dir")
    @classmethod
    def _anchor_shared_path(cls, value: Path) -> Path:
        """A relative shared-data path means "in the main repository", never "in the cwd": a second
        worktree or another working directory must not see an empty ledger and a fresh budget (nor
        its own private hand-off repositories or cassettes). Absolute values are untouched."""
        return anchor_to_main_repo(value)

    @field_validator("record_cassettes", mode="before")
    @classmethod
    def _blank_record_flag_is_off(cls, value: object) -> object:
        """``MDE_RECORD_CASSETTES=`` (set but empty) means off, as it always did."""
        if isinstance(value, str) and not value.strip():
            return False
        return value

    @property
    def live_enabled(self) -> bool:
        """Live calls need LLM_MODE=live and not forbidden (brief 7.9)."""
        return self.llm_mode == "live" and not self.forbid_live

    def model_for_route(self, route: str) -> str:
        return self.model_routes.get(route, self.openai_model)

    def secret_values(self) -> list[str]:
        """Secret values for log scrubbing. Never log the return value."""
        out: list[str] = []
        for s in (self.openai_api_key, self.gateway_service_token, self.api_dev_token):
            if s is not None and s.get_secret_value():
                out.append(s.get_secret_value())
        password = _url_password(self.database_url)
        if password:
            out.append(password)
        return out


def _url_password(url: str) -> str | None:
    try:
        return urlsplit(url).password
    except ValueError:
        return None


def load_settings(**overrides: object) -> Settings:
    """Load settings from the process environment and (if present) the dotenv file.

    ``MDE_ENV_FILE`` selects the file; an empty value disables file loading (tests do this).
    """
    env_file = os.environ.get("MDE_ENV_FILE", ".env")
    return Settings(_env_file=env_file or None, **overrides)  # type: ignore[arg-type]


def describe_presence(settings: Settings) -> dict[str, str]:
    """Safe report: whether each setting is present/overridden. Never includes values."""
    report: dict[str, str] = {}
    for name in ("openai_api_key", "gateway_service_token"):
        report[name.upper()] = "present" if getattr(settings, name) is not None else "absent"
    report["OPENAI_MODEL"] = "default" if settings.openai_model == DEFAULT_MODEL else "custom"
    report["LLM_MODE"] = settings.llm_mode
    report["LLM_BUDGET_USD"] = str(settings.llm_budget_usd)
    report["DATABASE_URL"] = "set"
    report["GATEWAY_MODE"] = settings.gateway_mode
    report["RESEARCH_DEPTH"] = settings.research_depth
    return report
