"""Typed loader for the Compass catalog (data, not logic; hard rule 8).

The catalog is a set of data files next to this module:

* ``slots.yaml``        every slot Compass can fill, with a bilingual fallback card
* ``frames/*.yaml``     one question frame per file
* ``families.yaml``     neutral capability families (seeds, not archetypes)
* ``capabilities.yaml`` tier B capabilities and declined categories, with keyword guards
* ``no_ask.yaml``       topics Compass never asks about
* ``strings/{en,hi}.json``  flat ``key -> text`` dictionaries with identical key sets

``load_catalog()`` validates the whole set and raises :class:`CatalogError` with a precise
message. It is cached; call ``load_catalog.cache_clear()`` in tests that need a fresh read.

``ask_if`` mini-grammar (the engine evaluates it; the loader only checks syntax and that every
referenced slot exists)::

    expr    := or_expr
    or_expr := and_expr ("or" and_expr)*
    and_expr:= not_expr ("and" not_expr)*
    not_expr:= "not" not_expr | atom
    atom    := "(" expr ")" | filled('slot.id') | empty('slot.id')
             | value('slot.id') == 'x' | value('slot.id') != 'x'
             | value('slot.id') in ['a', 'b']

Example: ``filled('idea.category') and value('idea.category') != 'internal_tool'``.
Use :func:`parse_ask_if` to get a small AST, or :func:`ask_if_slot_refs` for the referenced
slots. No ``eval`` is ever used.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

CATALOG_DIR = Path(__file__).resolve().parent

Lang = Literal["en", "hi"]
Consumer = Literal[
    "research",
    "blueprint",
    "design",
    "screens",
    "verify",
    "change_control",
    "frontend",
    "backend",
    "qa",
    "all",
]
CardType = Literal[
    "choose_one",
    "pick_many",
    "priority",
    "confirm",
    "look_signals",
    "quick_form",
    "upload_or_link",
    "open_prompt",
]

SUPPORTED_LANGS: tuple[str, ...] = ("en", "hi")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class CatalogError(Exception):
    """The catalog data is missing, malformed or inconsistent."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class L10n(_Model):
    """A text in English and Hindi. ``get(lang)`` falls back to English."""

    en: str
    hi: str

    @field_validator("en", "hi")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty text")
        if "TODO" in value:
            raise ValueError("TODO left in text")
        return value

    def get(self, lang: str = "en") -> str:
        """Return the text for ``lang`` (``en`` when the language is unknown or empty)."""
        text = getattr(self, lang, None) if lang in SUPPORTED_LANGS else None
        return text if isinstance(text, str) and text.strip() else self.en


class ValueSchema(_Model):
    kind: Literal[
        "text", "enum", "enum_list", "text_list", "object_list", "object", "bool", "geo", "locales"
    ]
    options: list[str] = []
    fields: list[str] = []


class FallbackOption(_Model):
    id: str
    label: L10n
    value: str | None = None


class FallbackCard(_Model):
    type: CardType
    question: L10n
    why_we_ask: L10n
    options: list[FallbackOption]
    placeholder: L10n | None = None


class SlotDef(_Model):
    id: str
    group: str
    label: L10n
    level: Literal["must", "should", "may", "generated"]
    must_where: str | None = None
    consumers: list[Consumer]
    filled_by: list[Literal["U", "D", "I", "P"]]
    impact: int
    risk: Literal["low", "medium", "high"]
    effort: Literal["tap", "text", "upload"]
    value_schema: ValueSchema
    default: str | list[str] | None = None
    ask_if: str | None = None
    brief_path: str
    order: int
    must_confirm: bool = False
    fallback_card: FallbackCard


class FrameDef(_Model):
    id: str
    purpose: str
    slots: list[str]
    card_type: CardType
    option_source: Literal["document", "family", "model", "fixed"]
    order: int
    option_count: int | None = 3  # None means a checklist
    max_question_words: int = 20
    max_option_words: int = 8
    guidance: str


class Feature(_Model):
    id: str
    name: L10n
    intent: str


class Family(_Model):
    id: str
    label: L10n
    keywords: list[str]
    typical_features: list[Feature]
    typical_roles: list[str]
    typical_integrations: list[str]
    typical_business_models: list[str]


