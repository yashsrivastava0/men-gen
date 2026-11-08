"""Plan summary: the deterministic parts (facts for C11, the template fallback, the PRD view).

``AppSpec.summary`` is the plain-language PRD view of spec section 1: a lead sentence (the C11
``plan_summary`` text, or the template sentence when C11 is unavailable) followed by short lines
built from the spec: who uses it, the journeys, the look, what is mocked, what was assumed, what
research added and what was left for phase two. Everything except the C11 sentence is code.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from mde_contracts.calls import C11Input
from mde_contracts.spec import AppSpec, BacklogItem
from mde_core.redact import find_secrets
from mde_spec_builder.design import get_theme
from mde_spec_builder.rules import Rules, load_rules

MAX_LEAD_CHARS = 400
MAX_FACTS_FLOWS = 6
MAX_LIST = 6
_SPACE = re.compile(r"\s+")


def _join(items: Sequence[str], limit: int = MAX_LIST, more: str | None = None) -> str:
    shown = list(items[:limit])
    text = ", ".join(shown)
    if len(items) > limit:
        rest = len(items) - limit
        text += f" {more.format(n=rest) if more else f'and {rest} more'}"
    return text


def _phase_two(spec: AppSpec) -> list[BacklogItem]:
    """The backlog items that wait for a later phase (not the screens the person took out)."""
    removed = load_rules().change_ops.removed_reason
    return [b for b in spec.backlog if b.reason != removed]


def flow_names(spec: AppSpec) -> list[str]:
    return [f.name for f in spec.flows]


def look_label(spec: AppSpec) -> str:
    try:
        return get_theme(spec.design.direction).label
    except KeyError:
        return spec.design.direction


def template_sentence(spec: AppSpec, rules: Rules | None = None) -> str:
    """The deterministic stand-in for C11's ``plan_summary``."""
    rules = rules or load_rules()
    return rules.summary.fallback_sentence.format(
        name=spec.app.name, screens=len(spec.screens), flows=len(spec.flows)
    )


def summary_facts(spec: AppSpec) -> list[str]:
    """Plain facts handed to C11 (never the file itself): small, ordered, no ids."""
    facts = [f"App: {spec.app.name}: {_oneline(spec.app.purpose)}"]
    facts.append(f"Audience: {_oneline(spec.app.audience)}")
    facts.append(f"{len(spec.screens)} screens in {len(spec.flows)} journeys")
    names = flow_names(spec)[:MAX_FACTS_FLOWS]
    if names:
        facts.append("Journeys: " + ", ".join(names))
    facts.append("Roles: " + ", ".join(r.name for r in spec.roles.roles))
    facts.append(f"Look: {look_label(spec)}")
    mocked = [i.name for i in spec.integrations.items if i.mode == "mock"]
    if mocked:
        facts.append("Mocked for now: " + ", ".join(mocked))
    assumed = [a.label for a in spec.assumptions.assumptions if a.slot_id]
    if assumed:
        facts.append("Assumed: " + ", ".join(assumed))
    added = [a.title for a in spec.assumptions.research_additions if a.included]
    if added:
        facts.append("Added from research: " + ", ".join(added))
    later = _phase_two(spec)
    if later:
        facts.append("Left for phase two: " + ", ".join(b.title for b in later))
    return facts


def summary_input(spec: AppSpec, max_words: int = 60) -> C11Input:
    return C11Input(
        kind="plan_summary",
        language=spec.header.language,
        app_name=spec.app.name,
        facts=summary_facts(spec),
        max_words=max_words,
    )


def _oneline(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def clean_lead(text: str | None) -> str | None:
    """A usable one-paragraph lead from model text, or None (empty, a secret, or too long)."""
    if text is None:
        return None
    cleaned = _oneline(text)
    if not cleaned or len(cleaned) > MAX_LEAD_CHARS or find_secrets(cleaned):
        return None
    return cleaned


def prd_lines(spec: AppSpec, rules: Rules | None = None) -> list[str]:
    """The deterministic lines of the PRD view."""
    rules = rules or load_rules()
    t = rules.summary
    lines = [
        t.users.format(users=_oneline(spec.app.audience).rstrip(".")),
        t.journeys.format(
            screens=len(spec.screens),
            flows=len(spec.flows),
            flow_names=_join(flow_names(spec), more=t.more_flows) or "none yet",
        ),
        t.look.format(look=look_label(spec).lower(), nav=spec.design.nav_style or "standard"),
    ]
    mocked = [i.name for i in spec.integrations.items if i.mode == "mock"]
    lines.append(t.mocked.format(mocked=_join(mocked)) if mocked else t.nothing_mocked)
    assumed = [a.label for a in spec.assumptions.assumptions if a.slot_id]
    if assumed:
        lines.append(t.assumed.format(assumed=_join(assumed)))
    added = [a.title for a in spec.assumptions.research_additions if a.included]
    if added:
        lines.append(t.research.format(added=_join(added)))
    later = _phase_two(spec)
    if later:
        lines.append(t.backlog.format(backlog=_join([b.title for b in later])))
    return lines


def compose_summary(spec: AppSpec, lead: str | None = None, rules: Rules | None = None) -> str:
    """``AppSpec.summary``: the lead, then the PRD lines as a short list."""
    rules = rules or load_rules()
    head = lead or rules.summary.lead.format(name=spec.app.name, purpose=_oneline(spec.app.purpose))
    body = "\n".join(f"- {line}" for line in prd_lines(spec, rules))
    return f"{head}\n\n{body}"
