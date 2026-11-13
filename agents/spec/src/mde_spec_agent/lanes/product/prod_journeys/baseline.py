"""The draft flows and stages in the output schema (from the baseline graph) and the deterministic completion.

The completion fills only what code can read: a primary journey without moments gets its stake stage (a stage whose
actions name a payment or a commitment) and its last stage, a primary journey without an unhappy path gets the generic
connection one, every stage gets an emotion by position (assumed: no `emotion_ref`), and deep apps get the variants the
call must write, copied from the flow they vary."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import clip
from mde_spec_core.graph import Prov, SpecGraph

from .schema import FlowRec, JourneysIn, JourneysOut, StageRec


def _prov(prov: Sequence[Prov], fallback: str) -> list[str]:
    return [f"{p.kind}:{p.ref}" for p in prov if p.ref] or [fallback]


def baseline_journeys_out(graph: SpecGraph, flow_ids: Collection[str]) -> JourneysOut:
    out = JourneysOut()
    for fid in sorted(flow_ids):
        f = graph.flows[fid]
        out.flows.append(
            FlowRec(
                id=f.id,
                of="",
                name=f.name,
                variant=f.variant,
                primary=f.primary,
                role_ids=list(f.role_ids),
                app_ids=list(f.app_ids),
                mock_profile=f.mock_profile,  # type: ignore[arg-type]
                moments=list(f.moments_that_matter),
                unhappy=list(f.unhappy_paths),
                prov=_prov(f.provenance, f"assumed:journey {f.id}"),
            )
        )
        out.stages += [
            StageRec(
                flow=f.id,
                n=s.n,
                goal=s.goal,
                screen_ids=list(s.screen_ids),
                action_keys=list(s.action_keys),
                emotion=s.emotion,
                emotion_ref="",
                pain=s.pain,
            )
            for s in f.stages
        ]
    return out


def _emotion(position: int, total: int, stake: bool) -> str:
    h = vocab.hints()
    if stake:
        return h["emotion_stake"]
    if position == 1:
        return h["emotion_first"]
    if position == total and total > 1:
        return h["emotion_last"]
    return h["emotion_middle"]


def _stake(stage: StageRec) -> bool:
    words = set(vocab.hints()["stake_words"])
    return any(w in words for k in stage.action_keys for w in k.split("_"))


def complete(inp: JourneysIn) -> JourneysOut:
    out = inp.baseline.model_copy(deep=True)
    for fl in out.flows:
        stages = sorted((s for s in out.stages if s.flow == fl.id), key=lambda s: s.n)
        carries = fl.primary or not inp.primary_only_emotion
        for s in stages:
            if carries and s.emotion == "calm" and not s.emotion_ref:
                s.emotion = _emotion(s.n, len(stages), _stake(s))  # type: ignore[assignment]
        if fl.primary and stages and not fl.moments:
            stake = [s.n for s in stages if _stake(s)]
            fl.moments = sorted({*stake[:1], stages[-1].n})
        if fl.primary and not fl.unhappy and stages:
            fl.unhappy = [
                vocab.hints()["unhappy_generic"].format(goal=clip(stages[0].goal.lower(), 80))
            ]
    base_flows = {f.id: f for f in out.flows}
    n = 0
    for row in inp.variants_wanted:
        variant, _, base_id = row.partition(":")
        base = base_flows.get(base_id)
        spec = vocab.hints()["variants"].get(variant)
        if base is None or spec is None:
            continue
        n += 1
        key = f"NEW-{variant}_{base_id.lower().replace('-', '_')}"
        out.flows.append(
            FlowRec(
                id=key,
                of=base_id,
                name=f"{base.name} ({spec['name_suffix']})",
                variant=variant,  # type: ignore[arg-type]
                primary=False,
                role_ids=list(base.role_ids),
                app_ids=list(base.app_ids),
                mock_profile=("empty" if variant == "first_run" else "typical"),  # type: ignore[arg-type]
                moments=[],
                unhappy=[],
                prov=[f"assumed:{variant} variant of {base_id}"],
            )
        )
        for s in (x for x in out.stages if x.flow == base_id):
            out.stages.append(
                StageRec(
                    flow=key,
                    n=s.n,
                    goal=spec["goal_prefix"] + s.goal[:1].lower() + s.goal[1:],
                    screen_ids=list(s.screen_ids),
                    action_keys=list(s.action_keys),
                    emotion=spec["emotion"],
                    emotion_ref="",
                    pain="",
                )
            )
    return out
