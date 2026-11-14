"""Plain-text views of the graph, the Brief and the self-check that the model calls read (all by code).

Everything here is DATA for a model, never an instruction. The gateway wraps each list element in its own untrusted
block and caps a block at a fixed size, so long views are cut into chunks of whole lines (`chunk_lines`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mde_spec_core.graph import SpecGraph
from mde_spec_core.render import bound_fields
from mde_spec_core.verify_port import BriefView

CHUNK_CHARS = 15_000  # below the gateway's untrusted block cap, so nothing is cut mid-line
MAX_LINES_PER_KIND = 400  # a huge ERP never floods a call: the rest is counted, not listed

ID_RE = re.compile(r"\b[A-Z]{1,6}-S?\d{1,6}\b")
FREEDOM_CODES = frozenset({"V-24", "V-25", "V-37", "V-38", "V-42", "V-43"})


def chunk_lines(lines: Iterable[str], limit: int = CHUNK_CHARS) -> list[str]:
    """Whole lines joined into blocks of at most `limit` characters (an over-long line is cut on its own)."""
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for raw in lines:
        line = raw if len(raw) <= limit else raw[: limit - 1] + "…"
        if cur and size + len(line) + 1 > limit:
            out.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def lines_of(chunks: Iterable[str]) -> list[str]:
    return [ln for c in chunks for ln in c.split("\n") if ln.strip()]


def ids_in(text: str) -> set[str]:
    return set(ID_RE.findall(text))


def _capped(rows: list[str], kind: str) -> list[str]:
    if len(rows) <= MAX_LINES_PER_KIND:
        return rows
    return [
        *rows[:MAX_LINES_PER_KIND],
        f"... {len(rows) - MAX_LINES_PER_KIND} more {kind} not listed",
    ]


def _clip(text: str, n: int = 160) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


def _prov(node: Any) -> str:
    kinds = sorted({p.kind for p in getattr(node, "provenance", [])})
    return "+".join(kinds) or "-"


def structure_digest(graph: SpecGraph) -> list[str]:
    """The frozen structure in plain lines: apps, roles, modules, entities and their statuses, screens with their
    exits and flows. Only live (`in`) nodes; `later` and `out` are named once so the critic knows they are deferred."""
    m = graph.meta
    out = [f"program {m.program_id or '-'} profile {m.profile} languages {','.join(m.languages)}"]
    for aid in sorted(graph.apps):
        a = graph.apps[aid]
        out.append(
            f"APP {aid} {a.name} surface={a.surface} roles={','.join(sorted(a.role_ids))} modules={len(a.module_ids)}"
        )
    out += _capped(
        [
            f"ROLE {r.id} {r.name} apps={','.join(sorted(r.app_ids))} home={r.home_screen or '-'}"
            for r in sorted(graph.roles.values(), key=lambda x: x.id)
        ],
        "roles",
    )
    out += _capped(
        [
            f"MODULE {x.id} {x.name} apps={','.join(sorted(x.app_ids))} shared={x.shared} "
            f"screens={len(x.screen_ids)} entities={','.join(sorted(x.entity_ids))}"
            for x in sorted(graph.modules.values(), key=lambda x: x.id)
        ],
        "modules",
    )
    out += _capped(
        [
            f"ENTITY {e.id} {e.name} owner={e.owner_module or '-'} status={e.status} "
            f"states={'>'.join(e.lifecycle.states) or '-'} access="
            + ",".join(
                f"{r.role_id}:{'/'.join(r.actions)}"
                for r in sorted(e.access, key=lambda y: y.role_id)
            )
            for e in sorted(graph.entities.values(), key=lambda x: x.id)
        ],
        "entities",
    )
    out += _capped(
        [
            f"FEATURE {f.id} {f.name} status={f.status} priority={f.user_priority} tier={f.tier} "
            f"evidenced={f.user_evidenced} prov={_prov(f)} apps={','.join(sorted(f.app_ids))}"
            for f in sorted(graph.features.values(), key=lambda x: x.id)
        ],
        "features",
    )
    screens = []
    for sid in sorted(graph.screens):
        s = graph.screens[sid]
        b = bound_fields(s)
        ops = b.pop("<ops>", [])
        goes = sorted(
            {st.target for a in s.actions for st in a.effect if st.verb == "go" and st.target}
        )
        screens.append(
            f"SCREEN {sid} {s.name} app={s.app_id} module={s.module_id or '-'} type={s.screen_type} status={s.status} "
            f"roles={','.join(sorted(s.role_ids))} shows={','.join(sorted(b))} ops={','.join(ops)} "
            f"exits={','.join(sorted(set(s.leads_to) | set(goes)))} tier={s.tier} prov={_prov(s)} "
            f"stories={','.join(sorted(s.stories))} claims={','.join(sorted(s.claims_used))} "
            f"purpose={_clip(s.purpose)}"
        )
    out += _capped(screens, "screens")
    out += _capped(
        [
            f"FLOW {f.id} {f.name} variant={f.variant} primary={f.primary} apps={','.join(sorted(f.app_ids))} "
            f"stages={'>'.join(','.join(st.screen_ids) for st in f.stages)} unhappy={len(f.unhappy_paths)}"
            for f in sorted(graph.flows.values(), key=lambda x: x.id)
        ],
        "flows",
    )
    out += _capped(
        [
            f"STORY {t.id} role={t.role_id} voice={t.voice} tier={t.tier} assumed={t.assumed} "
            f"features={','.join(sorted(t.feature_ids))} policy={t.policy_id or '-'} "
            f"screens={','.join(sorted(t.screen_ids))} accept={len(t.acceptance)} "
            f"want={_clip(t.want)} so_that={_clip(t.so_that)}"
            for t in sorted(graph.stories.values(), key=lambda x: x.id)
        ],
        "stories",
    )
    out += _capped(
        [
            f"RULE {r.id} {r.subject}: {r.statement}"
            + (f" value={r.value} {r.unit or ''}".rstrip() if r.value else "")
            for r in sorted(graph.rules.values(), key=lambda x: x.id)
        ],
        "rules",
    )
    return out


def brief_text(brief: BriefView) -> str:
    parts = [f"Summary: {brief.summary}"] if brief.summary else []
    parts += [f"Quote: {q}" for q in brief.quotes]
    return "\n".join(parts)


def fact_lines(brief: BriefView) -> list[str]:
    return [
        f"FACT {f.id} -> {f.node}" + (f".{f.field}" if f.field else "") + f": {f.text or f.value}"
        for f in brief.locked_facts
    ]


def coverage_lines(check: Any) -> list[str]:
    """The coverage matrix as lines: one summary line per kind, then every gap."""
    cov = getattr(check, "coverage", None)
    if cov is None:
        return []
    out = [
        f"COVERAGE entities={len(cov.entities)} roles={len(cov.roles)} flows={len(cov.flows)} "
        f"features={len(cov.features)} gaps={len(cov.gaps)}"
    ]
    out += [f"GAP {g.id}: {g.problem} [{g.kind}]" for g in cov.gaps]
    return out


def freedom_lines(check: Any) -> list[str]:
    """The freedom checks (closed vocabularies, form words, mobile fit, opportunity discipline) as lines."""
    found = getattr(check, "findings", None) or []
    return [
        f"{f.code} {f.severity} {f.where}: {f.why}"
        for f in found
        if f.code in FREEDOM_CODES and f.severity != "info"
    ]


def confirm_lines(confirm: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"{int(c.get('rank', i))}|{c.get('category', 'other')}|{c.get('id', '')}|{c.get('text', '')}"
        for i, c in enumerate(confirm, start=1)
    ]


def parse_confirm_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in lines:
        parts = ln.split("|", 3)
        if len(parts) == 4 and parts[0].strip().isdigit():
            out.append(
                {"rank": int(parts[0]), "category": parts[1], "id": parts[2], "text": parts[3]}
            )
    return out


def defect_lines(defects: Iterable[Any]) -> list[str]:
    """Defects already found by code, so the model does not repeat them."""
    return [
        f"{d.code} {d.severity} {d.node}{('.' + d.field) if d.field else ''}: {d.reason}"
        for d in defects
    ]


_ACCEPT_RE = re.compile(r"^\s*-\s+(GIVEN\b.+)$")
_READ_SPLIT = re.compile(r"^=== (?P<path>.+?) ===$", re.M)


def criteria_from_reads(reads: Mapping[str, str]) -> list[str]:
    """The acceptance criteria written in the task card's screen files, de-duplicated, in file order."""
    seen: dict[str, None] = {}
    for path in sorted(reads):
        if not path.startswith("screens/"):
            continue
        for line in reads[path].splitlines():
            m = _ACCEPT_RE.match(line)
            if m:
                seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def read_blocks(reads: Mapping[str, str]) -> list[str]:
    """One block per read file, headed with its path (so the model and the mock can tell the files apart)."""
    return [f"=== {p} ===\n{reads[p]}" for p in sorted(reads)]


def split_read_blocks(blocks: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in blocks:
        head, _, body = block.partition("\n")
        m = _READ_SPLIT.match(head)
        if m:
            out[m.group("path")] = body
    return out
