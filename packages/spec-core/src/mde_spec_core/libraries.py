"""Libraries as versioned data (doc 15 7.2): state library, system screens, scenarios, error catalogue,
exact synonyms and the form-vocabulary lint.

Content lives in `data/libraries/*.yaml`; this module loads it (cached, strict models) and exposes typed
accessors. Code instantiates library content into the graph so a model never writes it (doc 18 3.2).
No model, no network.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from functools import cache, lru_cache
from string import Formatter
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import vocab
from .canon import sha256_json
from .config import DATA_DIR, default_config
from .graph import Acceptance, StateSpec
from .vocab import PROFILE_ORDER, normalise_word

LIB_DIR = DATA_DIR / "libraries"

LIBRARY_FILES = (
    "error_catalogue",
    "form_words",
    "scenarios",
    "state_library",
    "synonyms",
    "system_screens",
)


class LibraryError(ValueError):
    """A library file is malformed, or a lookup asked for something that does not exist."""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


@cache
def _raw(name: str) -> dict[str, Any]:
    path = LIB_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "version" not in data:
        raise LibraryError(f"{path.name}: a mapping with a `version` key is required")
    return data


@lru_cache(maxsize=1)
def libraries_version() -> str:
    """Hash of every library file's parsed content (a result-cache key part)."""
    return sha256_json({name: _raw(name) for name in LIBRARY_FILES})


@lru_cache(maxsize=1)
def content_version() -> str:
    """Vocabularies and libraries together: one key for 'the data Spec ran with'."""
    return sha256_json({"vocab": vocab.vocab_version(), "libraries": libraries_version()})


# ------------------------------------------------------------------ scenarios


class ScenarioDef(_M):
    id: str
    name: str
    applies_to: Any
    mock_profile: str
    states: list[str]
    given: str
    when: str
    then: str
    extra: bool = False

    @field_validator("applies_to")
    @classmethod
    def _applies(cls, v: Any) -> Any:
        if v == "any" or (isinstance(v, list) and v and all(isinstance(s, str) for s in v)):
            return v
        raise ValueError("applies_to is `any` or a list of screen types")

    def applies(self, screen_type: str) -> bool:
        return self.applies_to == "any" or screen_type in self.applies_to


class ScenariosFile(_M):
    version: int
    scenarios: list[ScenarioDef]


@lru_cache(maxsize=1)
def scenarios() -> dict[str, ScenarioDef]:
    f = ScenariosFile.model_validate(_raw("scenarios"))
    out: dict[str, ScenarioDef] = {}
    for s in f.scenarios:
        if s.id in out:
            raise LibraryError(f"duplicate scenario id {s.id}")
        out[s.id] = s
    return out


def scenario(scenario_id: str) -> ScenarioDef:
    try:
        return scenarios()[scenario_id]
    except KeyError:
        raise LibraryError(f"unknown scenario '{scenario_id}'") from None


def _fill(text: str, screen: str) -> str:
    return text.replace("{screen}", screen)


class AcceptanceTemplate(_M):
    """A scenario ready to attach to a screen: wording may be a type-specific override."""

    id: str
    name: str
    mock_profile: str
    given: str
    when: str
    then: str

    def to_acceptance(self, screen_name: str) -> Acceptance:
        return Acceptance(
            given=_fill(self.given, screen_name),
            when=_fill(self.when, screen_name),
            then=_fill(self.then, screen_name),
        )


# ------------------------------------------------------------------ state library


class StateEntryRaw(_M):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: str
    message: str | None = None
    copy_text: str | None = Field(default=None, alias="copy")


class BehaviorRaw(_M):
    id: str
    text: str
    copy_keys: list[str] = []


class ScenarioOverride(_M):
    given: str
    when: str
    then: str


class ScreenTypeLibRaw(_M):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    states: list[StateEntryRaw]
    extra_copy: dict[str, str] = Field(default_factory=dict, alias="copy")
    behaviors: list[BehaviorRaw]
    acceptance: dict[Literal["compact", "standard", "deep"], list[str]]
    acceptance_overrides: dict[str, ScenarioOverride] = {}


class CommonStateRaw(_M):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    message: str
    copy_text: str = Field(alias="copy")


class CommonRaw(_M):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    states: dict[str, CommonStateRaw]
    extra_copy: dict[str, str] = Field(alias="copy")


