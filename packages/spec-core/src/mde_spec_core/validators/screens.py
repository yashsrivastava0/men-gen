"""V-13, V-14, V-22, V-24, V-25, V-27, V-37, V-38, V-39: what a screen card must carry, and the freedom checks."""

from __future__ import annotations

import re

from mde_spec_core.graph import Screen
from mde_spec_core.ids import namespaced_copy_key
from mde_spec_core.render import render_closure

from .base import Context, Finding, validator

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_UNITS = re.compile(r"\b\d+\s?(px|pt|dp|rem|em)\b", re.I)


def _free_text(s: Screen) -> list[tuple[str, str]]:
    out = [(f"{s.id}.purpose", s.purpose)]
    out += [(f"{s.id}.{r.key}", r.what) for r in s.regions]
    out += [(f"{s.id}.{a.key}", a.what + " " + a.precondition) for a in s.actions]
    out += [(f"{s.id}.state.{st.kind}", st.message) for st in s.states]
    out += [(f"{s.id}.usually", u.note) for u in s.usually]
    out += [(f"{s.id}.{c.id}", c.when + " " + c.effect) for c in s.could]
    return out


@validator("V-13", "Mandatory states present for each screen type")
def v13(ctx: Context) -> list[Finding]:
    out = []
    for s in ctx.graph.screens.values():
        need = ctx.vocab.required_states.get(s.screen_type, [])
        have = {st.kind for st in s.states}
        for k in need:
            if k not in have:
                out.append(
                    Finding(
                        "V-13",
                        "blocker",
                        s.id,
                        f"missing state {k} for screen type {s.screen_type}",
                        "instantiate it from the state library (R-code, provenance library, tier Default)",
                        "A",
                    )
                )
    return out


