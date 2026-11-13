"""Gate 1 after a call: apply the patch to a COPY of the graph and run the validators (doc 18 3.1).

Findings about nodes the patch touched become validator messages for ONE targeted re-ask (the runner's ledger enforces
one per node in the whole run). Findings about other nodes are not this lane's to fix and are ignored here."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.run.patch import Patch, apply_patch
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context

TOKEN_AND_ASSET_CHECKS = {"V-12", "V-43"}  # later gates, but cheap and decided by this lane


def touched_ids(patch: Patch) -> set[str]:
    ids: set[str] = set()
    for nodes in patch.get("tables", {}).values():
        ids |= set(nodes)
    return ids | set(patch.get("tokens", {}))


def gate1_messages(graph: SpecGraph, patch: Patch, config: SpecConfig, **ctx_kw: Any) -> list[str]:
    touched = touched_ids(patch)
    if not touched:
        return []
    copy = graph.model_copy(deep=True)
    apply_patch(copy, patch)
    ctx = make_context(copy, config=config, **ctx_kw)
    findings = run_all(ctx, gate=1) + run_all(ctx, gate=3, only=TOKEN_AND_ASSET_CHECKS)
    out: list[str] = []
    for f in findings:
        if f.severity != "blocker":
            continue
        base = f.where.split(".", 1)[0]
        if f.where in touched or base in touched:
            out.append(f"{f.code} {f.where}: {f.why}; {f.fix}")
    return sorted(set(out))