class StateLibraryFile(_M):
    version: int
    common: CommonRaw
    screen_types: dict[str, ScreenTypeLibRaw]


@lru_cache(maxsize=1)
def state_library_file() -> StateLibraryFile:
    return StateLibraryFile.model_validate(_raw("state_library"))


def _state_key(screen_type: str, kind: str, *, common: bool) -> str:
    if common:
        return f"lib.common.{kind}"
    domain = vocab.state_kinds_file().domain_prefix
    if kind.startswith(domain):
        return f"lib.{screen_type}.domain.{kind[len(domain) :]}"
    return f"lib.{screen_type}.{kind}"


class StateTemplate(_M):
    kind: str
    message: str
    copy_key: str
    copy_text: str

    def to_state_spec(self) -> StateSpec:
        return StateSpec(kind=self.kind, message=self.message, origin="library")


class BehaviorTemplate(_M):
    id: str
    text: str
    copy_keys: list[str] = []


class StateLibraryEntry(_M):
    screen_type: str
    profile: str
    states: list[StateTemplate]
    behaviors: list[BehaviorTemplate]
    acceptance: list[AcceptanceTemplate]

    def required_state_kinds(self) -> list[str]:
        return [s.kind for s in self.states]


def _resolve_states(screen_type: str) -> list[StateTemplate]:
    lib = state_library_file()
    raw = lib.screen_types[screen_type]
    out: list[StateTemplate] = []
    for st in raw.states:
        if st.message is not None and st.copy_text is not None:
            out.append(
                StateTemplate(
                    kind=st.kind,
                    message=st.message,
                    copy_key=_state_key(screen_type, st.kind, common=False),
                    copy_text=st.copy_text,
                )
            )
            continue
        default = lib.common.states.get(st.kind)
        if default is None:
            raise LibraryError(
                f"state_library {screen_type}: '{st.kind}' needs a message and copy (no common default)"
            )
        out.append(
            StateTemplate(
                kind=st.kind,
                message=st.message if st.message is not None else default.message,
                copy_key=_state_key(screen_type, st.kind, common=st.copy_text is None),
                copy_text=st.copy_text if st.copy_text is not None else default.copy_text,
            )
        )
    return out


@lru_cache(maxsize=1)
def copy_table() -> dict[str, str]:
    """Every library copy key with its source-language (English) text. Keys are unique."""
    lib = state_library_file()
    table: dict[str, str] = {}

    def put(key: str, text: str) -> None:
        if key in table and table[key] != text:
            raise LibraryError(f"duplicate library copy key {key}")
        table[key] = text

    for kind, st in lib.common.states.items():
        put(f"lib.common.{kind}", st.copy_text)
    for name, text in lib.common.extra_copy.items():
        put(f"lib.common.{name}", text)
    for stype, raw in lib.screen_types.items():
        for tmpl in _resolve_states(stype):
            put(tmpl.copy_key, tmpl.copy_text)
        for name, text in raw.extra_copy.items():
            put(f"lib.{stype}.{name}", text)
    return table


def _acceptance_ids(screen_type: str, profile: str) -> list[str]:
    raw = state_library_file().screen_types[screen_type].acceptance
    ids: list[str] = []
    for prof in ("compact", "standard", "deep"):
        if PROFILE_ORDER[prof] <= PROFILE_ORDER[profile]:
            ids.extend(raw.get(prof, []))
    return ids


def acceptance_template(screen_type: str, scenario_id: str) -> AcceptanceTemplate:
    sc = scenario(scenario_id)
    ov = state_library_file().screen_types[screen_type].acceptance_overrides.get(scenario_id)
    return AcceptanceTemplate(
        id=sc.id,
        name=sc.name,
        mock_profile=sc.mock_profile,
        given=ov.given if ov else sc.given,
        when=ov.when if ov else sc.when,
        then=ov.then if ov else sc.then,
    )


def has_state_library(screen_type: str) -> bool:
    return screen_type in state_library_file().screen_types


