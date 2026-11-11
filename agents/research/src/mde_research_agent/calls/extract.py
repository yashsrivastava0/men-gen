"""RSCH-2: extraction. Reads passages of ONE page and returns typed, flat items, each with a verbatim quote.

One call spec per job (``rsch.extract.<job>``): its output is one array per kind the job produces (competitors,
screen patterns, rules, ...), each item flat (common fields plus the kind's own fields, no URL, action or tool
fields), so the strict schema stays at depth 5 and a page can plant nothing the pipeline would act on. CODE verifies
every quote against the stored page afterwards (RV-02). The call has no tools and the page text is wrapped as
untrusted. Mock handler: a deterministic extractor that builds valid items from real sentences of the passages
(quotes are verbatim substrings), so offline runs exercise the whole pipeline. Fallback: nothing extracted.
"""

from __future__ import annotations

import types
import typing
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, create_model
from pydantic.fields import FieldInfo

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.catalog.loader import JOB_NAMES
from mde_research_agent.core.passages import SEPARATOR, windows
from mde_research_agent.core.text import STOP_WORDS, tokens
from mde_research_agent.domain.atoms import COMMON_FIELDS, PAYLOADS, AtomCommon, wire_atom_model

JOB_KINDS: dict[str, tuple[str, ...]] = {
    "features": ("competitor", "reference_check", "trend"),
    "structure": ("screen_pattern", "navigation_pattern", "role_pattern"),
    "flows": ("workflow",),
    "data": ("entity_pattern", "lifecycle", "rule", "compliance_note"),
    "services": ("service_option", "compliance_note"),
    "design": ("design_convention", "script_need", "trend"),
    "screens": ("screen_pattern",),
    "local": ("local_format", "data_hint"),
}
KIND_GUIDE: dict[str, str] = {
    "competitor": "competitor: one comparable app or product: its name, kind, region, platforms, pricing model, and every feature it lists (feature_key short lowercase, level core/standard/premium) and screens it shows.",
    "reference_check": "reference_check: what the page says about a named app the person mentioned: status found, not_found or ambiguous, candidates, its features and flows.",
    "screen_pattern": "screen_pattern: one screen: screen_key, purpose, role, sections in order, key components, user actions, states (loading, empty, error, success, offline with what each shows), validations, microcopy notes.",
    "navigation_pattern": "navigation_pattern: a navigation pattern (bottom_tabs, drawer, stack, dashboard_hub, wizard ...), when it is used, typical items.",
    "role_pattern": "role_pattern: a user role, its typical tasks and permissions.",
    "workflow": "workflow: one journey: name, role, ordered steps (one short action each), alternates, exceptions, empty/error/offline cases.",
    "entity_pattern": "entity_pattern: one data entity: its fields (name, type, required, allowed values joined with |, validation), relations to other entities, KPIs.",
    "lifecycle": "lifecycle: an entity's states and the transitions between them with the trigger of each.",
    "rule": "rule: one business or regulatory rule: statement, subject, value, unit, formula, authority. Copy numbers exactly.",
    "service_option": "service_option: one provider or portal for a need: provider, region fit, auth method, endpoints or webhook events, sandbox, constraints, fees, limits, integration notes, current status.",
    "compliance_note": "compliance_note: a regulatory or compliance requirement as information: topic, authority, summary, effective date.",
    "design_convention": "design_convention: one UI/UX convention with its aspect (layout, navigation, onboarding, components, color_mood, typography, density, iconography, motion, accessibility), the convention and examples.",
    "script_need": "script_need: font, direction and text-expansion needs of a script: script, languages, direction, font notes, text expansion.",
    "local_format": "local_format: an unusual format or ID: kind (currency, phone, address, date, number, id_format), pattern, example, validation.",
    "data_hint": "data_hint: realistic sample data: kind (name_style, price_range, unit, edge_case, sample_record), value range, several examples.",
    "trend": "trend: something that changed recently: topic, what changed, since when, direction (rising, declining, stable, unknown).",
}


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractIn(W):
    job: str
    question: str
    anchor: str
    place: str
    language: str  # the language of the page text, when known
    focus: str  # what to pull out for this job
    kinds: list[str]  # kinds this call may return
    want_kinds: list[str]  # kinds the topic's open slots need most
    kind_guides: list[str]
    slots: list[str]  # plain descriptions of what the topic still needs
    page_title: str
    page_domain: str
    page_published: str | None = None
    passages: str
    retry_keys: list[str]  # a second try: copy these items' quotes EXACTLY from the passages


_MODELS: dict[str, type[BaseModel]] = {}


def extract_out_model(job: str) -> type[BaseModel]:
    """Output model of ``rsch.extract.<job>``: one array of flat items per kind of the job."""
    if job not in _MODELS:
        fields: dict[str, Any] = {k: (list[wire_atom_model(k)], ...) for k in JOB_KINDS[job]}  # type: ignore[misc]
        _MODELS[job] = create_model(f"Extract{job.capitalize()}Out", __base__=W, **fields)
    return _MODELS[job]