class CapabilityRule(_Model):
    id: str
    label: L10n
    kind: Literal["tier_b", "declined"]
    keywords: list[str]
    reason: L10n | None = None
    alternative: L10n | None = None


class _SafeFormat(dict[str, Any]):
    """Leave unknown ``{name}`` placeholders untouched instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class Catalog(_Model):
    slots: dict[str, SlotDef]
    frames: dict[str, FrameDef]
    families: dict[str, Family]
    capabilities: dict[str, CapabilityRule]
    no_ask: list[str]
    strings: dict[str, dict[str, str]]

    def t(self, key: str, lang: str = "en", **values: object) -> str:
        """Look up a UI string.

        Falls back to English, then to the key itself. ``{name}`` placeholders are filled from
        ``values``; placeholders without a value are left as written.
        """
        text = self.strings.get(lang, {}).get(key)
        if text is None:
            text = self.strings.get("en", {}).get(key)
        if text is None:
            return key
        if not values:
            return text
        return text.format_map(_SafeFormat(values))

    def slot_ids_by_order(self) -> list[str]:
        """Slot ids sorted by dependency stage, then by catalog position (stable)."""
        position = {slot_id: i for i, slot_id in enumerate(self.slots)}
        return sorted(self.slots, key=lambda s: (self.slots[s].order, position[s]))

    def declined(self) -> list[CapabilityRule]:
        """Declined categories, in file order."""
        return [c for c in self.capabilities.values() if c.kind == "declined"]

    def tier_b(self) -> list[CapabilityRule]:
        """Tier B capabilities, in file order."""
        return [c for c in self.capabilities.values() if c.kind == "tier_b"]


# ---------------------------------------------------------------------------------------------
# ask_if grammar: tokenizer, recursive-descent parser (validation and AST; never eval)
# ---------------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>'[^'\\]*')
      | (?P<op>==|!=|\(|\)|\[|\]|,)
      | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    )""",
    re.VERBOSE,
)
_FUNCS = ("filled", "empty", "value")


