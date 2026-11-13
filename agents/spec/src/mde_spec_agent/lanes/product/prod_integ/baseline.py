"""The draft integration records in the output schema (from the baseline graph) and the deterministic completion."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_core.graph import Prov, SpecGraph

from .schema import EventRec, IntegIn, IntegOut, IntegRec


def _prov(prov: Sequence[Prov], fallback: str) -> list[str]:
    return [f"{p.kind}:{p.ref}" for p in prov if p.ref] or [fallback]


def baseline_integ_out(graph: SpecGraph, ids: Collection[str]) -> IntegOut:
    out = IntegOut()
    for iid in sorted(ids):
        i = graph.integrations[iid]
        out.integrations.append(
            IntegRec(
                id=i.id,
                need_key=i.need_key,
                name=i.name,
                mode=i.mode if i.mode in vocab.integration_modes() else "mock",  # type: ignore[arg-type]
                provider_side_flow=i.provider_side_flow,
                flow=i.flow,
                interruption=i.interruption,
                pending_timeout=i.pending_timeout,
                mock_behaviour=i.mock_behaviour,
                provider_ids=list(i.provider_ids),
                claims=list(i.claims_used),
                prov=_prov(i.provenance, f"assumed:integration need {i.need_key}"),
            )
        )
        out.events += [EventRec(integration=i.id, name=e) for e in i.events]
    return out


def complete(inp: IntegIn) -> IntegOut:
    """Provider-side flows get an interrupted-flow rule and a pending timeout (V-35), a mock behaviour and the standard
    events when the draft has none, from the library texts (hints.yaml). Nothing else is added."""
    d = vocab.hints()["integration_defaults"]
    out = inp.baseline.model_copy(deep=True)
    has_events = {e.integration for e in out.events}
    for r in out.integrations:
        if r.provider_side_flow:
            r.flow = r.flow or d["flow"]
            r.interruption = r.interruption or d["interruption"]
            r.pending_timeout = r.pending_timeout or d["pending_timeout"]
            if r.id not in has_events:
                out.events += [EventRec(integration=r.id, name=n) for n in d["events"]]
        r.mock_behaviour = r.mock_behaviour or d["mock_behaviour"]
    return out
