"""What the person said about one screen reaches the spec (spec 3.6, 3.7; eval BUG-3).

Two inputs, one rule: nothing is edited without going through the checks the model's own proposals
go through (``proposals.validate_and_apply``) or a deterministic, sanitized edit.

* ``BuildRequest.change_ops`` (screen-level kinds): ``add_screen`` becomes an ``add_screen``
  proposal (name, route and recipe from the few words given, recipe words are data in
  ``rules.yaml``), ``remove_screen`` a ``remove_item`` proposal, ``patch_screen`` / ``set_copy`` /
  ``other`` (an instruction for a screen) store the instruction as the screen's ``notes``. A screen
  is named by id, route, or words ("the cart screen"). Ops that only change slots, features,
  research toggles or assumptions are already in the inputs and are not read here.
* ``BuildRequest.screen_notes`` (screen id, route or words -> text, from Discover): attached to the
  matching screen the same way.

A screen that carries a note is marked ``source: custom``; C10 sees the note (``context.screen_draft``)
and the engine re-runs C10 for just that screen. A request that cannot be done is dropped with a
recorded reason and one plain line (``rules.yaml``, ``change_ops.messages``) for the Plan view; it
never fails the build. A removed screen is remembered in the backlog (reason ``removed at your
request``, its route as the hint), so that a later build, full or patch, keeps it out. On a full
rebuild ``carry_over`` brings the earlier version's removals, notes and added screens along.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from mde_contracts.calls import ChangeOp, Proposal
from mde_contracts.spec import AppSpec, BacklogItem, ScreenBlock
from mde_core.redact import strip_hidden_chars
from mde_spec_builder import sanitize as sx
from mde_spec_builder.incremental import removed_routes, screen_level
from mde_spec_builder.plan_view import screen_title
from mde_spec_builder.proposals import AppliedProposal, ApplyResult, DroppedProposal
from mde_spec_builder.rules import ChangeOpRules, load_rules
from mde_spec_builder.sanitize import Reject
from mde_spec_builder.validators.leaks import has_secret

MAX_NOTE = 300  # characters of one note
MAX_NOTES = 600  # characters of all the notes of one screen (the oldest go first)
MAX_NAME = 60
NOTE_SEPARATOR = "; "

Validate = Callable[[AppSpec, Sequence[Proposal]], ApplyResult]

_ID = re.compile(r"\bSCR-\d{3}\b", re.IGNORECASE)
_ROUTE = re.compile(r"(?<![\w/])/[A-Za-z0-9_\-\[\]/]*")
_WORD = re.compile(r"[a-z0-9]+")
_SPACE = re.compile(r"\s+")
_FILLER = frozenset({"screen", "page", "tab", "the", "a", "an", "of", "and", "to", "your", "my"})
_TITLE_FILLER = frozenset({*_FILLER, "new"})


# --------------------------------------------------------------------------- the result


@dataclass
class Requests:
    """What applying the requests did."""

    spec: AppSpec
    applied: list[AppliedProposal] = field(default_factory=list)
    dropped: list[DroppedProposal] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # plain lines for what could not be done
    briefs: set[str] = field(default_factory=set)  # screens whose brief (C10) is rewritten
    changed: bool = False  # the spec is not the one that went in


# --------------------------------------------------------------------------- finding a screen


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def _words(text: str, skip: frozenset[str] = frozenset()) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in skip}


def _route_words(route: str) -> set[str]:
    found: set[str] = set()
    for segment in route.split("/"):
        if segment and not segment.startswith(("(", "[")):
            found |= _words(segment)
    return found


def _same_route(a: str, b: str) -> bool:
    return a.lower().rstrip("/") == b.lower().rstrip("/")


def match_screen(spec: AppSpec, text: str | None) -> ScreenBlock | None:
    """The screen a few words are about: an id (``SCR-009``), a route (``/cart``), else the screen
    whose route and title words appear most in the text (shorter routes win a tie). None when no
    screen is named; never a guess between unrelated screens."""
    if not text or not text.strip():
        return None
    by_id = {s.id.upper(): s for s in spec.screens}
    for found in _ID.findall(text):
        if found.upper() in by_id:
            return by_id[found.upper()]
    for token in _ROUTE.findall(text):
        for screen in spec.screens:
            if len(token) > 1 and _same_route(screen.route, token):
                return screen
    wanted = _words(text, _FILLER)
    if not wanted:
        return None
    best: tuple[tuple[int, int, int, int, int], ScreenBlock] | None = None
    for index, screen in enumerate(spec.screens):
        route = _route_words(screen.route)
        title = _words(screen_title(screen), _TITLE_FILLER)
        in_route, in_title = len(route & wanted), len(title & wanted)
        if not in_route and not in_title:
            continue
        key = (
            in_route,
            in_title,
            -len(route - wanted),
            -screen.route.count("/"),
            -index,
        )
        if best is None or key > best[0]:
            best = (key, screen)
    return best[1] if best is not None else None


def _resolve(spec: AppSpec, ref: str | None, text: str | None = None) -> ScreenBlock | None:
    """The screen named by ``ref`` (id, route or words), else the one ``text`` is about."""
    return match_screen(spec, ref) or match_screen(spec, text)


# --------------------------------------------------------------------------- editing helpers


def _note_text(text: str | None) -> str:
    """A note as it is stored: hidden characters gone, one line, cut at a word, secret-scanned."""
    cleaned = _SPACE.sub(" ", strip_hidden_chars(text or "")).strip()
    if len(cleaned) > MAX_NOTE:
        cleaned = cleaned[:MAX_NOTE].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return sx.clean(cleaned, MAX_NOTE + 1, "note", required=False)


def _join_notes(existing: str | None, text: str) -> str:
    parts = [p for p in (existing or "").split(NOTE_SEPARATOR) if p]
    if text not in parts:
        parts.append(text)
    notes = NOTE_SEPARATOR.join(parts)
    while len(notes) > MAX_NOTES and len(parts) > 1:
        parts.pop(0)
        notes = NOTE_SEPARATOR.join(parts)
    return notes


def _replace(spec: AppSpec, screen: ScreenBlock) -> AppSpec:
    return spec.model_copy(
        update={"screens": [screen if s.id == screen.id else s for s in spec.screens]}
    )


def _name_of(text: str) -> str:
    """A screen name from the words of a request: ``add a photo gallery screen`` -> Photo gallery."""
    kept = [w for w in _SPACE.sub(" ", text).strip().split(" ") if w.lower() not in _FILLER]
    name = " ".join(kept) or text.strip()
    if len(name) > MAX_NAME:
        name = name[:MAX_NAME].rsplit(" ", 1)[0]
    return name[:1].upper() + name[1:]


def _route_for(spec: AppSpec, name: str) -> str:
    slug = "-".join(_WORD.findall(name.lower()))[:40].strip("-")
    return f"/{slug}" if slug else f"/screen-{len(spec.screens) + 1}"


def _recipe_for(text: str, rules: ChangeOpRules) -> str:
    wanted = _words(text)
    for entry in rules.recipe_keywords:
        if wanted & {_stem(w) for w in entry.words}:
            return entry.recipe
    return rules.default_recipe


def _plain(reason: str) -> str:
    return reason.strip().rstrip(".")


# --------------------------------------------------------------------------- the applier


class _Applier:
    def __init__(self, spec: AppSpec, validate: Validate) -> None:
        self.out = Requests(spec)
        self._validate = validate
        self._rules = load_rules().change_ops

    @property
    def spec(self) -> AppSpec:
        return self.out.spec

    def _say(self, template: str, **values: str) -> None:
        self.out.lines.append(template.format(**values))

    def _run(self, proposal: Proposal) -> AppliedProposal | None:
        """One proposal through the checks; its result is kept, a drop is remembered."""
        result = self._validate(self.spec, [proposal])
        self.out.applied += result.applied
        self.out.dropped += result.dropped
        if result.dropped:
            return None
        self.out.spec = result.spec
        self.out.changed = True
        return result.applied[0]

    # ----- notes

    def note(self, screen: ScreenBlock, text: str | None) -> bool:
        """Store ``text`` as a note of ``screen`` (marked ``custom``) and queue its brief."""
        messages = self._rules.messages
        title = screen_title(screen)
        if not text or not text.strip():
            self._say(messages.no_detail, screen=title)
            return False
        try:
            cleaned = _note_text(text)
        except Reject:
            self._say(messages.secret, screen=title)
            return False
        if not cleaned:
            self._say(messages.no_detail, screen=title)
            return False
        notes = _join_notes(screen.notes, cleaned)
        if notes == screen.notes and screen.source == "custom":
            return True  # already there (a build that repeats the same note)
        updated = screen.model_copy(update={"notes": notes, "source": "custom"})
        self.out.spec = _replace(self.spec, updated)
        self.out.changed = True
        self.out.briefs.add(screen.id)
        return True

    # ----- screens

    def add(self, text: str | None) -> None:
        messages = self._rules.messages
        cleaned = _SPACE.sub(" ", strip_hidden_chars(text or "")).strip()
        if not cleaned:
            self._say(messages.no_name)
            return
        name = _name_of(cleaned)
        self.add_screen(
            name,
            _route_for(self.spec, name),
            _recipe_for(cleaned, self._rules),
            self._rules.purpose.format(name=name),
        )

    def add_screen(
        self, name: str, route: str, recipe: str, purpose: str, notes: str | None = None
    ) -> ScreenBlock | None:
        proposal = Proposal(
            kind="add_screen",
            route=route,
            recipe=recipe,
            name=name,
            purpose=purpose,
            rationale="You asked for this screen.",
        )
        applied = self._run(proposal)
        if applied is None:
            reason = self.out.dropped[-1].reason
            self._say(self._rules.messages.cannot_add, what=_short(name), reason=_plain(reason))
            return None
        screen_id = applied.created_ids[0]
        screen = next(s for s in self.spec.screens if s.id == screen_id)
        self.out.spec = _replace(self.spec, screen.model_copy(update={"source": "custom"}))
        self.out.briefs.add(screen_id)
        for part in (notes or "").split(NOTE_SEPARATOR):
            if part:
                self.note(_screen(self.spec, screen_id), part)
        return _screen(self.spec, screen_id)

    def remove(self, screen: ScreenBlock) -> bool:
        proposal = Proposal(
            kind="remove_item", target=screen.id, rationale="You asked to take it out."
        )
        title = screen_title(screen)
        if self._run(proposal) is None:
            reason = self.out.dropped[-1].reason
            self._say(self._rules.messages.cannot_remove, screen=title, reason=_plain(reason))
            return False
        item = BacklogItem(
            title=title, reason=self._rules.removed_reason, screens_hint=[screen.route]
        )
        if item not in self.spec.backlog:
            self.out.spec = self.spec.model_copy(update={"backlog": [*self.spec.backlog, item]})
        return True

    # ----- the two kinds of input

    def op(self, op: ChangeOp) -> None:
        if not screen_level(op):
            return  # slots, features, toggles: already in the inputs of this build
        text = op.instruction or op.value_text
        if op.kind == "add_screen":
            self.add(text)
            return
        screen = _resolve(self.spec, op.target_id, text)
        if screen is None:
            if text or op.target_id:
                self._say(
                    self._rules.messages.unknown_screen, what=_short(text or op.target_id or "")
                )
            return
        if op.kind == "remove_screen":
            self.remove(screen)
        else:  # patch_screen, set_copy, other: the instruction is the screen's note
            self.note(screen, text)

    def screen_note(self, key: str, text: str) -> None:
        screen = _resolve(self.spec, key, text)
        if screen is None:
            self._say(self._rules.messages.unknown_screen, what=_short(key))
            return
        self.note(screen, text)


def _screen(spec: AppSpec, screen_id: str) -> ScreenBlock:
    return next(s for s in spec.screens if s.id == screen_id)


def _short(text: str, limit: int = 60) -> str:
    """Words of the person's request as they appear in a plain line: one line, quotes softened, cut,
    and never a secret (the Plan view is stored)."""
    cleaned = _SPACE.sub(" ", strip_hidden_chars(text)).strip().replace('"', "'")
    if has_secret(cleaned):
        return "something you wrote"
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- public API


def apply_requests(
    spec: AppSpec,
    ops: Sequence[ChangeOp],
    screen_notes: Mapping[str, str],
    validate: Validate,
    *,
    previous: AppSpec | None = None,
) -> Requests:
    """``spec`` with the screen notes and screen-level change requests of a build applied.

    ``previous`` is the earlier version on a full rebuild: what the person asked for before
    (removed screens, notes, added screens) is brought along first. Ops come in the order given,
    after the notes of Discover.
    """
    applier = _Applier(spec, validate)
    if previous is not None:
        _carry_over(applier, previous)
    for key, text in screen_notes.items():
        applier.screen_note(key, text)
    for op in ops:
        applier.op(op)
    return applier.out


def _carry_over(applier: _Applier, previous: AppSpec) -> None:
    """Replay the earlier version's own requests on a rebuilt baseline: removals first, then notes
    on pack screens and the screens the person added (by route)."""
    for route in sorted(removed_routes(previous)):
        screen = next((s for s in applier.spec.screens if s.route == route), None)
        if screen is not None:
            applier.remove(screen)
    for old in previous.screens:
        if old.source != "custom":
            continue
        here = next((s for s in applier.spec.screens if s.route == old.route), None)
        if here is not None:
            for part in (old.notes or "").split(NOTE_SEPARATOR):
                if part:
                    applier.note(_screen(applier.spec, here.id), part)
        else:
            applier.add_screen(
                screen_title(old),
                old.route,
                old.recipe,
                old.purpose,
                old.notes,
            )


__all__ = ["Requests", "Validate", "apply_requests", "match_screen", "screen_level"]
