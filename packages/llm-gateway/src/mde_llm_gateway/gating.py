"""Live gating (brief 7.9): who may create a provider that spends real money.

A live provider needs ``LLM_MODE=live`` and NOT ``MDE_FORBID_LIVE=1`` (the pytest autouse fixture sets
the latter, so a test can never go live by accident, even if ``LLM_MODE=live`` is set in the outer
environment).
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
    if settings.llm_mode != "live":
        raise LiveCallForbidden("live LLM calls need LLM_MODE=live")


# Which Settings field holds each vendor's key, and the environment variable it is read from.
VENDOR_KEYS: dict[str, tuple[str, str]] = {
    "openai": ("openai_api_key", "OPENAI_API_KEY"),
    "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
}


def require_api_key(settings: Settings, vendor: str = "openai", *, needed_by: str = "") -> str:
    """The API key of ``vendor`` for a live client. Never log or echo the return value.

    ``needed_by`` (for example ``"route fast uses claude-haiku-5-5"``) makes the error say why.
    """
    if vendor not in VENDOR_KEYS:
        raise ConfigError(
            f"no provider is known for vendor {vendor!r} (known: {', '.join(sorted(VENDOR_KEYS))})"
        )
    field, env_name = VENDOR_KEYS[vendor]
    key = getattr(settings, field, None)
    if key is None or not key.get_secret_value():
        why = f" ({needed_by})" if needed_by else ""
        raise ConfigError(f"{env_name} is not set; live mode needs it{why}")
    return str(key.get_secret_value())
