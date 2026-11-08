"""Live gating (brief 7.9): who may create a provider that spends real money.

A live provider needs ALL of: ``LLM_MODE=live``, ``ALLOW_LIVE_LLM=1`` and NOT ``MDE_FORBID_LIVE=1``
(the pytest autouse fixture sets the last one, so a test can never go live by accident, even if the
other two are set in the outer environment).
"""

from __future__ import annotations

from mde_core.errors import ConfigError, LiveCallForbidden
from mde_core.settings import Settings


def require_live(settings: Settings) -> None:
    """Raise :class:`LiveCallForbidden` unless live calls are allowed by ``settings``."""
    if settings.forbid_live:
        raise LiveCallForbidden(
            "MDE_FORBID_LIVE=1: live LLM calls are forbidden in this process (tests set it)"
        )
    if settings.llm_mode != "live" or not settings.allow_live_llm:
        raise LiveCallForbidden(
            "live LLM calls need LLM_MODE=live AND ALLOW_LIVE_LLM=1 (both are required)"
        )


def require_api_key(settings: Settings) -> str:
    """The API key for a live client. Never log or echo the return value."""
    key = settings.openai_api_key
    if key is None or not key.get_secret_value():
        raise ConfigError("OPENAI_API_KEY is not set; live mode needs it")
    return key.get_secret_value()