def state_library_for(screen_type: str, profile: str) -> StateLibraryEntry:
    """Required states, standard behaviours and default acceptance scenarios for a screen type.

    Acceptance is cumulative by detail profile: compact 3, standard 5, deep 8 at least (spec-config).
    """
    if profile not in PROFILE_ORDER:
        raise LibraryError(f"unknown detail profile '{profile}'")
    if not has_state_library(screen_type):
        raise LibraryError(f"no state library entry for screen type '{screen_type}'")
    raw = state_library_file().screen_types[screen_type]
    return StateLibraryEntry(
        screen_type=screen_type,
        profile=profile,
        states=_resolve_states(screen_type),
        behaviors=[BehaviorTemplate(**b.model_dump()) for b in raw.behaviors],
        acceptance=[
            acceptance_template(screen_type, sid) for sid in _acceptance_ids(screen_type, profile)
        ],
    )


# ------------------------------------------------------------------ system screens


class SysVariant(_M):
    name: str
    appears_when: Any


class SysRegion(_M):
    key: str
    what: str
    shape: str
    importance: str
    attrs: list[str] = []


class SysAction(_M):
    key: str
    intent: str
    what: str
    importance: str
    when: Any = None
    destructive: bool = False
    irreversible: bool = False


class SysInput(_M):
    key: str
    type: str
    required: bool = False


class SysProvenance(_M):
    kind: Literal["policy", "library"]
    ref: str


class SystemScreen(_M):
    id: str
    name: str
    screen_type: str
    appears_when: Any
    purpose: str
    regions: list[SysRegion]
    actions: list[SysAction]
    provenance: SysProvenance
    inputs: list[SysInput] = []
    variants: list[SysVariant] = []
    suppressed_in_profiles: list[Literal["compact", "standard", "deep"]] = []
    link_out_allowed: bool = False
    per: Literal["permission"] | None = None


class NotScreen(_M):
    name: str
    kind: Literal["configuration", "state"]
    note: str
    behavior_category: str | None = None
    scenario: str | None = None


class SystemScreensFile(_M):
    version: int
    screens: list[SystemScreen]
    not_screens: list[NotScreen]


@lru_cache(maxsize=1)
def system_screens_file() -> SystemScreensFile:
    return SystemScreensFile.model_validate(_raw("system_screens"))


@lru_cache(maxsize=1)
def system_screens() -> dict[str, SystemScreen]:
    return {s.id: s for s in system_screens_file().screens}


def system_screens_for(
    facts: Collection[str] | Mapping[str, bool],
    profile: str | None = None,
) -> list[SystemScreen]:
    """The system screens that apply to an app with these facts (declaration order).

    `profile` drops screens suppressed for that detail profile (onboarding is off for compact).
    """
    out: list[SystemScreen] = []
    for s in system_screens().values():
        if not vocab.evaluate_predicate(s.appears_when, facts):
            continue
        if profile is not None and profile in s.suppressed_in_profiles:
            continue
        out.append(s)
    return out


def active_variants(screen: SystemScreen, facts: Collection[str] | Mapping[str, bool]) -> list[str]:
    """The variants (states of one screen) that apply, such as phone_code and email_password."""
    return [v.name for v in screen.variants if vocab.evaluate_predicate(v.appears_when, facts)]


def not_screens() -> list[NotScreen]:
    return list(system_screens_file().not_screens)


# ------------------------------------------------------------------ error catalogue


class ErrorDef(_M):
    code: str
    validators: list[str]
    severity: Literal["error", "warning", "info"]
    audience: Literal["agent", "owner"]
    repair_class: Literal["A", "B", "C"]
    args: list[str]
    where: str
    why: str
    fix: str


class ErrorCatalogueFile(_M):
    version: int
    repair_classes: dict[str, str]
    errors: list[ErrorDef]


@lru_cache(maxsize=1)
def errors() -> dict[str, ErrorDef]:
    f = ErrorCatalogueFile.model_validate(_raw("error_catalogue"))
    out: dict[str, ErrorDef] = {}
    for e in f.errors:
        if e.code in out:
            raise LibraryError(f"duplicate error code {e.code}")
        out[e.code] = e
    return out


def error(code: str) -> ErrorDef:
    try:
        return errors()[code]
    except KeyError:
        raise LibraryError(f"unknown error code '{code}'") from None


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def make_error(code: str, **args: Any) -> dict[str, str]:
    """A structured, agent-readable error {code, severity, audience, repair_class, where, why, fix}."""
    e = error(code)
    missing = [a for a in e.args if a not in args]
    if missing:
        raise LibraryError(f"error {code} needs arguments {missing}")
    fmt = {a: args[a] for a in e.args}
    return {
        "code": e.code,
        "severity": e.severity,
        "audience": e.audience,
        "repair_class": e.repair_class,
        "where": e.where.format(**fmt),
        "why": e.why.format(**fmt),
        "fix": e.fix.format(**fmt),
    }


