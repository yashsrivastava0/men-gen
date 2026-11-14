"""Predicate facts (``vocab/predicates.yaml``) that hold for an input (doc 15 3.1, 7.4).

Derived by code from the sign-in methods, the account model, the integrations, the locales, the sensitive-data flags and the
features the person wants in this release (a feature the person deferred is not read: live tracking they put off does not
make the app realtime). Word lists live in ``data/facts.yaml``. Research may add a hint through its own profile signals.
"""

from __future__ import annotations

import re
from functools import cache

from mde_compass_intake import ResearchView, SpecInput
from mde_spec_core import vocab

from ._data import facts_data


@cache
def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z])(?:{alt})(?![A-Za-z])", re.IGNORECASE)


def _hit(text: str, words: list[str]) -> bool:
    return bool(words) and _pattern(tuple(words)).search(text) is not None


def _norm(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def _wanted_features(spec_input: SpecInput):
    return [
        f
        for f in spec_input.in_scope_features()
        if f.scope_status == "in" and not f.deferred_by_user
    ]


def _brief_text(spec_input: SpecInput) -> str:
    """The Brief's own words, for the inference of facts nobody stated: the idea, the audience, every in-scope feature, the
    roles, the entities and their field names, the screens and the stated rules and domain facts."""
    parts = [spec_input.idea.summary, *spec_input.audience]
    parts += [f"{f.name} {f.intent}" for f in spec_input.in_scope_features()]
    parts += [f"{r.name} {r.goal}" for r in spec_input.clean_roles()]
    for e in spec_input.structure.entities:
        parts.append(f"{e.name} " + " ".join(x.key.replace("_", " ") for x in e.fields))
    parts += [s.name for s in spec_input.structure.screens]
    parts += [f.statement for f in spec_input.rules_and_numbers]
    parts += [f.statement for f in spec_input.domain_facts]
    return " ".join(p for p in parts if p)


def infer_sensitive(spec_input: SpecInput) -> dict[str, list[str]]:
    """Kinds of sensitive data (``facts.yaml`` ``sensitive_kinds``) the Brief's words imply and Compass did not state, with
    the words that matched. Empty when every implied kind is stated: nothing is inferred over what the person said."""
    data = facts_data()
    stated = {_norm(t) for t in spec_input.sensitive_data}
    text = _brief_text(spec_input)
    out: dict[str, list[str]] = {}
    for kind, row in sorted(data.sensitive_kinds.items()):
        if stated & {_norm(w) for w in row.stated}:
            continue
        found = sorted({m.group(0).lower() for m in _pattern(tuple(row.words)).finditer(text)})
        if found:
            out[kind] = found
    return out


def facts_from_input(
    spec_input: SpecInput, research_view: ResearchView | None = None, *, infer: bool = True
) -> set[str]:
    """The predicate facts that hold. Every name is a name in ``vocab/predicates.yaml`` (checked by the tests).
    ``infer=False`` leaves out what is only inferred from the Brief's words (what the seed marks as assumed)."""
    data = facts_data()
    facts: set[str] = {"always"}

    # --- accounts and sign-in -------------------------------------------------------------------------------
    methods = {_norm(v) for v in spec_input.auth_methods.values}
    for fact, words in data.auth_methods.items():
        if methods & {_norm(w) for w in words}:
            facts.add(fact)
    model = {_norm(v) for v in spec_input.account_model.values}
    has_auth_screen = any(s.pattern == "auth" for s in spec_input.structure.screens)
    if (
        methods
        or has_auth_screen
        or (model and model != {"none"})
        or any(r.apps or r.goal for r in spec_input.clean_roles())
    ):
        facts.add("accounts")
    if "accounts" in facts and not (model & {_norm(w) for w in data.managed_accounts}):
        facts.add("accounts_creatable_in_app")
    if model & {_norm(w) for w in data.multi_role_accounts}:
        facts.add("multi_role_user")

    # --- words in what the person wants in this release ------------------------------------------------------
    feature_text = " ".join(f"{f.name} {f.intent}" for f in _wanted_features(spec_input))
    screen_text = " ".join(s.name for s in spec_input.structure.screens)
    flow_text = " ".join(f.name for f in spec_input.structure.flows)
    haystack = f"{feature_text} {screen_text} {flow_text}"
    for fact, words in data.keywords.items():
        # screen names only count for the facts a screen name proves (an inbox, a photo screen)
        text = haystack if fact not in {"payments", "realtime"} else f"{feature_text} {flow_text}"
        if _hit(text, words):
            facts.add(fact)
    if any(s.pattern == "notifications" for s in spec_input.structure.screens):
        facts.add("in_app_notifications")
    if any(s.pattern == "checkout" for s in spec_input.structure.screens):
        facts.add("payments")

    # --- integrations -----------------------------------------------------------------------------------------
    for integ in spec_input.integrations:
        for fact in data.integrations.get(_norm(integ.name), []):
            facts.add(fact)
    if "payments" in facts:
        # money moves through the app (an integration, a checkout screen or a payment feature): a provider's own
        # step is implied whether or not Compass listed the integration
        facts.add("provider_side_flow")

    # --- sensitive data, languages, entity field types -----------------------------------------------------------
    if spec_input.sensitive_data:
        facts.add("sensitive_data")
        if {_norm(t) for t in spec_input.sensitive_data} & {
            _norm(t) for t in data.identity_sensitive
        }:
            facts.add("minors_or_health_or_identity")
    if infer and infer_sensitive(spec_input):
        facts |= {"sensitive_data", "minors_or_health_or_identity"}  # assumed, never stated
    if len(spec_input.locales.codes) > 1:
        facts.add("multi_language")
    if spec_input.locales.rtl:
        facts.add("right_to_left")
    for ent in spec_input.structure.entities:
        for fld in ent.fields:
            for fact in data.field_types.get(_norm(fld.type), []):
                facts.add(fact)

    # --- research hints ---------------------------------------------------------------------------------------------
    if research_view is not None and not research_view.thin:
        for signal in research_view.profile.get("signals", []) or []:
            for fact in data.research_signals.get(_norm(str(signal)), []):
                facts.add(fact)

    if "push_notifications" in facts:
        facts.add("deep_links")
    # a fact never named by the vocabulary would be a bug here, not in the data
    known = set(vocab.predicates())
    unknown = facts - known
    if unknown:
        raise ValueError(f"facts_from_input produced unknown predicates {sorted(unknown)}")
    return facts
