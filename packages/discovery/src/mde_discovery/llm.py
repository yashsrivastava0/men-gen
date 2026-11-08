"""LLM access for Discover: one place that calls the gateway, counts calls and falls back.

Every call goes through ``GatewayClient.run`` (never the gateway package). A failure that is not a
bug (``StructuredOutputError``, ``BudgetExceeded``, ``CircuitOpen``, ``ProviderError``, a timeout)
returns the spec's deterministic fallback instead, so Discover never surfaces a raw error. The
fallbacks are the same pure functions the offline mock handlers use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from mde_contracts.calls import C1Input, C1Output, C14Input, C14Output
from mde_contracts.engines import RunContext
from mde_contracts.gateway import CallContext, CallSpec, GatewayClient
from mde_core.errors import GatewayError
from mde_core.logging import get_logger
from mde_prompts.calls.c1_understand.keyword import understand as keyword_understand
from mde_prompts.calls.c2_interpret_brief.keyword import interpret as keyword_interpret
from mde_prompts.calls.c3_respond.snippets import respond as canned_reply
from mde_prompts.calls.c5_write_card.template import write_card as options_card
from mde_prompts.calls.c11_write_summary.template import summarise as template_sentence
from mde_prompts.calls.c14_interpret_change.rules import clarify_text
from mde_prompts.registry import load_spec

log = get_logger(__name__)
O = TypeVar("O", bound=BaseModel)  # noqa: E741

# Failures that mean "use the fallback". Anything else is a bug and propagates.
FALLBACK_ON: tuple[type[BaseException], ...] = (GatewayError, TimeoutError, asyncio.TimeoutError)


def use_the_buttons(inp: C14Input) -> C14Output:
    """C14 fallback: ask the user to use the buttons or edit the row (no guessing)."""
    return C14Output(
        understood=False, ops=[], clarify_question=clarify_text(inp.language), confidence=0.0
    )


def keyword_understander(inp: C1Input) -> C1Output:
    """C1 fallback: the keyword understander. A result it cannot place is intent ``other``."""
    return keyword_understand(inp)


FALLBACKS: dict[str, Callable[[Any], BaseModel]] = {
    "discovery.keyword_understander": keyword_understander,
    "discovery.keyword_archetype_match": keyword_interpret,
    "discovery.canned_reply": canned_reply,
    "discovery.options_card": options_card,
    "discovery.template_sentence": template_sentence,
    "discovery.use_the_buttons": use_the_buttons,
}


class Llm:
    """Thin wrapper over the gateway: build the context, run, fall back, count."""

    def __init__(self, gateway: GatewayClient) -> None:
        self._gateway = gateway

    def spec(self, call_id: str) -> CallSpec[Any, Any]:
        return load_spec(call_id)

    def call_context(self, ctx: RunContext, call_id: str) -> CallContext:
        return CallContext(
            request_id=ctx.request_id,
            budget_scope=ctx.session_scope,
            purpose=f"discover.{call_id.split('.')[0].lower()}",
            session_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
            cancel=ctx.cancel,
        )

    async def run(
        self, ctx: RunContext, call_id: str, inp: BaseModel, *, fallback: bool = True
    ) -> tuple[Any, bool, int]:
        """(output, used_fallback, calls_made). ``calls_made`` counts the attempt even on failure."""
        spec = self.spec(call_id)
        try:
            result = await self._gateway.run(spec, inp, self.call_context(ctx, call_id))
        except FALLBACK_ON as exc:
            log.warning("discover_llm_fallback", call=call_id, error=type(exc).__name__)
            return self._fallback(spec, inp), True, 1
        if result.output is None:
            return self._fallback(spec, inp), True, 1
        return result.output, False, 1

    @staticmethod
    def _fallback(spec: CallSpec[Any, Any], inp: BaseModel) -> BaseModel:
        name = spec.fallback
        if name is None or name not in FALLBACKS:
            raise RuntimeError(f"{spec.id}: no deterministic fallback registered ({name!r})")
        return FALLBACKS[name](inp)
