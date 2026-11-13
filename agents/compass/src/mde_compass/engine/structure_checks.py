"""The checks on the structure of the app (ADR 0017; plan 5b and 5d): pure functions of a ``Brief``.

``run_checks`` returns ``Issue`` rows; severity and who fixes each code come from ``catalog/structure_rules.yaml``
(data). Three kinds of check, cheapest first:

* structure: every pointer resolves, every screen belongs to a module, every module has screens, every entity is used,
  every role and app has screens, every in-scope feature has a screen, a ``must`` feature has a flow with acceptance;
* logic: a flow's role can open every step, a role that creates or edits an entity has at least that access to it, no
  screen belongs to a feature that was ruled out, money fields are typed ``money``, identifiers are ascii;
* sufficiency and realism: a feature or module that is thin for its band, a detail fact no module received, and the
  rule pack of ``structure_rules.yaml`` (a Brief that says health data needs a consent screen).

``apply_fixes`` performs the deterministic fixes (``fix: code``) in place and returns what it did. Whatever needs a
model (``repair``, ``expand``) is handed to the repair-and-expansion pass by ``engine/structure_draft``. Nothing here
ever invents a fact, edits a number or adds a screen: the checker only reports, prunes and tidies.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from mde_compass.catalog.size_bands import SizeBand
from mde_compass.catalog.structure_patterns import load_structure_patterns, words_of
from mde_compass.catalog.structure_rules import Rule, load_check_policy, load_structure_rules
from mde_compass.domain.brief import Brief, DomainFact
from mde_compass.domain.common import Provenance
from mde_compass.domain.structure import (
    AccessLevel,
    EntityField,
    FlowStep,
    ScreenEntityUse,
    ScreenRecord,
    access_allows,
)
from mde_compass.engine import structure_derive as derive

Severity = Literal["error", "warn"]
KEY_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MODULE_TOO_BIG = 12
NEAR_DUPLICATE = 0.8  # word-set overlap of two screen names in one module


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    fix: str  # code | repair | expand | none
    ids: tuple[str, ...] = ()
    module: str = ""  # the module the issue belongs to ("" = the structure as a whole)
    detail: str = (
        ""  # English machine text: it goes into the repair input and the plain-words notice
    )

    def line(self) -> str:
        return f"{self.code} {' '.join(self.ids)}: {self.detail}".strip()


def make_issue(code: str, ids: Sequence[str] = (), *, module: str = "", detail: str = "") -> Issue:
    """An issue with the severity and the fixer the policy file gives its code."""
    policy = load_check_policy()[code]
    severity: Severity = "error" if policy.severity == "error" else "warn"
    return Issue(code, severity, policy.fix, tuple(ids), module, detail)


_issue = make_issue


# ------------------------------------------------------------------------------------------------ helpers


def slug_key(text: str, fallback: str) -> str:
    """An ascii snake_case identifier from any text (a Hindi name falls back to ``fallback``)."""
    folded = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()
    )
    key = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")[:40]
    if not key or not key[0].isalpha():
        key = fallback if not key else f"{fallback}_{key}"[:40]
    return key


def _name_words(name: str) -> set[str]:
    return {w for w in words_of(name) if len(w) > 1 or w.isdigit()}


def _text_of(*parts: str) -> str:
    return " ".join(p for p in parts if p).casefold()


def _item_values(brief: Brief, section: str, key: str) -> list[str]:
    item = getattr(brief, section).get(key)
    value = item.value if item is not None and item.has_value() else None
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if isinstance(value, str) and value else []


# --------------------------------------------------------------------------------------------- the checks


def _pointers(brief: Brief) -> list[Issue]:
    ids = {
        "app": {a.id for a in brief.apps},
        "role": {r.id for r in brief.roles},
        "feature": {f.id for f in brief.scope},
        "module": {m.id for m in brief.modules},
        "screen": {s.id for s in brief.screens},
        "entity": {e.id for e in brief.entities},
    }
    out: list[Issue] = []

    def check(owner: str, module: str, kind: str, refs: Iterable[str]) -> None:
        for ref in refs:
            if ref and ref not in ids[kind]:
                out.append(
                    _issue(
                        "dangling_ref",
                        (owner, ref),
                        module=module,
                        detail=f"{owner} points at {kind} {ref}, which does not exist",
                    )
                )

    for m in brief.modules:
        check(m.id, m.id, "app", m.apps)
        check(m.id, m.id, "feature", m.features)
        check(m.id, m.id, "role", m.roles)
        check(m.id, m.id, "screen", [m.entry])
    for s in brief.screens:
        if s.module and s.module not in ids["module"]:
            continue  # reported as screen_without_module
        check(s.id, s.module, "app", s.apps)
        check(s.id, s.module, "role", s.roles)
        check(s.id, s.module, "feature", s.serves)
        check(s.id, s.module, "entity", [u.entity for u in s.entities])
    for f in brief.flows:
        check(f.id, "", "role", [f.role])
        check(f.id, "", "feature", f.features)
        check(f.id, "", "screen", [step.screen for step in f.steps])
    for e in brief.entities:
        check(e.id, e.module, "module", [e.module])
        check(e.id, e.module, "role", list(e.access))
        check(e.id, e.module, "entity", [fld.ref for fld in e.fields])
    return out


def _modules_and_screens(brief: Brief) -> list[Issue]:
    out: list[Issue] = []
    module_ids = {m.id for m in brief.modules}
    by_module: dict[str, list[ScreenRecord]] = {}
    for s in brief.screens:
        by_module.setdefault(s.module, []).append(s)
        if s.module not in module_ids:
            out.append(
                _issue(
                    "screen_without_module",
                    (s.id,),
                    detail=f"screen {s.id} {s.name!r} is not in a module",
                )
            )
    home = load_structure_patterns().home_patterns()
    for m in brief.modules:
        screens = by_module.get(m.id, [])
        if not screens:
            out.append(
                _issue(
                    "module_without_screens",
                    (m.id,),
                    module=m.id,
                    detail=f"module {m.id} {m.name!r} has no screens",
                )
            )
            continue
        if m.entry not in {s.id for s in screens}:
            out.append(
                _issue(
                    "module_entry_missing",
                    (m.id,),
                    module=m.id,
                    detail=f"module {m.id} has no valid entry screen",
                )
            )
        if len(screens) > MODULE_TOO_BIG:
            out.append(
                _issue(
                    "module_too_big",
                    (m.id,),
                    module=m.id,
                    detail=f"module {m.id} has {len(screens)} screens; consider splitting it",
                )
            )
        seen: dict[str, str] = {}
        for s in screens:
            folded = " ".join(words_of(s.name))
            if folded in seen:
                out.append(
                    _issue(
                        "screen_duplicate_name",
                        (s.id, seen[folded]),
                        module=m.id,
                        detail=f"two screens in {m.id} are called {s.name!r}",
                    )
                )
            seen.setdefault(folded, s.id)
        for i, a in enumerate(screens):
            wa = _name_words(a.name)
            for b in screens[i + 1 :]:
                wb = _name_words(b.name)
                if (
                    wa
                    and wb
                    and a.name.casefold() != b.name.casefold()
                    and len(wa & wb) / len(wa | wb) >= NEAR_DUPLICATE
                ):
                    out.append(
                        _issue(
                            "screen_near_duplicate",
                            (a.id, b.id),
                            module=m.id,
                            detail=f"{a.name!r} and {b.name!r} look like the same screen",
                        )
                    )
    patterns = load_structure_patterns().patterns
    for s in brief.screens:
        if s.pattern and s.pattern not in patterns:
            out.append(
                _issue(
                    "unknown_pattern",
                    (s.id,),
                    module=s.module,
                    detail=f"screen {s.id} has the unknown pattern {s.pattern!r}",
                )
            )
        if not s.roles:
            out.append(
                _issue(
                    "screen_without_roles",
                    (s.id,),
                    module=s.module,
                    detail=f"screen {s.id} says nobody can open it",
                )
            )
    in_flow = {step.screen for f in brief.flows for step in f.steps}
    entries = {m.entry for m in brief.modules if m.entry}
    frame = load_structure_patterns().chrome_patterns()
    for s in brief.screens:
        if (
            s.id not in in_flow
            and s.id not in entries
            and s.pattern not in home
            and s.pattern not in frame
        ):
            out.append(
                _issue(
                    "screen_unreachable",
                    (s.id,),
                    module=s.module,
                    detail=f"screen {s.id} is in no flow and is not an entry",
                )
            )
    return out


def _flows(brief: Brief) -> list[Issue]:
    out: list[Issue] = []
    screens = {s.id: s for s in brief.screens}
    for f in brief.flows:
        known = [step for step in f.steps if step.screen in screens]
        if len(known) < 2:
            out.append(
                _issue(
                    "flow_too_short",
                    (f.id,),
                    detail=f"flow {f.id} {f.name!r} has fewer than two steps on real screens",
                )
            )
        if f.role:
            for step in known:
                s = screens[step.screen]
                if s.roles and f.role not in s.roles:
                    out.append(
                        _issue(
                            "flow_role_cannot_access_step",
                            (f.id, s.id, f.role),
                            module=s.module,
                            detail=f"{f.role} goes through {f.id} but cannot open {s.id} {s.name!r}",
                        )
                    )
    return out


def _entities(brief: Brief) -> list[Issue]:
    out: list[Issue] = []
    used = {u.entity for s in brief.screens for u in s.entities}
    types = set(load_structure_patterns().field_types)
    hints = load_structure_patterns().field_hints
    for e in brief.entities:
        if not e.fields:
            out.append(
                _issue(
                    "entity_without_fields",
                    (e.id,),
                    module=e.module,
                    detail=f"entity {e.id} {e.name!r} has no fields",
                )
            )
        if e.id not in used:
            out.append(
                _issue(
                    "entity_unused",
                    (e.id,),
                    module=e.module,
                    detail=f"no screen shows or edits {e.id} {e.name!r}",
                )
            )
        if not e.access:
            out.append(
                _issue(
                    "entity_access_missing",
                    (e.id,),
                    module=e.module,
                    detail=f"nobody is said to have access to {e.id}",
                )
            )
        if not KEY_OK.match(e.key):
            out.append(
                _issue(
                    "entity_key_invalid",
                    (e.id,),
                    module=e.module,
                    detail=f"entity {e.id} needs an ascii snake_case key",
                )
            )
        for fld in e.fields:
            if not KEY_OK.match(fld.key):
                out.append(
                    _issue(
                        "field_key_invalid",
                        (e.id, fld.key),
                        module=e.module,
                        detail=f"field {fld.key!r} of {e.id} needs an ascii snake_case key",
                    )
                )
            if fld.type not in types:
                out.append(
                    _issue(
                        "unknown_field_type",
                        (e.id, fld.key),
                        module=e.module,
                        detail=f"field {fld.key!r} of {e.id} has the unknown type {fld.type!r}",
                    )
                )
                continue
            wanted = set(words_of(f"{fld.key} {fld.name}"))
            for hint in hints:
                if fld.type == hint.type or not wanted & hint.words:
                    continue
                if hint.auto and fld.type in ("text", "number"):
                    out.append(
                        _issue(
                            "field_type_money",
                            (e.id, fld.key),
                            module=e.module,
                            detail=f"field {fld.key!r} of {e.id} looks like {hint.type} but is {fld.type}",
                        )
                    )
                elif not hint.auto and fld.type == "text":
                    out.append(
                        _issue(
                            "field_type_hint",
                            (e.id, fld.key),
                            module=e.module,
                            detail=f"field {fld.key!r} of {e.id} may be {hint.type}, not text",
                        )
                    )
                break
    by_id = {e.id: e for e in brief.entities}
    for s in brief.screens:
        for use in s.entities:
            e = by_id.get(use.entity)
            if e is None or not e.access:
                continue  # unknown: dangling_ref; no access given at all: entity_access_missing
            levels: dict[str, AccessLevel] = {role: e.access.get(role, "none") for role in s.roles}
            nobody = not any(access_allows(lv, use.use) for lv in levels.values())
            blocked: list[str] = []
            for role in s.roles:
                level = levels[role]
                if not access_allows(level, use.use):
                    if level != "none" and use.use != "view" and not nobody:
                        continue  # one screen, two kinds of person: those with less access only look at it
                    blocked.append(role)
            if blocked:  # one issue per screen and entity, naming every role it concerns
                out.append(
                    _issue(
                        "screen_entity_access_conflict",
                        (s.id, e.id, *blocked),
                        module=s.module,
                        detail=(
                            f"{', '.join(blocked)} can open {s.id}, which {use.use}s {e.id}, "
                            f"but may only {levels[blocked[0]]} it"
                        ),
                    )
                )
    return out


def _coverage(brief: Brief, band: SizeBand) -> list[Issue]:
    out: list[Issue] = []
    served: dict[str, int] = {}
    for s in brief.screens:
        for fid in s.serves:
            served[fid] = served.get(fid, 0) + 1
    module_of_feature: dict[str, str] = {}
    for m in brief.modules:
        for fid in m.features:
            module_of_feature.setdefault(fid, m.id)
    for f in brief.scope:
        if f.scope_status != "in":
            continue
        n = served.get(f.id, 0)
        module = module_of_feature.get(f.id, "")
        if n == 0:
            out.append(
                _issue(
                    "feature_without_screen",
                    (f.id,),
                    module=module,
                    detail=f"in-scope feature {f.id} {f.name!r} has no screen",
                )
            )
        elif f.priority == "must" and n < band.must_feature_screens_min:
            out.append(
                _issue(
                    "feature_thin",
                    (f.id,),
                    module=module,
                    detail=f"must-have {f.id} {f.name!r} has {n} screens; this size of app needs at least {band.must_feature_screens_min}",
                )
            )
        if f.priority == "must" and not any(
            f.id in fl.features and fl.acceptance for fl in brief.flows
        ):
            out.append(
                _issue(
                    "must_feature_without_flow",
                    (f.id,),
                    module=module,
                    detail=f"must-have {f.id} {f.name!r} has no flow with acceptance lines",
                )
            )
    screens_per_module: dict[str, int] = {}
    for s in brief.screens:
        screens_per_module[s.module] = screens_per_module.get(s.module, 0) + 1
    for m in brief.modules:
        n = screens_per_module.get(m.id, 0)
        if m.kind:
            continue  # a standard module (account, notifications, help, administration) is small by nature
        if 0 < n < band.module_screens_min:
            out.append(
                _issue(
                    "module_thin",
                    (m.id,),
                    module=m.id,
                    detail=f"module {m.id} {m.name!r} has {n} screens; this size of app needs at least {band.module_screens_min}",
                )
            )
    in_screens = {s.id for s in brief.screens}
    roles_with_access = {r for s in brief.screens for r in s.roles} | {
        r for e in brief.entities for r in e.access
    }
    home = load_structure_patterns().home_patterns()
    entry = {m.entry for m in brief.modules if m.entry}
    for r in brief.roles:
        if r.id not in roles_with_access:
            out.append(
                _issue(
                    "role_without_access",
                    (r.id,),
                    detail=f"role {r.id} {r.name_in_user_words!r} has no screen and no entity access",
                )
            )
        elif not any(
            r.id in s.roles and (s.pattern in home or s.id in entry)
            for s in brief.screens
            if s.id in in_screens
        ):
            out.append(
                _issue(
                    "role_without_home",
                    (r.id,),
                    detail=f"role {r.id} {r.name_in_user_words!r} has no home screen",
                )
            )
    with_screens = {a for s in brief.screens for a in s.apps} | {
        a for m in brief.modules for a in m.apps if any(sc.module == m.id for sc in brief.screens)
    }
    for a in brief.apps:
        if a.id not in with_screens and (brief.screens or brief.modules):
            out.append(
                _issue(
                    "app_without_screens", (a.id,), detail=f"app {a.id} {a.name!r} has no screens"
                )
            )
    excluded = derive.excluded_features(brief)
    for s in brief.screens:
        if s.serves and set(s.serves) <= excluded:
            out.append(
                _issue(
                    "screen_for_excluded_feature",
                    (s.id,),
                    module=s.module,
                    detail=f"screen {s.id} {s.name!r} only serves features that are out of scope or unsupported",
                )
            )
    return out


def _size(brief: Brief, band: SizeBand) -> list[Issue]:
    verdict = derive.size_verdict(brief, band)
    n = len(brief.screens)
    if verdict == "over":
        return [
            _issue(
                "over_band",
                detail=f"{n} screens is more than a {band.id} app; a larger band fits better",
            )
        ]
    if verdict == "under" and n:
        return [
            _issue(
                "thin_draft",
                detail=f"{n} screens is fewer than a {band.id} app usually has (only a warning: a simple app stays small)",
            )
        ]
    return []


def _orphans(brief: Brief, facts: Iterable[DomainFact] | None) -> list[Issue]:
    if not brief.modules:
        return []
    routing = derive.route_facts(brief, facts)
    return [
        _issue(
            "fact_orphan",
            (f.id,),
            detail=f"the detail {f.id} reached no module: {f.statement[:120]}",
        )
        for f in routing.orphans
    ]


def _rules(brief: Brief, rules: Sequence[Rule], lang: str) -> list[Issue]:
    if not brief.modules:
        return []
    sensitive = {v.casefold() for v in _item_values(brief, "data", "sensitive")}
    auth = {v.casefold() for v in _item_values(brief, "access", "auth_methods")}
    locales = _item_values(brief, "experience", "locales")
    integrations = " ".join(i.name for i in brief.integrations).casefold()
    role_text = " ".join(f"{r.name_in_user_words} {r.goal}" for r in brief.roles).casefold()
    haystack = _text_of(
        *[
            m.name for m in brief.modules
        ],  # a module's name counts, its purpose does not: only a screen is a screen
        *[f"{s.name} {s.purpose}" for s in brief.screens],
        *[f"{f.name} {f.goal}" for f in brief.flows],
        *[e.name for e in brief.entities],
    )
    out: list[Issue] = []
    for rule in rules:
        if rule.sensitive_any and not rule.sensitive_any & sensitive:
            continue
        if rule.auth_any and not rule.auth_any & auth:
            continue
        if rule.locales_min and len(locales) < rule.locales_min:
            continue
        if rule.integration_words and not any(
            w in words_of(integrations) for w in rule.integration_words
        ):
            continue
        if rule.roles_min and len(brief.roles) < rule.roles_min:
            continue
        if rule.role_words and not any(w in words_of(role_text) for w in rule.role_words):
            continue
        for req in rule.requires:
            if any(w in haystack for w in req.words):
                continue
            why = req.why.get(lang) or req.why["en"]
            out.append(_issue("rule_unmet", (rule.id, req.id), detail=why))
    return out


def run_checks(
    brief: Brief,
    band: SizeBand,
    *,
    facts: Iterable[DomainFact] | None = None,
    rules: Sequence[Rule] | None = None,
    lang: str = "en",
) -> list[Issue]:
    """Every issue the structure has, in a stable order (pointers first). Empty for a Brief with no structure."""
    if not derive.has_structure(brief):
        return []
    return [
        *_pointers(brief),
        *_modules_and_screens(brief),
        *_flows(brief),
        *_entities(brief),
        *_coverage(brief, band),
        *_size(brief, band),
        *_orphans(brief, facts),
        *_rules(brief, load_structure_rules() if rules is None else rules, lang),
    ]


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def needs_model(issues: Iterable[Issue]) -> list[Issue]:
    """The issues the repair-and-expansion pass handles: the model's to fix (``repair``) or to add to (``expand``)."""
    return [i for i in issues if i.fix in ("repair", "expand")]


