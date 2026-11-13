"""The glossary and the delight block, built by code at the Freeze (doc 18 5, FN-33).

The glossary lists entity, role and status names, the key verbs and the direction's voice line, within
`budgets.glossary_max` tokens; it leads the prefix of every Screens and Locale call so parallel calls say Order and
Booking the same way. The delight block collects the COULD entries of the primary journeys within
`budgets.delight_block_max` tokens. Both are deterministic and use the renderer's token counter.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .canon import canonical_json
from .config import SpecConfig, default_config
from .graph import Direction, SpecGraph
from .render import TokenCounter

_KLASS_RANK = {"primary": 0, "secondary": 1, "join": 2, "log": 3}
_MAX_VERBS = 12
_MAX_ROLES = 12
_MIN_PRIMARY = 5  # primary entity names are the last thing to go


def _counter(cfg: SpecConfig) -> TokenCounter:
    return TokenCounter(cfg.budgets.token_factor_chars_per_token)


def _scope(graph: SpecGraph) -> list[Any]:
    return [e for _, e in sorted(graph.entities.items()) if not e.later and e.status == "in"]


def _verbs(graph: SpecGraph) -> list[str]:
    counts: Counter[str] = Counter()
    for s in graph.screens.values():
        if s.status != "in":
            continue
        for a in s.actions:
            counts[a.intent] += 1
    return [v for v, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:_MAX_VERBS]


def build_glossary(
    graph: SpecGraph, direction: Direction | None = None, *, config: SpecConfig | None = None
) -> dict[str, Any]:
    """Names and key verbs plus the voice line, at most `glossary_max` tokens (counted on `glossary_text`, the part
    that enters the prefix; `dropped` and `tokens` are bookkeeping). When over the cap, items are dropped from the
    least important end (statuses of minor entities, verbs, terms, minor entity names, roles, primary statuses,
    last of all primary entity names beyond five) and listed in `dropped`. The voice line is only shortened as a
    last resort."""
    cfg = config or default_config()
    count = _counter(cfg).count
    cap = cfg.budgets.glossary_max
    ents = sorted(_scope(graph), key=lambda e: (_KLASS_RANK.get(e.klass, 9), e.id))
    roles = {r.id: r.name for _, r in sorted(graph.roles.items()) if r.status == "in"}
    g: dict[str, Any] = {
        "version": 1,
        "voice": (direction.voice_line if direction else "") or "",
        "entities": {e.id: {"term": e.name, "statuses": list(e.lifecycle.states)} for e in ents},
        "roles": dict(list(roles.items())[:_MAX_ROLES]),
        "verbs": _verbs(graph),
        "terms": dict(sorted(graph.glossary.items())),
        "language": graph.meta.source_language,
    }
    dropped: list[str] = []

    def size() -> int:
        return count(canonical_json(g))

    primary_ids = {e.id for e in ents if e.klass == "primary"}
    non_primary = [e.id for e in reversed(ents) if e.klass != "primary"]
    steps: list[tuple[str, str | None]] = [("statuses", i) for i in non_primary]
    steps += [("verbs", None)] * len(g["verbs"])
    steps += [("terms", None)]
    steps += [("entity", i) for i in non_primary]
    steps += [("role", r) for r in reversed(list(g["roles"]))]
    steps += [("statuses", e.id) for e in reversed(ents) if e.klass == "primary"]
    steps += [("entity", e.id) for e in reversed(ents) if e.klass == "primary"]
    for kind, key in steps:
        if size() <= cap:
            break
        if kind == "statuses" and key in g["entities"] and g["entities"][key]["statuses"]:
            g["entities"][key]["statuses"] = []
            dropped.append(f"statuses of {key}")
        elif kind == "verbs" and g["verbs"]:
            dropped.append(f"verb {g['verbs'].pop()}")
        elif kind == "terms" and g["terms"]:
            g["terms"] = {}
            dropped.append("terms")
        elif kind == "entity" and key in g["entities"]:
            if size() > cap and (key not in primary_ids or len(g["entities"]) > _MIN_PRIMARY):
                del g["entities"][key]
                dropped.append(f"entity {key}")
        elif kind == "role" and key in g["roles"]:
            del g["roles"][key]
            dropped.append(f"role {key}")
    words = g["voice"].split()
    while size() > cap and words:  # last resort: shorten the voice line
        words.pop()
        g["voice"] = " ".join(words)
        if "voice line shortened" not in dropped:
            dropped.append("voice line shortened")
    return {**g, "dropped": dropped, "tokens": size()}


def glossary_text(glossary: dict[str, Any]) -> str:
    """What goes into the shared prefix: the glossary without its own bookkeeping (`dropped`, `tokens`)."""
    return canonical_json({k: v for k, v in glossary.items() if k not in ("dropped", "tokens")})


def delight_block(graph: SpecGraph, *, config: SpecConfig | None = None) -> str:
    """The COULD entries of the primary journeys, one line each, at most `delight_block_max` tokens. Entries of a
    tier C capability (not supportable) are left out. Empty text when there are none."""
    cfg = config or default_config()
    count = _counter(cfg).count
    seen: set[str] = set()
    entries = []
    for fid in sorted(graph.flows):
        flow = graph.flows[fid]
        if not flow.primary or flow.status != "in":
            continue
        for stage in sorted(flow.stages, key=lambda s: s.n):
            for sid in stage.screen_ids:
                scr = graph.screens.get(sid)
                if scr is None:
                    continue
                for c in sorted(scr.could, key=lambda c: c.id):
                    if c.id in seen or c.tier == "C":
                        continue
                    seen.add(c.id)
                    entries.append(c)
    if not entries:
        return ""
    lines = ["Delight guidance (COULD, never required):"]
    cap = cfg.budgets.delight_block_max
    for c in entries:
        line = f"- {c.id} ({c.kind}) {c.when}: {c.effect}"
        if count("\n".join([*lines, line])) > cap:
            break
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""
