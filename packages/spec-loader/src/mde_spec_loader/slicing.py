"""``slice(step_or_screen)``: the ready-made context bundle for one task, built in tiers (spec 7.2).

| tier | content                                                                  | loaded    |
| ---- | ------------------------------------------------------------------------ | --------- |
| 0    | header front matter, summary line, tone, naming, stack rules             | always    |
| 1    | design and navigation blocks                                             | every run |
| 2    | the task's block(s), the entities, operations, integrations and roles    | per task  |
|      | they reference (carved out of their blocks), kit-component signatures    |           |
| 3    | neighbouring screens                                                     | on demand |

A screen task therefore stays far below 10k estimated tokens whether the app has 12 or 50 screens.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from mde_contracts.spec import AppSpec, EnumDef
from mde_spec_loader.codec import dump_yaml, model_data
from mde_spec_loader.errors import UnknownIdError
from mde_spec_loader.fragments import fenced, item_text
from mde_spec_loader.graph import Refs, is_step_id
from mde_spec_loader.models import Slice, SliceTier
from mde_spec_loader.tokens import estimate_tokens

if TYPE_CHECKING:
    from mde_spec_loader.index import SpecIndex

_TAG_RE: Final = re.compile(r"@spec\s+([A-Za-z0-9_-]+)@v(\d+)")
_TIER1_IDS: Final = ("DESIGN", "NAV")
SUMMARY_LINE_MAX: Final = 300


def format_spec_tag(spec_id: str, version: int) -> str:
    """The header a generated file carries, e.g. ``@spec SCR-014@v7``."""
    return f"@spec {spec_id}@v{version}"


def parse_spec_tag(text: str) -> tuple[str, int] | None:
    """Find a ``@spec <id>@v<version>`` header in ``text``; None if there is none."""
    match = _TAG_RE.search(text)
    return None if match is None else (match.group(1), int(match.group(2)))


def _summary_line(summary: str) -> str:
    for line in summary.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:SUMMARY_LINE_MAX]
    return ""


def _tier0(index: SpecIndex, spec: AppSpec, stack_rules: Sequence[str]) -> str:
    header = spec.header
    app = spec.app
    lines = [
        index.get(["HEADER"])["HEADER"],
        "",
        f"Summary: {_summary_line(spec.summary)}",
        f"Tone: {app.tone}; language: {header.language}; platforms: {', '.join(app.platforms)}",
        f'Naming: app "{app.name}" ({header.app_id}); ids are SCR-###, FLW-###, ENT-Name, '
        f"API-name, ROLE-name, INT-name; every generated file starts with its header "
        f"`@spec <ID>@v{header.spec_version}`.",
        f"Rules: stack profile {header.stack_profile}; never edit APP_SPEC.md and never ask the "
        "user; a gap you cannot resolve goes to raise_decision(question, options, default); "
        "screens marked locked are skipped.",
    ]
    lines.extend(f"Stack rule: {rule}" for rule in stack_rules)
    return "\n".join(lines)


def _enums_fragment(spec: AppSpec, names: Sequence[str]) -> str | None:
    wanted = set(names)
    chosen: list[EnumDef] = [e for e in spec.data.enums if e.name in wanted]
    if not chosen:
        return None
    body = dump_yaml({"enums": [model_data(e) for e in chosen]})
    return fenced("ENUMS", "enums", body)


def _merge_refs(parts: Sequence[Refs]) -> Refs:
    def union(getter: str) -> list[str]:
        merged: set[str] = set()
        for part in parts:
            merged.update(getattr(part, getter))
        return sorted(merged)

    return Refs(
        entities=union("entities"),
        operations=union("operations"),
        integrations=union("integrations"),
        roles=union("roles"),
        screens=union("screens"),
        flows=union("flows"),
        enums=union("enums"),
        research=union("research"),
    )


def build_slice(
    index: SpecIndex,
    target: str,
    *,
    kit_signatures: Mapping[str, Sequence[str]] | None = None,
    stack_rules: Sequence[str] = (),
    tier3: bool = False,
    max_neighbors: int = 6,
) -> Slice:
    """Assemble the tiered bundle for a step (``S0``..``S10``), a screen or a flow."""
    spec = index.spec
    graph = index.graph
    screens = {s.id: s for s in spec.screens}
    flows = {f.id: f for f in spec.flows}
    steps = {s.id: s for s in spec.build.steps}
    version = spec.header.spec_version

    primary: list[str] = []  # ids of the task's own blocks, in order
    locked = False
    kit_lines: list[str] = []
    refs_sources: list[str] = []
    if is_step_id(target):
        step = steps.get(target)
        if step is None:
            raise UnknownIdError(target)
        primary.append(step.id)
        for load_id in step.load:
            if load_id not in primary and load_id not in _TIER1_IDS:
                primary.append(load_id)
        if step.flow is not None and step.flow not in primary:
            primary.append(step.flow)
        for screen_id in step.screens:
            if screen_id not in primary:
                primary.append(screen_id)
    elif target in screens:
        screen = screens[target]
        primary.append(target)
        locked = screen.locked
        kit_lines = list((kit_signatures or {}).get(screen.recipe, ()))
    elif target in flows:
        primary.append(target)
        for screen_id in flows[target].screens:
            if screen_id in screens and screen_id not in primary:
                primary.append(screen_id)
    else:
        raise UnknownIdError(target)

    for pid in primary:
        if pid in screens or pid in flows:
            refs_sources.append(pid)

    # ---------------------------------------------------------------- tiers 0 and 1
    tier0_text = _tier0(index, spec, stack_rules)
    tier1_blocks = index.get(_TIER1_IDS)
    tier1_text = "\n\n".join(tier1_blocks[i] for i in _TIER1_IDS)

    # ---------------------------------------------------------------- tier 2
    included: set[str] = {"HEADER", *_TIER1_IDS}
    primary_texts: list[str] = []
    primary_ids: list[str] = []
    for pid in primary:
        if pid in included:
            continue
        primary_texts.append(index.get([pid])[pid])
        primary_ids.append(pid)
        included.add(pid)

    refs = _merge_refs([graph.refs(pid) for pid in refs_sources])
    ref_texts: list[str] = []
    ref_ids: list[str] = []
    for rid in refs.entities:
        text = item_text(spec, rid)
        if text is not None and rid not in included:
            ref_texts.append(text)
            ref_ids.append(rid)
            included.add(rid)
    enums_text = _enums_fragment(spec, refs.enums)
    if enums_text is not None:
        ref_texts.append(enums_text)
        ref_ids.append("ENUMS")
    for ids in (refs.operations, refs.integrations, refs.roles):
        for rid in ids:
            text = item_text(spec, rid)
            if text is not None and rid not in included:
                ref_texts.append(text)
                ref_ids.append(rid)
                included.add(rid)

    tier2_parts: list[str] = []
    if locked:
        tier2_parts.append(f"LOCKED: {target} was edited by hand; do not regenerate it.")
    tier2_parts.extend(primary_texts)
    if ref_texts:
        tier2_parts.append("### Referenced by this task")
        tier2_parts.extend(ref_texts)
    if kit_lines:
        tier2_parts.append("### Kit components (signatures)")
        tier2_parts.append("\n".join(f"- {line}" for line in kit_lines))
    tier2_text = "\n\n".join(tier2_parts)

    # ---------------------------------------------------------------- tier 3
    tier3_ids: list[str] = []
    tier3_text = ""
    if tier3 and target in screens:
        neighbor_ids = [n for n in graph.neighbors(target) if n not in included][:max_neighbors]
        if neighbor_ids:
            tier3_ids = neighbor_ids
            tier3_text = "\n\n".join(index.get([n])[n] for n in neighbor_ids)

    headings = [
        ("## Tier 0: always", tier0_text),
        ("## Tier 1: design and navigation", tier1_text),
        ("## Tier 2: the task", tier2_text),
    ]
    if tier3_text:
        headings.append(("## Tier 3: neighbouring screens (on demand)", tier3_text))
    tag = format_spec_tag(target, version)
    text = f"<!-- spec slice: {target} v{version} -->\n" + "\n\n".join(
        f"{title}\n\n{body}" for title, body in headings
    )
    tiers = [
        SliceTier(tier=0, block_ids=["HEADER"], token_estimate=estimate_tokens(tier0_text)),
        SliceTier(tier=1, block_ids=list(_TIER1_IDS), token_estimate=estimate_tokens(tier1_text)),
        SliceTier(
            tier=2,
            block_ids=[*primary_ids, *ref_ids],
            token_estimate=estimate_tokens(tier2_text),
        ),
    ]
    if tier3_text:
        tiers.append(
            SliceTier(tier=3, block_ids=tier3_ids, token_estimate=estimate_tokens(tier3_text))
        )
    return Slice(
        id=target,
        spec_tag=tag,
        text=text,
        block_ids=[b for tier in tiers for b in tier.block_ids],
        token_estimate=estimate_tokens(text),
        tiers=tiers,
        locked=locked,
    )
