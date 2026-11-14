"""Mechanical repairs of a PROD-JOURNEYS answer (R-code: logged, never silent; a re-ask is for what code cannot mend).

* the draft flow's own alternates come first and stay as written; the model's additions follow;
* a flow the answer leaves out, or writes without stages, is the draft's (completed) flow;
* stages are numbered 1, 2, 3 in the order given; unknown screens and action keys a stage's screens do not have are removed;
  if the first or the last stage is then left without a screen, the flow is the draft's;
* unknown roles, apps, moments and an unknown `emotion_ref` are removed; a primary journey without moments or an unhappy path
  gets the draft's;
* a requested variant the answer lacks is made (copied from the flow it varies); a variant that varies nothing is dropped;
* a clause with a layout word or a number no source states is cut (a blank goal takes the draft's)."""

from __future__ import annotations

from mde_spec_agent.lanes.product.kit.common import scrub, scrub_row, source_numbers
from mde_spec_core.callkit.ladder import FixKind

from .baseline import complete
from .schema import FlowRec, JourneysIn, JourneysOut, StageRec


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


def repair(inp: JourneysIn, out: JourneysOut) -> tuple[JourneysOut, list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    o = out.model_copy(deep=True)
    draft = complete(inp)
    draft_flow = {f.id: f for f in draft.flows}
    draft_stages = {
        f.id: sorted((s for s in draft.stages if s.flow == f.id), key=lambda s: s.n)
        for f in draft.flows
    }
    keys = {s.id: set(s.action_keys) for s in inp.screens}
    roles, apps = set(inp.role_ids), set(inp.app_ids)
    src = "\n".join([*inp.slice, *inp.facts, *inp.research, inp.baseline.model_dump_json()])
    nums = source_numbers(src)
    base_unhappy = {f.id: f.unhappy for f in inp.baseline.flows}

    # one record per flow id (the first wins); variants that vary nothing are dropped
    seen: set[str] = set()
    flows: list[FlowRec] = []
    for f in o.flows:
        if f.id in seen:
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    f.id,
                    "the flow was listed twice: the second dropped",
                )
            )
            continue
        seen.add(f.id)
        if f.id.startswith("NEW-") and (f.of not in inp.flow_ids or f.variant == "main"):
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    f.id,
                    "a variant that varies no draft flow: dropped",
                )
            )
            continue
        flows.append(f)
    o.flows = flows
    o.stages = [s for s in o.stages if s.flow in {f.id for f in flows}]
    # a draft flow the answer left out, and the variants it was asked for
    for fid in inp.flow_ids:
        if fid not in {f.id for f in o.flows}:
            o.flows.append(draft_flow[fid].model_copy(deep=True))
            o.stages += [s.model_copy() for s in draft_stages[fid]]
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    fid,
                    "the flow was left out: the draft flow is used",
                )
            )
    have = {(f.variant, f.of) for f in o.flows if f.id.startswith("NEW-")}
    for f in draft.flows:
        if f.id.startswith("NEW-") and (f.variant, f.of) not in have and f.of in inp.flow_ids:
            o.flows.append(f.model_copy(deep=True))
            o.stages += [s.model_copy() for s in draft_stages[f.id]]
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    f.id,
                    f"the {f.variant} variant was asked for and missing: copied from {f.of}",
                )
            )

    for f in o.flows:
        d = draft_flow.get(f.id) or draft_flow.get(f.of)
        stages = sorted((s for s in o.stages if s.flow == f.id), key=lambda s: s.n)
        if not stages and d is not None:
            src_id = f.id if f.id in draft_stages else f.of
            stages = [s.model_copy(update={"flow": f.id}) for s in draft_stages.get(src_id, [])]
            o.stages += stages
            rows.append(
                _row(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    f.id,
                    "the flow had no stages: the draft's stages are used",
                )
            )
        # roles, apps
        before_ra = (list(f.role_ids), list(f.app_ids))
        f.role_ids = [r for r in f.role_ids if r in roles] or list(d.role_ids if d else [])
        f.app_ids = [a for a in f.app_ids if a in apps] or list(d.app_ids if d else [])
        if before_ra != (f.role_ids, f.app_ids):
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    f.id,
                    "unknown roles or apps were removed",
                    str(before_ra),
                )
            )
        # stages: numbering, screens, action keys, texts, emotion_ref
        if [s.n for s in stages] != list(range(1, len(stages) + 1)):
            rows.append(
                _row(
                    FixKind.EXACT_SYNONYM,
                    f.id,
                    "stages were numbered with gaps or twice: renumbered 1, 2, 3",
                    ",".join(str(s.n) for s in stages),
                )
            )
        for i, s in enumerate(stages, 1):
            s.n = i
            s.screen_ids = [x for x in s.screen_ids if x in keys]
            have_keys = {k for x in s.screen_ids for k in keys[x]}
            bad = [k for k in s.action_keys if k not in have_keys]
            if bad:
                s.action_keys = [k for k in s.action_keys if k in have_keys]
                rows.append(
                    _row(
                        FixKind.DROP_UNKNOWN_OPTIONAL,
                        f.id,
                        f"stage {i}: action keys its screens do not have were removed",
                        ",".join(bad),
                    )
                )
            if s.emotion_ref and s.emotion_ref not in inp.ref_ids:
                rows.append(
                    _row(
                        FixKind.DROP_UNKNOWN_OPTIONAL,
                        f.id,
                        f"stage {i}: emotion_ref is not a fact of this call: cleared",
                        s.emotion_ref,
                    )
                )
                s.emotion_ref = ""
            for field in ("goal", "pain"):
                before = getattr(s, field)
                after, cut = scrub(before, src, nums)
                if cut:
                    if field == "goal" and not after.strip():
                        dg = draft_stages.get(f.id, [])
                        after = dg[i - 1].goal if i - 1 < len(dg) else "continue with the next step"
                    setattr(s, field, after)
                    rows.append(scrub_row(f.id, f"stage {i} {field}", before, after, cut))
            if not s.goal.strip():
                s.goal = "continue with the next step"
        if stages and (not stages[0].screen_ids or not stages[-1].screen_ids):
            dstages = draft_stages.get(f.id if f.id in draft_stages else f.of, [])
            if dstages:
                o.stages = [x for x in o.stages if x.flow != f.id] + [
                    x.model_copy(update={"flow": f.id}) for x in dstages
                ]
                stages = list(dstages)
                rows.append(
                    _row(
                        FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                        f.id,
                        "the first or last stage had no screen: the draft's stages are used",
                    )
                )
        nums_n = {s.n for s in stages}
        kept_moments = sorted({m for m in f.moments if m in nums_n})
        if kept_moments != sorted(set(f.moments)):
            rows.append(
                _row(
                    FixKind.DROP_UNKNOWN_OPTIONAL,
                    f.id,
                    "moments that are no stage number were removed",
                    str(f.moments),
                )
            )
        f.moments = kept_moments
        if f.primary and not f.moments and d is not None:
            f.moments = [m for m in d.moments if m in nums_n]
        # the flow's own alternates first, the model's additions after
        own = base_unhappy.get(f.id, [])
        extra = []
        for u in f.unhappy:
            if u in own:
                continue
            after, cut = scrub(u, src, nums)
            if cut:
                rows.append(scrub_row(f.id, "unhappy path", u, after, cut))
            if after.strip():
                extra.append(after)
        merged = list(dict.fromkeys([*own, *extra]))
        if merged != f.unhappy:
            if [u for u in own if u not in f.unhappy[: len(own)]]:
                rows.append(
                    _row(
                        FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                        f.id,
                        "the flow's own alternates were dropped or reordered: restored first",
                    )
                )
            f.unhappy = merged
        if f.primary and not f.unhappy and d is not None:
            f.unhappy = list(d.unhappy)
    o.stages = sorted(o.stages, key=lambda s: (s.flow, s.n))
    return o, rows


__all__ = ["StageRec", "repair"]
