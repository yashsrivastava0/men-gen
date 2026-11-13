"""Closed vocabularies as versioned data (doc 15 2.2, doc 17 2.2, 2.3, 2.7, 2.8, 4.1).

Everything here is read from `data/vocab/*.yaml`; a vocabulary change is a data change. The graph
keeps these values as plain strings and validators ask this module whether a value is known.
No model, no network. Loaders are cached; `vocab_version()` is a hash for result-cache keys.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from functools import cache, lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from .canon import sha256_json
from .config import DATA_DIR

VOCAB_DIR = DATA_DIR / "vocab"

Profile = Literal["compact", "standard", "deep"]
PROFILE_ORDER: dict[str, int] = {"compact": 0, "standard": 1, "deep": 2}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class VocabError(ValueError):
    """A vocabulary file is malformed or inconsistent."""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalise_word(word: str) -> str:
    """Lowercase; spaces and hyphens become underscores (so `right-click` is `right_click`)."""
    return re.sub(r"[\s\-]+", "_", word.strip().lower())


# ------------------------------------------------------------------ file access


@cache
def _raw(name: str) -> dict[str, Any]:
    path = VOCAB_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "version" not in data:
        raise VocabError(f"{path.name}: a mapping with a `version` key is required")
    return data


VOCAB_FILES = (
    "asset_kinds",
    "behavior_categories",
    "direction_axes",
    "importance",
    "input_types",
    "intents",
    "mock_profiles",
    "motion_intents",
    "predicates",
    "screen_types",
    "shapes",
    "state_kinds",
)


@lru_cache(maxsize=1)
def vocab_version() -> str:
    """Hash of every vocabulary file's parsed content: a result-cache key part (doc 15 10.5)."""
    return sha256_json({name: _raw(name) for name in VOCAB_FILES})


# ------------------------------------------------------------------ shapes


class ShapeAttr(_M):
    name: str
    type: Literal["enum", "ref", "integer", "bool"]
    values: list[str] = []
    what: str = ""


class Shape(_M):
    name: str
    what: str
    example: str = ""
    variant_attr: str | None = None
    needs_cardinality: bool = False
    attributes: list[ShapeAttr] = []

    def attribute(self, name: str) -> ShapeAttr | None:
        for attr in self.attributes:
            if attr.name == name:
                return attr
        return None


class Bucket(_M):
    min: int
    max: int | None


class ShapesFile(_M):
    version: int
    other_prefix: str
    cardinality_buckets: dict[str, Bucket]
    common_attributes: list[ShapeAttr]
    shapes: list[Shape]


@lru_cache(maxsize=1)
def shapes_file() -> ShapesFile:
    return ShapesFile.model_validate(_raw("shapes"))


@lru_cache(maxsize=1)
def shapes() -> dict[str, Shape]:
    return {s.name: s for s in shapes_file().shapes}


def shape(name: str) -> Shape | None:
    return shapes().get(name)


def is_known_shape(name: str) -> bool:
    """True only for a shape in the vocabulary. `other:<name>` is not known (see `shape_status`)."""
    return name in shapes()


def shape_status(name: str) -> Literal["known", "other", "unknown"]:
    """`other:<name>` is allowed and goes to the gap list (V-38); anything else is unknown."""
    if name in shapes():
        return "known"
    prefix = shapes_file().other_prefix
    if name.startswith(prefix) and _NAME_RE.match(name[len(prefix) :]):
        return "other"
    return "unknown"


def cardinality_bucket(count: int) -> str:
    """The bucket a count falls into: one, few, many or very_many (count 0 counts as `one`)."""
    for name, b in shapes_file().cardinality_buckets.items():
        if count >= b.min and (b.max is None or count <= b.max):
            return name
    return "one"


def check_region_shape(
    shape_name: str, shape_kind: str | None = None, attrs: Iterable[str] = ()
) -> list[str]:
    """Plain problems with a region's shape, variant and `name=value` attributes (V-38). Empty = fine.

    An `other:<name>` shape is accepted with any attributes (it is gap-listed elsewhere).
    """
    status = shape_status(shape_name)
    if status == "unknown":
        return [f"unknown shape '{shape_name}'; use a known shape or other:<name>"]
    if status == "other":
        return []
    sh = shapes()[shape_name]
    problems: list[str] = []
    if shape_kind is not None:
        if sh.variant_attr is None:
            problems.append(
                f"shape '{shape_name}' has no variants; shape_kind '{shape_kind}' is not allowed"
            )
        else:
            va = sh.attribute(sh.variant_attr)
            if va is not None and va.type == "enum" and shape_kind not in va.values:
                problems.append(
                    f"shape '{shape_name}' variant '{shape_kind}' is not one of {va.values}"
                )
    allowed = {a.name: a for a in shapes_file().common_attributes}
    allowed.update({a.name: a for a in sh.attributes})
    for raw in attrs:
        name, sep, value = raw.partition("=")
        spec = allowed.get(name)
        if not sep or spec is None:
            problems.append(f"shape '{shape_name}' has no attribute '{name}'")
        elif spec.type == "enum" and value not in spec.values:
            problems.append(f"attribute '{name}' of '{shape_name}' must be one of {spec.values}")
        elif spec.type == "integer" and not value.lstrip("-").isdigit():
            problems.append(f"attribute '{name}' of '{shape_name}' must be a whole number")
        elif spec.type == "bool" and value not in ("true", "false"):
            problems.append(f"attribute '{name}' of '{shape_name}' must be true or false")
        elif spec.type == "ref" and not value:
            problems.append(f"attribute '{name}' of '{shape_name}' needs a reference")
    return problems


