"""Rule-based change interpreter: the offline brain of C14 (mock handler and engine fallback).

Turns "change the look to bold dark", "remove reviews", "add a wishlist", "make the buttons
rounder", "no login" into ``ChangeOp`` patch operations. Only what the text states becomes an op;
anything it cannot map gives ``understood=False`` and one short clarifying line.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from mde_contracts.calls import C1Input, C14Input, C14Output, ChangeOp, SlotUpdate
from mde_prompts.calls.c1_understand import lexicon as lx
from mde_prompts.calls.c1_understand.keyword import detect_archetype, extract_updates

MAX_MESSAGE_CHARS = 8_000  # a change request is a sentence; the rules read at most this much
RESEARCH_ID = re.compile(r"\bRA-\d{3}\b", re.IGNORECASE)
# "add a wishlist screen": the name is 3..60 characters that start and end on a non-space (so the
# gaps around it are never ambiguous with it: the old ``\s+(?:a|an|the|one)?\s*(.{3,60}?)\s+`` had
# four adjacent whitespace-capable quantifiers and grew like n**4 on "add" + spaces). Whitespace
# gaps are bounded, so the match is linear; the message is whitespace-folded before it is tried.
ADD_SCREEN = re.compile(
    r"\badd\s{1,8}(?:(?:a|an|the|one)\b\s{1,8})?(\S[^\n]{1,58}?\S)\s{1,8}(?:screen|page|tab)\b",
    re.IGNORECASE,
)
_HEDGE = re.compile(
    r"\b(?:but|lekin|magar|however)\b|[,;]\s{0,8}(?:and\s{1,8})?(?=make|change|add|remove)",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    text = (
        resources.files("mde_prompts.calls.c14_interpret_change")
        .joinpath("rules.yaml")
        .read_text(encoding="utf-8")
    )
    raw: dict[str, Any] = yaml.safe_load(text)
    return {k: (lx.norm_all(v) if isinstance(v, list) else v) for k, v in raw.items()}


def clarify_text(lang: str | None) -> str:
    return str(lx.localized(_rules()["clarify"], lang))


def _cues(name: str) -> tuple[str, ...]:
    cues: tuple[str, ...] = _rules()[name]
    return cues


def _value_fields(update: SlotUpdate) -> dict[str, Any]:
    if update.value_list is not None:
        return {"value_list": update.value_list}
    if update.value_bool is not None:
        return {"value_bool": update.value_bool}
    return {"value_text": update.value_text}


def _after_but(msg: str) -> str:
    """For 'looks good but make it blue' only the part after the hedge describes a change."""
    m = _HEDGE.search(msg)
    return msg[m.end() :].strip() if m else msg


def _current_list(inp: C14Input, slot_id: str) -> list[str]:
    for s in inp.slots:
        if s.slot_id == slot_id and s.value:
            try:
                value = json.loads(s.value)
            except ValueError:
                return []
            return [str(x) for x in value] if isinstance(value, list) else []
    return []


def _current_archetype(inp: C14Input) -> str | None:
    for s in inp.slots:
        if s.slot_id == "idea.archetype" and s.value:
            return s.value.strip('"')
    return None


def interpret(inp: C14Input) -> C14Output:
    """Deterministic C14: patch operations for a change request, or an approval, or a question."""
    msg = lx.squash_spaces(lx.clip(inp.message, MAX_MESSAGE_CHARS)).strip()
    lang = inp.language
    if lx.is_phrase(msg, "approve"):
        return C14Output(understood=True, ops=[], is_approval=True, confidence=0.9)
    text = _after_but(msg) if lx.phrase_ends_message(msg, "approve") else msg
    norm = lx.normalize(text)
    ops: list[ChangeOp] = []
    removing = lx.has_phrase(text, _cues("remove_cues"))
    adding = lx.has_phrase(text, _cues("add_cues")) and not removing
    selected = inp.selected_screen_id

    # research additions: "exclude RA-003"
    for m in RESEARCH_ID.finditer(text):
        ops.append(
            ChangeOp(
                kind="toggle_research_item", target_id=m.group(0).upper(), value_bool=not removing
            )
        )

    # screens by id or by name from the outline
    if inp.outline is not None and not ops:
        for screen in inp.outline.screens:
            words = [w for w in (screen.purpose, screen.route.strip("/").replace("-", " ")) if w]
            hit = screen.id.casefold() in norm or any(
                lx.keyword_hits(text, [w]) for w in words if len(w) > 3
            )
            if hit and removing and lx.has_phrase(text, _cues("screen_words")):
                ops.append(ChangeOp(kind="remove_screen", target_id=screen.id))
                break

    if any(o.kind == "remove_screen" for o in ops):
        return C14Output(understood=True, ops=_dedupe(ops), is_approval=False, confidence=0.8)

    if lx.has_phrase(text, _cues("screen_words")) and not removing:
        # a request about one screen is a screen note or a new screen, never a feature toggle
        m = ADD_SCREEN.search(text)
        ops.append(
            ChangeOp(kind="add_screen", instruction=m.group(1).strip())
            if m
            else ChangeOp(kind="patch_screen", target_id=selected, instruction=msg)
        )
        return C14Output(understood=True, ops=_dedupe(ops), is_approval=False, confidence=0.75)

    # the app type decides which feature ids exist
    archetype = _current_archetype(inp) or detect_archetype(text)[0]
    shim = C1Input(message=text, slots=inp.slots)
    hint = lx.has_phrase(text, _cues("look_verbs"))
    updates = extract_updates(shim, text, archetype, None, look_hint=hint)
    current_features = _current_list(inp, "scope.features")
    for u in updates:
        if u.slot_id == "idea.archetype":
            continue
        if u.slot_id == "scope.features":
            for fid in u.value_list or []:
                if u.negate or fid not in current_features:
                    ops.append(
                        ChangeOp(kind="toggle_feature", target_id=fid, value_bool=not u.negate)
                    )
            continue
        if u.negate:
            ops.append(ChangeOp(kind="clear_slot", slot_id=u.slot_id))
        else:
            ops.append(ChangeOp(kind="set_slot", slot_id=u.slot_id, **_value_fields(u)))
    if updates and any(u.slot_id == "idea.archetype" and not u.negate for u in updates):
        known = _current_archetype(inp)
        for u in updates:
            if u.slot_id == "idea.archetype" and not u.negate and u.value_text != known:
                ops.append(
                    ChangeOp(kind="set_slot", slot_id="idea.archetype", value_text=u.value_text)
                )

    # a feature named with a removal cue but not matched by the checklist words above is skipped on
    # purpose: guessing a feature id is worse than asking.
    # rename the app
    if lx.has_phrase(text, _cues("rename_cues")) and not any(
        o.slot_id == "brand.name" for o in ops
    ):
        for u in extract_updates(shim, text, archetype, None):
            if u.slot_id == "brand.name":
                ops.append(ChangeOp(kind="set_slot", slot_id="brand.name", **_value_fields(u)))

    # tweaks and copy on a screen
    if not ops and lx.has_phrase(text, _cues("tweak_cues")):
        ops.append(
            ChangeOp(kind="patch_screen", target_id=selected, instruction=msg)
            if selected
            else ChangeOp(kind="other", instruction=msg)
        )
    if not ops and lx.has_phrase(text, _cues("copy_cues")):
        ops.append(ChangeOp(kind="set_copy", target_id=selected, instruction=msg))
    if not ops and adding:
        m = ADD_SCREEN.search(text)
        if m:
            ops.append(ChangeOp(kind="add_screen", instruction=m.group(1).strip()))
    if not ops and lx.has_phrase(text, _cues("screen_words")):
        ops.append(ChangeOp(kind="patch_screen", target_id=selected, instruction=msg))

    ops = _dedupe(ops)
    if ops:
        return C14Output(understood=True, ops=ops, is_approval=False, confidence=0.8)
    return C14Output(
        understood=False,
        ops=[],
        clarify_question=clarify_text(lang),
        is_approval=False,
        confidence=0.3,
    )


def _dedupe(ops: list[ChangeOp]) -> list[ChangeOp]:
    seen: set[str] = set()
    out: list[ChangeOp] = []
    for op in ops:
        key = op.model_dump_json()
        if key not in seen:
            seen.add(key)
            out.append(op)
    return out
