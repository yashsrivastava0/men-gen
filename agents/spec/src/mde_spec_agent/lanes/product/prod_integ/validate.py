"""Local checks of a PROD-INTEG answer: one record per need of the plan, provider-side flows carry an interrupted-flow
rule and a pending timeout (V-35), provider names come only from the closed list (V-45), no invented number, no form word,
provenance real. A null or a conflict stays empty."""

from __future__ import annotations

from collections import Counter

from mde_spec_agent.lanes.product.kit.common import (
    Errors,
    invented_numbers,
    new_form_words,
    source_numbers,
)

from .repair import repair
from .schema import IntegIn, IntegOut


def validate(inp: IntegIn, out: IntegOut) -> list[str]:
    out, _ = repair(
        inp, out
    )  # what code mends mechanically is not an error (the patch makes the same repair)
    e = Errors()
    source = "\n".join([*inp.slice, *inp.facts, *inp.research, inp.baseline.model_dump_json()])
    nums = source_numbers(source)
    ids = Counter(r.id for r in out.integrations)
    closed: set[str] = set()
    for line in inp.provider_names:  # "id|name|need": the id and the name are both citable
        closed |= {p.casefold() for p in line.split("|")[:2] if p and p != "-"}
    for r in out.integrations:
        where = f"integration {r.id}"
        if r.id not in inp.integration_ids:
            e.add(f"{where}: not a need of the plan; use one of {inp.integration_ids}")
        if ids[r.id] > 1:
            e.add(f"{where}: listed twice")
        if r.provider_side_flow:
            if not r.interruption.strip():
                e.add(
                    f"{where}: a provider-side flow needs an interrupted-flow rule (what happens when the app is closed or the connection drops)"
                )
            if not r.pending_timeout.strip():
                e.add(
                    f"{where}: a provider-side flow needs a pending timeout (what is shown while the result is pending and when waiting ends)"
                )
            if not r.flow.strip():
                e.add(f"{where}: describe the provider-neutral flow")
        for pid in r.provider_ids:
            if pid.casefold() not in closed:
                e.add(
                    f"{where}: provider {pid!r} is not in the closed list {sorted(closed)[:12]}; a provider name comes only from the research dossiers or the Brief"
                )
        text = " ".join([r.flow, r.interruption, r.pending_timeout, r.mock_behaviour])
        for bad in invented_numbers(text, nums):
            e.add(
                f"{where}: the text states {bad}, which no source states; the limit stays on the confirm list (never invent a number)"
            )
        for w in new_form_words(text, source):
            e.add(f"{where}: uses the form word {w!r}; say what happens, not how it looks")
    for need in inp.integration_ids:
        if need not in ids:
            e.add(f"integration {need}: no record; write one record per need")
    for ev in out.events:
        if ev.integration not in inp.integration_ids:
            e.add(f"event {ev.name}: integration {ev.integration} is not a need of the plan")
        if not ev.name.strip():
            e.add(f"event of {ev.integration}: name is empty")
    return e.items