def _tokenize(expr: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = expr.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise CatalogError(f"ask_if: unexpected text at position {pos} in {expr!r}")
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


class _Parser:
    """Recursive-descent parser for the ask_if mini-grammar. Produces nested tuples."""

    def __init__(self, expr: str) -> None:
        self.expr = expr
        self.tokens = _tokenize(expr)
        self.i = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _next(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise CatalogError(f"ask_if: unexpected end of expression {self.expr!r}")
        self.i += 1
        return tok

    def _expect(self, text: str) -> None:
        tok = self._next()
        if tok[1] != text:
            raise CatalogError(f"ask_if: expected {text!r} but found {tok[1]!r} in {self.expr!r}")

    def parse(self) -> tuple[Any, ...]:
        node = self._or()
        if self._peek() is not None:
            raise CatalogError(f"ask_if: trailing text {self._peek()!r} in {self.expr!r}")
        return node

    def _or(self) -> tuple[Any, ...]:
        node = self._and()
        while self._peek() == ("word", "or"):
            self._next()
            node = ("or", node, self._and())
        return node

    def _and(self) -> tuple[Any, ...]:
        node = self._not()
        while self._peek() == ("word", "and"):
            self._next()
            node = ("and", node, self._not())
        return node

    def _not(self) -> tuple[Any, ...]:
        if self._peek() == ("word", "not"):
            self._next()
            return ("not", self._not())
        return self._atom()

    def _string(self) -> str:
        kind, text = self._next()
        if kind != "str":
            raise CatalogError(f"ask_if: expected a quoted string, found {text!r} in {self.expr!r}")
        return text[1:-1]

    def _atom(self) -> tuple[Any, ...]:
        kind, text = self._next()
        if text == "(":
            node = self._or()
            self._expect(")")
            return node
        if kind != "word" or text not in _FUNCS:
            raise CatalogError(f"ask_if: unexpected {text!r} in {self.expr!r}")
        self._expect("(")
        slot = self._string()
        self._expect(")")
        if text in ("filled", "empty"):
            return (text, slot)
        op = self._next()
        if op[1] in ("==", "!="):
            return ("value", slot, op[1], self._string())
        if op == ("word", "in"):
            self._expect("[")
            items = [self._string()]
            while self._peek() == ("op", ","):
                self._next()
                items.append(self._string())
            self._expect("]")
            return ("value", slot, "in", tuple(items))
        raise CatalogError(f"ask_if: value('{slot}') needs ==, != or in ({self.expr!r})")


def parse_ask_if(expr: str) -> tuple[Any, ...]:
    """Parse an ``ask_if`` expression into a nested-tuple AST or raise :class:`CatalogError`.

    Nodes: ``("and", a, b)``, ``("or", a, b)``, ``("not", a)``, ``("filled", slot)``,
    ``("empty", slot)``, ``("value", slot, "==" | "!=", text)``, ``("value", slot, "in", (..))``.
    """
    return _Parser(expr).parse()


def ask_if_slot_refs(expr: str) -> list[str]:
    """Slot ids referenced by an ``ask_if`` expression (validates the syntax too)."""
    refs: list[str] = []

    def walk(node: tuple[Any, ...]) -> None:
        tag = node[0]
        if tag in ("and", "or"):
            walk(node[1])
            walk(node[2])
        elif tag == "not":
            walk(node[1])
        else:
            refs.append(node[1])

    walk(parse_ask_if(expr))
    return refs


# ---------------------------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"cannot read {path.name}: {exc}") from exc


def _build[T: BaseModel](model: type[T], raw: Any, where: str) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise CatalogError(f"{where}: {loc}: {first['msg']}") from exc


def _index[T: BaseModel](items: Iterable[T], what: str) -> dict[str, T]:
    out: dict[str, T] = {}
    for item in items:
        item_id: str = item.id  # type: ignore[attr-defined]
        if item_id in out:
            raise CatalogError(f"duplicate {what} id {item_id!r}")
        out[item_id] = item
    return out


def _list_of(raw: Any, key: str, filename: str) -> list[Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get(key), list):
        raise CatalogError(f"{filename}: top level must be a mapping with a list under '{key}'")
    return raw[key]


def _load_strings() -> dict[str, dict[str, str]]:
    strings: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGS:
        path = CATALOG_DIR / "strings" / f"{lang}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"cannot read strings/{lang}.json: {exc}") from exc
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            raise CatalogError(f"strings/{lang}.json must be a flat dict of strings")
        strings[lang] = data
    return strings


def _validate_strings(strings: dict[str, dict[str, str]]) -> None:
    en, hi = strings["en"], strings["hi"]
    if set(en) != set(hi):
        diff = sorted(set(en) ^ set(hi))
        raise CatalogError(f"strings key mismatch between en and hi: {diff[:8]}")
    for lang, table in strings.items():
        for key, text in table.items():
            if not text.strip() or "TODO" in text:
                raise CatalogError(f"strings/{lang}.json: bad text for {key!r}")
    for key in en:
        if set(_PLACEHOLDER.findall(en[key])) != set(_PLACEHOLDER.findall(hi[key])):
            raise CatalogError(f"strings: placeholder mismatch between en and hi for {key!r}")


def _validate_card(slot: SlotDef) -> None:
    card = slot.fallback_card
    where = f"slot {slot.id!r} fallback card"
    if card.type == "choose_one":
        if len(card.options) != 3:
            raise CatalogError(f"{where}: choose_one needs exactly 3 options")
        for lang in SUPPORTED_LANGS:
            labels = [o.label.get(lang).strip().casefold() for o in card.options]
            if len(set(labels)) != 3:
                raise CatalogError(f"{where}: options are not distinct in {lang}")
    ids = [o.id for o in card.options]
    if len(set(ids)) != len(ids):
        raise CatalogError(f"{where}: duplicate option ids")
    if card.type in ("open_prompt", "upload_or_link", "quick_form") and card.placeholder is None:
        raise CatalogError(f"{where}: {card.type} needs a placeholder")
    if card.type in ("choose_one", "pick_many", "priority") and not card.options:
        raise CatalogError(f"{where}: {card.type} needs options")


def _validate_slots(slots: dict[str, SlotDef]) -> None:
    for slot in slots.values():
        if not 1 <= slot.impact <= 5:
            raise CatalogError(f"slot {slot.id!r}: impact must be 1..5")
        if not 1 <= slot.order <= 8:
            raise CatalogError(f"slot {slot.id!r}: order must be 1..8")
        if not slot.consumers or not slot.filled_by:
            raise CatalogError(f"slot {slot.id!r}: consumers and filled_by must not be empty")
        if not slot.brief_path.strip():
            raise CatalogError(f"slot {slot.id!r}: brief_path is empty")
        _validate_card(slot)
        if slot.ask_if is not None:
            for ref in ask_if_slot_refs(slot.ask_if):
                if ref not in slots:
                    raise CatalogError(f"slot {slot.id!r}: ask_if references unknown slot {ref!r}")
            if slot.id in ask_if_slot_refs(slot.ask_if):
                raise CatalogError(f"slot {slot.id!r}: ask_if refers to the slot itself")


def _validate_frames(frames: dict[str, FrameDef], slots: dict[str, SlotDef]) -> None:
    for frame in frames.values():
        if not frame.slots:
            raise CatalogError(f"frame {frame.id!r}: no slots")
        for ref in frame.slots:
            if ref not in slots:
                raise CatalogError(f"frame {frame.id!r}: unknown slot {ref!r}")
        if frame.option_count is not None and frame.option_count < 1:
            raise CatalogError(f"frame {frame.id!r}: option_count must be >= 1 or null")


def _validate_families(families: dict[str, Family]) -> None:
    for fam in families.values():
        if not fam.keywords:
            raise CatalogError(f"family {fam.id!r}: no keywords")
        if len({f.id for f in fam.typical_features}) != len(fam.typical_features):
            raise CatalogError(f"family {fam.id!r}: duplicate feature ids")
        for feature in fam.typical_features:
            if not feature.intent.strip():
                raise CatalogError(f"family {fam.id!r}: feature {feature.id!r} has no intent")


def _validate_capabilities(caps: dict[str, CapabilityRule]) -> None:
    for cap in caps.values():
        if not cap.keywords:
            raise CatalogError(f"capability {cap.id!r}: no keywords")
        if cap.kind == "declined" and (cap.reason is None or cap.alternative is None):
            raise CatalogError(f"capability {cap.id!r}: declined needs reason and alternative")


def _read_frames() -> list[Any]:
    paths = sorted((CATALOG_DIR / "frames").glob("*.yaml"))
    return [_read_yaml(p) for p in paths]


@functools.lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    """Load and validate the whole catalog (cached). Raises :class:`CatalogError`."""
    slots_raw = _list_of(_read_yaml(CATALOG_DIR / "slots.yaml"), "slots", "slots.yaml")
    slots = _index((_build(SlotDef, r, "slots.yaml") for r in slots_raw), "slot")

    frames = _index((_build(FrameDef, r, "frames/") for r in _read_frames()), "frame")

    fam_raw = _list_of(_read_yaml(CATALOG_DIR / "families.yaml"), "families", "families.yaml")
    families = _index((_build(Family, r, "families.yaml") for r in fam_raw), "family")

    cap_raw = _list_of(
        _read_yaml(CATALOG_DIR / "capabilities.yaml"), "capabilities", "capabilities.yaml"
    )
    capabilities = _index((_build(CapabilityRule, r, "capabilities.yaml") for r in cap_raw), "cap")

    no_ask = _list_of(_read_yaml(CATALOG_DIR / "no_ask.yaml"), "no_ask", "no_ask.yaml")
    if not all(isinstance(x, str) and x.strip() for x in no_ask):
        raise CatalogError("no_ask.yaml: entries must be non-empty strings")

    strings = _load_strings()
    _validate_strings(strings)
    _validate_slots(slots)
    _validate_frames(frames, slots)
    _validate_families(families)
    _validate_capabilities(capabilities)

    return Catalog(
        slots=slots,
        frames=frames,
        families=families,
        capabilities=capabilities,
        no_ask=list(no_ask),
        strings=strings,
    )