def by_module(issues: Iterable[Issue]) -> dict[str, list[Issue]]:
    """Issues grouped by the module they belong to (``""`` = the structure as a whole), in first-seen order."""
    grouped: dict[str, list[Issue]] = {}
    for i in issues:
        grouped.setdefault(i.module, []).append(i)
    return grouped


# ----------------------------------------------------------------------------------- deterministic fixes


def _prune_refs(brief: Brief) -> int:
    ids = {
        "app": {a.id for a in brief.apps},
        "role": {r.id for r in brief.roles},
        "feature": {f.id for f in brief.scope},
        "module": {m.id for m in brief.modules},
        "screen": {s.id for s in brief.screens},
        "entity": {e.id for e in brief.entities},
    }
    changed = 0

    def keep(kind: str, refs: list[str]) -> list[str]:
        nonlocal changed
        kept = [r for r in refs if r in ids[kind]]
        changed += len(refs) - len(kept)
        return kept

    for i, m in enumerate(brief.modules):
        entry = m.entry if m.entry in ids["screen"] else ""
        changed += int(entry != m.entry)
        brief.modules[i] = m.model_copy(
            update={
                "apps": keep("app", m.apps),
                "features": keep("feature", m.features),
                "roles": keep("role", m.roles),
                "entry": entry,
            }
        )
    for i, s in enumerate(brief.screens):
        uses = [u for u in s.entities if u.entity in ids["entity"]]
        changed += len(s.entities) - len(uses)
        brief.screens[i] = s.model_copy(
            update={
                "apps": keep("app", s.apps),
                "roles": keep("role", s.roles),
                "serves": keep("feature", s.serves),
                "entities": uses,
            }
        )
    for i, f in enumerate(brief.flows):
        steps = [st for st in f.steps if st.screen in ids["screen"]]
        changed += len(f.steps) - len(steps)
        role = f.role if (not f.role or f.role in ids["role"]) else ""
        changed += int(role != f.role)
        brief.flows[i] = f.model_copy(
            update={"steps": steps, "features": keep("feature", f.features), "role": role}
        )
    for i, e in enumerate(brief.entities):
        access = {r: lv for r, lv in e.access.items() if r in ids["role"]}
        changed += len(e.access) - len(access)
        fields = [
            fld.model_copy(
                update={"ref": fld.ref if (not fld.ref or fld.ref in ids["entity"]) else ""}
            )
            for fld in e.fields
        ]
        changed += sum(1 for a, b in zip(e.fields, fields, strict=True) if a.ref != b.ref)
        module = e.module if (not e.module or e.module in ids["module"]) else ""
        changed += int(module != e.module)
        brief.entities[i] = e.model_copy(
            update={"access": access, "fields": fields, "module": module}
        )
    return changed


