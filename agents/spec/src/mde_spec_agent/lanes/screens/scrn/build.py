"""From a SCRN output to Screen nodes (code, deterministic). Cards are WRITTEN by the model but never say where or how.

Code owns: states (instantiated from the library), the acceptance minimum (library), copy keys (minted, namespaced by the
screen id), COULD ids, the sort order of regions and actions, hero citations and everything the card does not carry
(provenance, roles, route name and path, stories). `assemble` returns the new nodes AND every problem it found, word for
word: the validators of the call give them back to the model once (the one re-ask per node), then the fallback is used.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from mde_spec_agent.derive.text import first_sentences, humanize
from mde_spec_agent.lanes.screens.common import LaneRules, needs_decision_flag
from mde_spec_agent.lanes.screens.scrn.schema import (
    AcceptOut,
    ActionOut,
    CardOut,
    CouldOut,
    InputOut,
    RegionOut,
    ScrnIn,
    ScrnOut,
    UsuallyOut,
)
from mde_spec_core import libraries, vocab
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import (
    Acceptance,
    Action,
    Could,
    Guard,
    Input,
    Prov,
    Region,
    RouteParam,
    Screen,
    SpecGraph,
    Step,
    Usually,
)
from mde_spec_core.ids import is_valid_key, namespaced_copy_key, parse_id
from mde_spec_core.libraries import form_word_findings, key_names_a_widget

_ENTFIELD = re.compile(r"\bENT-[A-Z]?\d+\.[a-z][a-z0-9_]*")
_FLOW_STEP = re.compile(r"(FLW-\w+) step (\d+)")
_EVENT = re.compile(r"^[a-z][a-z0-9_]*$")
PROMPT_REF = "spec.scrn"


class Fixes(
    list
):  # list[str]: the plain log lines; `rows` are the same facts for the run's R-code log
    """Mechanical repairs made by code (R-code, doc 18 3.2). Each is logged word for word and counted at G2; none is silent."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []

    def add(self, kind: str, node: str, what: str, before: str = "", after: str = "") -> None:
        self.append(f"{node}: {what}")
        self.rows.append(
            {"kind": kind, "node": node, "what": what, "before": str(before), "after": str(after)}
        )


DROP = "drop_unknown_optional"
SYNONYM = "exact_synonym"
DEFAULT = "instantiate_library_default"


@dataclass
class Built:
    screens: dict[str, Screen] = field(default_factory=dict)
    copy: dict[str, str] = field(default_factory=dict)  # source-language text by key
    errors: list[str] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    notes: Fixes = field(default_factory=Fixes)


# ---------------------------------------------------------------- parsing the typed strings


def parse_step(text: str, where: str, errors: list[str]) -> Step | None:
    t = " ".join(text.split())
    if t == "stay":
        return Step(verb="stay")
    verb, _, rest = t.partition(" ")
    if verb in ("call", "go", "external_flow") and rest.strip():
        return Step(verb=verb, target=rest.strip())  # type: ignore[arg-type]
    errors.append(
        f"{where}: effect step {text!r} is not one of 'call OP-x', 'go SCR-y', 'stay', 'external_flow name'"
    )
    return None


def parse_guard(text: str, where: str, errors: list[str]) -> Guard | None:
    t = " ".join(text.split())
    if t in ("signed_in", "role_has_access"):
        return Guard(pred=t)  # type: ignore[arg-type]
    parts = t.split(" ", 2)
    if parts[0] in ("field_equals", "field_in") and len(parts) == 3 and "." in parts[1]:
        ent, _, fld = parts[1].partition(".")
        values = [parts[2]] if parts[0] == "field_equals" else [v for v in parts[2].split("|") if v]
        if values:
            return Guard(pred=parts[0], entity=ent, field=fld, values=values)  # type: ignore[arg-type]
    errors.append(
        f"{where}: guard {text!r} must be signed_in, role_has_access, 'field_equals ENT-x.field value' "
        "or 'field_in ENT-x.field a|b'"
    )
    return None


