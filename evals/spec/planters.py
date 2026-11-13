"""The eight MEANING defects of doc 18 section 3.4, planted for real (the placeholders in `mde_spec_core.planted` are
left alone). Each planter changes the graph the way the doc describes; none is owned by a validator, so a code
self-check can only catch one by accident. They plant into a copy of a clean pack that `prepare_baseline` first makes
carry what the defects need (a Locked number, a payment-failure path); `brief_for` is the approved Brief those facts
come from, so a critic that is given the Brief can see the departure.

A planter raises `NotApplicable` when the pack has nothing it could change (the report says so; it is never counted as
caught or missed).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mde_spec_core.graph import (
    AccessRow,
    Prov,
    Rule,
    SpecGraph,
    Step,
    Story,
)
from mde_spec_core.planted import Defect, Plant
from mde_spec_core.render import bound_fields
from mde_spec_core.verify_port import BriefView, LockedFact

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
PAYMENT_WORDS = re.compile(r"pay|declin|charge|refund|card", re.I)

# What Research is taken to have found, for the "Default contradicts an official Research fact" defect. It is given to
# the clean run and the planted run alike (it is part of the truth the pack is judged against).
OFFICIAL_ITEM = {
    "id": "RI-OFFICIAL-1",
    "evidence": "official",
    "text": "Payment providers charge a fee on every card payment.",
}


class NotApplicable(RuntimeError):
    """The pack has nothing this planter could change."""


@dataclass
class Note:
    touched: list[str]
    detail: str = ""


def next_id(prefix: str, existing: Iterable[str]) -> str:
    """A new id in the same style as the existing ones (`RUL-007`, `RUL-S007`), never a collision."""
    ids = list(existing)
    nums = [int(m.group(1)) for i in ids if (m := re.search(r"(\d+)$", i))]
    style = "S" if any(re.search(r"-S\d+$", i) for i in ids) else ""
    n = (max(nums) if nums else 0) + 1
    while True:
        cand = f"{prefix}-{style}{n:03d}"
        if cand not in ids:
            return cand
        n += 1


# ---------------------------------------------------------------------------------------------------- the clean side


def prepare_baseline(graph: SpecGraph) -> SpecGraph:
    """A copy of a good graph that carries what the meaning defects need to change: its first numeric rule is Locked
    with user evidence (the Brief's number), and one flow names a payment-failure path. Nothing else changes."""
    g = graph.model_copy(deep=True)
    for r in sorted(g.rules.values(), key=lambda x: x.id):
        if r.value and _NUM.fullmatch(str(r.value).strip()):
            r.tier = "locked"
            if not any(p.kind in ("user", "compass", "policy") for p in r.provenance):
                r.provenance = [*r.provenance, Prov(kind="user", ref="brief")]
            break
    flows = sorted(g.flows.values(), key=lambda f: (not f.primary, f.id))
    if flows and not any(
        PAYMENT_WORDS.search(p) for f in g.flows.values() for p in f.unhappy_paths
    ):
        flows[0].unhappy_paths = [
            *flows[0].unhappy_paths,
            "If the payment fails, the user can retry or choose another way to pay, and nothing is charged twice.",
        ]
    return g


def brief_for(graph: SpecGraph) -> BriefView:
    """The approved Brief as a critic would be given it, derived from the CLEAN graph: Locked rule numbers as Locked
    facts, what the user asked for as quotes, who may do what to which entity."""
    quotes: list[str] = []
    facts: list[LockedFact] = []
    for r in sorted(graph.rules.values(), key=lambda x: x.id):
        if r.tier == "locked" and r.value:
            facts.append(
                LockedFact(
                    id=f"FACT-{r.id}",
                    node=r.id,
                    field="value",
                    value=str(r.value),
                    text=f"{r.statement} ({r.value} {r.unit or ''})".strip(),
                )
            )
        if r.statement:
            quotes.append(
                f"{r.subject}: {r.statement}"
                + (f" ({r.value} {r.unit or ''})".rstrip() if r.value else "")
            )
    for f in sorted(graph.features.values(), key=lambda x: x.id):
        if f.status == "in" and f.user_evidenced:
            quotes.append(f"The user asked for: {f.name}. {f.intent}".strip())
    if not any(f.user_evidenced for f in graph.features.values()):
        for e in sorted(graph.entities.values(), key=lambda x: x.id):
            quotes.append(f"The app handles {e.name}.")
    roles = {r.id: r.name for r in graph.roles.values()}
    for e in sorted(graph.entities.values(), key=lambda x: x.id):
        for row in sorted(e.access, key=lambda x: x.role_id):
            quotes.append(
                f"{roles.get(row.role_id, row.role_id)} may {'/'.join(row.actions)} {e.name}."
            )
    for fl in sorted(graph.flows.values(), key=lambda x: x.id):
        for path in fl.unhappy_paths:
            if PAYMENT_WORDS.search(path):
                quotes.append(f"In {fl.name}: {path}")
    return BriefView(summary=_summary(graph), quotes=quotes[:60], locked_facts=facts)


def _summary(g: SpecGraph) -> str:
    return (
        f"A {g.meta.profile} app program with {len(g.apps)} app(s), {len(g.roles)} role(s), "
        f"{len(g.entities)} thing(s) it manages and {len(g.screens)} screen(s)."
    )


def research_notes(items: Iterable[dict[str, Any]]) -> list[str]:
    return [f"{i['id']} [{i['evidence']}] {i.get('text', '')}".strip() for i in items]


# ---------------------------------------------------------------------------------------------------- the planters


def _m_changed_locked_number(p: Plant) -> Note:
    rules = [
        r
        for r in sorted(p.graph.rules.values(), key=lambda x: x.id)
        if r.value and _NUM.fullmatch(str(r.value).strip())
    ]
    if not rules:
        raise NotApplicable("no rule carries a number")
    r = rules[0]
    old = str(r.value).strip()
    num = float(old)
    new = str(int(num) + 1) if num == int(num) else str(round(num + 1, 4))
    r.value = new
    r.statement = re.sub(rf"(?<![\d.]){re.escape(old)}(?![\d.])", new, r.statement)
    return Note([r.id], f"{old} became {new}")


def _m_contradictory_rules(p: Plant) -> Note:
    rules = sorted(p.graph.rules.values(), key=lambda x: (not x.value, x.id))
    if not rules:
        raise NotApplicable("the pack has no rules")
    r = rules[0]
    rid = next_id("RUL", p.graph.rules)
    p.graph.add(
        "rules",
        Rule(
            id=rid,
            subject=r.subject,
            statement=f"the opposite holds: no {r.subject} applies in any case",
            value="0" if r.value else None,
            unit=r.unit if r.value else None,
            module_id=r.module_id,
            provenance=[Prov(kind="model", ref="proof-plant")],
        ),
    )
    return Note([r.id, rid], f"{rid} contradicts {r.id}")


def remove_screens(g: SpecGraph, gone: set[str]) -> None:
    """Delete screens and every reference to them, so the structure stays valid and only the meaning is lost."""
    for sid in gone:
        g.screens.pop(sid, None)
    for m in g.modules.values():
        m.screen_ids = [s for s in m.screen_ids if s not in gone]
    for s in g.screens.values():
        s.leads_to = [t for t in s.leads_to if t not in gone]
        s.reached_from = [t for t in s.reached_from if t.split(".", 1)[0] not in gone]
        for a in s.actions:
            a.effect = [
                Step(verb="stay") if st.verb == "go" and st.target in gone else st
                for st in a.effect
            ]
        if not s.actions:
            continue
    for f in g.flows.values():
        for st in f.stages:
            st.screen_ids = [x for x in st.screen_ids if x not in gone]
    for st in g.stories.values():
        st.screen_ids = [x for x in st.screen_ids if x not in gone]
    for r in g.roles.values():
        if r.home_screen in gone:
            r.home_screen = None


def _m_feature_missing(p: Plant) -> Note:
    g = p.graph
    feats = [
        f
        for f in sorted(g.features.values(), key=lambda x: x.id)
        if f.status == "in" and f.user_evidenced
    ]
    for f in feats:
        mine = {s for st in g.stories.values() if f.id in st.feature_ids for s in st.screen_ids}
        other = {
            s for st in g.stories.values() if f.id not in st.feature_ids for s in st.screen_ids
        }
        gone = {s for s in mine - other if s in g.screens and not g.screens[s].system}
        if not gone:
            continue
        for sid in [
            k
            for k, st in g.stories.items()
            if f.id in st.feature_ids and set(st.screen_ids) <= gone
        ]:
            del g.stories[sid]
        del g.features[f.id]
        remove_screens(g, gone)
        return Note([f.id, *sorted(gone)], f"feature {f.id} and its only screen(s) are gone")
    # no user-stated feature with screens of its own: the thing the app handles that only one screen shows
    by_ent: dict[str, set[str]] = {}
    for sid, s in g.screens.items():
        if s.system or s.status != "in":
            continue
        for e in bound_fields(s):
            if e != "<ops>":
                by_ent.setdefault(e, set()).add(sid)
    cands = sorted((len(v), k) for k, v in by_ent.items() if len(v) == 1)
    if cands:
        ent = cands[0][1]
        gone = by_ent[ent]
        remove_screens(g, gone)
        return Note([ent, *sorted(gone)], f"the only screen that shows {ent} is gone")
    live = [
        sid
        for sid, s in sorted(g.screens.items())
        if not s.system
        and s.status == "in"
        and s.screen_type not in ("auth", "legal", "permission")
    ]
    if not live:
        raise NotApplicable("no screen that could stand for a user-stated feature")
    gone = {live[-1]}
    remove_screens(g, gone)
    return Note(
        sorted(gone), f"{live[-1]} (what the user asked for, with no other screen for it) is gone"
    )


def _m_role_sees_forbidden(p: Plant) -> Note:
    for e in sorted(p.graph.entities.values(), key=lambda x: x.id):
        for row in sorted(e.access, key=lambda x: x.role_id):
            if set(row.actions) <= {"view", "none"}:
                was = "/".join(row.actions)
                row.actions = ["manage"]
                return Note(
                    [e.id, row.role_id], f"{row.role_id} may {was} {e.id} in the Brief, now manage"
                )
    for e in sorted(p.graph.entities.values(), key=lambda x: x.id):
        listed = {r.role_id for r in e.access}
        spare = [r for r in sorted(p.graph.roles) if r not in listed]
        if e.access and spare:
            e.access.append(AccessRow(role_id=spare[0], actions=["manage"]))
            return Note([e.id, spare[0]], f"{spare[0]} was not listed for {e.id}, now manage")
    raise NotApplicable("no role and entity pair to widen")


def _m_journey_without_payment_failure(p: Plant) -> Note:
    touched: list[str] = []
    for f in sorted(p.graph.flows.values(), key=lambda x: x.id):
        keep = [u for u in f.unhappy_paths if not PAYMENT_WORDS.search(u)]
        if len(keep) != len(f.unhappy_paths):
            f.unhappy_paths = keep
            touched.append(f.id)
    if not touched:
        raise NotApplicable("no journey names a payment-failure path")
    return Note(touched, "payment-failure paths removed")


def _m_story_contradicts_rule(p: Plant) -> Note:
    g = p.graph
    rules = sorted(g.rules.values(), key=lambda x: (not x.value, x.id))
    if not rules:
        raise NotApplicable("the pack has no rules")
    r = rules[0]
    want = f"never to be subject to any {r.subject}"
    so = f"{r.subject} must not apply to me at all"
    stories = sorted(g.stories.values(), key=lambda x: x.id)
    if stories:
        s = stories[0]
        s.want, s.so_that = want, so
        return Note([s.id, r.id], f"story {s.id} now contradicts {r.id}")
    if not g.roles:
        raise NotApplicable("no role to write a story for")
    sid = next_id("US", g.stories)
    g.add(
        "stories",
        Story(
            id=sid,
            role_id=sorted(g.roles)[0],
            want=want,
            so_that=so,
            provenance=[Prov(kind="model", ref="proof-plant")],
        ),
    )
    return Note([sid, r.id], f"new story {sid} contradicts {r.id}")


def _m_default_contradicts_official(p: Plant) -> Note:
    cands = [
        s
        for s in sorted(p.graph.screens.values(), key=lambda x: x.id)
        if not s.system and s.tier != "locked"
    ]
    if not cands:
        raise NotApplicable("no Default screen to write the claim on")
    s = cands[-1]
    s.purpose = "Paying is free of any fee: no fee is ever charged for a payment"
    s.provenance = [*s.provenance, Prov(kind="research", ref=OFFICIAL_ITEM["id"])]
    s.claims_used = [*s.claims_used, OFFICIAL_ITEM["id"]]
    s.tier = "default"
    p.ctx_kwargs["research_items"] = [OFFICIAL_ITEM]
    return Note([s.id], f"{s.id} says the opposite of {OFFICIAL_ITEM['id']}")


def _m_assumed_shown_as_locked(p: Plant) -> Note:
    cands = [
        s
        for s in sorted(p.graph.screens.values(), key=lambda x: x.id)
        if not s.system and s.tier != "locked"
    ]
    if not cands:
        raise NotApplicable("no screen to mark")
    s = cands[0]
    s.assumed = True
    s.tier = "locked"
    s.provenance = [Prov(kind="assumed", ref="the proof assumed this")]
    return Note([s.id], f"{s.id} is an assumption shown as Locked")


# Same ids and order as `mde_spec_core.planted.MEANING`, so reports line up with the lead's catalogue.
MEANING_PLANTERS = {
    "changed_locked_number": _m_changed_locked_number,
    "two_contradictory_rules": _m_contradictory_rules,
    "user_feature_missing_from_inventory": _m_feature_missing,
    "role_sees_what_brief_forbids": _m_role_sees_forbidden,
    "journey_without_payment_failure_path": _m_journey_without_payment_failure,
    "story_contradicts_a_rule": _m_story_contradicts_rule,
    "default_contradicts_official_research": _m_default_contradicts_official,
    "assumed_item_shown_as_locked": _m_assumed_shown_as_locked,
}
# Kinds whose change appears in the rendered pack files (the rest change the graph and what a critic reads of it).
PACK_VISIBLE = {
    "changed_locked_number": True,
    "two_contradictory_rules": True,
    "user_feature_missing_from_inventory": True,
    "role_sees_what_brief_forbids": True,
    "journey_without_payment_failure_path": False,
    "story_contradicts_a_rule": False,
    "default_contradicts_official_research": True,
    "assumed_item_shown_as_locked": True,
}


def _wrap(kind: str):
    def apply(p: Plant) -> None:
        p.ctx_kwargs["_note"] = MEANING_PLANTERS[kind](p)

    return apply


def real_meaning_defects() -> list[Defect]:
    """The eight meaning defects as `planted.Defect`s with real planters (`expected` is "V-17" only for the last one,
    exactly as the lead's catalogue says: code does catch an assumed item shown as Locked)."""
    return [
        Defect(
            kind_id,
            "meaning",
            "V-17" if kind_id == "assumed_item_shown_as_locked" else "",
            _wrap(kind_id),
        )
        for kind_id in MEANING_PLANTERS
    ]
