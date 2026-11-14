"""Input honesty: what the owner must be told about the INPUT, and what the derivation decided for them.

Spec reads a Compass export and a Research file it did not write. Some of what it finds changes how far the result can be
trusted, and the owner must hear it at G1.5 and G2 in plain words, never discover it later:

* weak inputs and an approval that cannot be verified (SPC-IN-04, SPC-IN-05): the reader sets ``trust.weak_inputs`` and
  ``input_binding``, and here they become a banner, a risk line and a flag;
* research that was written for another Brief (SPC-RS-06);
* payments that the Brief implies but Compass's own list of outside services lacks, or lists for later (SPC-IN-09);
* sign-in that differs by kind of user (SPC-IN-10): one rule in the graph, marked for confirmation when it contradicts
  Compass's single sign-in setting;
* an outside service nobody could choose a provider for (SPC-RS-05): a rule marked for confirmation;
* Compass's own structure issues (SPC-IN-17), kept for the G1.5 page;
* a requested capability outside the fixed stack (SPC-UX-06, SPC-FE-05): the capability and its tier come from the
  platform capability sheet, never from this code;
* a store-rule sheet that is old (SPC-OP-07), judged against the injected clock's date, never against the wall clock.

Pure code, no model. A field the sources do not support stays empty or ``assumed`` with its provenance: nothing here creates
evidence. Every word the owner reads lives in ``data/honesty.yaml``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from mde_compass_intake import ResearchView, SpecInput
from mde_compass_intake.spec_input import StructureIssue
from mde_spec_core.graph import Prov, Rule, SpecGraph
from mde_spec_core.ids import IdRegistry
from mde_spec_core.platform_data import CapabilitySheet, default_capability_sheet
from mde_spec_core.policy import PolicySheet, default_policy_sheet

from ._data import honesty_data
from .signin import RoleSignIn, declared_methods

TIER_ORDER = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True)
class Note:
    """One thing the owner must be told. ``banner`` shows it at the top of the page, ``risk`` lists it among the risks;
    every note is also a flag (the list that travels in ``evidence/self-check.json``)."""

    code: str
    text: str
    fix: str = ""
    severity: str = "warning"
    banner: bool = False
    risk: bool = False


@dataclass(frozen=True)
class GateNotes:
    notes: tuple[Note, ...] = ()
    differences: tuple[str, ...] = ()  # lines for "what differs from the approved Brief"
    structure: tuple[dict[str, Any], ...] = ()  # Compass's structure issues in plain words (G1.5)

    def banners(self) -> list[Note]:
        return [n for n in self.notes if n.banner]


# ---------------------------------------------------------------- small helpers


def _join(items: list[str], last: str = "and") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def _norm_key(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def _role_name(
    graph: SpecGraph, spec_input: SpecInput, registry: IdRegistry | None, rid: str
) -> str:
    """The name of a Compass role id as the graph knows it (aliases followed); the reader's own name as a fallback."""
    cur = registry.resolve(rid) if registry is not None else rid
    node = graph.roles.get(cur or rid)
    if node is None:
        for r in graph.roles.values():
            if rid in r.aliases:
                node = r
                break
    if node is not None and node.name:
        return node.name
    for r in spec_input.roles:
        if r.id == rid and r.name:
            return r.name
    return ""


# ---------------------------------------------------------------- weak inputs, unverified approval, stale research


def _weak_notes(spec_input: SpecInput) -> list[Note]:
    d = honesty_data()
    if not spec_input.trust.weak_inputs:
        return []
    w, u = d.weak_inputs, d.unverified
    reasons: list[str] = []
    if spec_input.trust.degraded:
        parts = [w["probe_words"].get(p, p) for p in spec_input.trust.probes]
        reasons.append(w["degraded"].format(parts=_join(parts)) if parts else w["degraded_unnamed"])
    binding = spec_input.input_binding
    out: list[Note] = []
    if binding != "verified":
        why = u.get(binding, u["unverified_no_baseline"])
        reasons.append(why)
        out.append(
            Note(
                "input_unverified",
                u["flag"].format(why=why),
                u["fix"],
                "warning",
            )
        )
    if not reasons:
        reasons.append(w["degraded_unnamed"])
    out.insert(
        0,
        Note(
            "weak_inputs",
            w["banner"].format(reasons=_join(reasons)),
            w["fix"],
            "warning",
            banner=True,
            risk=True,
        ),
    )
    return out


