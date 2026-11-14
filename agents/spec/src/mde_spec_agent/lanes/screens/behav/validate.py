"""Local checks of a BEHAV output (the call input is enough): every asked category once, plain text, ids from the call,
provider-side categories name the integration they are about."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut
from mde_spec_core.libraries import form_word_findings

MAX_TEXT = 900


def known_ids(inp: BehavIn) -> set[str]:
    """The ids a basis may cite: those of this call. An unknown id is repaired by code (`repair_basis`), never re-asked."""
    from mde_spec_agent.lanes.screens.behav.mock import ids_of

    return set(ids_of([*inp.roles, *inp.entities, *inp.rules]) + [i.id for i in inp.integrations])


def names_of(inp: BehavIn) -> list[str]:
    """Entity and integration names of this call (a behaviour that names one of them still has a subject)."""
    ents = [e.split(" ", 1)[-1].split(" (")[0].lower() for e in inp.entities]
    return [n for n in [*ents, *[i.name.lower() for i in inp.integrations]] if len(n) > 2]


def _row(category: str, what: str, before: str) -> dict[str, str]:
    return {
        "kind": "drop_unknown_optional",
        "node": category,
        "what": what,
        "before": before,
        "after": "",
    }


def repair_basis(inp: BehavIn, out: BehavOut) -> tuple[BehavOut, list[dict[str, str]]]:
    """R-code for BEHAV: drop a basis id that is not an id of this call. The behaviour stays only if another basis id
    remains or its text names an integration or an entity of the call; otherwise it goes (the baseline text for that
    category stays, flagged assumed). A behaviour with no basis at all is simply `assumed`. Returns the repaired output and
    R-code log rows (kind, node, what, before, after)."""
    known, names = known_ids(inp), names_of(inp)
    kept: list = []
    log: list[dict[str, str]] = []
    for b in out.behaviors:
        good = [r for r in b.basis if r in known]
        dropped = [r for r in b.basis if r not in known]
        if dropped:
            log.append(
                _row(
                    b.category,
                    f"dropped the unknown basis id(s) {', '.join(dropped)}",
                    " ".join(dropped),
                )
            )
            if not good and not any(n in b.text.lower() for n in names):
                log.append(
                    _row(
                        b.category,
                        "nothing grounds it any more, so the behaviour is removed",
                        b.text[:80],
                    )
                )
                continue
        kept.append(b.model_copy(update={"basis": good, "assumed": b.assumed or not good}))
    return BehavOut(behaviors=kept), log


def validate(inp: BehavIn, out: BehavOut) -> list[str]:
    errors: list[str] = []
    asked = [c.name for c in inp.categories]
    got = [b.category for b in out.behaviors]
    for c in asked:
        if got.count(c) != 1:
            errors.append(f"category {c}: exactly one behaviour is needed, got {got.count(c)}")
    for c in sorted(set(got) - set(asked)):
        errors.append(f"category {c} was not asked for")
    for b in out.behaviors:
        if not b.text.strip():
            errors.append(f"{b.category}: the text is empty")
        elif len(b.text) > MAX_TEXT:
            errors.append(f"{b.category}: the text is longer than {MAX_TEXT} characters")
        if form_word_findings(b.text):
            errors.append(
                f"{b.category}: form words in the text; say what happens, not how it looks"
            )
    side = [i for i in inp.integrations if i.provider_side]
    for b in out.behaviors:
        provider = b.category in ("interrupted_external_flow", "pending_timeouts")
        if provider and side and not any(_about(i, b) for i in side):
            errors.append(
                f"{b.category}: name the integration it is about in the text ({side[0].name}) "
                f"or cite its id ({side[0].id}) in basis"
            )
    return errors


def _about(i, b) -> bool:
    """The behaviour is about integration `i` when its basis cites the integration's id, or its text names the integration
    (the name as the draft writes it, or the name without a plural s: "payments" is named by "payment provider")."""
    name = i.name.lower().strip()
    text = b.text.lower()
    return i.id in b.basis or name in text or (len(name) > 3 and name.rstrip("s") in text)