@validator("V-14", "Every copy key exists for every locale; no hardcoded strings")
def v14(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    langs = g.meta.languages or [g.meta.source_language]
    for s in g.screens.values():
        for k in s.copy_keys:
            if not k.startswith(namespaced_copy_key(s.id, "")):
                out.append(
                    Finding(
                        "V-14",
                        "blocker",
                        s.id,
                        f"copy key {k} is not namespaced by the screen id",
                        "copy keys are minted by code, namespaced by screen id",
                        "A",
                    )
                )
            for loc in langs:
                if k not in g.locale_copy.get(loc, {}):
                    sev = "warning" if loc in ctx.degraded_locales else "blocker"
                    out.append(
                        Finding(
                            "V-14",
                            sev,
                            s.id,
                            f"copy key {k} missing for locale {loc}",
                            "write the copy (re-ask) or mark the locale degraded",
                            "B",
                        )
                    )
    return out


@validator(
    "V-22", "Every action and input has a label key (and an announcement where the state changes)"
)
def v22(ctx: Context) -> list[Finding]:
    out = []
    for s in ctx.graph.screens.values():
        have = set(s.copy_keys)
        for kind, keys in (
            ("action", [a.key for a in s.actions]),
            ("input", [i.key for i in s.inputs]),
        ):
            for k in keys:
                if namespaced_copy_key(s.id, k) not in have:
                    out.append(
                        Finding(
                            "V-22",
                            "blocker",
                            f"{s.id}.{k}",
                            f"{kind} has no label copy key",
                            "add the label key",
                            "B",
                        )
                    )
    return out


@validator(
    "V-24", "Freedom schema: closed vocabularies, tier ceilings, no forbidden fields", gate=1
)
def v24(ctx: Context) -> list[Finding]:
    v, out = ctx.vocab, []
    for s in ctx.graph.screens.values():
        if v.screen_types and s.screen_type not in v.screen_types:
            out.append(
                Finding(
                    "V-24",
                    "blocker",
                    s.id,
                    f"screen_type {s.screen_type} is not in the closed vocabulary",
                    "use a known type (exact synonym map, else re-ask)",
                    "B",
                )
            )
        for r in s.regions:
            if v.shapes and r.shape not in v.shapes and not r.shape.startswith("other:"):
                out.append(
                    Finding(
                        "V-24",
                        "blocker",
                        f"{s.id}.{r.key}",
                        f"shape {r.shape} is not in the closed vocabulary",
                        "use a known shape or other:<name>",
                        "B",
                    )
                )
        for a in s.actions:
            if v.intents and a.intent not in v.intents:
                out.append(
                    Finding(
                        "V-24",
                        "blocker",
                        f"{s.id}.{a.key}",
                        f"intent {a.intent} is not in the closed vocabulary",
                        "use a known intent",
                        "B",
                    )
                )
            if len(a.effect) > 3:
                out.append(
                    Finding(
                        "V-24",
                        "blocker",
                        f"{s.id}.{a.key}",
                        "effect has more than three steps",
                        "keep at most three typed steps",
                        "B",
                    )
                )
        for st in s.states:
            if v.state_kinds and st.kind not in v.state_kinds and not st.kind.startswith("domain:"):
                out.append(
                    Finding(
                        "V-24",
                        "blocker",
                        f"{s.id}.state.{st.kind}",
                        "unknown state kind",
                        "use a known state kind or domain:<name>",
                        "B",
                    )
                )
        for i in s.inputs:
            if v.input_types and i.type not in v.input_types:
                out.append(
                    Finding(
                        "V-24",
                        "blocker",
                        f"{s.id}.{i.key}",
                        f"input type {i.type} is not in the closed vocabulary",
                        "use a semantic input type",
                        "B",
                    )
                )
        if s.tier == "locked" and not any(
            p.kind in ("user", "compass", "policy") for p in s.provenance
        ):
            out.append(
                Finding(
                    "V-24",
                    "blocker",
                    s.id,
                    "tier above its ceiling: Locked needs user, document or policy evidence",
                    "downgrade to Default (R-code)",
                    "A",
                )
            )
    return out


@validator("V-25", "Form-vocabulary lint on free text and identifiers (warning in v1)")
def v25(ctx: Context) -> list[Finding]:
    out = []
    deny = {w.lower() for w in ctx.vocab.form_words}
    allow = {w.lower() for w in ctx.vocab.form_word_allow}
    suffixes = tuple(ctx.config.checks.widget_key_suffixes)
    for s in ctx.graph.screens.values():
        per_card = 0
        for where, text in _free_text(s):
            low = text.lower()
            hits = sorted(
                {w for w in deny if re.search(rf"\b{re.escape(w)}\b", low) and w not in allow}
            )
            if hits or _HEX.search(text) or _UNITS.search(text):
                per_card += 1
                out.append(
                    Finding(
                        "V-25",
                        "warning",
                        where,
                        f"form words in free text: {', '.join(hits) or 'hex or unit'}",
                        "say what it communicates, not how it looks",
                        "B",
                    )
                )
        for key in [r.key for r in s.regions] + [a.key for a in s.actions] + s.copy_keys:
            if key.endswith(suffixes):
                out.append(
                    Finding(
                        "V-25",
                        "warning",
                        f"{s.id}.{key}",
                        "identifier ends in a widget or trigger word",
                        "name the meaning (pay_amount), not the widget",
                        "B",
                    )
                )
        if per_card > ctx.config.checks.freedom_lint_warnings_per_card_max:
            out.append(
                Finding(
                    "V-25",
                    "info",
                    s.id,
                    f"{per_card} lint warnings on one card",
                    "tune the deny list or fix the card",
                    "B",
                )
            )
    return out


@validator(
    "V-27",
    "Closure completeness: a slice holds everything its bindings, operations and copy keys need",
)
def v27(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    blocks = ctx.pack.blocks if ctx.pack else None
    lang = g.meta.source_language
    for s in g.screens.values():
        if s.status != "in":
            continue
        text = render_closure(s, g, blocks)
        if "(unresolved)" in text:
            out.append(
                Finding(
                    "V-27",
                    "blocker",
                    s.id,
                    "closure has an unresolved reference",
                    "fix the binding or operation",
                    "B",
                )
            )
        for k in s.copy_keys:
            if k not in g.locale_copy.get(lang, {}):
                out.append(
                    Finding(
                        "V-27",
                        "blocker",
                        s.id,
                        f"closure lacks source copy for {k}",
                        "write the source copy",
                        "B",
                    )
                )
    return out


def _is_view_only(s: Screen) -> bool:
    return all(a.intent in ("view", "navigate", "cancel", "refresh", "share") for a in s.actions)


def _mf_add(out: list[Finding], s: Screen, waived: bool, sev: str, why: str, gross: bool) -> None:
    """A mobile-fit finding: a warning, or a blocker for a gross breach without a recorded waiver."""
    if gross and not waived:
        sev = "blocker"
    out.append(
        Finding(
            "V-37",
            sev,
            s.id,
            why,
            "split the screen (Product call) or record a mobile_fit_waiver",
            "B",
        )
    )  # type: ignore[arg-type]


@validator(
    "V-37",
    "Mobile-fit (warning by default; error for hover, right-click, tooltip and gross breaches)",
)
def v37(ctx: Context) -> list[Finding]:
    mf, out = ctx.config.checks.mobile_fit, []
    for s in ctx.graph.screens.values():
        if s.surface != "phone":
            continue  # web portals are exempt from the column limit and keep the rest (doc 19 2.6): handled below
        waived = s.id in ctx.waivers

        prim = sum(1 for r in s.regions if r.importance == "primary")
        if prim > mf.max_primary_regions:
            _mf_add(
                out,
                s,
                waived,
                "warning",
                f"{prim} primary regions (limit {mf.max_primary_regions})",
                prim >= mf.max_primary_regions * mf.gross_breach_factor,
            )
        if len(s.regions) > mf.max_regions:
            _mf_add(
                out,
                s,
                waived,
                "warning",
                f"{len(s.regions)} regions (limit {mf.max_regions})",
                len(s.regions) >= mf.max_regions * mf.gross_breach_factor,
            )
        if s.screen_type == "form" and len(s.inputs) > mf.max_form_inputs:
            _mf_add(
                out,
                s,
                waived,
                "warning",
                f"{len(s.inputs)} inputs on a form step (limit {mf.max_form_inputs})",
                len(s.inputs) >= mf.max_form_inputs * mf.gross_breach_factor,
            )
        pa = [a for a in s.actions if a.importance == "primary"]
        if _is_view_only(s) and pa:
            out.append(
                Finding(
                    "V-37",
                    "warning",
                    s.id,
                    "view-only screen carries a primary action",
                    "a view-only screen has none",
                    "B",
                )
            )
        if len(pa) > 1 and not _is_view_only(s):
            out.append(
                Finding(
                    "V-37",
                    "warning",
                    s.id,
                    f"{len(pa)} primary actions (one per decision)",
                    "an approve and reject pair is one decision; otherwise demote one",
                    "B",
                )
            )
        for a in s.actions:
            if a.intent in ctx.vocab.phone_blocked_intents:
                out.append(
                    Finding(
                        "V-37",
                        "blocker",
                        f"{s.id}.{a.key}",
                        f"intent {a.intent} cannot exist on a phone",
                        "use a touch intent",
                        "B",
                    )
                )
        for r in s.regions:
            if "columns" in " ".join(r.attrs) and r.shape == "collection":
                cols = [
                    int(x.split("=")[1])
                    for x in r.attrs
                    if x.startswith("columns=") and x.split("=")[1].isdigit()
                ]
                if cols and cols[0] > mf.max_phone_columns:
                    _mf_add(
                        out,
                        s,
                        waived,
                        "warning",
                        f"{r.key}: {cols[0]} columns on a phone (limit {mf.max_phone_columns})",
                        cols[0] >= mf.max_phone_columns * mf.gross_breach_factor,
                    )
        if len(s.purpose) > mf.long_text_chars:
            out.append(
                Finding(
                    "V-37",
                    "info",
                    s.id,
                    "long text: mark for progressive disclosure",
                    "keep the purpose short",
                    "B",
                )
            )
    return out


@validator(
    "V-38",
    "Shapes and intents: known shape (or other:<name>), cardinality bucket for lists, intents from the closed set, needs cover every region",
)
def v38(ctx: Context) -> list[Finding]:
    out = []
    v = ctx.vocab
    for s in ctx.graph.screens.values():
        for r in s.regions:
            if r.shape.startswith("other:"):
                out.append(
                    Finding(
                        "V-38",
                        "info",
                        f"{s.id}.{r.key}",
                        f"{r.shape} goes to the gap list",
                        "the front end composes or builds it",
                        "C",
                    )
                )
            elif v.shapes and r.shape not in v.shapes:
                out.append(
                    Finding(
                        "V-38",
                        "blocker",
                        f"{s.id}.{r.key}",
                        f"unknown shape {r.shape}",
                        "use a known shape",
                        "B",
                    )
                )
            if r.shape == "collection" and not (r.cardinality or r.maximum):
                out.append(
                    Finding(
                        "V-38",
                        "blocker",
                        f"{s.id}.{r.key}",
                        "collection has no cardinality bucket",
                        "state typical and maximum",
                        "B",
                    )
                )
    if ctx.pack is not None:
        covered = {u for n in ctx.pack.needs for u in n["used_by"]}
        for s in ctx.graph.screens.values():
            if s.status == "in":
                for r in s.regions:
                    if f"{s.id}.{r.key}" not in covered:
                        out.append(
                            Finding(
                                "V-38",
                                "blocker",
                                f"{s.id}.{r.key}",
                                "needs.json does not cover this region",
                                "Spec bug: rebuild needs",
                                "A",
                            )
                        )
    return out


@validator(
    "V-39",
    "Block integrity: every use_block resolves, exact bind set and visibility, no block has a single use",
)
def v39(ctx: Context) -> list[Finding]:
    if ctx.pack is None:
        return []
    blocks, out = ctx.pack.blocks, []
    used: dict[str, int] = {}
    for s in ctx.graph.screens.values():
        for r in s.regions:
            if r.use_block:
                bid = r.use_block.split("@")[0]
                if bid not in blocks:
                    out.append(
                        Finding(
                            "V-39",
                            "blocker",
                            f"{s.id}.{r.key}",
                            f"use_block {bid} does not resolve",
                            "mint the block",
                            "A",
                        )
                    )
                used[bid] = used.get(bid, 0) + 1
    for bid, n in used.items():
        if n < 2:
            out.append(
                Finding(
                    "V-39",
                    "warning",
                    bid,
                    "block with a single use",
                    "a block recurs three times or across apps",
                    "A",
                )
            )
    return out
