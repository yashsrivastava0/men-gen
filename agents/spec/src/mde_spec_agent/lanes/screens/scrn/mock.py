"""Mock handler of SCRN: DETERMINISTIC, derived from the input only. It reuses the baseline card of every slot and improves
it the way a careful writer would: regions bound to real fields get a freshness and expected counts, actions get the
errors of their operation and a precondition from its lifecycle, the card gets analytics events, an accessibility
statement, acceptance scenarios for the happy path and the operation's own failures, USUALLY lines from the research items
it was offered (their evidence class, never raised) and COULD entries that rest on a journey moment or the delight
library, each with a basis. It never writes layout, widgets or motion choices of its own."""

from __future__ import annotations

import re

from mde_spec_agent.derive.text import first_sentences, humanize, slug
from mde_spec_agent.lanes.screens.common import lane_rules
from mde_spec_agent.lanes.screens.scrn.schema import (
    AcceptOut,
    ActionOut,
    CardOut,
    CopyOut,
    CouldOut,
    RegionOut,
    ScrnIn,
    ScrnOut,
    SlotIn,
    UsuallyOut,
)
from mde_spec_core.libraries import form_word_findings

_IMPERATIVE = re.compile(r"\b(should|must|always|never|ensure|make sure)\b", re.I)
_VERB_BY_METHOD = {"POST": "created", "PUT": "updated", "PATCH": "updated", "DELETE": "deleted"}


def _clean(text: str, screen_type: str) -> str:
    return "" if form_word_findings(text, screen_type) else text


def _first_role(slot: SlotIn) -> str:
    if not slot.roles:
        return "person"
    return slot.roles[0].split(" ", 1)[-1].lower()


def _regions(slot: SlotIn) -> list[RegionOut]:
    ents = {e.id: e for e in slot.entities}
    out: list[RegionOut] = []
    for r in slot.regions:
        upd: dict[str, object] = {}
        if r.freshness is None and any(b.startswith("OP-") for b in r.bind):
            upd["freshness"] = "on_open"
        if r.shape == "collection" and r.typical is None and r.maximum is None:
            for b in r.bind:
                e = ents.get(b.split(".")[0])
                if e and (e.typical or e.maximum):
                    upd.update(typical=e.typical, maximum=e.maximum)
                    break
        out.append(r.model_copy(update=upd) if upd else r)
    return out


def _actions(slot: SlotIn) -> list[ActionOut]:
    ops = {o.id: o for o in slot.operations}
    ents = {e.id: e for e in slot.entities}
    out: list[ActionOut] = []
    for a in slot.actions:
        upd: dict[str, object] = {}
        calls = [s.split(" ", 1)[1] for s in a.effect if s.startswith("call ")]
        op = next((ops[c] for c in calls if c in ops), None)
        if op is not None and not a.unspecified:
            errors = list(dict.fromkeys([*a.errors, *op.errors]))
            upd["errors"] = errors
            if op.idempotent and "idempotent" not in a.flags:
                upd["flags"] = [*a.flags, "idempotent"]
            ent = ents.get(op.entity or "")
            if op.from_states and ent is not None:
                if a.precondition is None:
                    upd["precondition"] = f"the {ent.name.lower()} is " + " or ".join(
                        op.from_states
                    )
                if not a.guard and ent.status_field:
                    ref = f"{ent.id}.{ent.status_field}"
                    upd["guard"] = [
                        f"field_equals {ref} {op.from_states[0]}"
                        if len(op.from_states) == 1
                        else f"field_in {ref} " + "|".join(op.from_states)
                    ]
        out.append(a.model_copy(update=upd) if upd else a)
    return out


