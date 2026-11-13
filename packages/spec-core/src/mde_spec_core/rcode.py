"""R-code: the ONLY mechanical fixes (doc 18 3.2). Each is logged (never silent) and counted on the G2 page.

1 sort and normalise bytes; 2 mint a MISSING id (a duplicate id is never fixed by code); 3 instantiate a missing
standard state, behaviour, scenario or system screen from the library (provenance library, tier Default); 4 map an
unknown closed-vocabulary word through the EXACT-synonym table (else R-ask); 5 drop an unknown optional field;
6 downgrade a Locked claim without evidence to Default. An invented fact is removed, never repaired into a Locked one.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from pydantic import BaseModel

from . import libraries, vocab
from .callkit.ladder import FixKind, RCodeLog
from .canon import normalise_text
from .graph import Acceptance, Behavior, Prov, SpecGraph
from .ids import IdRegistry

_EVIDENCE = {"user", "compass", "policy"}


def sort_bytes(files: dict[str, str], log: RCodeLog | None = None) -> RCodeLog:
    """Fix 1: LF endings, trimmed lines, one final newline."""
    log = RCodeLog() if log is None else log
    for path, text in list(files.items()):
        if path.endswith((".md", ".json", ".yaml")):
            fixed = normalise_text(text)
            if fixed != text:
                files[path] = fixed
                log.record(
                    FixKind.SORT_BYTES, path, "normalised line endings and final newline", "", ""
                )
    return log


def mint_missing_ids(
    items: list[dict[str, Any]], kind: str, registry: IdRegistry, log: RCodeLog | None = None
) -> RCodeLog:
    """Fix 2: an item with no id gets one in the Spec namespace. A DUPLICATE id is left alone (R-ask)."""
    log = RCodeLog() if log is None else log
    for it in items:
        if not it.get("id"):
            it["id"] = registry.mint(kind)
            log.record(
                FixKind.MINT_MISSING_ID, it["id"], f"minted a missing {kind} id", "", it["id"]
            )
    return log


def instantiate_states(graph: SpecGraph, log: RCodeLog | None = None) -> RCodeLog:
    """Fix 3a: a missing standard state of the screen type's library entry is added (origin library, Default)."""
    log = RCodeLog() if log is None else log
    for s in graph.screens.values():
        if not libraries.has_state_library(s.screen_type):
            continue
        lib = libraries.state_library_for(s.screen_type, graph.meta.profile)
        have = {st.kind for st in s.states}
        for t in lib.states:
            if t.kind not in have:
                s.states.append(t.to_state_spec())
                if t.copy_key not in s.copy_keys:
                    s.copy_keys.append(t.copy_key)
                for loc in graph.meta.languages or [graph.meta.source_language]:
                    graph.locale_copy.setdefault(loc, {}).setdefault(t.copy_key, t.copy_text)
                log.record(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    s.id,
                    f"added standard state {t.kind}",
                    "",
                    t.kind,
                )
    return log


def instantiate_acceptance(graph: SpecGraph, log: RCodeLog | None = None) -> RCodeLog:
    """Fix 3b: fewer acceptance scenarios than the profile's minimum are topped up from the scenario library."""
    log = RCodeLog() if log is None else log
    for s in graph.screens.values():
        if not libraries.has_state_library(s.screen_type):
            continue
        lib = libraries.state_library_for(s.screen_type, graph.meta.profile)
        have = {(a.given, a.when, a.then) for a in s.acceptance}
        for tpl in lib.acceptance:
            a: Acceptance = tpl.to_acceptance(s.name)
            if len(s.acceptance) >= len(lib.acceptance):
                break
            if (a.given, a.when, a.then) not in have:
                s.acceptance.append(a)
                log.record(
                    FixKind.INSTANTIATE_LIBRARY_DEFAULT,
                    s.id,
                    "added a library acceptance scenario",
                    "",
                    a.given,
                )
    return log