# ------------------------------------------------------------------ synonyms


class SynonymEntry(_M):
    to: str
    attrs: list[str] = []


class NotSynonym(_M):
    word: str
    why: str


class SynonymsFile(_M):
    version: int
    not_synonyms: list[NotSynonym]
    vocabs: dict[str, dict[str, str | SynonymEntry]]


@lru_cache(maxsize=1)
def synonyms_file() -> SynonymsFile:
    return SynonymsFile.model_validate(_raw("synonyms"))


def synonym_entry(vocab_name: str, word: str) -> SynonymEntry | None:
    """The exact synonym of `word` in a closed vocabulary, or None. `btn` is never a synonym."""
    table = synonyms_file().vocabs.get(vocab_name)
    if table is None:
        raise LibraryError(f"no synonym table for vocabulary '{vocab_name}'")
    hit = table.get(normalise_word(word))
    if hit is None:
        return None
    return SynonymEntry(to=hit) if isinstance(hit, str) else hit


def synonym(vocab_name: str, word: str) -> str | None:
    """The vocabulary value `word` is an exact synonym of, or None (then the item is asked again)."""
    entry = synonym_entry(vocab_name, word)
    return entry.to if entry else None


def canonical(vocab_name: str, word: str) -> str | None:
    """`word` if it is already a vocabulary value, else its exact synonym, else None."""
    norm = normalise_word(word)
    if norm in vocab.vocab_names(vocab_name):
        return norm
    return synonym(vocab_name, word)


# ------------------------------------------------------------------ form-vocabulary lint (V-25)


class FormAllow(_M):
    phrases: list[str]
    by_screen_type: dict[str, list[str]]


class FormWordsFile(_M):
    version: int
    deny: dict[str, list[str]]
    hex_pattern: str
    allow: FormAllow


@lru_cache(maxsize=1)
def form_words_file() -> FormWordsFile:
    return FormWordsFile.model_validate(_raw("form_words"))


class FormWordFinding(_M):
    word: str
    category: str
    start: int


@lru_cache(maxsize=1)
def _deny_patterns() -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for category, words in form_words_file().deny.items():
        for w in words:
            # Units may follow digits ("12px"), so they only need a letter-free start.
            start = r"(?<![A-Za-z])" if category == "unit" else r"\b"
            if w.endswith("*"):
                pat = rf"{start}{re.escape(w[:-1])}\w*"
            else:
                pat = start + r"\s+".join(re.escape(p) for p in w.split()) + r"\b"
            out.append((category, re.compile(pat, re.IGNORECASE)))
    out.append(("colour", re.compile(form_words_file().hex_pattern)))
    return out


@lru_cache(maxsize=1)
def _allow_phrase_pattern() -> re.Pattern[str]:
    phrases = sorted(form_words_file().allow.phrases, key=len, reverse=True)
    return re.compile("|".join(re.escape(p) for p in phrases), re.IGNORECASE)


def form_word_findings(text: str, screen_type: str | None = None) -> list[FormWordFinding]:
    """Form words in free text (V-25): position, widget, unit, motion and colour words, and hex values.

    Allowed content uses (right-to-left, "top 20", a map on a map screen) are not reported.
    """
    scrubbed = _allow_phrase_pattern().sub(lambda m: " " * len(m.group(0)), text)
    allowed = {w.lower() for w in form_words_file().allow.by_screen_type.get(screen_type or "", [])}
    found: list[FormWordFinding] = []
    for category, pat in _deny_patterns():
        for m in pat.finditer(scrubbed):
            if m.group(0).lower() in allowed:
                continue
            found.append(FormWordFinding(word=m.group(0), category=category, start=m.start()))
    return sorted(found, key=lambda f: f.start)


def key_names_a_widget(key: str) -> bool:
    """An identifier that ends in a widget or trigger word names a widget, not a meaning (V-25)."""
    suffixes = default_config().checks.widget_key_suffixes
    return any(key.endswith(s) for s in suffixes)


# ------------------------------------------------------------------ cross-reference check