def guard_text(g: Guard) -> str:
    if g.pred in ("signed_in", "role_has_access"):
        return g.pred
    return f"{g.pred} {g.entity}.{g.field} " + ("|".join(g.values))


def step_text(s: Step) -> str:
    return s.verb if s.target is None else f"{s.verb} {s.target}"


def parse_param(text: str, where: str, errors: list[str]) -> RouteParam | None:
    parts = text.split(":", 2)
    if len(parts) >= 2 and is_valid_key(parts[0]) and parts[1]:
        return RouteParam(name=parts[0], type=parts[1], source=parts[2] if len(parts) > 2 else "")
    errors.append(f"{where}: route parameter {text!r} must look like name:type:source")
    return None


def _canon(vocab_name: str, word: str) -> str:
    return libraries.canonical(vocab_name, word) or word


# ---------------------------------------------------------------- the pieces


_TRUE = {"true", "yes"}
_FALSE = {"false", "no"}


def repair_attrs(shape: str, attrs: list[str], where: str, notes: Fixes) -> list[str]:
    """R-code on the attributes of a shape (drop_unknown_optional): a boolean attribute takes a clear yes/no/true/false word
    (any case) and is written true or false; a value that is not clearly boolean, or a whole-number attribute that is not
    a number, is dropped and logged. Everything else is left to the vocabulary check (which re-asks)."""
    sh = vocab.shape(shape)
    if sh is None:
        return list(attrs)
    allowed = {a.name: a for a in vocab.shapes_file().common_attributes}
    allowed.update({a.name: a for a in sh.attributes})
    out: list[str] = []
    for raw in attrs:
        name, sep, value = raw.partition("=")
        spec = allowed.get(name)
        if sep and spec is not None and spec.type == "bool":
            v = value.strip().lower()
            if v in _TRUE | _FALSE:
                fixed = "true" if v in _TRUE else "false"
                if fixed != value:
                    notes.add(
                        SYNONYM,
                        where,
                        f"attribute {name}={value!r} written as {fixed}",
                        raw,
                        f"{name}={fixed}",
                    )
                out.append(f"{name}={fixed}")
            else:
                notes.add(
                    DROP, where, f"dropped attribute {name}={value!r}: not true or false", raw
                )
        elif (
            sep
            and spec is not None
            and spec.type == "integer"
            and not value.strip().lstrip("-").isdigit()
        ):
            notes.add(DROP, where, f"dropped attribute {name}={value!r}: not a whole number", raw)
        else:
            out.append(raw)
    return out


def split_guards(
    texts: list[str], where: str, errors: list[str], notes: Fixes
) -> tuple[list[Guard], list[str]]:
    """R-code on guards: a guard outside the closed grammar is dropped and its meaning is kept as plain words (the caller
    writes it into a free-text field). Returns the structured guards and the plain-language notes."""
    guards: list[Guard] = []
    plain: list[str] = []
    for t in texts:
        tmp: list[str] = []
        g = parse_guard(t, where, tmp)
        if g is not None:
            guards.append(g)
            continue
        words = " ".join(t.replace("_", " ").replace(".", " ").split())
        if words:
            plain.append(words)
            notes.add(
                DROP,
                where,
                f"guard {t!r} is outside the grammar; kept as the plain words {words!r}",
                t,
                words,
            )
    return guards, plain


def _check_key(key: str, where: str, errors: list[str]) -> None:
    if not is_valid_key(key):
        errors.append(f"{where}: key {key!r} must be lower snake case")
    elif key_names_a_widget(key):
        errors.append(f"{where}: key {key!r} ends in a widget or trigger word; name the meaning")


def _clean(
    text: str | None, where: str, screen_type: str, notes: Fixes, fallback: str | None
) -> str | None:
    """A form word in free text is a WARNING-grade lint (V-25): never a re-ask. The text is replaced by `fallback` (the
    baseline text, or empty); with no fallback the text is kept and the warning stays visible at G2. Logged as R-code."""
    if not text:
        return text
    hits = form_word_findings(text, screen_type)
    if not hits:
        return text
    words = ", ".join(sorted({h.word.lower() for h in hits}))
    if fallback is None:
        notes.add(
            DROP, where, f"form words ({words}) kept: nothing to replace them with", text, text
        )
        return text
    notes.add(DROP, where, f"form words ({words}); replaced by the baseline text", text, fallback)
    return fallback