def _stale_research(spec_input: SpecInput, research: ResearchView | None) -> list[Note]:
    if research is None or not spec_input.brief_hash:
        return []
    d = honesty_data().research_stale
    if research.brief_hash:
        if research.is_for(spec_input.brief_hash):
            return []
        text = d["other_brief"]
    elif research.thin:  # a failed or empty file names no Brief; research was already reported thin
        return []
    else:
        text = d["no_hash"]
    return [
        Note("research_stale", text, d["fix"], "warning", banner=True, risk=True),
    ]


# ---------------------------------------------------------------- payments implied but missing


def _payment_notes(spec_input: SpecInput, facts: set[str]) -> tuple[list[Note], list[str]]:
    if "payments" not in facts:
        return [], []
    d = honesty_data().payment_mismatch
    names = set(d["names"])
    words = re.compile(r"\b(?:" + "|".join(map(re.escape, d["name_words"])) + r")", re.IGNORECASE)
    listed = [
        i for i in spec_input.integrations if _norm_key(i.name) in names or words.search(i.name)
    ]
    if listed and not all(i.preference == "real_later" for i in listed):
        return (
            [],
            [],
        )  # the person listed it for now (or has not decided): nothing contradicts the Brief
    if listed:
        text, diff = d["real_later"], d["difference_real_later"]
    else:
        text, diff = d["not_listed"], d["difference"]
    return [Note("payment_mismatch", text, d["fix"], "warning", risk=True)], [diff]


# ---------------------------------------------------------------- sign-in by kind of user


def _method_text(methods: tuple[str, ...]) -> str:
    words = honesty_data().signin["method_words"]
    return " or ".join(words.get(m, m) for m in methods)


@dataclass(frozen=True)
class SignInPlan:
    statement: str
    conflict: bool
    note: Note | None
    difference: str | None
    prov: tuple[Prov, ...]
    locked: bool


def signin_plan(
    spec_input: SpecInput,
    signins: tuple[RoleSignIn, ...],
    graph: SpecGraph,
    registry: IdRegistry | None,
) -> SignInPlan | None:
    """One sentence about who signs in how, when the Brief's facts name more than one way or contradict its setting."""
    if not signins:
        return None
    d = honesty_data().signin
    declared = declared_methods(spec_input)
    union = {m for s in signins for m in s.methods}
    extra = union - declared if declared else set()
    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for s in signins:
        who = [_role_name(graph, spec_input, registry, r) for r in s.role_ids]
        for w in who:
            if w and w not in groups[s.methods]:
                groups[s.methods].append(w)
        if not who:
            groups[s.methods].append("")
    per_role: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for methods, who_list in groups.items():
        for w in who_list:
            if w:
                per_role[w].add(methods)
    same_role_clash = any(len(ms) > 1 for ms in per_role.values())
    if len(groups) < 2 and not extra:
        return None
    parts = []
    for methods, who_list in sorted(groups.items(), key=lambda kv: (kv[0], kv[1])):
        names = [w for w in who_list if w] or [d["unknown_who"]]
        parts.append(
            d["group"].format(
                who=_join(sorted(names)),
                verb="signs in" if len(names) == 1 else "sign in",
                method=_method_text(methods),
            )
        )
    groups_text = "; ".join(parts)
    conflict = bool(extra) or same_role_clash
    listed = _method_text(tuple(sorted(declared))) if declared else ""
    statement = d["rule_statement"].format(groups=groups_text)
    note = diff = None
    if conflict:
        setting = d["setting_missing"].format(listed=listed) if declared and extra else ""
        statement += d["conflict_tail"].format(setting=setting)
        if extra and declared:
            flag = d["flag"].format(groups=groups_text, listed=listed)
            diff = d["difference"].format(groups=groups_text, listed=listed)
        else:
            flag = d["flag_same_role"].format(groups=groups_text)
            diff = d["difference_same_role"].format(groups=groups_text)
        note = Note("signin_conflict", flag, d["fix"], "warning", risk=True)
    prov = tuple(
        Prov(kind="user" if s.locked else "compass", ref=s.fact_id, quote_id=s.quote_id)
        for s in signins
    )
    return SignInPlan(statement, conflict, note, diff, prov, all(s.locked for s in signins))