# ------------------------------------------------------------------ intents and inputs


class Intent(_M):
    name: str
    group: str
    what: str
    input_type: str | None = None
    phone_ok: bool
    destructive: bool
    irreversible: bool
    needs_confirmation: bool


class ForbiddenIntent(_M):
    name: str
    why: str


class IntentsFile(_M):
    version: int
    groups: dict[str, str]
    forbidden_on_phone: list[ForbiddenIntent]
    intents: list[Intent]


@lru_cache(maxsize=1)
def intents_file() -> IntentsFile:
    return IntentsFile.model_validate(_raw("intents"))


@lru_cache(maxsize=1)
def intents() -> dict[str, Intent]:
    return {i.name: i for i in intents_file().intents}


def intent(name: str) -> Intent | None:
    return intents().get(name)


def is_known_intent(name: str) -> bool:
    return name in intents()


def is_phone_forbidden_intent(name: str) -> bool:
    """Hover, right-click, tooltip and breadcrumb cannot exist on a phone (V-37 error)."""
    return normalise_word(name) in {f.name for f in intents_file().forbidden_on_phone}


class InputType(_M):
    name: str
    what: str
    sensitive: bool = False
    needs_permission: str | None = None


class FieldOnlyType(_M):
    name: str
    what: str


class InputTypesFile(_M):
    version: int
    input_types: list[InputType]
    field_only_types: list[FieldOnlyType]


@lru_cache(maxsize=1)
def input_types_file() -> InputTypesFile:
    return InputTypesFile.model_validate(_raw("input_types"))


@lru_cache(maxsize=1)
def input_types() -> dict[str, InputType]:
    return {t.name: t for t in input_types_file().input_types}


def is_known_input_type(name: str, *, field: bool = False) -> bool:
    """Form inputs use the 13 semantic types; data fields (`field=True`) also use id, enum, list, ref."""
    if name in input_types():
        return True
    return field and name in {t.name for t in input_types_file().field_only_types}


# ------------------------------------------------------------------ motion, assets, mock


class MotionIntent(_M):
    name: str
    group: str
    essential: bool
    what: str


class MotionIntentsFile(_M):
    version: int
    groups: dict[str, str]
    motion_intents: list[MotionIntent]


@lru_cache(maxsize=1)
def motion_intents_file() -> MotionIntentsFile:
    return MotionIntentsFile.model_validate(_raw("motion_intents"))


@lru_cache(maxsize=1)
def motion_intents() -> dict[str, MotionIntent]:
    return {m.name: m for m in motion_intents_file().motion_intents}


def is_essential_motion(name: str) -> bool:
    m = motion_intents().get(name)
    return bool(m and m.essential)


class AssetKind(_M):
    name: str
    what: str
    source_policies: list[str]
    needs_alt_text: bool
    needs_license_rule: bool
    data_bound_capable: bool
    fallback: str


class AssetKindsFile(_M):
    version: int
    purposes: list[str]
    source_policies: list[str]
    asset_kinds: list[AssetKind]


@lru_cache(maxsize=1)
def asset_kinds_file() -> AssetKindsFile:
    return AssetKindsFile.model_validate(_raw("asset_kinds"))


@lru_cache(maxsize=1)
def asset_kinds() -> dict[str, AssetKind]:
    return {k.name: k for k in asset_kinds_file().asset_kinds}


class MockProfile(_M):
    name: str
    what: str
    list_size: str
    errors: str
    demonstrates: list[str]


class MockProfilesFile(_M):
    version: int
    mock_profiles: list[MockProfile]
    domain_state_profile: str


@lru_cache(maxsize=1)
def mock_profiles_file() -> MockProfilesFile:
    return MockProfilesFile.model_validate(_raw("mock_profiles"))


@lru_cache(maxsize=1)
def mock_profiles() -> dict[str, MockProfile]:
    return {m.name: m for m in mock_profiles_file().mock_profiles}


