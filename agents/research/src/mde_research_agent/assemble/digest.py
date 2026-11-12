"""Lane digests: compact, table-style text rendered by CODE for each Spec lane.

A digest lets a model (or a person) read one lane's slice and use it at once: the top norms with how common they
are and how recent, the usual screens, entity field tables, journeys with exceptions, provider comparison tables,
design conventions per aspect, formats and sample-data hints, and what needs confirming. It is built only from the
items (every number, name and phrase comes from them: RV-05), never from a model, and it never contains quotes.
Sections are in priority order; when the character budget (soft tokens x 4) is used up the rest is summarised as
"(+N more)".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from mde_research_agent.assemble.items import BuiltItem
from mde_research_agent.domain.research import Confirm, DefaultProposed, Gap, Norm

LEVEL_ORDER = {"core": 0, "common": 1, "emerging": 2, "advanced": 3, "limited_evidence": 4}
CHARS_PER_TOKEN = 4


def _join(values: Iterable[object], limit: int = 8) -> str:
    items = [str(v) for v in values if str(v).strip()]
    more = f" (+{len(items) - limit})" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def _date(b: BuiltItem) -> str:
    r = b.item.recency
    return r.newest_source.strftime("%Y-%m") if r and r.newest_source else "n/a"


def _flags(b: BuiltItem) -> str:
    keep = [
        f
        for f in b.item.flags
        if f
        in (
            "single-source",
            "conflict",
            "stale",
            "needs-confirm",
            "legal-adjacent",
            "unchecked",
            "limited_evidence",
        )
    ]
    return f" [{', '.join(keep)}]" if keep else ""


class _Doc:
    def __init__(self, cap_chars: int) -> None:
        self.cap = cap_chars
        self.lines: list[str] = []
        self.used = 0
        self.hidden = 0

    def section(self, title: str, rows: list[str]) -> None:
        if not rows:
            return
        header = f"## {title}"
        if self.used + len(header) > self.cap:
            self.hidden += len(rows)
            return
        self.lines.append(header)
        self.used += len(header) + 1
        for i, row in enumerate(rows):
            if self.used + len(row) + 1 > self.cap:
                self.hidden += len(rows) - i
                break
            self.lines.append(row)
            self.used += len(row) + 1

    def text(self) -> str:
        if self.hidden:
            self.lines.append(f"(+{self.hidden} more lines not shown)")
        return "\n".join(self.lines)


def _norm_rows(norms: list[Norm], area: str, limit: int = 40) -> list[str]:
    rows = sorted(
        (n for n in norms if n.area == area),
        key=lambda n: (LEVEL_ORDER[n.prevalence.level], -n.prevalence.seen_in, n.key),
    )
    out = []
    for n in rows[:limit]:
        when = n.recency.newest_source.strftime("%Y-%m") if n.recency.newest_source else "n/a"
        brief = f" -> {n.matches_brief}" if n.matches_brief else " (not in Brief)"
        out.append(
            f"{n.prevalence.level:<16} {n.prevalence.seen_in}/{n.prevalence.of} | {n.label} | newest {when}{brief}"
        )
    return out


def _by_kind(items: list[BuiltItem], kind: str) -> list[BuiltItem]:
    return [b for b in items if b.item.kind == kind]


def blueprint_digest(items: list[BuiltItem], norms: list[Norm], cap: int) -> str:
    d = _Doc(cap)
    d.section(
        "FEATURES BY COMMONNESS (level | seen in n of N comparable apps | feature | newest source | Brief match)",
        _norm_rows(norms, "feature"),
    )
    d.section(
        "COMPARABLE APPS",
        [
            f"{b.item.data.get('name')} ({b.item.data.get('kind')}, {b.item.data.get('region') or 'region n/a'}): pricing {b.item.data.get('pricing_model') or 'n/a'} | "
            f"{len(b.item.data.get('features', []))} features: {_join((f.get('feature_key') for f in b.item.data.get('features', [])), 6)} | {_date(b)}"
            for b in _by_kind(items, "competitor")[:12]
        ],
    )
    d.section(
        "TYPICAL SCREENS (level | seen in n of N sources | screen)", _norm_rows(norms, "screen")
    )
    d.section(
        "SCREEN PURPOSES",
        [
            f"{b.item.data.get('screen_key')}: {b.item.data.get('purpose')} [{b.item.data.get('role') or 'any role'}]"
            for b in _by_kind(items, "screen_pattern")[:20]
        ],
    )
    d.section(
        "NAVIGATION",
        [
            f"{b.item.data.get('pattern')}: {b.item.data.get('when_used')} | items: {_join(b.item.data.get('typical_items', []), 6)}"
            for b in _by_kind(items, "navigation_pattern")
        ],
    )
    d.section(
        "ROLES",
        [
            f"{b.item.data.get('role')}: {_join(b.item.data.get('typical_tasks', []), 6)} | access: {_join(b.item.data.get('typical_permissions', []), 4)}"
            for b in _by_kind(items, "role_pattern")
        ],
    )
    d.section(
        "JOURNEYS (steps > ... | alternates | exceptions)",
        [
            f"{b.item.data.get('name')} [{b.item.data.get('role') or 'any'}]: {' > '.join(b.item.data.get('steps', [])[:10])}"
            f" | alt: {_join(b.item.data.get('alternates', []), 3)} | exc: {_join(b.item.data.get('exceptions', []), 3)}{_flags(b)}"
            for b in _by_kind(items, "workflow")[:14]
        ],
    )
    d.section(
        "ENTITIES (field:type!required{values})",
        [
            f"{b.item.data.get('entity')}: "
            + ", ".join(
                f"{f.get('name')}:{f.get('type')}{'!' if f.get('required') else ''}{'{' + str(f.get('values')) + '}' if f.get('values') else ''}"
                for f in b.item.data.get("fields", [])[:14]
            )
            + (
                f" | relations: {_join(b.item.data.get('relations', []), 4)}"
                if b.item.data.get("relations")
                else ""
            )
            for b in _by_kind(items, "entity_pattern")[:10]
        ],
    )
    d.section(
        "LIFECYCLES",
        [
            f"{b.item.data.get('entity')}: {' > '.join(b.item.data.get('states', []))} | transitions: "
            + _join(
                (
                    f"{t.get('from_state')}->{t.get('to_state')} ({t.get('trigger')})"
                    for t in b.item.data.get("transitions", [])
                ),
                5,
            )
            for b in _by_kind(items, "lifecycle")[:8]
        ],
    )
    rules = _by_kind(items, "rule") + _by_kind(items, "compliance_note")
    d.section(
        "RULES (statement | value unit | authority)",
        [
            (
                f"{b.item.data.get('statement')} | {b.item.data.get('value') or ''} {b.item.data.get('unit') or ''} | {b.item.data.get('authority') or 'n/a'} | {_date(b)}{_flags(b)}"
                if b.item.kind == "rule"
                else f"{b.item.data.get('topic')}: {b.item.data.get('summary')} | {b.item.data.get('authority') or 'n/a'} | {_date(b)}{_flags(b)}"
            )
            for b in rules[:24]
        ],
    )
    d.section(
        "TRENDS",
        [
            f"{b.item.data.get('topic')}: {b.item.data.get('what_changed')} ({b.item.data.get('direction')}, since {b.item.data.get('since') or 'n/a'})"
            for b in _by_kind(items, "trend")[:8]
        ],
    )
    return d.text()


def integrations_digest(items: list[BuiltItem], norms: list[Norm], cap: int) -> str:
    d = _Doc(cap)
    by_need: dict[str, list[BuiltItem]] = defaultdict(list)
    for b in _by_kind(items, "service_option"):
        by_need[str(b.item.data.get("need", ""))].append(b)
    for need in sorted(by_need):
        rows = [
            f"{b.item.data.get('provider')} | auth: {b.item.data.get('auth_method') or 'n/a'} | events: {_join(b.item.data.get('endpoints_or_events', []), 4)} | "
            f"sandbox: {b.item.data.get('sandbox') or 'n/a'} | limits: {b.item.data.get('limits') or 'n/a'} | fees: {b.item.data.get('fees') or 'n/a'} | "
            f"status: {b.item.data.get('current_status') or 'n/a'} | fit: {b.item.data.get('region_fit') or 'n/a'} | {_date(b)}{_flags(b)}"
            for b in sorted(by_need[need], key=lambda x: x.item.id)
        ]
        d.section(f"PROVIDERS FOR {need.upper()}", rows)
    facts: dict[str, list[BuiltItem]] = defaultdict(list)
    for b in _by_kind(items, "provider_fact"):
        facts[str(b.item.data.get("need", "")).casefold()].append(b)
    for need in sorted(facts):
        d.section(
            f"FACTS ABOUT PROVIDERS FOR {need.upper()} (provider | field | value | evidence)",
            [
                f"{b.item.data.get('provider')} | {b.item.data.get('field')} | {b.item.data.get('value')} | {b.item.evidence}{_flags(b)}"
                for b in sorted(
                    facts[need],
                    key=lambda x: (
                        str(x.item.data.get("provider")),
                        str(x.item.data.get("field")),
                        x.item.id,
                    ),
                )
            ],
        )
    d.section(
        "PLATFORM RULES (capability | platform | rule | effect on the build | evidence)",
        [
            f"{b.item.data.get('capability')} | {b.item.data.get('platform') or 'platform not stated'} | {b.item.data.get('rule')} | "
            f"{b.item.data.get('effect_on_build') or 'effect not stated'} | {b.item.evidence}{_flags(b)}"
            for b in sorted(
                _by_kind(items, "platform_constraint"),
                key=lambda x: (str(x.item.data.get("capability")), x.item.id),
            )
        ],
    )
    d.section(
        "PORTALS AND COMPLIANCE",
        [
            f"{b.item.data.get('topic')}: {b.item.data.get('summary')} | {b.item.data.get('authority') or 'n/a'}{_flags(b)}"
            for b in _by_kind(items, "compliance_note")[:10]
        ],
    )
    return d.text()


def design_digest(items: list[BuiltItem], norms: list[Norm], cap: int) -> str:
    d = _Doc(cap)
    by_aspect: dict[str, list[BuiltItem]] = defaultdict(list)
    for b in _by_kind(items, "design_convention"):
        by_aspect[str(b.item.data.get("aspect"))].append(b)
    for aspect in sorted(by_aspect):
        d.section(
            f"CONVENTIONS: {aspect.upper()}",
            [
                f"{b.item.data.get('convention')} | e.g. {_join(b.item.data.get('examples', []), 3)} | "
                + (
                    f"seen in {b.item.prevalence.seen_in}/{b.item.prevalence.of} ({b.item.prevalence.level})"
                    if b.item.prevalence
                    else "evidence n/a"
                )
                + f" | {_date(b)}"
                for b in by_aspect[aspect]
            ],
        )
    d.section(
        "SCRIPT AND FONT NEEDS",
        [
            f"{b.item.data.get('script')} ({_join(b.item.data.get('languages', []), 3)}, {b.item.data.get('direction')}): {b.item.data.get('font_notes') or ''} {b.item.data.get('text_expansion') or ''}".strip()
            for b in _by_kind(items, "script_need")
        ],
    )
    d.section(
        "FORMATS",
        [
            f"{b.item.data.get('kind')}: {b.item.data.get('pattern')} e.g. {b.item.data.get('example') or 'n/a'}"
            for b in _by_kind(items, "local_format")[:8]
        ],
    )
    d.section(
        "TRENDS",
        [
            f"{b.item.data.get('topic')}: {b.item.data.get('what_changed')} ({b.item.data.get('direction')})"
            for b in _by_kind(items, "trend")[:8]
        ],
    )
    return d.text()


def screens_digest(items: list[BuiltItem], norms: list[Norm], cap: int) -> str:
    d = _Doc(cap)
    d.section(
        "SCREEN PATTERNS (sections | components | actions | states | validations)",
        [
            f"{b.item.data.get('screen_key')} [{b.item.data.get('role') or 'any'}]: {b.item.data.get('purpose')} | sections: {_join(b.item.data.get('sections', []), 8)} | "
            f"components: {_join(b.item.data.get('key_components', []), 6)} | actions: {_join(b.item.data.get('actions', []), 6)} | "
            f"states: {_join((f'{s.get("state")}={s.get("content")}' for s in b.item.data.get('states', [])), 5)} | validations: {_join(b.item.data.get('validations', []), 4)}{_flags(b)}"
            for b in _by_kind(items, "screen_pattern")[:20]
        ],
    )
    d.section(
        "JOURNEY EXCEPTIONS AND EMPTY/ERROR/OFFLINE CASES",
        [
            f"{b.item.data.get('name')}: exc {_join(b.item.data.get('exceptions', []), 3)} | cases {_join(b.item.data.get('empty_error_offline_cases', []), 3)}"
            for b in _by_kind(items, "workflow")[:12]
        ],
    )
    d.section(
        "PERMISSIONS AND STORE RULES THE SCREENS MUST CARRY",
        [
            f"{b.item.data.get('capability')} | {b.item.data.get('rule')} | {b.item.data.get('effect_on_build') or 'effect not stated'}"
            for b in _by_kind(items, "platform_constraint")[:12]
        ],
    )
    d.section(
        "RULES THAT SHAPE SCREENS",
        [
            f"{b.item.data.get('statement')} | {b.item.data.get('value') or ''} {b.item.data.get('unit') or ''}"
            for b in _by_kind(items, "rule")[:10]
        ],
    )
    return d.text()


def mock_digest(items: list[BuiltItem], norms: list[Norm], cap: int) -> str:
    d = _Doc(cap)
    d.section(
        "FORMATS (kind | pattern | example | validation)",
        [
            f"{b.item.data.get('kind')} | {b.item.data.get('pattern')} | {b.item.data.get('example') or 'n/a'} | {b.item.data.get('validation') or 'n/a'}"
            for b in _by_kind(items, "local_format")
        ],
    )
    d.section(
        "SAMPLE DATA (kind | range | examples)",
        [
            f"{b.item.data.get('kind')} | {b.item.data.get('value_range') or 'n/a'} | {_join(b.item.data.get('examples', []), 8)}"
            for b in _by_kind(items, "data_hint")
        ],
    )
    d.section(
        "ENTITY SHAPES FOR MOCKS",
        [
            f"{b.item.data.get('entity')}: {_join((f.get('name') for f in b.item.data.get('fields', [])), 10)}"
            for b in _by_kind(items, "entity_pattern")[:8]
        ],
    )
    return d.text()


def verify_digest(
    items: list[BuiltItem],
    confirm: list[Confirm],
    defaults: list[DefaultProposed],
    gaps: list[Gap],
    cap: int,
) -> str:
    d = _Doc(cap)
    by_id = {b.item.id: b for b in items}
    d.section(
        "CONFIRM WITH THE OWNER",
        [
            f"{c.item_id}: {by_id[c.item_id].item.label if c.item_id in by_id else ''} | {c.why}"
            for c in confirm
        ],
    )
    d.section(
        "DEFAULTS PROPOSED (builder need | items)",
        [f"{x.need}: {x.default} ({len(x.item_ids)} items)" for x in defaults],
    )
    d.section(
        "COMPLIANCE NOTES (information, not advice)",
        [
            f"{b.item.data.get('topic')}: {b.item.data.get('summary')}"
            for b in _by_kind(items, "compliance_note")[:10]
        ],
    )
    d.section(
        "GAPS (not found or not run)", [f"{g.target_id} {g.reason}: {g.label}" for g in gaps[:20]]
    )
    return d.text()
