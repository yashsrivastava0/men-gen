"""Smoke driver: one real session plus one direct call of every call type, with a per-call report.

Used twice with the same code: offline in mock mode (a test keeps it working) and live (batch A, at most
USD 0.10, only with ``LLM_MODE=live`` and ``ALLOW_LIVE_LLM=1`` set by the user; see the runbook). It confirms
the model id, that every strict schema is accepted, effort and cost reporting, and which calls fell back.
Vision and streaming are reported as unsupported: the gateway has no image input and does not stream.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mde_compass.config import MODEL_ID
from mde_compass.engine.ports import TurnContext
from mde_compass.engine.service import Service, tenant_scope
from mde_compass.testing import Conversation

IDEA = "I want an app for my restaurant in Pune where customers can order food and track delivery"
CHUNK = (
    "Customers must be able to reorder a previous meal in two taps. "
    "Refunds are handled by the restaurant owner. Payment provider: TBD."
)


class CallReport(BaseModel):
    spec_id: str
    outcome: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: str = "0.0000"
    latency_ms: int = 0


class SmokeReport(BaseModel):
    model: str = MODEL_ID
    mode: str
    final_state: str
    calls: list[CallReport] = Field(default_factory=list)
    call_types_seen: list[str] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)
    spent_usd: str = "0.0000"
    cap_usd: str = "0.00"
    errors: list[str] = Field(default_factory=list)
    vision: str = "unsupported by the gateway (no image input); not tested"
    streaming: str = "unsupported by the gateway; assistant_delta is never emitted"


async def run_smoke(service: Service) -> SmokeReport:
    errors: list[str] = []
    conv = await Conversation.start(service)
    await conv.say(IDEA)
    steps = 0
    while await conv.state() != "BRIEF_REVIEW" and steps < 30 and conv.last_card() is not None:
        await conv.tap_recommended()
        steps += 1
    # a question, a chat change and a disliked assumption exercise answer / interpret_change / make_variants
    await conv.say("How long will this take and what will it cost?")
    await conv.say("Please make the app Hindi first")
    await conv.feedback("features", "too_much", "I did not like the features list")
    brief = await conv.brief()
    assumptions = brief.get("assumptions") or []
    if assumptions:
        await conv.feedback(f"assumptions.{assumptions[0]['id']}", "dont_want_assumption")
    # the intake-side calls, directly with tiny inputs (documents are never sent whole)
    ctx = TurnContext(session_id=conv.session_id, tenant_id=conv.caller.tenant_id)
    ports = service.ports
    assert ports is not None
    with tenant_scope(conv.caller.tenant_id):
        try:
            statements = await ports.chunk_reader.read(
                ctx, chunk_text=CHUNK, source_id="SRC-001", pointer="p1"
            )
            await ports.claim_merger.merge(ctx, statements, source_id="SRC-001")
            from mde_compass.domain import Brief

            await ports.ambiguity_scanner.scan(
                ctx, Brief.model_validate({k: v for k, v in brief.items() if not k.startswith("_")})
            )
        except Exception as exc:
            errors.append(f"direct calls: {type(exc).__name__}: {exc}")
    entries: list[dict[str, Any]] = await service.calls(conv.caller, conv.session_id, limit=500)
    calls = [CallReport(**e) for e in entries]
    seen = sorted({c.spec_id for c in calls})
    snap = service.budget_snapshot()
    return SmokeReport(
        mode=str(snap["mode"]),
        final_state=await conv.state(),
        calls=calls,
        call_types_seen=seen,
        fallbacks=sorted({c.spec_id for c in calls if c.outcome == "fallback"}),
        spent_usd=str(snap["spent_usd"]),
        cap_usd=str(snap["cap_usd"]),
        errors=errors,
    )