def _region(r: RegionOut, base: Screen, errors: list[str], notes: Fixes) -> Region:
    where = f"{base.id}.{r.key}"
    _check_key(r.key, where, errors)
    shape = r.shape if r.shape.startswith("other:") else _canon("shape", r.shape)
    attrs = repair_attrs(shape, r.attrs, where, notes)
    for p in vocab.check_region_shape(shape, r.shape_kind, attrs):
        errors.append(f"{where}: {p}")
    if shape == "collection" and not (r.cardinality or r.maximum):
        errors.append(
            f"{where}: a collection needs a cardinality bucket (one, few, many, very_many)"
        )
    if r.cardinality and r.cardinality not in vocab.shapes_file().cardinality_buckets:
        errors.append(f"{where}: cardinality {r.cardinality!r} is not a bucket")
    what = _clean(
        r.what,
        where,
        base.screen_type,
        notes,
        next((x.what for x in base.regions if x.key == r.key), None),
    )
    return Region(
        key=r.key,
        what=" ".join((what or "").split()),
        shape=shape,
        shape_kind=r.shape_kind or None,
        attrs=sorted(attrs),
        bind=list(r.bind),
        importance=r.importance,
        freshness=r.freshness or None,
        cardinality=r.cardinality or None,
        typical=r.typical,
        maximum=r.maximum,
        live=r.live,
    )


def _action(
    a: ActionOut,
    base: Screen,
    graph: SpecGraph,
    errors: list[str],
    flags: list[dict[str, Any]],
    notes: Fixes,
) -> Action:
    where = f"{base.id}.{a.key}"
    _check_key(a.key, where, errors)
    intent = _canon("intent", a.intent)
    if not vocab.is_known_intent(intent):
        errors.append(f"{where}: intent {a.intent!r} is not in the closed vocabulary of intents")
    what = _clean(
        a.what,
        where,
        base.screen_type,
        notes,
        next((x.what for x in base.actions if x.key == a.key), None),
    )
    pre = _clean(a.precondition, f"{where} precondition", base.screen_type, notes, "")
    steps: list[Step] = []
    for t in a.effect:
        s = parse_step(t, where, errors)
        if s:
            steps.append(s)
    if len(steps) > 3:
        errors.append(f"{where}: effect has more than three steps")
    integ = (
        {i.need_key for i in graph.integrations.values()}
        | {i.id for i in graph.integrations.values()}
        | {i.name for i in graph.integrations.values()}
    )
    if a.unspecified:
        flags.append(
            needs_decision_flag(
                where,
                f"{a.what or a.key}: the card needs an operation Product never wrote; the action is kept marked unspecified",
            )
        )
        steps = []
    else:
        for s in steps:
            if s.verb == "call" and s.target not in graph.operations:
                errors.append(
                    f"{where}: {s.target} is not an operation; use an operation of the slice or set unspecified"
                )
            elif s.verb == "go" and (
                s.target not in graph.screens or graph.screens[s.target or ""].status != "in"
            ):
                errors.append(f"{where}: go {s.target} is not a live screen")
            elif s.verb == "external_flow" and s.target not in integ:
                errors.append(f"{where}: external flow {s.target} has no integration record")
        if not steps:
            errors.append(f"{where}: an action needs an effect (or unspecified)")
    guards, plain = split_guards(a.guard, where, errors, notes)
    precondition = "; ".join([p for p in [" ".join((pre or "").split()), *plain] if p])
    return Action(
        key=a.key,
        importance=a.importance,
        intent=intent,
        what=" ".join((what or "").split()),
        precondition=precondition,
        effect=steps,
        guard=guards,
        errors=list(dict.fromkeys(a.errors)),
        flags=sorted(set(a.flags)),
        unspecified=a.unspecified,
    )