# ---------------------------------------------------------------- Compass's structure issues (G1.5)


def _labels(spec_input: SpecInput) -> dict[str, str]:
    out: dict[str, str] = {}
    for coll in (
        spec_input.roles,
        spec_input.apps,
        spec_input.features,
        spec_input.modules,
        spec_input.structure.screens,
        spec_input.structure.entities,
        spec_input.structure.flows,
    ):
        for x in coll:
            if x.name:
                out[x.id] = x.name
    return out


_ID = re.compile(r"\b[A-Z]{2,5}-\d{1,6}\b")


def _words(text: str, labels: dict[str, str]) -> str:
    return _ID.sub(lambda m: f"'{labels[m.group(0)]}'" if m.group(0) in labels else "an item", text)


def structure_rows(spec_input: SpecInput) -> list[dict[str, Any]]:
    """Compass's structure issues grouped by kind and said in plain words: one row each, with a count."""
    d = honesty_data().structure_issues
    labels = _labels(spec_input)
    by: dict[str, list[StructureIssue]] = defaultdict(list)
    for i in spec_input.structure.issues:
        by[i.code].append(i)
    rows = []
    for code in sorted(by):
        items = by[code]
        subject_prefix = {
            "module_too_big": "MOD-",
            "must_feature_without_flow": "FTR-",
        }.get(code, "SCR-")
        ids = sorted({x for i in items for x in i.ids if x.startswith(subject_prefix)})
        named = [labels.get(x, "") for x in ids]
        named = [f"'{n}'" for n in named if n]
        limit = int(d["max_listed"])
        shown = _join(named[:limit]) + (
            f" and {len(named) - limit} more" if len(named) > limit else ""
        )
        detail = _words(items[0].detail, labels)
        template = d.get(code, d["generic"])
        text = template.format(screens=shown or "some screens", detail=detail, code=code)
        severity = "error" if any(i.severity == "error" for i in items) else "warning"
        rows.append(
            {
                "what": f"{text} ({len(items)})" if len(items) > 1 else text,
                "severity": severity,
                "count": len(items),
                "code": code,
            }
        )
    rows.sort(key=lambda r: (r["severity"] != "error", r["code"]))
    return rows


# ---------------------------------------------------------------- requested capabilities


def _phrase_pattern(words: list[str]) -> re.Pattern[str]:
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z])(?:{alt})(?![A-Za-z])", re.IGNORECASE)


_CAP_PATTERNS: dict[str, re.Pattern[str]] = {}


def capability_of(text: str, sheet: CapabilitySheet | None = None) -> tuple[str, str] | None:
    """``(capability id, tier)`` a requested feature asks for, or None. The words are data; the tier is the sheet's. When
    several capabilities match, the highest tier wins (the owner must hear about the hardest part first)."""
    sheet = sheet or default_capability_sheet()
    words = honesty_data().capability_words
    best: tuple[int, int, str] | None = None
    for n, (cap, ws) in enumerate(words.items()):
        pat = _CAP_PATTERNS.get(cap)
        if pat is None:
            pat = _CAP_PATTERNS[cap] = _phrase_pattern(ws)
        if not pat.search(text):
            continue
        try:
            tier = sheet.tier_of(cap)
        except KeyError:
            continue
        key = (-TIER_ORDER[tier], n, cap)
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return best[2], sheet.tier_of(best[2])