def _screen_to_show(screens: Sequence[ScreenRecord], module: str) -> int:
    """The index of the screen that should show an entity nobody shows: a list or detail screen of its owner module,
    else any screen of it, else a list or detail screen anywhere, else the first screen."""
    shows = ("list", "detail")
    for ok in (
        [i for i, s in enumerate(screens) if s.module == module and s.pattern in shows],
        [i for i, s in enumerate(screens) if s.module == module],
        [i for i, s in enumerate(screens) if s.pattern in shows],
    ):
        if ok:
            return ok[0]
    return 0


def fix_flow_access(brief: Brief) -> list[str]:
    """The last resort after the repair pass, for a journey step its own role cannot open. The module of the screen
    says who belongs there: when the journey's role is one of the module's roles the model simply left the role off
    the screen, so it is added; when it is not, the journey strayed into somebody else's work (a customer's order
    walking through the kitchen's screens) and the step is dropped (a journey left with fewer than two steps goes in
    ``apply_fixes``). In place; one plain line per change."""
    done: list[str] = []
    roles = {r.id for r in brief.roles}
    module_roles = {m.id: set(m.roles) for m in brief.modules}
    for k, flow in enumerate(brief.flows):
        if flow.role not in roles:
            continue
        kept: list[FlowStep] = []
        for step in flow.steps:
            i = next((n for n, s in enumerate(brief.screens) if s.id == step.screen), -1)
            if i < 0:
                kept.append(step)
                continue
            s = brief.screens[i]
            if not s.roles or flow.role in s.roles:
                kept.append(step)
            elif flow.role in module_roles.get(s.module, set()):
                brief.screens[i] = s.model_copy(update={"roles": [*s.roles, flow.role]})
                kept.append(step)
                done.append(f"{s.id} can now be opened by {flow.role}, who works in {s.module}")
            else:
                done.append(
                    f"{flow.id} no longer goes through {s.id}: {flow.role} does not work in {s.module}"
                )
        if len(kept) != len(flow.steps):
            brief.flows[k] = flow.model_copy(update={"steps": kept})
    return done


