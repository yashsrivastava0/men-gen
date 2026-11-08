"""Registries: canonical call ids with their IO models, and the set of exported JSON Schemas."""

from __future__ import annotations

from pydantic import BaseModel

from mde_contracts import brief, calls, cards, events, gates, plan, policy, research, slots, spec

# call id -> (input model, output model or None for free-text S1)
CALL_IO: dict[str, tuple[type[BaseModel], type[BaseModel] | None]] = {
    "C1.understand": (calls.C1Input, calls.C1Output),
    "C2.interpret_brief": (calls.C2Input, calls.C2Output),
    "C3.respond": (calls.C3Input, calls.C3Output),
    "C5.write_card": (calls.C5Input, calls.C5Output),
    "C6.plan_research": (calls.C6Input, calls.C6Output),
    "S1.web_search": (calls.S1Input, None),
    "C7.extract": (calls.C7Input, calls.C7Output),
    "C8.synthesize_research": (calls.C8Input, calls.C8Output),
    "C9a.screens_nav": (calls.C9aInput, calls.C9aOutput),
    "C9b.data_api_roles": (calls.C9bInput, calls.C9bOutput),
    "C9c.design_tokens": (calls.C9cInput, calls.C9cOutput),
    "C10.screen_briefs": (calls.C10Input, calls.C10Output),
    "C11.write_summary": (calls.C11Input, calls.C11Output),
    "C12.critic": (calls.C12Input, calls.C12Output),
    "C13.repair": (calls.C13Input, calls.C13Output),
    "C14.interpret_change": (calls.C14Input, calls.C14Output),
}


def call_folder(call_id: str) -> str:
    """``C1.understand`` -> ``c1_understand`` (folder under ``mde_prompts/calls/``)."""
    return call_id.lower().replace(".", "_")


def llm_output_models() -> dict[str, type[BaseModel]]:
    """Structured output models that must convert to OpenAI strict JSON schema."""
    return {cid: out for cid, (_, out) in CALL_IO.items() if out is not None}


def exported_models() -> dict[str, type[BaseModel]]:
    """Name -> model for ``just export-schemas`` (events, cards, Brief, Research Note, Plan view,
    every spec block, policy, call IO)."""
    out: dict[str, type[BaseModel]] = {
        # events
        "event.UserMessage": events.UserMessage,
        "event.OptionSelected": events.OptionSelected,
        "event.UploadAdded": events.UploadAdded,
        "event.GateDecision": events.GateDecision,
        "event.UiAction": events.UiAction,
        "event.AssistantMessage": events.AssistantMessage,
        "event.CardEvent": events.CardEvent,
        "event.ProgressEvent": events.ProgressEvent,
        "event.GateEvent": events.GateEvent,
        "event.PlanReady": events.PlanReady,
        "event.SpecReady": events.SpecReady,
        "event.StateChanged": events.StateChanged,
        "event.Notice": events.Notice,
        # cards / slots / gates
        "Card": cards.Card,
        "CardInstance": cards.CardInstance,
        "SlotRecord": slots.SlotRecord,
        "SlotDef": slots.SlotDef,
        "ApprovalRecord": gates.ApprovalRecord,
        "GateStatus": gates.GateStatus,
        # outputs of the engines
        "Brief": brief.Brief,
        "ResearchNote": research.ResearchNote,
        "PlanView": plan.PlanView,
        "Policy": policy.Policy,
        # spec blocks
        "spec.AppSpec": spec.AppSpec,
        "spec.SpecHeader": spec.SpecHeader,
        "spec.AppBlock": spec.AppBlock,
        "spec.DesignBlock": spec.DesignBlock,
        "spec.NavigationBlock": spec.NavigationBlock,
        "spec.DataBlock": spec.DataBlock,
        "spec.ApiBlock": spec.ApiBlock,
        "spec.RolesBlock": spec.RolesBlock,
        "spec.IntegrationsBlock": spec.IntegrationsBlock,
        "spec.BehaviorBlock": spec.BehaviorBlock,
        "spec.ScreenBlock": spec.ScreenBlock,
        "spec.FlowBlock": spec.FlowBlock,
        "spec.BuildSequence": spec.BuildSequence,
        "spec.AcceptanceBlock": spec.AcceptanceBlock,
        "spec.AssumptionsBlock": spec.AssumptionsBlock,
        "spec.RevisionBlock": spec.RevisionBlock,
    }
    for cid, (inp, outp) in CALL_IO.items():
        out[f"call.{cid}.input"] = inp
        if outp is not None:
            out[f"call.{cid}.output"] = outp
    return out