def _analytics(slot: SlotIn, actions: list[ActionOut]) -> list[str]:
    rules = lane_rules()
    ops = {o.id: o for o in slot.operations}
    ents = {e.id: e for e in slot.entities}
    out: list[str] = list(slot.card.analytics)
    for a in actions:
        for st in a.effect:
            if not st.startswith("call "):
                continue
            op = ops.get(st.split(" ", 1)[1])
            if op is None or op.method == "GET":
                continue
            verb = rules.analytics_verb_by_intent.get(a.intent) or _VERB_BY_METHOD.get(
                op.method, "updated"
            )
            ent = ents.get(op.entity or "")
            ev = f"{slug(ent.name if ent else slot.name)}_{verb}"
            if ev not in out:
                out.append(ev)
    return out[:4]


def _accessibility(slot: SlotIn, actions: list[ActionOut], regions: list[RegionOut]) -> str:
    rules = lane_rules().accessibility
    live = next((r for r in regions if r.live != "none" or r.shape == "sequence"), None)
    if live is not None:
        what = live.what.rstrip(".").lower()
        return _clean(rules["live"].format(what=what), slot.screen_type) or rules["plain"]
    mutating = next(
        (
            a
            for a in actions
            if any(s.startswith("call ") for s in a.effect) and a.importance == "primary"
        ),
        None,
    )
    if mutating is not None:
        return (
            _clean(rules["action"].format(what=mutating.what.rstrip(".").lower()), slot.screen_type)
            or rules["plain"]
        )
    return rules["plain"]


def _acceptance(slot: SlotIn, actions: list[ActionOut]) -> list[AcceptOut]:
    ops = {o.id: o for o in slot.operations}
    role = _first_role(slot)
    out: list[AcceptOut] = []
    have = {(a.given, a.when, a.then) for a in slot.library_acceptance}
    for a in actions:
        if len(out) >= slot.extra_acceptance_max:
            break
        calls = [s.split(" ", 1)[1] for s in a.effect if s.startswith("call ")]
        op = next((ops[c] for c in calls if c in ops and ops[c].method != "GET"), None)
        if op is None or a.importance != "primary":
            continue
        goes = [s.split(" ", 1)[1] for s in a.effect if s.startswith("go ")]
        then = "the change is saved and the person " + (
            f"reaches {goes[0]}" if goes else "stays here and sees the result"
        )
        row = (f"a signed-in {role} on {slot.name}", f"they {a.what.rstrip('.')}", then)
        if row not in have and all(not form_word_findings(t, slot.screen_type) for t in row):
            out.append(AcceptOut(screen=slot.id, given=row[0], when=row[1], then=row[2]))
            have.add(row)
        for code in op.errors:
            if len(out) >= slot.extra_acceptance_max:
                break
            if code in ("network", "forbidden", "not_found"):
                continue  # the library scenarios already cover these
            row = (
                f"a signed-in {role} on {slot.name} and the request fails with {code.replace('_', ' ')}",
                f"they {a.what.rstrip('.')}",
                "they stay here, see the reason in plain words and can try again or leave",
            )
            if row not in have and all(not form_word_findings(t, slot.screen_type) for t in row):
                out.append(AcceptOut(screen=slot.id, given=row[0], when=row[1], then=row[2]))
                have.add(row)
    return out[: slot.extra_acceptance_max]


def _usually(slot: SlotIn) -> list[UsuallyOut]:
    out: list[UsuallyOut] = []
    for n in slot.norms:
        note = first_sentences(n.label, 1)[:220].rstrip()
        if not note or _IMPERATIVE.search(note) or form_word_findings(note, slot.screen_type):
            continue
        out.append(UsuallyOut(screen=slot.id, note=note, evidence=n.evidence, item=n.id))  # type: ignore[arg-type]
        if len(out) >= slot.usually_max:
            break
    return out