def library_texts() -> Iterable[tuple[str, str, str | None]]:
    """Every free text the lint must keep clean: (where, text, screen_type).

    The error catalogue, the synonym table and the form-word list are excluded: they must name the words.
    """
    for sid, sc in scenarios().items():
        for field in ("given", "when", "then", "name"):
            yield f"scenario {sid}.{field}", getattr(sc, field), None
    lib = state_library_file()
    for kind, st in lib.common.states.items():
        yield f"common state {kind}.message", st.message, None
        yield f"common state {kind}.copy", st.copy_text, None
    for name, text in lib.common.extra_copy.items():
        yield f"common copy {name}", text, None
    for stype, raw in lib.screen_types.items():
        for st in raw.states:
            if st.message:
                yield f"{stype} state {st.kind}.message", st.message, stype
            if st.copy_text:
                yield f"{stype} state {st.kind}.copy", st.copy_text, stype
        for name, text in raw.extra_copy.items():
            yield f"{stype} copy {name}", text, stype
        for b in raw.behaviors:
            yield f"{stype} behaviour {b.id}", b.text, stype
        for sid, ov in raw.acceptance_overrides.items():
            for field in ("given", "when", "then"):
                yield f"{stype} override {sid}.{field}", getattr(ov, field), stype
    for sysc in system_screens().values():
        yield f"system {sysc.id}.purpose", sysc.purpose, sysc.screen_type
        for r in sysc.regions:
            yield f"system {sysc.id} region {r.key}", r.what, sysc.screen_type
        for a in sysc.actions:
            yield f"system {sysc.id} action {a.key}", a.what, sysc.screen_type
    for c in vocab.behavior_categories().values():
        yield f"behaviour category {c.name}.default_text", c.default_text, None


def check_libraries() -> list[str]:
    """Every cross-reference inside and between library files; an empty list means consistent."""
    problems: list[str] = []
    mins = default_config().profiles.acceptance_scenarios_min
    lib = state_library_file()
    types = vocab.screen_types()
    for t in types:
        if t not in lib.screen_types:
            problems.append(f"state_library: no entry for screen type {t}")
    table = copy_table()
    for stype, raw in lib.screen_types.items():
        if stype not in types:
            problems.append(f"state_library: unknown screen type {stype}")
            continue
        for st in raw.states:
            if not vocab.is_known_state_kind(st.kind):
                problems.append(f"state_library {stype}: unknown state kind {st.kind}")
        kinds = [s.kind for s in raw.states]
        if len(kinds) != len(set(kinds)):
            problems.append(f"state_library {stype}: a state kind is listed twice")
        for b in raw.behaviors:
            problems += [
                f"state_library {stype} behaviour {b.id}: unknown copy key {k}"
                for k in b.copy_keys
                if k not in table
            ]
        for prof in ("compact", "standard", "deep"):
            ids = _acceptance_ids(stype, prof)
            if len(ids) != len(set(ids)):
                problems.append(f"state_library {stype}: a scenario is repeated by {prof}")
            if len(ids) < mins[prof]:
                problems.append(
                    f"state_library {stype}: {len(ids)} scenarios for {prof}, need {mins[prof]}"
                )
            for sid in ids:
                sc = scenarios().get(sid)
                if sc is None:
                    problems.append(f"state_library {stype}: unknown scenario {sid}")
                elif not sc.applies(stype):
                    problems.append(f"state_library {stype}: scenario {sid} does not apply")
        deep = set(_acceptance_ids(stype, "deep"))
        for need in ("permission_denied", "edit_conflict"):
            if need not in deep:
                problems.append(f"state_library {stype}: deep profile lacks {need}")
        for sid in raw.acceptance_overrides:
            if sid not in deep:
                problems.append(f"state_library {stype}: override for unused scenario {sid}")
    for sid, sc in scenarios().items():
        if sc.mock_profile not in vocab.mock_profiles():
            problems.append(f"scenario {sid}: unknown mock profile {sc.mock_profile}")
        problems += [
            f"scenario {sid}: unknown state {s}"
            for s in sc.states
            if not vocab.is_known_state_kind(s)
        ]
        if sc.applies_to != "any":
            problems += [
                f"scenario {sid}: unknown screen type {t}" for t in sc.applies_to if t not in types
            ]
    problems += _check_system_screens()
    problems += _check_errors()
    problems += _check_synonyms()
    return problems


