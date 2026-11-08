"""API settings: the shared ``mde_core.Settings`` plus what only the HTTP service needs.

Names follow the environment variables (case-insensitive), like the base class. Everything has a
safe default, so ``ApiSettings()`` works offline with no key and no network.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import NoDecode

from mde_context_api.bindings import PolicyBinding
from mde_context_api.identity import TokenTable, parse_api_tokens, valid_tenant
from mde_core.settings import Settings

DEFAULT_DEV_TOKEN = "dev-token"  # noqa: S105 - the documented dev default, never a secret


class ApiSettings(Settings):
    # --- policies (BRIEF 8.7: guided -> guided-default, enterprise -> a configurable default) ---
    default_guided_policy: str = "guided-default"
    default_enterprise_policy: str = "enterprise-it-services-firm"

    # --- the Spec Builder: ``real`` imports mde_spec_builder; ``fake`` is the scripted dev builder
    spec_builder: Literal["real", "fake"] = "real"

    # --- the budget pause (spec 9: pause before the Plan phase when the cap is reached) -------
    # What one plan run needs under the session's and the whole budget's caps (USD): below this much
    # headroom the flow pauses before planning instead of letting every call fall back one by one.
    budget_plan_reserve_usd: str = "0.05"

    # --- the interactive docs: /docs, /redoc and /openapi.json are unauthenticated, so they are
    # off unless API_DOCS=1 (the OpenAPI document is still exported offline: docs/api/openapi.json)
    api_docs: bool = False

    # --- who the caller is -------------------------------------------------------------------
    # ``API_TOKENS`` (JSON: token -> {tenant_id, user_id, roles, ...}) switches to the token table:
    # a presented token IS the identity and the X-* headers are ignored. Unset = dev headers (one
    # shared token plus self-declared identity: local use only, never on a network).
    api_tokens: SecretStr | None = None

    # ``TENANT_POLICIES`` (JSON: tenant id -> {allowed_policies, default_policy, default_mode}):
    # the sessions a tenant may start. A tenant with an entry cannot pick another mode or policy.
    tenant_policies: Annotated[dict[str, PolicyBinding], NoDecode] = Field(default_factory=dict)

    # --- abuse limits (per process; the budget guard stays the authority on spend) -------------
    # 0 turns a limit off (for example a local load test). Session creation is limited per caller
    # and per tenant because every live session may spend up to the session cap.
    api_max_streams_per_principal: int = Field(default=5, ge=0)
    api_session_rate_per_minute: int = Field(default=10, ge=0)
    api_session_rate_per_hour: int = Field(default=100, ge=0)
    api_tenant_session_rate_per_minute: int = Field(default=10, ge=0)
    api_tenant_session_rate_per_hour: int = Field(default=100, ge=0)

    # --- request limits ---------------------------------------------------------------------
    max_body_bytes: int = Field(
        default=1_048_576,  # 1 MB
        ge=1024,
        validation_alias=AliasChoices("API_MAX_BODY_BYTES", "MAX_BODY_BYTES", "max_body_bytes"),
    )
    max_upload_text_bytes: int = Field(default=524_288, ge=1024)  # text/markdown content
    max_upload_image_bytes: int = Field(default=10_485_760, ge=1024)  # declared size of an image
    max_org_profile_bytes: int = Field(default=65_536, ge=1024)

    # --- the in-process job worker ----------------------------------------------------------
    worker_enabled: bool = True  # WORKER_ENABLED=0: run `python -m mde_context_api.worker` instead
    worker_poll_seconds: float = Field(default=0.5, gt=0)
    worker_heartbeat_seconds: float = Field(default=10.0, gt=0)
    worker_recover_after_seconds: float = Field(default=120.0, gt=0)
    worker_stop_timeout_seconds: float = Field(default=10.0, gt=0)

    # --- SSE ----------------------------------------------------------------------------------
    sse_poll_seconds: float = Field(default=0.5, gt=0)  # one short transaction per poll
    # an idle stream polls less and less often, up to this long between polls, and goes back to
    # ``sse_poll_seconds`` as soon as an event is sent (so many idle streams cannot hammer the pool)
    sse_max_poll_seconds: float = Field(default=2.0, gt=0)
    sse_keepalive_seconds: float = Field(default=15.0, gt=0)
    sse_max_seconds: float = Field(
        default=900.0, gt=0
    )  # a stream ends after this; clients reconnect
    sse_batch_size: int = Field(default=200, ge=1, le=500)

    # --- serving (python -m mde_context_api) -------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8100, ge=1, le=65535)

    _token_table: TokenTable | None = PrivateAttr(default=None)

    @field_validator("tenant_policies", mode="before")
    @classmethod
    def _parse_tenant_policies(cls, value: object) -> object:
        """``TENANT_POLICIES`` arrives as a JSON string from the environment (blank = none)."""
        if isinstance(value, str):
            if not value.strip():
                return {}
            try:
                return json.loads(value)
            except ValueError:
                raise ValueError("TENANT_POLICIES is not valid JSON") from None
        return value

    @field_validator("tenant_policies")
    @classmethod
    def _check_tenant_policies(cls, value: dict[str, PolicyBinding]) -> dict[str, PolicyBinding]:
        for tenant, binding in value.items():
            if not valid_tenant(tenant):
                raise ValueError("TENANT_POLICIES: a key is not a valid tenant id")
            if not binding.restricts:
                raise ValueError(
                    "TENANT_POLICIES: an entry needs allowed_policies, default_policy "
                    "or default_mode"
                )
        return value

    @field_validator("api_tokens")
    @classmethod
    def _blank_tokens_are_unset(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def _build_token_table(self) -> ApiSettings:
        """Parse ``API_TOKENS`` once, at load: a bad table stops the service at start-up. The
        message never contains a token or a value."""
        raw = self.api_tokens
        self._token_table = (
            None
            if raw is None
            else parse_api_tokens(raw.get_secret_value(), forbidden=(DEFAULT_DEV_TOKEN,))
        )
        return self

    @property
    def token_table(self) -> TokenTable | None:
        """The configured tokens, or ``None`` in dev-header mode."""
        return self._token_table

    @property
    def token_mode(self) -> bool:
        return self._token_table is not None

    @property
    def dev_token(self) -> str:
        return self.api_dev_token.get_secret_value()

    def secret_values(self) -> list[str]:
        """The shared secrets plus every configured token (so none can reach a log line)."""
        out = super().secret_values()
        if self._token_table is not None:
            out.extend(self._token_table.tokens)
        return out


def load_api_settings(**overrides: object) -> ApiSettings:
    """Like ``mde_core.settings.load_settings`` for the API's settings class.

    ``MDE_ENV_FILE`` selects the dotenv file (default ``.env``); an empty value disables file
    loading (the tests do this).
    """
    env_file = os.environ.get("MDE_ENV_FILE", ".env")
    return ApiSettings(_env_file=env_file or None, **overrides)  # type: ignore[arg-type]
