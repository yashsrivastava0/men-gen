"""Mock handler of BEHAV: deterministic. A mixed category extends the library text with this app's facts; an app-specific
one is written from the integration records, the entities and the languages (templates are DATA: data/lane.yaml)."""

from __future__ import annotations

import re
from collections import defaultdict

from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut, BehOut, CatIn
from mde_spec_agent.lanes.screens.common import lane_rules

_ID = re.compile(r"\b([A-Z]{2,5}-S?\d{1,6})\b")


def ids_of(lines: list[str]) -> list[str]:
    return [m for line in lines for m in _ID.findall(line.split(" ", 1)[0])]


def render(cat: CatIn, inp: BehavIn) -> tuple[str, list[str]]:
    tmpl = lane_rules().behav_mock
    roles = ", ".join(r.split(" ", 1)[-1] for r in inp.roles) or "the app's roles"
    ents = ", ".join(e.split(" ", 1)[-1].split(" (")[0] for e in inp.entities[:8]) or "none yet"
    base = {
        "library": cat.library_text,
        "roles": roles,
        "entities": ents,
        "languages": ", ".join(inp.languages),
        "what": cat.what,
        "flow": "",
        "interruption": "",
        "pending_timeout": "",
    }
    basis: list[str] = []
    if cat.name in ("interrupted_external_flow", "pending_timeouts"):
        parts = []
        for i in inp.integrations:
            if not i.provider_side:
                continue
            fields = {
                **base,
                "flow": i.flow or i.name,
                "interruption": i.interruption,
                "pending_timeout": i.pending_timeout,
            }
            parts.append(
                f"{i.name}: " + tmpl[cat.name].format_map(defaultdict(str, fields)).strip()
            )
            basis.append(i.id)
        text = " ".join(parts)
    else:
        text = tmpl.get(cat.name, tmpl["default"]).format_map(defaultdict(str, base)).strip()
        if cat.name == "state":
            basis = ids_of(inp.entities[:8])
        elif cat.name == "session":
            basis = ids_of(inp.roles[:3])
    return " ".join(text.split())[:700], basis


def handle(inp: BehavIn) -> BehavOut:
    out: list[BehOut] = []
    for cat in inp.categories:
        text, basis = render(cat, inp)
        out.append(BehOut(category=cat.name, text=text, basis=basis, assumed=not basis))
    return BehavOut(behaviors=out)
