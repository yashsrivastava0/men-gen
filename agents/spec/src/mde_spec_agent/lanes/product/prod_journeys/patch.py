"""The patch a PROD-JOURNEYS answer makes: the draft flows rewritten with stages that carry an emotion line, moments and
unhappy paths; variants become new flows. The flow's own alternates stay first. Nothing is removed."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from mde_spec_agent.lanes.product.kit.common import (
    ProvAllowed,
    merge_prov,
    safe_prov,
    tier_and_assumed,
)
from mde_spec_agent.lanes.product.kit.env import Env, input_hash_of
from mde_spec_agent.lanes.product.kit.gate import Minter
from mde_spec_agent.run.patch import Patch
from mde_spec_core.graph import Flow, Stage

from .schema import JourneysIn, JourneysOut


def build_journeys_patch(
    env: Env, inp: JourneysIn, out: JourneysOut, minter: Minter, flagged: bool
) -> Patch:
    allowed = ProvAllowed.of(inp)
    ih = input_hash_of(inp)
    stages_of: dict[str, list[Any]] = defaultdict(list)
    for s in out.stages:
        stages_of[s.flow].append(s)
    ids = {f.id: (minter.mint("FLW") if f.id.startswith("NEW-") else f.id) for f in out.flows}
    nodes: dict[str, Any] = {}
    rcode: list[dict[str, str]] = []
    for rec in out.flows:
        fid = ids[rec.id]
        prov, _, rows = safe_prov(rec.prov, env.quote_of, allowed.labels, fid, claims_field=False)
        rcode.extend(rows)
        stages = [
            Stage(
                n=s.n,
                goal=s.goal,
                screen_ids=list(s.screen_ids),
                action_keys=list(s.action_keys),
                emotion=s.emotion,  # type: ignore[arg-type]
                emotion_assumed=not s.emotion_ref,
                pain=s.pain,
            )
            for s in sorted(stages_of.get(rec.id, []), key=lambda x: x.n)
        ]
        base = env.graph.flows.get(fid)
        if base is None:
            tier, assumed = tier_and_assumed(prov, allowed.labels)
            flow = Flow(
                id=fid,
                tier=tier,  # type: ignore[arg-type]
                assumed=assumed,
                provenance=prov,
                name=rec.name,
                role_ids=list(rec.role_ids),
                app_ids=list(rec.app_ids),
                primary=False,
                variant=rec.variant,  # type: ignore[arg-type]
            )
        else:
            flow = base.model_copy(deep=True)
            flow.provenance = merge_prov(flow.provenance, prov)
        flow.stages = stages
        flow.moments_that_matter = sorted(set(rec.moments))
        flow.unhappy_paths = list(
            dict.fromkeys([*(base.unhappy_paths if base else []), *rec.unhappy])
        )
        flow.mock_profile = rec.mock_profile
        flow.input_hash = ih
        flow.degraded = flow.degraded or flagged
        nodes[fid] = flow.model_dump(mode="json")
    patch: Patch = {"tables": {"flows": dict(sorted(nodes.items()))}, "replace": True}
    if rcode:
        patch["rcode"] = rcode
    return patch
