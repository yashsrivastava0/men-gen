"""The closed list of item kinds and their ``data`` payloads (doc 13 section 9.2).

One payload model per kind. The same model is used twice:

* as the ``data`` of an item in ``research.json`` (validated per kind, RV-01);
* as the extraction wire format: ``wire_atom_model(kind)`` is the payload plus the common fields
  (``norm_key``, ``label``, ``claim``, ``quote``, ``impact``, ``published_on``) in ONE flat object, minus
  the fields in ``NON_WIRE`` (a URL a page could plant, or a flag only code sets). Flat objects keep the
  strict-schema depth at 5 and give the model nothing to act with: no URL, action or tool fields (I-11).

Every list is a list of short strings or of small flat objects; ``|`` joins enumerations into one string
(``values: "draft|sent|paid"``) so nesting never exceeds the strict-schema limit.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from mde_research_agent.domain.typed import ground_provider_fact

KINDS: tuple[str, ...] = (
    "competitor",
    "reference_check",
    "screen_pattern",
    "navigation_pattern",
    "role_pattern",
    "workflow",
    "entity_pattern",
    "lifecycle",
    "rule",
    "service_option",
    "provider_fact",
    "platform_constraint",
    "compliance_note",
    "design_convention",
    "script_need",
    "local_format",
    "data_hint",
    "trend",
)
Impact = Literal["normal", "high"]


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeatureLevel(W):
    feature_key: str
    level: Literal["core", "standard", "premium"] | None = (
        None  # null: the page does not say which tier
    )


class ScreenRef(W):
    screen_key: str


class ScreenState(W):
    state: Literal["loading", "empty", "error", "success", "default", "offline"]
    content: str | None = None  # null: the page does not describe what the state shows


class FieldSpec(W):
    name: str
    type: str | None = None  # null unless the passage states the type
    required: bool | None = None  # null unless the passage states it
    values: str | None = None  # allowed values joined with |; null unless the passage lists them
    validation: str | None = None


class Transition(W):
    from_state: str
    to_state: str
    trigger: str | None = None  # null: the page does not say what causes it


class CompetitorData(W):
    name: str
    kind: Literal["app", "platform", "service", "marketplace", "other"] | None = (
        None  # null: not stated
    )
    region: str | None = None
    url: str | None = None  # filled by code from the source page, never by the model
    platforms: list[str]
    pricing_model: str | None = None
    features: list[FeatureLevel]
    screens: list[ScreenRef]


class ReferenceCheckData(W):
    name: str
    status: Literal["found", "not_found", "ambiguous"]
    candidates: list[str]
    features: list[FeatureLevel]
    flows: list[str]


class ScreenPatternData(W):
    screen_key: str
    purpose: str | None = (
        None  # null: the page does not state it (the checker may null it: RV-01 must still pass)
    )
    role: str | None = None
    sections: list[str]  # ordered: never joined across sources
    key_components: list[str]
    actions: list[str]
    states: list[ScreenState]
    validations: list[str]
    microcopy_notes: list[str]
    section_variants: list[list[str]] = Field(
        default_factory=list
    )  # set by code when sources order differently


class NavigationPatternData(W):
    pattern: str
    when_used: str | None = None  # null: the page does not state it (the checker may null it)
    typical_items: list[str]


class RolePatternData(W):
    role: str
    typical_tasks: list[str]
    typical_permissions: list[str]


class WorkflowData(W):
    name: str
    role: str | None = None
    steps: list[str]  # ordered: never joined across sources
    alternates: list[str]
    exceptions: list[str]
    empty_error_offline_cases: list[str]
    step_variants: list[list[str]] = Field(
        default_factory=list
    )  # set by code when sources order differently


class EntityPatternData(W):
    entity: str
    fields: list[FieldSpec]
    relations: list[str]
    kpis: list[str]


class LifecycleData(W):
    entity: str
    states: list[str]
    transitions: list[Transition]  # ordered: never joined across sources
    transition_variants: list[list[str]] = Field(
        default_factory=list
    )  # "from>to: trigger" lines, set by code


class RuleData(W):
    statement: str
    subject: str
    value: str | None = None
    unit: str | None = None
    formula: str | None = None
    authority: str | None = None


class ServiceOptionData(W):
    need: str
    provider: str
    region_fit: str | None = None
    auth_method: str | None = None
    endpoints_or_events: list[str]
    sandbox: str | None = None
    constraints: list[str]
    fees: str | None = None
    limits: str | None = None
    integration_notes: str | None = None
    current_status: str | None = None


class TypedValue(W):
    """A figure the page states, in parts. Every part is optional and only filled when the quote states it; code checks
    the amount and the currency against the quote (``domain/typed.py``). ``amount`` is a decimal STRING, never a float."""

    amount: str | None = (
        None  # "2.9", "0.30", "1500": the number as the quote writes it, no separators or signs
    )
    currency: str | None = None  # ISO 4217 code, only when the quote shows the currency
    unit: str | None = None  # "percent", "transaction", "request", "day" ...
    period: str | None = None  # "month", "year", "business days" ...
    condition: str | None = None  # "above 10000", "for refunds": what the amount depends on


class ProviderFactData(W):
    """ONE fact about a provider with ONE quote (D-159): the dossier of a provider is assembled by code from these."""

    provider: str  # the name as the page states it
    need: str  # what the provider is for: payments, messaging, maps ...
    field: str  # a key of ``data/dossiers.yaml`` (code rejects any other)
    value: str  # the fact as the page states it
    typed: TypedValue | None = None  # fees, limits and times only
    countries: list[str]  # countries or regions the fact is stated for, as the page writes them


Platform = Literal["ios", "android", "both", "expo", "react_native", "web"]
EffectOnBuild = Literal[
    "needs_development_build", "needs_permission", "review_risk", "needs_config_plugin", "none"
]


class PlatformConstraintData(W):
    """One platform or toolchain rule for one capability (push, background location, in-app payments ...)."""

    capability: str
    platform: Platform | None = None  # null: the page does not say which platform
    rule: str
    effect_on_build: EffectOnBuild | None = (
        None  # null unless the page states what the rule means for the build (R27)
    )
    status: str | None = None  # "required", "deprecated", "recommended" ... only when stated


class ComplianceNoteData(W):
    topic: str
    authority: str | None = None
    summary: str
    effective_date: str | None = None
    regime: str | None = None  # the law, scheme or policy by name, as the page states it
    country: str | None = None  # the country it applies in, as the page writes it
    applies_to: list[str] = Field(
        default_factory=list
    )  # flows, roles or kinds of data it covers, as stated
    penalty: str | None = None  # the penalty or sanction, only when the page states one
    legal_adjacent: bool = True  # always true; set by code


class DesignConventionData(W):
    aspect: Literal[
        "layout",
        "navigation",
        "onboarding",
        "components",
        "color_mood",
        "typography",
        "density",
        "iconography",
        "motion",
        "accessibility",
    ]
    convention: str
    examples: list[str]
    applies_to_category: str | None = None


class ScriptNeedData(W):
    script: str
    languages: list[str]
    direction: Literal["ltr", "rtl"] | None = None  # null: the page does not state it
    font_notes: str | None = None
    text_expansion: str | None = None


class LocalFormatData(W):
    kind: Literal["currency", "phone", "address", "date", "number", "id_format"]
    pattern: str
    example: str | None = None
    validation: str | None = None


class DataHintData(W):
    kind: Literal["name_style", "price_range", "unit", "edge_case", "sample_record"]
    value_range: str | None = None
    examples: list[str]


class TrendData(W):
    topic: str
    what_changed: str
    since: str | None = None
    direction: Literal["rising", "declining", "stable", "unknown"] | None = None  # null: not stated


PAYLOADS: dict[str, type[W]] = {
    "competitor": CompetitorData,
    "reference_check": ReferenceCheckData,
    "screen_pattern": ScreenPatternData,
    "navigation_pattern": NavigationPatternData,
    "role_pattern": RolePatternData,
    "workflow": WorkflowData,
    "entity_pattern": EntityPatternData,
    "lifecycle": LifecycleData,
    "rule": RuleData,
    "service_option": ServiceOptionData,
    "provider_fact": ProviderFactData,
    "platform_constraint": PlatformConstraintData,
    "compliance_note": ComplianceNoteData,
    "design_convention": DesignConventionData,
    "script_need": ScriptNeedData,
    "local_format": LocalFormatData,
    "data_hint": DataHintData,
    "trend": TrendData,
}
# Fields the model must not produce (a planted URL; a flag only code sets).
NON_WIRE: dict[str, frozenset[str]] = {
    "competitor": frozenset({"url"}),
    "compliance_note": frozenset({"legal_adjacent"}),
    "workflow": frozenset({"step_variants"}),
    "lifecycle": frozenset({"transition_variants"}),
    "screen_pattern": frozenset({"section_variants"}),
}
# Ordered lists: sources are never concatenated (two step lists joined are a third, invented, list). The richest list
# is kept and the others go to the ``*_variants`` field of the same payload (doc 13, RC-3).
ORDERED_FIELDS: dict[str, tuple[str, str]] = {
    "workflow": ("steps", "step_variants"),
    "screen_pattern": ("sections", "section_variants"),
    "lifecycle": ("transitions", "transition_variants"),
}
COMMON_FIELDS = ("norm_key", "label", "claim", "quote", "impact", "about_region", "about_entity")


class AtomCommon(W):
    """Fields every extracted atom carries (flat, next to the kind's own fields)."""

    norm_key: str  # lowercase normalized key: the thing this atom is about ("class booking")
    label: str  # at most 8 words
    claim: str  # a paraphrase of at most 40 words
    quote: str  # a verbatim quote of at most 25 words copied from the passage
    impact: (
        Impact  # high: prices, fees, rules, legal or payment facts, formats that change validation
    )
    about_region: str  # ISO 3166-1 alpha-2 (lowercase) of the country the item is about, "global" or "unknown"
    about_entity: str | None = (
        None  # the company, product or authority the item is about, else null
    )


_WIRE_CACHE: dict[str, type[AtomCommon]] = {}


def wire_atom_model(kind: str) -> type[AtomCommon]:
    """The flat strict-schema atom for ``kind``: common fields plus the payload's wire fields."""
    if kind in _WIRE_CACHE:
        return _WIRE_CACHE[kind]
    payload = PAYLOADS[kind]
    skip = NON_WIRE.get(kind, frozenset())
    fields: dict[str, Any] = {
        name: (info.annotation, info)
        for name, info in payload.model_fields.items()
        if name not in skip
    }
    clash = set(fields) & set(COMMON_FIELDS)
    if clash:  # a payload field that shares a name with a common field would silently overwrite it
        raise ValueError(f"payload {kind} clashes with common fields: {sorted(clash)}")
    camel = "".join(part.capitalize() for part in kind.split("_"))
    model = create_model(f"{camel}Atom", __base__=AtomCommon, **fields)
    _WIRE_CACHE[kind] = model
    return model


def is_identifying(kind: str, field: str, sub: str) -> bool:
    """Is ``sub`` a REQUIRED attribute of the elements of the list ``field`` (a feature key, a state, a transition end)?
    Nulling it would leave an element that is not valid and means nothing, so the checker's path removes the element."""
    model = PAYLOADS.get(kind)
    info = model.model_fields.get(field) if model else None
    if info is None:
        return False
    for arg in getattr(info.annotation, "__args__", ()):
        fields = getattr(arg, "model_fields", None)
        if fields and sub in fields:
            return bool(fields[sub].is_required())
    return False


def payload_from_wire(kind: str, atom: AtomCommon) -> dict[str, Any]:
    """The ``data`` payload of an item from a validated wire atom (common fields removed).

    A kind with fields code can check against the atom's own quote (a typed value, a dossier field key) is checked here:
    a problem raises ``ValueError`` and the candidate becomes ``Rejected(invalid)``, one item at a time, so the model's
    other items on the page survive (the same path as a payload that does not fit its kind).
    """
    raw = atom.model_dump(mode="json", exclude=set(COMMON_FIELDS))
    data = PAYLOADS[kind].model_validate(raw).model_dump(mode="json")
    if kind == "provider_fact":
        return ground_provider_fact(data, atom.quote)
    return data


def validate_payload(kind: str, data: dict[str, Any]) -> list[str]:
    """Problems with ``data`` for ``kind`` (empty when valid): RV-01 'data matches kind'."""
    if kind not in PAYLOADS:
        return [f"unknown kind {kind!r}"]
    try:
        PAYLOADS[kind].model_validate(data)
    except ValueError as exc:
        return [str(exc).splitlines()[0]]
    return []