def mock_profile_for_state(kind: str) -> str | None:
    """The mock profile that can demonstrate a state kind (V-41); `domain:*` use the domain profile."""
    if kind.startswith(state_kinds_file().domain_prefix):
        return mock_profiles_file().domain_state_profile
    sk = state_kinds().get(kind)
    return sk.demonstrated_on if sk else None


# ------------------------------------------------------------------ screen types, states, importance


class ScreenTypeDef(_M):
    name: str
    what: str
    adds: list[str]
    typical_shapes: list[str]
    typical_intents: list[str]


class ScreenTypesFile(_M):
    version: int
    screen_types: list[ScreenTypeDef]


@lru_cache(maxsize=1)
def screen_types() -> dict[str, ScreenTypeDef]:
    f = ScreenTypesFile.model_validate(_raw("screen_types"))
    return {t.name: t for t in f.screen_types}


def is_known_screen_type(name: str) -> bool:
    return name in screen_types()


class StateKindDef(_M):
    name: str
    what: str
    recovery: str
    demonstrated_on: str


class StateKindsFile(_M):
    version: int
    domain_prefix: str
    state_kinds: list[StateKindDef]


@lru_cache(maxsize=1)
def state_kinds_file() -> StateKindsFile:
    return StateKindsFile.model_validate(_raw("state_kinds"))


@lru_cache(maxsize=1)
def state_kinds() -> dict[str, StateKindDef]:
    return {s.name: s for s in state_kinds_file().state_kinds}


def is_known_state_kind(kind: str) -> bool:
    """A base state kind, or `domain:<name>` with a well-formed name."""
    if kind in state_kinds():
        return True
    prefix = state_kinds_file().domain_prefix
    return kind.startswith(prefix) and bool(_NAME_RE.match(kind[len(prefix) :]))


class Level(_M):
    name: str
    what: str


class ImportanceFile(_M):
    version: int
    region: list[Level]
    action: list[Level]
    navigation: list[str]


@lru_cache(maxsize=1)
def importance_file() -> ImportanceFile:
    return ImportanceFile.model_validate(_raw("importance"))


def region_importances() -> list[str]:
    return [lv.name for lv in importance_file().region]


def action_importances() -> list[str]:
    return [lv.name for lv in importance_file().action]


# ------------------------------------------------------------------ direction axes


class AxisValue(_M):
    name: str
    what: str
    suits: list[str] = []


class Axis(_M):
    name: str
    field: str
    default: str
    values: list[AxisValue]

    @property
    def value_names(self) -> list[str]:
        return [v.name for v in self.values]


class DirectionAxesFile(_M):
    version: int
    diff_axes: list[str]
    axes: list[Axis]
    ambition_defaults: dict[str, str]


@lru_cache(maxsize=1)
def direction_axes_file() -> DirectionAxesFile:
    return DirectionAxesFile.model_validate(_raw("direction_axes"))


@lru_cache(maxsize=1)
def direction_axes() -> dict[str, Axis]:
    return {a.name: a for a in direction_axes_file().axes}


def direction_axis_differences(a: Mapping[str, str], b: Mapping[str, str]) -> list[str]:
    """The diff axes on which two directions (axis name -> value) differ."""
    return [ax for ax in direction_axes_file().diff_axes if a.get(ax) != b.get(ax)]


# ------------------------------------------------------------------ predicates


class PredicateDef(_M):
    name: str
    what: str


class PredicatesFile(_M):
    version: int
    predicates: list[PredicateDef]


@lru_cache(maxsize=1)
def predicates() -> dict[str, PredicateDef]:
    f = PredicatesFile.model_validate(_raw("predicates"))
    return {p.name: p for p in f.predicates}


def check_predicate(pred: Any) -> list[str]:
    """Problems with a structured predicate: `name | {all: [...]} | {any: [...]} | {not: p}`."""
    if isinstance(pred, str):
        return [] if pred in predicates() else [f"unknown predicate '{pred}'"]
    if isinstance(pred, dict) and len(pred) == 1:
        (op, arg), *_ = pred.items()
        if op in ("all", "any") and isinstance(arg, list) and arg:
            return [p for sub in arg for p in check_predicate(sub)]
        if op == "not":
            return check_predicate(arg)
    return [f"malformed predicate {pred!r}"]


def evaluate_predicate(pred: Any, facts: Collection[str] | Mapping[str, bool]) -> bool:
    """Evaluate a structured predicate against the facts that hold (a set of names, or name -> bool)."""
    holds = (
        (lambda n: bool(facts.get(n, False)))
        if isinstance(facts, Mapping)
        else (lambda n: n in facts)
    )

    def ev(p: Any) -> bool:
        if isinstance(p, str):
            return p == "always" or holds(p)
        (op, arg), *_ = p.items()
        if op == "all":
            return all(ev(s) for s in arg)
        if op == "any":
            return any(ev(s) for s in arg)
        if op == "not":
            return not ev(arg)
        raise VocabError(f"malformed predicate {p!r}")

    return ev(pred)