def apply_fixes(brief: Brief) -> list[str]:
    """The deterministic fixes (``fix: code``), in place. Returns one plain line per kind of thing done. Order matters:
    screens of excluded features go first (their flows lose steps), then dangling pointers, then tidying."""
    done: list[str] = []
    patterns = load_structure_patterns()
    # screens of features that were ruled out
    excluded = derive.excluded_features(brief)
    gone = {s.id for s in brief.screens if s.serves and set(s.serves) <= excluded}
    if gone:
        brief.screens[:] = [s for s in brief.screens if s.id not in gone]
        for s in sorted(gone):
            brief.ids.tombstone(s)
        done.append(
            f"removed {len(gone)} screen(s) of features that are out of scope or unsupported"
        )
    # a screen with no module: the module that delivers a feature it serves, when there is exactly one
    module_ids = {m.id for m in brief.modules}
    for i, s in enumerate(brief.screens):
        if s.module in module_ids:
            continue
        owners = {m.id for m in brief.modules if set(m.features) & set(s.serves)}
        if len(owners) == 1:
            brief.screens[i] = s.model_copy(update={"module": next(iter(owners))})
            done.append(f"put {s.id} into {next(iter(owners))}")
    pruned = _prune_refs(brief)
    if pruned:
        done.append(f"removed {pruned} pointer(s) to records that do not exist")
    # a role that opens a screen that only SHOWS an entity, where the entity lists no access for that role at all: the
    # model left the row out (catalogue data such as doctors or clinics); showing it is the least access that works.
    # An explicit "none" is a contradiction and stays an issue for the repair pass.
    by_entity = {e.id: i for i, e in enumerate(brief.entities)}
    added = 0
    for s in brief.screens:
        for use in s.entities:
            idx = by_entity.get(use.entity)
            if idx is None or use.use != "view" or not brief.entities[idx].access:
                continue
            for role in s.roles:
                if role not in brief.entities[idx].access:
                    ent = brief.entities[idx]
                    brief.entities[idx] = ent.model_copy(
                        update={"access": {**ent.access, role: "view"}}
                    )
                    added += 1
    if added:
        done.append(
            f"gave {added} role(s) view access to data their screens show and the entity left out"
        )
    # a screen shared by many roles that "shows" an entity most of them may not see (an explicit "none"): the data link
    # belongs to the one role that does see it, so the screen stops claiming it, as long as another screen shows it
    shown_on: dict[str, int] = {}
    for s in brief.screens:
        for u in s.entities:
            shown_on[u.entity] = shown_on.get(u.entity, 0) + 1
    by_ent = {e.id: e for e in brief.entities}
    dropped = 0
    for i, s in enumerate(brief.screens):
        keep: list[ScreenEntityUse] = []
        for u in s.entities:
            e = by_ent.get(u.entity)
            if e is not None and e.access and u.use == "view" and len(s.roles) >= 2:
                blind = [r for r in s.roles if e.access.get(r, "none") == "none"]
                if len(blind) * 2 > len(s.roles) and shown_on[u.entity] > 1:
                    shown_on[u.entity] -= 1
                    dropped += 1
                    continue
            keep.append(u)
        if len(keep) != len(s.entities):
            brief.screens[i] = s.model_copy(update={"entities": keep})
    if dropped:
        done.append(
            f"{dropped} shared screen(s) no longer claim data that most of their roles cannot see"
        )
    # unknown pattern: blank (the model may fill it in the next pass; never guessed)
    for i, s in enumerate(brief.screens):
        if s.pattern and s.pattern not in patterns.patterns:
            brief.screens[i] = s.model_copy(update={"pattern": ""})
    # duplicate screen names in a module: number the later ones
    seen: dict[tuple[str, str], int] = {}
    for i, s in enumerate(brief.screens):
        key = (s.module, " ".join(words_of(s.name)))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            brief.screens[i] = s.model_copy(update={"name": f"{s.name} ({seen[key]})"})
            done.append(f"renamed the repeated screen name of {s.id}")
    # module entry: the first home-pattern screen, else the first screen
    home = patterns.home_patterns()
    for i, m in enumerate(brief.modules):
        mine = [s for s in brief.screens if s.module == m.id]
        if mine and m.entry not in {s.id for s in mine}:
            entry = next((s.id for s in mine if s.pattern in home), mine[0].id)
            brief.modules[i] = m.model_copy(update={"entry": entry})
            done.append(f"set the entry screen of {m.id} to {entry}")
    # flows with fewer than two steps are dropped
    shorts = [f for f in brief.flows if len(f.steps) < 2]
    if shorts:
        brief.flows[:] = [f for f in brief.flows if len(f.steps) >= 2]
        for f in shorts:
            brief.ids.tombstone(f.id)
        done.append(f"dropped {len(shorts)} flow(s) with fewer than two steps")
    # identifiers and money types
    for i, e in enumerate(brief.entities):
        key = e.key if KEY_OK.match(e.key) else slug_key(e.name or e.key, "entity")
        fields: list[EntityField] = []
        used_keys: set[str] = set()
        for n, fld in enumerate(e.fields, 1):
            fkey = fld.key if KEY_OK.match(fld.key) else slug_key(fld.name or fld.key, f"field_{n}")
            while fkey in used_keys:
                fkey = f"{fkey}_{n}"[:40]
            used_keys.add(fkey)
            ftype = fld.type
            wanted = set(words_of(f"{fld.key} {fld.name}"))
            for hint in patterns.field_hints:
                if hint.auto and ftype in ("text", "number") and wanted & hint.words:
                    ftype = hint.type
                    break
            fields.append(
                fld.model_copy(
                    update={"key": fkey, "type": ftype if ftype in patterns.field_types else "text"}
                )
            )
        if key != e.key or fields != e.fields:
            brief.entities[i] = e.model_copy(update={"key": key, "fields": fields})
            done.append(f"tidied the identifiers and types of {e.id}")
    # an unused entity: shown by the first list or detail screen of its owner module, else of any module
    used = {u.entity for s in brief.screens for u in s.entities}
    for e in brief.entities:
        if e.id in used or not brief.screens:
            continue
        target = _screen_to_show(brief.screens, e.module)
        s = brief.screens[target]
        brief.screens[target] = s.model_copy(
            update={"entities": [*s.entities, ScreenEntityUse(entity=e.id, use="view")]}
        )
        done.append(f"{e.id} is now shown by {s.id}")
    return done