def instantiate_behaviors(
    graph: SpecGraph, facts: Collection[str] | Mapping[str, bool], log: RCodeLog | None = None
) -> RCodeLog:
    """Fix 3c: a missing behaviour category the library can supply is instantiated (an app_specific one never is)."""
    log = RCodeLog() if log is None else log
    have = {b.category for b in graph.behaviors.values()}
    n = len(graph.behaviors)
    for cat in vocab.behavior_categories_for(graph.meta.profile, facts):
        if cat.name in have or cat.supplied_by == "app_specific" or not cat.default_text:
            continue
        n += 1
        bid = f"BHV-S{n:03d}"
        graph.behaviors[bid] = Behavior(
            id=bid,
            category=cat.name,
            text=cat.default_text,
            provenance=[Prov(kind="library", ref=f"behavior.{cat.name}")],
        )
        log.record(
            FixKind.INSTANTIATE_LIBRARY_DEFAULT,
            bid,
            f"instantiated library behaviour {cat.name}",
            "",
            cat.name,
        )
    return log


def apply_synonyms(graph: SpecGraph, log: RCodeLog | None = None) -> RCodeLog:
    """Fix 4: an unknown closed-vocabulary word becomes its canonical word only when the EXACT-synonym table says so."""
    log = RCodeLog() if log is None else log

    def fix(vocab_name: str, word: str) -> str | None:
        return (
            None
            if vocab.vocab_names(vocab_name) and word in vocab.vocab_names(vocab_name)
            else libraries.canonical(vocab_name, word)
        )

    for s in graph.screens.values():
        if (c := fix("screen_type", s.screen_type)) and c != s.screen_type:
            log.record(FixKind.EXACT_SYNONYM, s.id, "screen_type synonym", s.screen_type, c)
            s.screen_type = c
        for r in s.regions:
            if not r.shape.startswith("other:") and (c := fix("shape", r.shape)) and c != r.shape:
                log.record(FixKind.EXACT_SYNONYM, f"{s.id}.{r.key}", "shape synonym", r.shape, c)
                r.shape = c
        for a in s.actions:
            if (c := fix("intent", a.intent)) and c != a.intent:
                log.record(FixKind.EXACT_SYNONYM, f"{s.id}.{a.key}", "intent synonym", a.intent, c)
                a.intent = c
    return log


def drop_unknown_optional(
    raw: dict[str, Any], model: type[BaseModel], node_id: str, log: RCodeLog | None = None
) -> RCodeLog:
    """Fix 5: a key the model has no field for (a model over-filled) is dropped, and logged. Operates on raw dicts."""
    log = RCodeLog() if log is None else log
    for k in [k for k in raw if k not in model.model_fields]:
        log.record(FixKind.DROP_UNKNOWN_OPTIONAL, node_id, f"dropped unknown field {k}", raw[k], "")
        del raw[k]
    return log


def downgrade_unevidenced_locked(graph: SpecGraph, log: RCodeLog | None = None) -> RCodeLog:
    """Fix 6: Locked without user, document or policy evidence (or resting on research or an assumption) becomes Default."""
    log = RCodeLog() if log is None else log
    for n in graph.all_nodes():
        if n.tier != "locked":
            continue
        kinds = {p.kind for p in n.provenance}
        if not kinds & _EVIDENCE or n.assumed:
            log.record(
                FixKind.DOWNGRADE_LOCKED_TO_DEFAULT,
                n.id,
                "Locked without evidence",
                "locked",
                "default",
            )
            n.tier = "default"
    return log


def run_rcode(graph: SpecGraph, facts: Collection[str] | Mapping[str, bool] = ()) -> RCodeLog:
    """Every graph-level fix, in a fixed order. Returns the log shown on the G2 page."""
    log = RCodeLog()
    apply_synonyms(graph, log)
    downgrade_unevidenced_locked(graph, log)
    instantiate_states(graph, log)
    instantiate_acceptance(graph, log)
    instantiate_behaviors(graph, facts, log)
    return log