def _could(slot: SlotIn) -> list[CouldOut]:
    rules = lane_rules()
    out: list[CouldOut] = []
    pattern = slot.delight[0] if slot.delight else None
    for m in slot.flows:
        if len(out) >= slot.could_max:
            break
        if not (m.matters or m.primary):
            continue
        kind = rules.could_kind_by_emotion.get(m.emotion, "clarity")
        when = rules.could_when.format(goal=m.goal.rstrip("."), flow=m.flow_name)
        effect = (
            pattern.effect
            if pattern and kind == "delight"
            else rules.could_effect_by_kind.get(kind, rules.could_effect_by_kind["clarity"])
        )
        if form_word_findings(when, slot.screen_type) or form_word_findings(
            effect, slot.screen_type
        ):
            continue
        motion = pattern.motion if pattern and kind == "delight" else None
        alt = pattern.reduced_motion_alternative if motion and pattern else None
        out.append(
            CouldOut(
                screen=slot.id,
                kind=kind,  # type: ignore[arg-type]
                when=when,
                effect=effect,
                basis=f"journey {m.flow} step {m.step}",
                basis_kind="journey_moment",
                assumed=m.assumed,
                motion=motion,
                reduced_motion_alt=alt
                if not form_word_findings(alt or "", slot.screen_type)
                else None,
                tier=(pattern.tier if pattern and motion else "A"),  # type: ignore[arg-type]
            )
        )
    if not out and pattern is not None and slot.could_max > 0:
        ok = not form_word_findings(pattern.moment, slot.screen_type) and not form_word_findings(
            pattern.effect, slot.screen_type
        )
        alt = pattern.reduced_motion_alternative
        if ok and not (pattern.motion and form_word_findings(alt, slot.screen_type)):
            out.append(
                CouldOut(
                    screen=slot.id,
                    kind="clarity" if pattern.motion in (None, "content_arrival") else "trust",
                    when=pattern.moment.rstrip("."),
                    effect=pattern.effect.rstrip("."),
                    basis=pattern.id,
                    basis_kind="library_pattern",
                    assumed=False,
                    motion=pattern.motion,
                    reduced_motion_alt=alt if pattern.motion else None,
                    tier=pattern.tier,  # type: ignore[arg-type]
                )
            )
    return out


def _card(slot: SlotIn, actions: list[ActionOut], regions: list[RegionOut]) -> CardOut:
    base = slot.card
    calls = any(s.startswith("call ") for a in actions for s in a.effect)
    profiles = {"typical"}
    if any(r.shape == "collection" for r in regions):
        profiles.add("empty")
    if calls:
        profiles.add("broken")
    if "offline" in slot.mock_profile_options and slot.screen_type in (
        "list",
        "detail",
        "dashboard",
    ):
        profiles.add("offline")
    states: list[str] = list(base.mock_states)
    by_entity: dict[str, list[str]] = {}
    for o in slot.mock_state_options:
        by_entity.setdefault(o.split(":")[0], []).append(o)
    if len(states) < 2:
        for opts in by_entity.values():
            states.extend(x for x in opts[:2] if x not in states)
    go = sorted({s.split(" ", 1)[1] for a in actions for s in a.effect if s.startswith("go ")})
    return base.model_copy(
        update={
            "purpose": first_sentences(base.purpose, 2),
            "leads_to": sorted(set(base.leads_to) | set(go)),
            "analytics": _analytics(slot, actions),
            "accessibility": _accessibility(slot, actions, regions),
            "mock_profiles": sorted(p for p in profiles if p in slot.mock_profile_options),
            "mock_states": sorted(dict.fromkeys(states))[:4],
            "confirm": [c.split(":")[0][:120] for c in slot.confirm_rules],
        }
    )


def handle(inp: ScrnIn) -> ScrnOut:
    cards, regions, actions, inputs, acceptance, usually, could, texts = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for slot in inp.slots:
        r = _regions(slot)
        a = _actions(slot)
        cards.append(_card(slot, a, r))
        regions += r
        actions += a
        inputs += slot.inputs
        acceptance += _acceptance(slot, a)
        usually += _usually(slot)
        could += _could(slot)
        texts += slot.texts
        for reg in r:
            texts.append(
                CopyOut(screen=slot.id, target="region", ref=reg.key, text=humanize(reg.key))
            )
    return ScrnOut(
        cards=cards,
        regions=regions,
        actions=actions,
        inputs=inputs,
        acceptance=acceptance,
        usually=usually,
        could=could,
        texts=texts,
    )