def map_capabilities(graph: SpecGraph) -> int:
    """Give every requested feature the capability and tier its words ask for. Returns how many were given one."""
    n = 0
    for f in graph.features.values():
        if f.capability:
            continue
        hit = capability_of(f"{f.name} {f.intent}")
        if hit is not None:
            f.capability, f.capability_tier = hit[0], hit[1]  # type: ignore[assignment]
            n += 1
    return n


# ---------------------------------------------------------------- the store-rule sheet's age


def policy_sheet_stale_note(today: date | None, sheet: PolicySheet | None = None) -> Note | None:
    """A flag when the store-rule sheet was last checked more than the allowed number of days before ``today``.

    ``today`` comes from the injected clock (a body never reads the wall clock, so the pack stays byte-stable): the flag is
    put on the G2 view and in ``evidence/self-check.json``, never in a file the pack hashes. No date, no judgement."""
    if today is None:
        return None
    sheet = sheet or default_policy_sheet()
    d = honesty_data().policy_sheet
    days = int(d["stale_after_days"])
    try:
        checked = date.fromisoformat(sheet.checked_on)
    except ValueError:
        return None
    if (today - checked).days <= days:
        return None
    return Note(
        "policy_sheet_stale",
        d["text"].format(checked_on=sheet.checked_on, days=days),
        d["fix"],
        "warning",
        risk=True,
    )


# ---------------------------------------------------------------- an outside service nobody could choose a provider for


def _need_matches(dossier: Any, need: str) -> bool:
    return need in {
        _norm_key(x) for x in (*dossier.integrations, *dossier.needs, dossier.capability)
    }


def add_provider_rules(
    graph: SpecGraph,
    research: ResearchView | None,
    registry: IdRegistry,
) -> int:
    """A confirm rule 'choose a provider' for each outside service with a provider's own step that Research gave no
    provider for. Needs a research file: with none, the thin-research risk already says that nothing was found."""
    if research is None:
        return 0
    d = honesty_data().provider
    n = 0
    for i in sorted(graph.integrations.values(), key=lambda x: x.id):
        if not i.provider_side_flow or i.provider_ids or i.mode != "mock":
            continue
        matched = [x for x in research.dossiers if _need_matches(x, i.need_key)]
        graph.add(
            "rules",
            Rule(
                id=registry.mint("RUL"),
                subject=d["subject"].format(need=i.name),
                statement=d["unverified"] if matched else d["no_dossier"],
                confirm=True,
                assumed=True,
                provenance=[Prov(kind="assumed", ref=f"no provider chosen for {i.need_key}")],
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------- the whole step


def apply_and_note(
    spec_input: SpecInput,
    research: ResearchView | None,
    facts: set[str],
    graph: SpecGraph,
    registry: IdRegistry,
    signins: tuple[RoleSignIn, ...],
) -> GateNotes:
    """Write what the derivation decided into the graph (capabilities, the sign-in rule, provider rules) and return the
    notes for the gates. Deterministic: the same input gives the same graph and the same notes."""
    notes: list[Note] = []
    diffs: list[str] = []
    notes += _weak_notes(spec_input)
    notes += _stale_research(spec_input, research)
    pay, pay_diff = _payment_notes(spec_input, facts)
    notes += pay
    diffs += pay_diff
    plan = signin_plan(spec_input, signins, graph, registry)
    if plan is not None:
        d = honesty_data().signin
        graph.add(
            "rules",
            Rule(
                id=registry.mint("RUL"),
                subject=d["rule_subject"],
                statement=plan.statement,
                confirm=plan.conflict,
                assumed=not plan.locked,
                provenance=list(plan.prov),
            ),
        )
        if plan.note is not None:
            notes.append(plan.note)
        if plan.difference:
            diffs.append(plan.difference)
    add_provider_rules(graph, research, registry)
    map_capabilities(graph)
    return GateNotes(tuple(notes), tuple(diffs), tuple(structure_rows(spec_input)))
