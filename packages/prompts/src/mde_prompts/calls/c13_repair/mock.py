"""Offline handler for C13: repair the given findings with ``fix_reference`` / ``remove_item``.

Deterministic and schema-valid. A finding that mentions an id the outline does not have is a dangling
reference: point it at the closest existing id of the same kind (``fix_reference``). A finding that
points at an extra item (not from the baseline) removes that item (``remove_item``).
"""

from __future__ import annotations

import difflib
import re

from mde_contracts.calls import C13Input, C13Output, KeyValue, Proposal, SpecProposals

MAX_PROPOSALS = 10
_ID = re.compile(
    r"\b(?:SCR-\d{3}|FLW-\d{3}|ENT-[A-Za-z][A-Za-z0-9]*|API-[A-Za-z][A-Za-z0-9]*"
    r"|ROLE-[a-z][a-z0-9_]*|INT-[a-z][a-z0-9_]*)\b"
)


def _known(inp: C13Input) -> dict[str, str]:
    """id -> source for everything the outline lists (``default`` when it has no source)."""
    known: dict[str, str] = {}
    for s in inp.outline.screens:
        known[s.id] = s.source
    for f in inp.outline.flows:
        known[f.id] = "default"
    for e in inp.outline.entities:
        known[e.id] = "default"
    for o in inp.outline.operations:
        known[o.id] = "default"
    for r in inp.outline.roles:
        known[r.id] = "default"
    for i in inp.outline.integrations:
        known[i.id] = "default"
    return known


def _closest(old: str, known: dict[str, str]) -> str | None:
    prefix = old.split("-", 1)[0]
    pool = sorted(i for i in known if i.startswith(prefix + "-"))
    if not pool:
        return None
    found = difflib.get_close_matches(old, pool, n=1, cutoff=0.0)
    return found[0] if found else pool[0]


def handle(input: C13Input) -> C13Output:
    known = _known(input)
    proposals: list[Proposal] = []
    seen: set[tuple[str, str | None, str]] = set()
    for finding in input.findings:
        mentioned = list(dict.fromkeys(_ID.findall(finding.message)))
        missing = [i for i in mentioned if i not in known]
        holders = [i for i in [*finding.target_ids, *mentioned] if i in known]
        if missing and holders:
            holder = holders[0]
            for old in missing:
                new = _closest(old, known)
                key = ("fix", holder, old)
                if new is None or key in seen:
                    continue
                seen.add(key)
                proposals.append(
                    Proposal(
                        kind="fix_reference",
                        target=holder,
                        source="repair",
                        rationale=f"{old} does not exist; {new} is the closest {old.split('-')[0]} item.",
                        pairs=[KeyValue(key=old, value=new)],
                    )
                )
                break
            continue
        for target in finding.target_ids:
            key = ("remove", target, "")
            if known.get(target) not in (None, "default") and key not in seen:
                seen.add(key)
                proposals.append(
                    Proposal(
                        kind="remove_item",
                        target=target,
                        source="repair",
                        rationale="The item was added later and causes the problem; removing it is the smallest fix.",
                    )
                )
                break
        if len(proposals) >= MAX_PROPOSALS:
            break
    return SpecProposals(
        proposals=proposals[:MAX_PROPOSALS], notes="mock: reference fixes and removals"
    )