# ------------------------------------------------------------------------------------- provenance guard


def downgrade_unsupported(brief: Brief, *, active: set[str], verified: set[str]) -> list[str]:
    """A record whose evidence cites facts (``FCT-###``) of which none is still active and verified is no longer
    supported by the person: Locked becomes Default and an accepted record is proposed again, so a correction by the
    person can never leave a stale Locked screen behind (the structure's twin of ``lock_guard``). Records that cite no
    fact (drafted by the model, or typed by the person with a turn id) are left alone. Returns the ids downgraded."""
    changed: list[str] = []

    def stale(provenance: Sequence[Provenance]) -> bool:
        cited = [p.ref for p in provenance if p.ref.startswith("FCT-")]
        return bool(cited) and not any(c in active and c in verified for c in cited)

    for section in ("modules", "screens", "flows", "entities"):
        records: list[Any] = getattr(brief, section)
        for i, rec in enumerate(records):
            if not stale(rec.provenance) or (rec.tier != "Locked" and rec.decision != "accepted"):
                continue
            records[i] = rec.model_copy(
                update={"tier": "Default", "status": "assumed", "decision": "proposed"}
            )
            changed.append(rec.id)
    return changed


__all__ = [
    "Issue",
    "apply_fixes",
    "by_module",
    "downgrade_unsupported",
    "errors",
    "make_issue",
    "needs_model",
    "run_checks",
    "slug_key",
]
