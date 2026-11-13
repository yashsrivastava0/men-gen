"""The repair protocol (doc 19 3.3): which defects become patches, and which nodes are `degraded`.

* Class A (a code fix): always a patch; Spec applies in at most 3 passes that must converge (`RoundLedger.next_class_a_pass`).
* Class B (a lane call) and C (a decision): a patch only while the phase's LLM round is open (at most
  `config.verify.max_llm_rounds` across the Freeze and the final pass) and only if the node has not yet used its ONE
  Verify-driven repair. A node already re-asked by the ladder may still take it.
* A blocker that cannot be patched any more leaves the node `degraded`: Spec marks it and lists it at G2.
"""

from __future__ import annotations

from collections.abc import Iterable

from mde_spec_core.graph import SpecGraph
from mde_spec_core.verify_port import Defect, Patch, RoundLedger
from mde_verify_agent.checks import patch_for


def plan_patches(
    defects: Iterable[Defect], ledger: RoundLedger, phase: str, graph: SpecGraph
) -> tuple[list[Patch], list[str], list[str]]:
    """Returns (patches, degraded node ids, notes). Mutates only the ledger (a node that takes its repair is charged)."""
    round_open = phase in ledger.phases
    patches: list[Patch] = []
    degraded: list[str] = []
    notes: list[str] = []
    charged: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    refused = 0
    for d in defects:
        if d.severity == "info":
            continue
        key = (d.node, d.field, d.change)
        if key in seen:
            continue
        seen.add(key)
        if d.cls == "A":
            patches.append(patch_for(d))
            continue
        if round_open and (d.node in charged or ledger.node_can_repair(d.node)):
            if d.node not in charged:
                ledger.charge_node(d.node)
                charged.add(d.node)
            patches.append(patch_for(d))
            continue
        refused += 1
        if d.severity == "blocker" and graph.find(d.node) is not None and d.node not in degraded:
            degraded.append(d.node)
    if refused:
        why = "no LLM round left" if not round_open else "the node already used its Verify repair"
        notes.append(
            f"{refused} class B/C finding(s) not patched ({why}); blockers among them are listed as degraded"
        )
    return patches, degraded, notes