# ------------------------------------------------------------------ behaviour categories


class BehaviorCategory(_M):
    name: str
    what: str
    supplied_by: Literal["library", "mixed", "app_specific"]
    applies_if: Any
    required_in: list[Profile]
    default_text: str = ""

    @field_validator("applies_if")
    @classmethod
    def _pred_shape(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and len(v) == 1 and next(iter(v)) in ("all", "any", "not"):
            return v
        raise ValueError(f"malformed predicate {v!r}")


class BehaviorCategoriesFile(_M):
    version: int
    behavior_categories: list[BehaviorCategory]


@lru_cache(maxsize=1)
def behavior_categories() -> dict[str, BehaviorCategory]:
    f = BehaviorCategoriesFile.model_validate(_raw("behavior_categories"))
    return {c.name: c for c in f.behavior_categories}


def behavior_categories_for(
    profile: str, facts: Collection[str] | Mapping[str, bool]
) -> list[BehaviorCategory]:
    """Categories V-35 requires for this detail profile and these facts (declaration order)."""
    return [
        c
        for c in behavior_categories().values()
        if profile in c.required_in and evaluate_predicate(c.applies_if, facts)
    ]


# ------------------------------------------------------------------ cross-reference check and names

_NAME_SOURCES: dict[str, str] = {
    "shape": "shapes",
    "intent": "intents",
    "screen_type": "screen_types",
    "state_kind": "state_kinds",
    "importance": "importance",
    "input_type": "input_types",
    "mock_profile": "mock_profiles",
    "asset_kind": "asset_kinds",
    "motion_intent": "motion_intents",
    "behavior_category": "behavior_categories",
}


def vocab_names(vocab: str) -> set[str]:
    """The closed set of names of one vocabulary (the keys of synonyms.yaml's `vocabs`)."""
    if vocab == "shape":
        return set(shapes())
    if vocab == "intent":
        return set(intents())
    if vocab == "screen_type":
        return set(screen_types())
    if vocab == "state_kind":
        return set(state_kinds())
    if vocab == "importance":
        return set(region_importances()) | set(action_importances())
    if vocab == "input_type":
        return set(input_types())
    if vocab == "mock_profile":
        return set(mock_profiles())
    if vocab == "asset_kind":
        return set(asset_kinds())
    if vocab == "motion_intent":
        return set(motion_intents())
    if vocab == "behavior_category":
        return set(behavior_categories())
    raise KeyError(vocab)


SYNONYM_VOCABS = tuple(_NAME_SOURCES)


def check_vocab() -> list[str]:
    """Every cross-reference between vocabulary files; an empty list means consistent."""
    problems: list[str] = []
    for i in intents().values():
        if i.input_type and i.input_type not in input_types():
            problems.append(f"intent {i.name}: unknown input_type {i.input_type}")
        if i.group not in intents_file().groups:
            problems.append(f"intent {i.name}: unknown group {i.group}")
    for m in motion_intents().values():
        if m.group not in motion_intents_file().groups:
            problems.append(f"motion {m.name}: unknown group {m.group}")
    for t in screen_types().values():
        problems += [
            f"screen_type {t.name}: unknown shape {s}"
            for s in t.typical_shapes
            if s not in shapes()
        ]
        problems += [
            f"screen_type {t.name}: unknown intent {s}"
            for s in t.typical_intents
            if s not in intents()
        ]
    for sk in state_kinds().values():
        if sk.demonstrated_on not in mock_profiles():
            problems.append(f"state {sk.name}: unknown mock profile {sk.demonstrated_on}")
    for mp in mock_profiles().values():
        problems += [
            f"mock {mp.name}: unknown state {s}" for s in mp.demonstrates if s not in state_kinds()
        ]
    if mock_profiles_file().domain_state_profile not in mock_profiles():
        problems.append("mock_profiles: domain_state_profile is not a profile")
    for k in asset_kinds().values():
        problems += [
            f"asset {k.name}: unknown source policy {p}"
            for p in k.source_policies
            if p not in asset_kinds_file().source_policies
        ]
    for ax in direction_axes().values():
        if ax.default not in ax.value_names:
            problems.append(f"axis {ax.name}: default {ax.default} is not a value")
    problems += [
        f"diff_axes: unknown axis {a}"
        for a in direction_axes_file().diff_axes
        if a not in direction_axes()
    ]
    for c in behavior_categories().values():
        problems += [f"behaviour {c.name}: {p}" for p in check_predicate(c.applies_if)]
        if c.supplied_by != "app_specific" and not c.default_text:
            problems.append(f"behaviour {c.name}: library-supplied but has no default_text")
    return problems
