"""The patch a PROD-INTEG answer makes: the draft integration nodes, rewritten with the answer's text."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.lanes.product.kit.common import ProvAllowed, merge_prov, prov_nodes
from mde_spec_agent.lanes.product.kit.env import Env, input_hash_of
from mde_spec_agent.lanes.product.kit.gate import Minter
from mde_spec_agent.run.patch import Patch

from .schema import IntegIn, IntegOut


def build_integ_patch(
    env: Env, inp: IntegIn, out: IntegOut, minter: Minter, flagged: bool
) -> Patch:
    allowed = ProvAllowed.of(inp)
    ih = input_hash_of(inp)
    events: dict[str, list[str]] = {}
    for e in out.events:
        events.setdefault(e.integration, [])
        if e.name not in events[e.integration]:
            events[e.integration].append(e.name)
    nodes: dict[str, Any] = {}
    for r in out.integrations:
        base = env.graph.integrations.get(r.id)
        if base is None:
            continue  # validated away: a need exists only when the plan made it
        node = base.model_copy(deep=True)
        node.name = base.name
        node.mode = r.mode  # type: ignore[assignment]
        node.provider_side_flow = r.provider_side_flow
        node.flow = r.flow
        node.interruption = r.interruption
        node.pending_timeout = r.pending_timeout
        node.mock_behaviour = r.mock_behaviour
        node.events = events.get(r.id, [])
        node.provider_ids = sorted(set(r.provider_ids))
        node.claims_used = sorted({*r.claims, *base.claims_used})
        node.provenance = merge_prov(node.provenance, prov_nodes(r.prov, env.quote_of))
        node.input_hash = ih
        node.degraded = node.degraded or flagged
        _ = allowed
        nodes[r.id] = node.model_dump(mode="json")
    return {"tables": {"integrations": dict(sorted(nodes.items()))}, "replace": True}
