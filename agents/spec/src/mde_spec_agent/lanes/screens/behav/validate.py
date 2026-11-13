"""Local checks of a BEHAV output (the call input is enough): every asked category once, plain text, ids from the call,
provider-side categories name the integration they are about."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.behav.mock import ids_of
from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut
from mde_spec_core.libraries import form_word_findings

MAX_TEXT = 900


def validate(inp: BehavIn, out: BehavOut) -> list[str]:
    errors: list[str] = []
    asked = [c.name for c in inp.categories]
    got = [b.category for b in out.behaviors]
    for c in asked:
        if got.count(c) != 1:
            errors.append(f"category {c}: exactly one behaviour is needed, got {got.count(c)}")
    for c in sorted(set(got) - set(asked)):
        errors.append(f"category {c} was not asked for")
    known = set(
        ids_of([*inp.roles, *inp.entities, *inp.rules, *[i.id for i in inp.integrations]])
        + [i.id for i in inp.integrations]
    )
    for b in out.behaviors:
        if not b.text.strip():
            errors.append(f"{b.category}: the text is empty")
        elif len(b.text) > MAX_TEXT:
            errors.append(f"{b.category}: the text is longer than {MAX_TEXT} characters")
        if form_word_findings(b.text):
            errors.append(
                f"{b.category}: form words in the text; say what happens, not how it looks"
            )
        for ref in b.basis:
            if ref not in known:
                errors.append(f"{b.category}: basis {ref!r} is not an id of this call")
        if not b.basis and not b.assumed:
            errors.append(f"{b.category}: no basis, so it must be marked assumed")
    side = [i for i in inp.integrations if i.provider_side]
    for b in out.behaviors:
        provider = b.category in ("interrupted_external_flow", "pending_timeouts")
        if provider and side and not any(i.name.lower() in b.text.lower() for i in side):
            errors.append(f"{b.category}: name the integration it is about ({side[0].name})")
    return errors
