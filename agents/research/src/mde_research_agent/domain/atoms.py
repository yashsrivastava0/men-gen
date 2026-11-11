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

from pydantic import BaseModel, ConfigDict, create_model

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
    level: Literal["core", "standard", "premium"]


class ScreenRef(W):
    screen_key: str


class ScreenState(W):
    state: Literal["loading", "empty", "error", "success", "default", "offline"]
    content: str


class FieldSpec(W):
    name: str
    type: str
    required: bool
    values: str | None = None  # allowed values joined with |
    validation: str | None = None


class Transition(W):
    from_state: str
    to_state: str
    trigger: str


class CompetitorData(W):
    name: str
    kind: Literal["app", "platform", "service", "marketplace", "other"]
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
    purpose: str
    role: str | None = None
    sections: list[str]
    key_components: list[str]
    actions: list[str]
    states: list[ScreenState]
    validations: list[str]
    microcopy_notes: list[str]


class NavigationPatternData(W):
    pattern: str
    when_used: str
    typical_items: list[str]


class RolePatternData(W):
    role: str
    typical_tasks: list[str]
    typical_permissions: list[str]


class WorkflowData(W):
    name: str
    role: str | None = None
    steps: list[str]
    alternates: list[str]
    exceptions: list[str]
    empty_error_offline_cases: list[str]


class EntityPatternData(W):
    entity: str
    fields: list[FieldSpec]
    relations: list[str]
    kpis: list[str]


class LifecycleData(W):
    entity: str
    states: list[str]
    transitions: list[Transition]


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


class ComplianceNoteData(W):
    topic: str
    authority: str | None = None
    summary: str
    effective_date: str | None = None
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
    direction: Literal["ltr", "rtl"]
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
    direction: Literal["rising", "declining", "stable", "unknown"]


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
}
COMMON_FIELDS = ("norm_key", "label", "claim", "quote", "impact", "published_on")


class AtomCommon(W):
    """Fields every extracted atom carries (flat, next to the kind's own fields)."""

    norm_key: str  # lowercase normalized key: the thing this atom is about ("class booking")
    label: str  # at most 8 words
    claim: str  # a paraphrase of at most 40 words
    quote: str  # a verbatim quote of at most 25 words copied from the passage
    impact: (
        Impact  # high: prices, fees, rules, legal or payment facts, formats that change validation
    )
    published_on: str | None = (
        None  # date the page or the claim states (YYYY-MM-DD, YYYY-MM or YYYY)
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


def payload_from_wire(kind: str, atom: AtomCommon) -> dict[str, Any]:
    """The ``data`` payload of an item from a validated wire atom (common fields removed)."""
    raw = atom.model_dump(mode="json", exclude=set(COMMON_FIELDS))
    return PAYLOADS[kind].model_validate(raw).model_dump(mode="json")


def validate_payload(kind: str, data: dict[str, Any]) -> list[str]:
    """Problems with ``data`` for ``kind`` (empty when valid): RV-01 'data matches kind'."""
    if kind not in PAYLOADS:
        return [f"unknown kind {kind!r}"]
    try:
        PAYLOADS[kind].model_validate(data)
    except ValueError as exc:
        return [str(exc).splitlines()[0]]
    return []
