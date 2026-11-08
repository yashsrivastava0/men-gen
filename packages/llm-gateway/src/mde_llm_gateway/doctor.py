"""``probe_model``: the cheap model-availability check used by ``scripts/doctor.py --live``.

It calls ``models.retrieve`` (metadata only: no tokens, no cost) and needs live mode to be enabled
(``LLM_MODE=live`` + ``ALLOW_LIVE_LLM=1``). The result string never contains the key.
"""

from __future__ import annotations

import openai

from mde_core.errors import ConfigError
from mde_core.redact import redact_text
from mde_core.settings import Settings
from mde_llm_gateway.gating import require_api_key, require_live


def probe_model(settings: Settings, *, client: openai.OpenAI | None = None) -> str:
    """Return ``"<model>: available"`` or a one-line reason it is not. Costs nothing."""
    require_live(settings)
    model = settings.openai_model
    api = client or openai.OpenAI(api_key=require_api_key(settings), max_retries=0, timeout=15.0)
    try:
        api.models.retrieve(model)
    except openai.NotFoundError:
        return f"{model}: NOT available on this key (HTTP 404); do not fall back to another model"
    except openai.AuthenticationError:
        return f"{model}: authentication failed (HTTP 401); check OPENAI_API_KEY"
    except openai.PermissionDeniedError:
        return f"{model}: not permitted for this project (HTTP 403)"
    except openai.APIStatusError as exc:
        return f"{model}: probe failed (HTTP {exc.status_code})"
    except openai.APIConnectionError as exc:
        return f"{model}: probe failed ({redact_text(type(exc).__name__)[0]}: network)"
    except openai.OpenAIError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"probe failed: {type(exc).__name__}") from None
    return f"{model}: available"