# --- the deterministic mock extractor -------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOP_WORDS and len(t) > 2]


def _phrase(sentence: str, n: int = 3) -> str:
    return " ".join(list(dict.fromkeys(_words(sentence)))[:n]) or "item"


def _fill(annotation: Any, sentence: str, depth: int = 0) -> Any:
    """A valid value for a field of a payload, built from the sentence's own words."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        non_null = [a for a in args if a is not type(None)]
        return _fill(non_null[0], sentence, depth) if non_null else None
    if origin is Literal:
        return args[0]
    if origin is list:
        inner = args[0]
        if inner is str:
            return list(dict.fromkeys(_words(sentence)))[:6]
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return [_fill_model(inner, sentence, i) for i in range(3)]
        return []
    if annotation is str:
        return _phrase(sentence, 4)
    if annotation is bool:
        return True
    if annotation is int:
        return 1
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _fill_model(annotation, sentence, 0)
    return None


def _fill_model(model: type[BaseModel], sentence: str, i: int) -> dict[str, Any]:
    words = list(dict.fromkeys(_words(sentence)))
    out: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        out[name] = _fill_field(info, sentence, words, i)
    return out


def _fill_field(info: FieldInfo, sentence: str, words: list[str], i: int) -> Any:
    ann = info.annotation
    if ann is str:
        return words[i] if i < len(words) else (words[0] if words else "item")
    return _fill(ann, sentence)


def synthesize_payload(kind: str, sentence: str) -> dict[str, Any]:
    """Payload fields of ``kind`` filled from ``sentence`` (valid per the kind's model, no wire-excluded fields)."""
    wire = wire_atom_model(kind)
    out: dict[str, Any] = {}
    for name, info in wire.model_fields.items():
        if name in COMMON_FIELDS:
            continue
        out[name] = _fill(info.annotation, sentence)
    return out


def _quote_of(sentence: str, max_words: int = 25) -> str:
    return " ".join(sentence.split()[:max_words])


def mock_extract(inp: ExtractIn) -> BaseModel:
    """Deterministic items from real sentences: the same input always gives the same output."""
    model = extract_out_model(inp.job)
    text = inp.passages
    spans = windows(text, window_chars=180)
    cues = {w for t in (inp.anchor, *inp.slots, inp.question) for w in _words(t)}
    scored: list[tuple[int, int, str]] = []
    for start, end in spans:
        sentence = " ".join(text[start:end].replace(SEPARATOR.strip(), " ").split())
        if len(sentence.split()) < 6:
            continue
        scored.append((-len(cues & set(_words(sentence))), start, sentence))
    scored.sort()
    kinds = [k for k in (inp.want_kinds or inp.kinds) if k in inp.kinds] or list(inp.kinds[:1])
    data: dict[str, list[dict[str, Any]]] = {k: [] for k in inp.kinds}
    for i, (_, _, sentence) in enumerate(scored[:4]):
        kind = kinds[i % len(kinds)]
        words = list(dict.fromkeys(_words(sentence)))
        common = {
            "norm_key": " ".join(words[:3]) or "item",
            "label": " ".join(sentence.split()[:6]),
            "claim": " ".join(sentence.split()[:38]),
            "quote": _quote_of(sentence),
            "impact": "high"
            if any(c.isdigit() for c in sentence)
            and kind in {"rule", "compliance_note", "service_option", "local_format"}
            else "normal",
            "published_on": inp.page_published,
        }
        data[kind].append({**common, **synthesize_payload(kind, sentence)})
    return model.model_validate(data)


def handle(inp: ExtractIn) -> BaseModel:
    return mock_extract(inp)


def nothing(inp: ExtractIn) -> BaseModel:
    return extract_out_model(inp.job).model_validate({k: [] for k in inp.kinds})


def _spec_for(job: str) -> ResearchCallSpec[ExtractIn, Any]:
    return ResearchCallSpec(
        spec=CallSpec(
            id=f"rsch.extract.{job}",
            version=1,
            input_model=ExtractIn,
            output_model=extract_out_model(job),
            system_template="extract.system.jinja",
            user_template="extract.user.jinja",
            template_root=Path(__file__).parent / "templates",
            route="fast",
            reasoning_effort="low",
            max_output_tokens=9000,
            mock_handler="mde_research_agent.calls.extract:handle",
            untrusted_inputs=("page_title", "passages"),
            cacheable=True,
            repair=True,
            fallback="research.extract",
        ),
        timeout_s=60.0,
        fallback=nothing,
    )


SPECS: dict[str, ResearchCallSpec[ExtractIn, Any]] = {job: _spec_for(job) for job in JOB_NAMES}

__all__ = ["JOB_KINDS", "KIND_GUIDE", "SPECS", "ExtractIn", "extract_out_model", "mock_extract"]
_ = (AtomCommon, PAYLOADS)  # re-exported for callers that build atoms
