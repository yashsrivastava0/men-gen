"""``probe_model``: the cheap model-availability check used by ``scripts/doctor.py --live``.

It asks each vendor for the metadata of every model the routes use (``models.retrieve``: no tokens, no
cost) and needs live mode to be enabled (``LLM_MODE=live``). The vendor of a model comes from the price
list, like everywhere else (rule 14). The result string never contains a key.

A model that is "available" here can still be refused by a real call (a spent balance answers real
calls, not metadata requests), so this is a reachability and permission check only.
"""

from __future__ import annotations

import anthropic
import openai

from mde_core.errors import ConfigError, UnknownModelPrice
from mde_core.redact import redact_text
from mde_core.settings import ROUTES, Settings
from mde_llm_gateway.gating import VENDOR_KEYS, require_api_key, require_live
from mde_llm_gateway.pricing import Pricing


def probe_model(
    settings: Settings,
    *,
    client: openai.OpenAI | None = None,
    anthropic_client: anthropic.Anthropic | None = None,
) -> str:
    """``"<model>: available"`` (or a one-line reason it is not) for each distinct routed model."""
    require_live(settings)
    pricing = Pricing.load()
    seen: list[str] = []
    lines: list[str] = []
    for route in ROUTES:
        model = settings.model_for_route(route)
        if model in seen:
            continue
        seen.append(model)
        try:
            vendor = pricing.vendor_for(model)
        except UnknownModelPrice:
            lines.append(f"{model}: no price on file (every call to it would be refused)")
            continue
        if vendor == "anthropic":
            lines.append(_probe_anthropic(settings, model, anthropic_client))
        elif vendor == "openai":
            lines.append(_probe_openai(settings, model, client))
        else:
            lines.append(f"{model}: no probe for vendor {vendor!r}")
    return "; ".join(lines)


def _probe_openai(settings: Settings, model: str, client: openai.OpenAI | None) -> str:
    api = client or openai.OpenAI(
        api_key=require_api_key(settings, "openai"), max_retries=0, timeout=15.0
    )
    try:
        api.models.retrieve(model)
    except openai.NotFoundError:
        return f"{model}: NOT available on this key (HTTP 404); do not fall back to another model"
    except openai.AuthenticationError:
        return f"{model}: authentication failed (HTTP 401); check {VENDOR_KEYS['openai'][1]}"
    except openai.PermissionDeniedError:
        return f"{model}: not permitted for this project (HTTP 403)"
    except openai.APIStatusError as exc:
        return f"{model}: probe failed (HTTP {exc.status_code})"
    except openai.APIConnectionError as exc:
        return f"{model}: probe failed ({redact_text(type(exc).__name__)[0]}: network)"
    except openai.OpenAIError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"probe failed: {type(exc).__name__}") from None
    return f"{model}: available"


def _probe_anthropic(settings: Settings, model: str, client: anthropic.Anthropic | None) -> str:
    api = client or anthropic.Anthropic(
        api_key=require_api_key(settings, "anthropic"), max_retries=0, timeout=15.0
    )
    try:
        api.models.retrieve(model)
    except anthropic.NotFoundError:
        return f"{model}: NOT available on this key (HTTP 404); do not fall back to another model"
    except anthropic.AuthenticationError:
        return f"{model}: authentication failed (HTTP 401); check {VENDOR_KEYS['anthropic'][1]}"
    except anthropic.PermissionDeniedError:
        return f"{model}: not permitted for this workspace (HTTP 403)"
    except anthropic.APIStatusError as exc:
        return f"{model}: probe failed (HTTP {exc.status_code})"
    except anthropic.APIConnectionError as exc:
        return f"{model}: probe failed ({redact_text(type(exc).__name__)[0]}: network)"
    except anthropic.AnthropicError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"probe failed: {type(exc).__name__}") from None
    return f"{model}: available"
