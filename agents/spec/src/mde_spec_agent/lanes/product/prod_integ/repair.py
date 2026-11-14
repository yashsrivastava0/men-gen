"""Mechanical repairs of a PROD-INTEG answer (R-code, logged): a need the answer leaves out is the draft's; a record that is not
a need of the plan is dropped; a provider-side flow without an interrupted-flow rule, a pending timeout or a flow takes the
library text (V-35); a provider name outside the closed list is removed; a clause with a layout word or a number no source
states is cut (a blank text takes the library's)."""

from __future__ import annotations

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import scrub, scrub_row, source_numbers
from mde_spec_core.callkit.ladder import FixKind

from .baseline import complete
from .schema import EventRec, IntegIn, IntegOut


def _row(
    kind: FixKind, node_id: str, what: str, before: str = "", after: str = ""
) -> dict[str, str]:
    return {
        "kind": kind.value,
        "node_id": node_id,
        "what": what,
        "before": before[:160],
        "after": after[:160],
    }


def repair(inp: IntegIn, out: IntegOut) -> tuple[IntegOut, list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    o = out.model_copy(deep=True)
    draft = complete(inp)
    d_rec = {r.id: r for r in draft.integrations}
    lib = vocab.hints()["integration_defaults"]
    src = "\n".join([*inp.slice, *inp.facts, *inp.research, inp.baseline.model_dump_json()])
    nums = source_numbers(src)
    closed: set[str] = set()
    for line in inp.provider_names:
        closed |= {p.casefold() for p in line.split("|")[:2] if p and p != "-"}

    kept, seen = [], set()
    for r in o.integrations:
        if r.id not in inp.integration_ids or r.id in seen:
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    r.id,
                    "not a need of the plan, or listed twice: dropped",
                )
            )
            continue
        seen.add(r.id)
        kept.append(r)
    o.integrations = kept
    for need in inp.integration_ids:
        if need not in seen and need in d_rec:
            o.integrations.append(d_rec[need].model_copy(deep=True))
            o.events += [e.model_copy() for e in draft.events if e.integration == need]
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    need,
                    "no record for this need: the draft's is used",
                )
            )
    for r in o.integrations:
        for field in ("flow", "interruption", "pending_timeout", "mock_behaviour"):
            before = getattr(r, field)
            after, cut = scrub(before, src, nums)
            if cut:
                setattr(r, field, after)
                rows.append(scrub_row(r.id, field, before, after, cut))
        if r.provider_side_flow:
            for field in ("flow", "interruption", "pending_timeout"):
                if not getattr(r, field).strip():
                    setattr(r, field, lib[field])
                    rows.append(
                        _row(
                            FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                            r.id,
                            f"a provider-side flow needs its {field}: the library text",
                        )
                    )
        if not r.mock_behaviour.strip():
            r.mock_behaviour = lib["mock_behaviour"]
        bad = [p for p in r.provider_ids if p.casefold() not in closed]
        if bad:
            r.provider_ids = [p for p in r.provider_ids if p.casefold() in closed]
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    r.id,
                    "a provider name outside the closed list was removed",
                    ", ".join(bad),
                )
            )
    ids = {r.id for r in o.integrations}
    ev = [e for e in o.events if e.integration in ids and e.name.strip()]
    if len(ev) != len(o.events):
        rows.append(
            _row(
                FixKind.DROP_UNKNOWN_OPTIONAL,
                "events",
                "events of no need, or with no name, were dropped",
            )
        )
    for r in o.integrations:
        if r.provider_side_flow and not any(e.integration == r.id for e in ev):
            ev += [EventRec(integration=r.id, name=n) for n in lib["events"]]
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    r.id,
                    "a provider-side flow has events: the library's",
                )
            )
    o.events = ev
    return o, rows