def _check_system_screens() -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for s in system_screens().values():
        if s.id in seen:
            problems.append(f"system screen {s.id} is listed twice")
        seen.add(s.id)
        if s.screen_type not in vocab.screen_types():
            problems.append(f"system {s.id}: unknown screen type {s.screen_type}")
        problems += [f"system {s.id}: {p}" for p in vocab.check_predicate(s.appears_when)]
        for v in s.variants:
            problems += [
                f"system {s.id} variant {v.name}: {p}"
                for p in vocab.check_predicate(v.appears_when)
            ]
        keys: list[str] = []
        for r in s.regions:
            keys.append(r.key)
            if vocab.shape_status(r.shape) == "unknown":
                problems.append(f"system {s.id} region {r.key}: unknown shape {r.shape}")
            problems += [
                f"system {s.id} region {r.key}: {p}"
                for p in vocab.check_region_shape(r.shape, None, r.attrs)
            ]
            if r.importance not in vocab.region_importances():
                problems.append(f"system {s.id} region {r.key}: unknown importance {r.importance}")
        for a in s.actions:
            keys.append(a.key)
            if not vocab.is_known_intent(a.intent):
                problems.append(f"system {s.id} action {a.key}: unknown intent {a.intent}")
            if a.importance not in vocab.action_importances():
                problems.append(f"system {s.id} action {a.key}: unknown importance {a.importance}")
            if a.when is not None:
                problems += [
                    f"system {s.id} action {a.key}: {p}" for p in vocab.check_predicate(a.when)
                ]
        for i in s.inputs:
            keys.append(i.key)
            if not vocab.is_known_input_type(i.type):
                problems.append(f"system {s.id} input {i.key}: unknown type {i.type}")
        if len(keys) != len(set(keys)):
            problems.append(f"system {s.id}: a region, action or input key is used twice")
        problems += [
            f"system {s.id}: key {k} names a widget" for k in keys if key_names_a_widget(k)
        ]
    for n in not_screens():
        if n.behavior_category and n.behavior_category not in vocab.behavior_categories():
            problems.append(
                f"not_screen {n.name}: unknown behaviour category {n.behavior_category}"
            )
        if n.scenario and n.scenario not in scenarios():
            problems.append(f"not_screen {n.name}: unknown scenario {n.scenario}")
    return problems


def _check_errors() -> list[str]:
    problems: list[str] = []
    covered: set[str] = set()
    for e in errors().values():
        covered.update(e.validators)
        used = placeholders(e.where) | placeholders(e.why) | placeholders(e.fix)
        if used - set(e.args):
            problems.append(
                f"error {e.code}: placeholders {sorted(used - set(e.args))} not in args"
            )
        if set(e.args) - used:
            problems.append(f"error {e.code}: args {sorted(set(e.args) - used)} unused")
    for n in range(1, 46):
        v = f"V-{n:02d}"
        if v != "V-18" and v not in covered:  # V-18 is superseded by V-26
            problems.append(f"no error code for validator {v}")
    return problems


def _check_synonyms() -> list[str]:
    problems: list[str] = []
    banned = {normalise_word(n.word) for n in synonyms_file().not_synonyms}
    for vname, table in synonyms_file().vocabs.items():
        try:
            names = vocab.vocab_names(vname)
        except KeyError:
            problems.append(f"synonyms: unknown vocabulary {vname}")
            continue
        for word, hit in table.items():
            entry = SynonymEntry(to=hit) if isinstance(hit, str) else hit
            if word != normalise_word(word):
                problems.append(f"synonyms {vname}: key '{word}' is not normalised")
            if word in names:
                problems.append(f"synonyms {vname}: '{word}' is already a vocabulary value")
            if word in banned:
                problems.append(f"synonyms {vname}: '{word}' must never be a synonym")
            if entry.to not in names:
                problems.append(f"synonyms {vname}: '{word}' maps to unknown '{entry.to}'")
            if entry.attrs:
                if vname != "shape":
                    problems.append(f"synonyms {vname}: only shapes carry attrs ('{word}')")
                else:
                    problems += [
                        f"synonyms shape '{word}': {p}"
                        for p in vocab.check_region_shape(entry.to, None, entry.attrs)
                    ]
    return problems