def _input(i: InputOut, base: Screen, errors: list[str]) -> Input:
    where = f"{base.id}.{i.key}"
    _check_key(i.key, where, errors)
    itype = _canon("input_type", i.type)
    if not vocab.is_known_input_type(itype):
        errors.append(f"{where}: input type {i.type!r} is not a semantic input type")
    return Input(
        key=i.key,
        type=itype,
        required=i.required,
        constraint=i.constraint or "",
        default=i.default or None,
        source=i.source,
    )


def could_id(screen_id: str, k: int) -> str:
    p = parse_id(screen_id)
    spec = 1 if (p and p.spec_minted) else 0
    num = p.number if p else 0
    return f"CP-S{spec}{num:03d}{k}"


def _could(
    c: CouldOut,
    k: int,
    base: Screen,
    graph: SpecGraph,
    inp: ScrnIn,
    errors: list[str],
    notes: Fixes,
) -> Could | None:
    where = f"{base.id}.could{k}"
    for text in (c.when, c.effect, c.reduced_motion_alt or ""):
        hits = form_word_findings(text, base.screen_type) if text else []
        if hits:  # a warning-grade lint is never a re-ask: the idea is dropped (it is Open and ignorable anyway)
            words = ", ".join(sorted({h.word.lower() for h in hits}))
            notes.add(DROP, where, f"COULD entry dropped: form words ({words})", text, "")
            return None
    if not c.basis.strip():
        errors.append(f"{where}: a COULD entry needs a basis")
    assumed = c.assumed
    m = _FLOW_STEP.search(c.basis)
    if c.basis_kind == "journey_moment":
        flow = graph.flows.get(m.group(1)) if m else None
        stage = (
            next((x for x in flow.stages if x.n == int(m.group(2))), None) if flow and m else None
        )
        if stage is None:
            errors.append(
                f"{where}: basis must name a journey moment as 'FLW-x step n' that exists in the slice"
            )
        elif stage.emotion_assumed:
            assumed = True  # an assumed basis makes the entry assumed
    if c.motion:
        if c.motion not in vocab.motion_intents():
            errors.append(f"{where}: motion {c.motion!r} is not a motion intent")
        elif vocab.is_essential_motion(c.motion) and not c.reduced_motion_alt:
            errors.append(f"{where}: essential motion needs a non-motion alternative")
    return Could(
        id=could_id(base.id, k),
        kind=c.kind,
        when=" ".join(c.when.split()),
        effect=" ".join(c.effect.split()),
        basis=" ".join(c.basis.split()),
        basis_kind=c.basis_kind,
        assumed=assumed,
        motion=c.motion or None,
        reduced_motion_alt=c.reduced_motion_alt or None,
        tier=c.tier,
    )


def _usually(
    u: UsuallyOut, base: Screen, graph: SpecGraph, inp: ScrnIn, built: Built, errors: list[str]
) -> Usually | None:
    where = f"{base.id}.usually"
    hits = form_word_findings(u.note, base.screen_type)
    if hits:
        words = ", ".join(sorted({h.word.lower() for h in hits}))
        built.notes.add(
            DROP, where, f"peer-practice line dropped: form words ({words})", u.note, ""
        )
        return None
    slot = next((s for s in inp.slots if s.id == base.id), None)
    norms = {n.id: n for n in (slot.norms if slot else [])}
    evidence = u.evidence
    item = u.item or None
    if item is not None:
        n = norms.get(item)
        if n is None:
            # R-code (drop an unknown optional field): a research item that was not offered is not a citation. The note
            # stays only as an unverified peer note; it is never given evidence it does not have (doc 18 3.2).
            built.notes.add(
                DROP,
                where,
                f"dropped the unknown research item {item!r}; the line is unverified",
                item,
                "",
            )
            item, evidence = None, "unverified"
        else:
            evidence = n.evidence  # type: ignore[assignment]
    elif u.evidence != "library":
        evidence = "unverified"  # evidence without an item is not evidence
    # bindings named in a USUALLY line must pass the access and contract checks, or they become suggestions
    for ref in _ENTFIELD.findall(u.note):
        eid, _, fld = ref.partition(".")
        e = graph.entities.get(eid)
        ok = bool(e and any(f.key == fld for f in e.fields))
        if ok and e:
            for row in e.access:
                if row.role_id in base.role_ids and row.actions == ["none"]:
                    ok = False
        if not ok:
            built.flags.append(
                {
                    "kind": "suggestion",
                    "step": "screens",
                    "node": base.id,
                    "why": f"a peer-practice line binds {ref}, which fails the access or contract check: offered as a suggestion at G2, not written on the card",
                }
            )
            return None
    return Usually(note=" ".join(u.note.split()), evidence=evidence, item=item)  # type: ignore[arg-type]


