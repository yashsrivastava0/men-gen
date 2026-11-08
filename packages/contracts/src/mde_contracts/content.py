"""Content models: the shapes of every YAML/JSON data file in ``mde_content`` (brief 8.6).

Everything that varies by archetype, company or policy is data validated by these models.

Domain-pack reference conventions (normative; ``mde_spec_builder`` B2 expands them):
- Pack screens are identified by a local ``key``; inside a pack, other screens are referenced as
  ``@<key>`` (e.g. ``go: "@cart"``). B2 assigns ``SCR-###`` ids and rewrites ``@key``.
- Pack flows list screen keys; B2 rewrites them to ids and assigns ``FLW-###``.
- Entities, API operations, roles and integrations already carry their final ids
  (``ENT-Product``, ``API-getProduct``, ``ROLE-customer``, ``INT-payments``).
- A pack screen/flow with ``feature`` set is included only when that feature id is selected in
  ``scope.features`` (core features are always on).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mde_contracts.cards import Card
from mde_contracts.common import Frozen
from mde_contracts.slots import SlotData, SlotDef
from mde_contracts.spec import (
    ApiOperation,
    DataBinding,
    DesignTokens,
    Entity,
    EnumDef,
    Integration,
    Role,
    ScreenAction,
)

# --------------------------------------------------------------------------- catalogs


class ArchetypeDef(Frozen):
    id: str  # "commerce" | "food_delivery" | "booking_services" | "internal_workforce" | "custom"
    label: str
    description: str
    keywords: list[str] = []
    multi_sided: bool = False
    i18n: dict[str, dict[str, str]] = Field(default_factory=dict)  # lang -> {label, description}


class SensitiveDomain(Frozen):
    id: str  # "health" | "finance" | "children" | "identity_documents" | "precise_location"
    label: str
    keywords: list[str] = []
    compliance_flags: list[str] = []
    conservative_defaults: dict[str, SlotData] = Field(default_factory=dict)  # slot id -> value


class SlotCatalog(Frozen):
    slots: list[SlotDef]


class Catalogs(Frozen):
    """``catalogs.yaml``: archetypes + sensitive domains + slot definitions in one file."""

    archetypes: list[ArchetypeDef]
    sensitive_domains: list[SensitiveDomain]
    slots: list[SlotDef]


# --------------------------------------------------------------------------- question bank


class StarterChip(Frozen):
    id: str
    label: str
    prefill: str | None = None  # text sent as if typed
    sets: dict[str, SlotData] = Field(default_factory=dict)
    archetype: str | None = None
    i18n: dict[str, str] = Field(default_factory=dict)  # lang -> label


class UiCopy(Frozen):
    """Canned, no-LLM copy with per-language variants. ``en`` is required."""

    id: str  # "greeting" | "nudge" | "finish_with_defaults" | "didnt_catch" | ...
    text: dict[str, str]  # lang -> text


class KnowledgeSnippet(Frozen):
    """Facts C3 may answer from (spec 3.2). C3 must say 'unknown' for anything not covered."""

    id: str
    topic: str  # "privacy" | "preview" | "code_download" | "how_it_works" | "cost" | ...
    text: dict[str, str]  # lang -> text
    keywords: list[str] = []


class QuestionBankFile(Frozen):
    """One file per archetype plus ``shared`` (archetype == "shared")."""

    archetype: str
    cards: list[Card]
    inspiration_cards: list[Card] = []  # help mode (three examples)
    starter_chips: list[StarterChip] = []
    ui_copy: list[UiCopy] = []
    knowledge: list[KnowledgeSnippet] = []


# --------------------------------------------------------------------------- domain packs


class PackFeature(Frozen):
    id: str
    label: str
    description: str = ""
    tier: Literal["core", "standard", "extra"] = "standard"  # core = always on
    default_on: bool = True
    i18n: dict[str, str] = Field(default_factory=dict)


class PackScreen(Frozen):
    key: str  # local key, unique in the pack
    route: str
    surface: str = "customer"
    recipe: str  # recipe id
    purpose: str
    auth: str = "public"
    feature: str | None = None
    data: list[DataBinding] = []
    sections: list[str] = []
    actions: list[ScreenAction] = []
    states: dict[str, dict[str, str]] = {}
    texts: dict[str, str] = {}
    checks: list[str] = []


class PackFlow(Frozen):
    key: str
    name: str
    primary: bool = False
    screens: list[str]  # screen keys, ordered
    feature: str | None = None
    purpose: str | None = None


class ComplianceTrigger(Frozen):
    id: str
    label: str
    sensitive_domain: str  # SensitiveDomain.id
    keywords: list[str] = []
    cards: list[str] = []  # must-confirm card ids
    conservative_defaults: dict[str, SlotData] = Field(default_factory=dict)
    research_hint: str | None = None  # topic for R4


class PackNavNode(Frozen):
    """Navigation node as authored in a pack: ``screen`` is ``@key`` (not ``SCR-###``).
    B2 rewrites it into a ``spec.NavNode`` with real screen ids (contracts 1.0.1, ADR-0003)."""

    id: str
    route: str
    kind: Literal["tabs", "stack", "modal", "screen", "auth_gate"]
    screen: str | None = Field(default=None, pattern=r"^@[a-z][a-z0-9_]*$")
    label: str | None = None
    auth: str = "public"
    children: list[PackNavNode] = []


class FeatureScreenParts(Frozen):
    """Pieces of one always-on screen that exist only when a feature is on."""

    sections: list[str] = []  # section names
    actions: list[str] = []  # action names
    data: list[str] = []  # exact ``bind`` strings


class FeatureParts(Frozen):
    """Everything a feature owns beyond the screens and flows that name it with ``feature``."""

    operations: list[str] = []  # ApiOperation ids
    entities: list[str] = []  # Entity ids
    integrations: list[str] = []  # Integration ids
    screens: dict[str, FeatureScreenParts] = {}  # screen key -> parts


class DomainPack(Frozen):
    id: str  # equals the archetype id
    archetype: str
    label: str
    description: str = ""
    features: list[PackFeature]
    roles: list[Role]
    entities: list[Entity]
    enums: list[EnumDef] = []
    screens: list[PackScreen]
    flows: list[PackFlow]
    navigation: PackNavNode  # screens referenced by ``@key``
    api_operations: list[ApiOperation]
    integrations: list[Integration] = []
    compliance_triggers: list[ComplianceTrigger] = []
    theme_candidates: list[str] = []  # the three look options shown (theme ids)
    default_theme: str | None = None
    # Feature-gated pieces the flat lists cannot express (operations, entities, integrations and
    # sections/actions inside always-on screens). Core features own nothing here.
    feature_parts: dict[str, FeatureParts] = {}


PackNavNode.model_rebuild()


# --------------------------------------------------------------------------- recipes / themes / stack


class Recipe(Frozen):
    id: str  # "list", "detail-with-sticky-cta", "form", ...
    name: str
    description: str
    kind: Literal[
        "auth",
        "profile",
        "settings",
        "list",
        "detail",
        "form",
        "search",
        "notifications",
        "dashboard",
        "other",
    ]
    states_provided: list[Literal["loading", "empty", "error", "offline"]] = [
        "loading",
        "empty",
        "error",
    ]
    default_sections: list[str] = []
    kit_components: list[str] = []  # kit components the recipe uses (signatures go in slices)


class RecipeLibrary(Frozen):
    recipes: list[Recipe]


class Theme(Frozen):
    id: str  # "clean-bright"
    label: str
    description: str
    tokens: DesignTokens
    contrast_pairs: list[tuple[str, str]] = [("text", "background"), ("text_on_primary", "primary")]
    nav_style: str = "bottom-tabs"


class ThemeLibrary(Frozen):
    themes: list[Theme]


class Capability(Frozen):
    id: str  # "camera" | "location" | "push" | "biometrics" | "maps" | "payments" | ...
    label: str
    supported: bool = True
    needs_dev_build: bool = False
    preview_limit: str | None = None  # what the web/Expo-Go preview cannot show
    note: str | None = None
    keywords: list[str] = []


class UnsupportedType(Frozen):
    id: str  # "game" | "ar" | "desktop" | "background_audio" | "hardware_heavy"
    label: str
    keywords: list[str] = []
    reason: str
    closest_option: str


class StackProfile(Frozen):
    id: str  # "expo-managed"
    label: str
    pinned_at_build: list[str] = ["expo_sdk", "react_native"]  # never hardcode version numbers
    capabilities: list[Capability]
    unsupported_types: list[UnsupportedType]
    max_screens_guided: int = 50