# ---------------------------------------------------------------- one screen


def _acceptance(
    base: Screen,
    extras: list[AcceptOut],
    profile: str,
    cfg: SpecConfig,
    errors: list[str],
    cap: int,
    notes: Fixes,
) -> list[Acceptance]:
    lib = libraries.state_library_for(base.screen_type, profile)
    out = [a.to_acceptance(base.name) for a in lib.acceptance]
    have = {(a.given, a.when, a.then) for a in out}
    if len(extras) > cap:
        notes.add(
            DROP,
            base.id,
            f"{len(extras)} extra acceptance scenarios, the first {cap} kept",
            str(len(extras)),
            str(cap),
        )
    for x in extras[:cap]:
        t = (x.given.strip(), x.when.strip(), x.then.strip())
        if not all(t):
            notes.add(
                DROP,
                base.id,
                "an acceptance scenario without GIVEN, WHEN and THEN was dropped",
                " | ".join(t),
                "",
            )
        if t not in have and all(t):
            out.append(Acceptance(given=t[0], when=t[1], then=t[2]))
            have.add(t)
    if len(out) < cfg.profiles.acceptance_scenarios_min[profile]:
        errors.append(
            f"{base.id}: fewer than {cfg.profiles.acceptance_scenarios_min[profile]} scenarios"
        )
    return out


def _states(base: Screen, profile: str) -> tuple[list[Any], list[str], dict[str, str]]:
    lib = libraries.state_library_for(base.screen_type, profile)
    states = list(base.states)
    have = {s.kind for s in states}
    keys: list[str] = []
    copy: dict[str, str] = {}
    for t in lib.states:
        if t.kind not in have:
            states.append(t.to_state_spec())
        keys.append(t.copy_key)
        copy[t.copy_key] = t.copy_text
    return states, keys, copy


def build_screen(
    base: Screen,
    inp: ScrnIn,
    out: ScrnOut,
    graph: SpecGraph,
    cfg: SpecConfig,
    rules: LaneRules,
    built: Built,
    errors: list[str],
) -> Screen:
    sid = base.id
    mine = lambda xs: [x for x in xs if x.screen == sid]  # noqa: E731
    slot = next((s for s in inp.slots if s.id == sid), None)
    cards = mine(out.cards)
    if cards:
        card = cards[0]
        if len(cards) > 1:
            built.notes.add(
                DROP, sid, f"{len(cards)} cards given; the first is used", str(len(cards)), "1"
            )
    else:  # the model gave no card for a screen of the call: code builds it from the baseline, it does not re-ask
        built.notes.add(DEFAULT, sid, "no card was given; the baseline card is used", "", sid)
        card = (
            slot.card
            if slot
            else CardOut(
                screen=sid,
                purpose=base.purpose,
                params=[],
                guards=[],
                role_conditions=None,
                reached_from=[],
                leads_to=[],
                accessibility=None,
                analytics=[],
                mock_profiles=[],
                mock_states=[],
                confirm=[],
            )  # fmt: skip
        )
    profile = inp.profile
    # regions, actions, inputs
    given_regions = mine(out.regions)
    if not given_regions and slot and slot.regions:
        built.notes.add(DEFAULT, sid, "no region was given; the baseline regions are used", "", sid)
        given_regions = list(slot.regions)
    regions = [_region(r, base, errors, built.notes) for r in given_regions]
    actions = [_action(a, base, graph, errors, built.flags, built.notes) for a in mine(out.actions)]
    inputs = [_input(i, base, errors) for i in mine(out.inputs)]
    if not regions:
        errors.append(
            f"{sid}: a card needs at least one region"
        )  # nothing in the baseline either: a real defect
    for what, names in (
        ("region", [r.key for r in regions]),
        ("action", [a.key for a in actions]),
        ("input", [i.key for i in inputs]),
    ):
        for k in sorted({k for k in names if names.count(k) > 1}):
            errors.append(f"{sid}: duplicate {what} key {k}")
    for r in regions:
        for b in r.bind:
            if b.startswith("OP-"):
                ok = b in graph.operations
            else:
                eid, _, fld = b.partition(".")
                e = graph.entities.get(eid)
                ok = bool(e and any(f.key == fld for f in e.fields))
            if not ok:
                errors.append(f"{sid}.{r.key}: binding {b} names no real field or operation")
    # purpose, route, guards
    purpose = first_sentences(card.purpose, 2)
    if not purpose:
        errors.append(f"{sid}: the purpose is empty")
    purpose = (
        _clean(purpose, f"{sid}.purpose", base.screen_type, built.notes, base.purpose) or purpose
    )
    accessibility = (
        _clean(card.accessibility, f"{sid}.accessibility", base.screen_type, built.notes, "") or ""
    )
    route = base.route.model_copy(deep=True)
    route_plain: list[str] = []
    if card.params:
        route.params = [p for t in card.params if (p := parse_param(t, sid, errors))]
        for p in route.params:
            if f":{p.name}" not in route.path:
                route.path = route.path.rstrip("/") + f"/:{p.name}"
    if card.guards:
        route.guards, route_plain = split_guards(card.guards, sid, errors, built.notes)
    # navigation facts: leads_to always contains every go target (code keeps the two consistent)
    go_targets = {s.target for a in actions for s in a.effect if s.verb == "go" and s.target}
    leads = sorted(
        {t for t in card.leads_to if t in graph.screens and t != sid}
        | {t or "" for t in go_targets}
    )
    reached = sorted({t for t in card.reached_from if t.split(".")[0] in graph.screens})
    # analytics, mock references
    analytics = []
    for ev in card.analytics:
        if _EVENT.match(ev) and not key_names_a_widget(ev):
            analytics.append(ev)
        else:
            errors.append(
                f"{sid}: analytics event {ev!r} must be lower snake case, named for what happened"
            )
    slot = next((s for s in inp.slots if s.id == sid), None)
    options = set(slot.mock_state_options) if slot else set()
    profiles_ok = set(vocab.mock_profiles())
    for ms in card.mock_states:
        if options and ms not in options:
            errors.append(
                f"{sid}: mock state {ms!r} is not an entity and lifecycle state of this screen"
            )
    for mp in card.mock_profiles:
        if mp not in profiles_ok:
            errors.append(f"{sid}: mock profile {mp!r} is not one of {sorted(profiles_ok)}")
    # states, acceptance
    states, lib_keys, lib_copy = _states(base, profile)
    extra_cap = rules.acceptance_extra_max.get(profile, 1)
    acceptance = _acceptance(
        base, mine(out.acceptance), profile, cfg, errors, extra_cap, built.notes
    )
    # usually, could
    usually = [
        u
        for x in mine(out.usually)[: rules.usually_max + 4]
        if (u := _usually(x, base, graph, inp, built, errors))
    ]
    if len(usually) > rules.usually_max:
        built.notes.add(
            DROP,
            sid,
            f"{len(usually)} usually lines, the first {rules.usually_max} kept",
            str(len(usually)),
            str(rules.usually_max),
        )
        usually = usually[: rules.usually_max]
    coulds = mine(out.could)
    cap = min(
        cfg.profiles.could_cap_per_screen,
        slot.could_max if slot else cfg.profiles.could_cap_per_screen,
    )
    if len(coulds) > cap:
        built.notes.add(
            DROP,
            sid,
            f"{len(coulds)} COULD entries, the first {cap} kept",
            str(len(coulds)),
            str(cap),
        )
    could = [
        c2
        for k, c in enumerate(coulds[:cap])
        if (c2 := _could(c, k + 1, base, graph, inp, errors, built.notes)) is not None
    ]
    # copy: keys minted by code, text from the model
    keys: dict[str, str] = {}
    texts = {(c.target, c.ref): " ".join(c.text.split()) for c in mine(out.texts) if c.text.strip()}
    need = [("title", "title", namespaced_copy_key(sid, "title"))]
    need += [("action", a.key, namespaced_copy_key(sid, a.key)) for a in actions]
    need += [("input", i.key, namespaced_copy_key(sid, i.key)) for i in inputs]
    for target, ref, key in need:
        text = texts.get((target, ref))
        if not text:  # code fills the label from the baseline, it does not re-ask
            text = next(
                (
                    c.text
                    for c in (slot.texts if slot else [])
                    if (c.target, c.ref) == (target, ref) and c.text.strip()
                ),
                humanize(ref) if target != "title" else base.name,
            )
            built.notes.add(
                DEFAULT,
                f"{sid}.{ref}",
                f"no copy text for the {target} {ref!r}; the baseline label is used",
                "",
                text,
            )
        keys[key] = text
    if rules.region_label_copy:
        for r in regions:
            text = texts.get(("region", r.key))
            if text:
                keys[namespaced_copy_key(sid, f"{r.key}_label")] = text
    built.copy.update(lib_copy)
    built.copy.update(keys)
    s = base.model_copy(deep=True)
    s.purpose = purpose
    s.route = route
    s.role_conditions = "; ".join(
        p for p in [" ".join((card.role_conditions or "").split()), *route_plain] if p
    )
    s.reached_from = reached
    s.leads_to = leads
    s.regions = sorted(regions, key=lambda r: r.key)
    s.actions = sorted(actions, key=lambda a: a.key)
    s.inputs = inputs
    s.states = states
    s.acceptance = acceptance
    s.analytics = sorted(set(analytics))
    s.accessibility = " ".join(accessibility.split())
    s.usually = usually
    s.could = could
    s.mock_profiles = sorted(set(card.mock_profiles))
    s.hero_refs = sorted(set(card.mock_states))
    s.confirm = list(dict.fromkeys(card.confirm))
    s.copy_keys = sorted(set(lib_keys) | set(keys))
    if not any(p.kind == "model" and p.ref == PROMPT_REF for p in s.provenance):
        s.provenance = [*s.provenance, Prov(kind="model", ref=PROMPT_REF)]
    return s


def assemble(
    graph: SpecGraph,
    inp: ScrnIn,
    out: ScrnOut,
    cfg: SpecConfig,
    rules: LaneRules,
    *,
    ids: Iterable[str] | None = None,
) -> Built:
    """The Screen nodes of one SCRN batch, the source copy they need, and every problem found."""
    built = Built()
    want = list(ids) if ids is not None else [s.id for s in inp.slots]
    known = set(want)
    for rec in (
        *out.cards,
        *out.regions,
        *out.actions,
        *out.inputs,
        *out.acceptance,
        *out.usually,
        *out.could,
        *out.texts,
    ):
        if rec.screen not in known:
            built.errors.append(
                f"a record names {rec.screen!r}, which is not a screen of this call"
            )
            break
    for sid in want:
        base = graph.screens.get(sid)
        if base is None:
            built.errors.append(f"{sid} is not a screen of the graph")
            continue
        errs: list[str] = []
        built.screens[sid] = build_screen(base, inp, out, graph, cfg, rules, built, errs)
        built.errors.extend(errs)
    return built


def screens_patch(built: Built, language: str) -> dict[str, Any]:
    """The checkpointable patch of a batch: Screen nodes (replace) and their source-language copy."""
    from mde_spec_agent.run.patch import node_patch

    return node_patch(
        "screens",
        [built.screens[k] for k in sorted(built.screens)],
        replace=True,
        locale_copy={language: dict(sorted(built.copy.items()))},
    )
