"""Shared formatting helpers for the view model: labels, values and provenance badges.

All user-facing text comes from ``catalog/strings`` (English and Hindi). A few labels the catalog does
not carry yet live in ``FALLBACK_STRINGS`` below; a catalog key of the same name always wins, so adding
the key to ``strings/*.json`` later needs no code change (the keys are listed in the hand-off report).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from mde_compass.catalog.loader import SUPPORTED_LANGS, Catalog, SlotDef
from mde_compass.domain.common import ItemMeta, Provenance
from mde_compass.views.models import BadgeKind, ViewBadge, ViewValue

# Provenance type -> badge kind (the five badge kinds users see).
BADGE_BY_PROVENANCE: dict[str, BadgeKind] = {
    "user_tap": "you_said",
    "user_text": "you_said",
    "voice": "you_said",
    "document": "from_document",
    "org_profile": "from_document",
    "assumed": "assumed",
    "pattern": "typical",
    "model_knowledge": "suggestion",
    "suggestion": "suggestion",
}
_BADGE_ORDER: tuple[BadgeKind, ...] = (
    "you_said",
    "from_document",
    "typical",
    "suggestion",
    "assumed",
)
_FALLBACK_ICONS: dict[str, str] = {
    "you_said": "◆",
    "from_document": "■",
    "assumed": "▲",
    "typical": "●",
    "suggestion": "○",
}

# Keys the catalog does not have (yet). Each entry is {en, hi}.
FALLBACK_STRINGS: dict[str, dict[str, str]] = {
    "view.untitled": {"en": "Untitled project", "hi": "बिना नाम का प्रोजेक्ट"},
    "view.empty.idea": {"en": "Nothing here yet.", "hi": "यहाँ अभी कुछ नहीं है।"},
    "view.empty.who": {"en": "We have not asked about this yet.", "hi": "इस बारे में अभी नहीं पूछा।"},
    "view.empty.apps_roles": {"en": "No apps or roles yet.", "hi": "अभी कोई ऐप या भूमिका नहीं है।"},
    "view.empty.features": {"en": "No features yet.", "hi": "अभी कोई सुविधा नहीं है।"},
    "view.empty.look": {"en": "No look or tone chosen yet.", "hi": "रूप या अंदाज़ अभी चुना नहीं गया।"},
    "view.empty.access": {"en": "No sign-in choices yet.", "hi": "लॉगिन के विकल्प अभी तय नहीं हैं।"},
    "view.empty.integrations_data": {
        "en": "No integrations or data sources yet.",
        "hi": "अभी कोई जुड़ाव या डेटा स्रोत नहीं है।",
    },
    "view.empty.limits": {"en": "No limits noted.", "hi": "कोई सीमा दर्ज नहीं है।"},
    "view.empty.words": {"en": "No special words yet.", "hi": "अभी कोई ख़ास शब्द नहीं हैं।"},
    "view.empty.assumed": {"en": "Nothing was assumed.", "hi": "कुछ भी मान्य नहीं लिया गया।"},
    "view.empty.unknown": {"en": "Nothing is open.", "hi": "कुछ भी खुला नहीं है।"},
    "view.empty.lookup": {
        "en": "Nothing to look up yet.",
        "hi": "खोजने के लिए अभी कुछ नहीं है।",
    },
    "view.empty.sources": {
        "en": "No documents or links read.",
        "hi": "कोई दस्तावेज़ या लिंक नहीं पढ़ा गया।",
    },
    "view.empty.cannot_build": {
        "en": "Nothing you asked for is out of reach.",
        "hi": "आपकी माँगी कोई चीज़ पहुँच से बाहर नहीं है।",
    },
    "view.group.apps": {"en": "Apps", "hi": "ऐप"},
    "view.group.roles": {"en": "Roles", "hi": "भूमिकाएँ"},
    "view.group.all_apps": {"en": "All apps", "hi": "सभी ऐप"},
    "view.group.other": {"en": "Other", "hi": "अन्य"},
    "view.theme.Business": {"en": "Business", "hi": "व्यवसाय"},
    "view.theme.Users": {"en": "Users", "hi": "उपयोगकर्ता"},
    "view.theme.Features": {"en": "Features", "hi": "सुविधाएँ"},
    "view.theme.Look": {"en": "Look", "hi": "रूप"},
    "view.theme.Access": {"en": "Access", "hi": "पहुँच"},
    "view.theme.Integrations": {"en": "Integrations", "hi": "जुड़ाव"},
    "view.field.reason": {"en": "Why: {value}", "hi": "क्यों: {value}"},
    "view.field.what_changes": {
        "en": "If you change this: {value}",
        "hi": "इसे बदलने पर: {value}",
    },
    "view.field.surface": {"en": "Works on: {value}", "hi": "इस पर चलेगा: {value}"},
    "view.field.audience": {"en": "For: {value}", "hi": "किनके लिए: {value}"},
    "view.field.roles": {"en": "Roles: {value}", "hi": "भूमिकाएँ: {value}"},
    "view.field.goal": {"en": "Goal: {value}", "hi": "लक्ष्य: {value}"},
    "view.field.permissions": {"en": "Can: {value}", "hi": "कर सकता है: {value}"},
    "view.field.synonyms": {"en": "Also said: {value}", "hi": "इसे ऐसे भी कहते हैं: {value}"},
    "view.field.avoid": {"en": "Avoid: {value}", "hi": "इस्तेमाल न करें: {value}"},
    "view.field.borrow": {"en": "Borrow: {value}", "hi": "लेना है: {value}"},
    "view.field.avoid_ref": {"en": "Avoid: {value}", "hi": "नहीं लेना: {value}"},
    "view.field.where": {"en": "Where: {value}", "hi": "कहाँ: {value}"},
    "view.field.languages": {"en": "Languages: {value}", "hi": "भाषाएँ: {value}"},
    "view.field.instead": {"en": "Instead: {value}", "hi": "इसके बदले: {value}"},
    "view.field.notes": {"en": "Notes: {value}", "hi": "टिप्पणी: {value}"},
    "view.field.coverage": {"en": "Read {value}", "hi": "पढ़ा गया {value}"},
    "view.field.kind": {"en": "Type: {value}", "hi": "प्रकार: {value}"},
    "view.field.preference": {"en": "Plan: {value}", "hi": "योजना: {value}"},
    "view.field.chat_language": {"en": "Chat: {value}", "hi": "बातचीत: {value}"},
    "view.field.app_language": {"en": "App: {value}", "hi": "ऐप: {value}"},
    "view.field.right_to_left": {"en": "Right-to-left: {value}", "hi": "दाएँ से बाएँ: {value}"},
    "view.yes": {"en": "Yes", "hi": "हाँ"},
    "view.no": {"en": "No", "hi": "नहीं"},
    "view.impact.high": {"en": "Important", "hi": "अहम"},
    "view.impact.low": {"en": "Minor", "hi": "छोटी"},
    "view.decision.accepted": {"en": "Accepted", "hi": "स्वीकार"},
    "view.decision.changed": {"en": "Changed", "hi": "बदला"},
    "view.decision.rejected": {"en": "Removed", "hi": "हटाया"},
    "view.blocking": {"en": "Blocks approval", "hi": "मंज़ूरी रोकता है"},
    "view.custom_feature": {
        "en": "Added by you or us: please confirm",
        "hi": "नई सुविधा: कृपया पुष्टि करें",
    },
    "view.pref.real_later": {"en": "Real service later", "hi": "असली सेवा बाद में"},
    "view.pref.mock": {"en": "Sample data for now", "hi": "अभी नमूना डेटा"},
    "view.pref.undecided": {"en": "Not decided yet", "hi": "अभी तय नहीं"},
    "view.sep": {"en": " · ", "hi": " · "},
}


def normalize_language(language: str) -> str:
    """``en`` or ``hi`` (anything else, including ``hi-Latn``-style tags, resolves by prefix then ``en``)."""
    code = (language or "en").lower().split("-")[0]
    return code if code in SUPPORTED_LANGS else "en"


class Labels:
    """Language-bound string lookup: catalog first, then ``FALLBACK_STRINGS``, then the key."""

    def __init__(self, catalog: Catalog, language: str = "en") -> None:
        self.catalog = catalog
        self.language = normalize_language(language)

    def has(self, key: str) -> bool:
        return key in self.catalog.strings.get("en", {})

    def t(self, key: str, **values: object) -> str:
        if self.has(key):
            return self.catalog.t(key, self.language, **values)
        fallback = FALLBACK_STRINGS.get(key)
        if fallback is None:
            return key
        text = fallback.get(self.language) or fallback["en"]
        return text.format_map(_Safe(values)) if values else text

    def slot_label(self, slot_id: str) -> str:
        slot = self.catalog.slots.get(slot_id)
        return slot.label.get(self.language) if slot else humanize(slot_id)

    def option_label(self, slot: SlotDef | None, value: str) -> str:
        """Localized label of an enum option (from the slot's fallback card), else a humanized token."""
        if slot is not None:
            for option in slot.fallback_card.options:
                if value in (option.value, option.id):
                    return option.label.get(self.language)
        return humanize(value)


class _Safe(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def humanize(token: str) -> str:
    """``calm_simple`` -> ``Calm simple`` (used only where the catalog has no label)."""
    text = token.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------------------------


def badge(kind: BadgeKind, labels: Labels) -> ViewBadge:
    """One provenance badge: text AND an icon, never colour alone."""
    icon = labels.t(f"badge.icon.{kind}")
    if icon == f"badge.icon.{kind}":
        icon = _FALLBACK_ICONS[kind]
    return ViewBadge(kind=kind, text=labels.t(f"badge.{kind}"), icon=icon)


def badge_kinds(
    provenance: Sequence[Provenance], *, status: str = "empty", has_value: bool = True
) -> list[BadgeKind]:
    """Distinct badge kinds for a provenance list, in a stable order. An assumed item with no
    provenance still shows *Assumed*."""
    kinds = {BADGE_BY_PROVENANCE[p.type] for p in provenance if p.type in BADGE_BY_PROVENANCE}
    if not kinds and has_value and status == "assumed":
        kinds.add("assumed")
    return [k for k in _BADGE_ORDER if k in kinds]


def badges_for(
    meta: ItemMeta, labels: Labels, *, has_value: bool = True, extra: Sequence[BadgeKind] = ()
) -> list[ViewBadge]:
    kinds = set(badge_kinds(meta.provenance, status=meta.status, has_value=has_value)) | set(extra)
    return [badge(k, labels) for k in _BADGE_ORDER if k in kinds]


# ---------------------------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------------------------

EMPTY = ViewValue(kind="empty")


def text_value(text: str) -> ViewValue:
    text = text.strip()
    return ViewValue(kind="text", text=text) if text else EMPTY


def list_value(items: Sequence[str]) -> ViewValue:
    cleaned = [i.strip() for i in items if i and i.strip()]
    return ViewValue(kind="list", items=cleaned) if cleaned else EMPTY


def chips_value(items: Sequence[str]) -> ViewValue:
    cleaned = [i.strip() for i in items if i and i.strip()]
    return ViewValue(kind="chips", items=cleaned) if cleaned else EMPTY


def scalar_text(value: JsonValue, labels: Labels, slot: SlotDef | None = None) -> str:
    """One scalar as display text (booleans localized, enum tokens labelled)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return labels.t("view.yes" if value else "view.no")
    if isinstance(value, str):
        text = value.strip()
        if slot is not None and slot.value_schema.kind in ("enum", "enum_list"):
            return labels.option_label(slot, text)
        return text
    return str(value)


def _geo_text(value: Mapping[str, JsonValue]) -> str:
    parts = [str(value.get(k) or "").strip() for k in ("city", "region", "country")]
    return ", ".join(p for p in parts if p)


def _object_line(obj: Mapping[str, JsonValue], labels: Labels) -> str:
    """A dict as one readable line: ``name`` first, then the other non-empty values."""
    head = ""
    for key in ("name", "label", "term", "title"):
        if obj.get(key):
            head = str(obj[key])
            break
    rest = [
        ", ".join(str(x) for x in v) if isinstance(v, list) else scalar_text(v, labels)
        for k, v in obj.items()
        if v not in (None, "", [], {}) and k not in ("name", "label", "term", "title", "id")
    ]
    return " · ".join(p for p in [head, *rest] if p)


def to_view_value(value: JsonValue, labels: Labels, slot: SlotDef | None = None) -> ViewValue:
    """Format any JSON value for display: text, a list (one object per line) or chips."""
    kind = slot.value_schema.kind if slot is not None else ""
    if value is None or value in ("", [], {}):
        return EMPTY
    if isinstance(value, dict):
        if kind == "geo" or {"city", "country"} & set(value):
            text = _geo_text(value)
            if text:
                return text_value(text)
        if kind == "locales" or {"chat", "app", "right_to_left"} & set(value):
            return list_value(_locale_lines(value, labels))
        return list_value([_object_line(value, labels)])
    if isinstance(value, list):
        if all(isinstance(v, dict) for v in value):
            return list_value([_object_line(v, labels) for v in value if isinstance(v, dict)])
        items = [scalar_text(v, labels, slot) for v in value]
        return chips_value(items)
    return text_value(scalar_text(value, labels, slot))


def _locale_lines(value: Mapping[str, JsonValue], labels: Labels) -> list[str]:
    lines: list[str] = []
    for key, label_key in (
        ("chat", "view.field.chat_language"),
        ("app", "view.field.app_language"),
        ("right_to_left", "view.field.right_to_left"),
    ):
        item = value.get(key)
        if item in (None, "", []):
            continue
        text = (
            ", ".join(str(x) for x in item) if isinstance(item, list) else scalar_text(item, labels)
        )
        lines.append(labels.t(label_key, value=text))
    return lines


def plain_text(value: JsonValue, labels: Labels, slot: SlotDef | None = None) -> str:
    """The value as a single line of text (for titles and chips)."""
    view = to_view_value(value, labels, slot)
    return view.text if view.kind == "text" else ", ".join(view.items)


def field_line(labels: Labels, key: str, value: str | Sequence[str]) -> str:
    """``view.field.<key>`` filled with a text or a comma-joined list; empty when there is no value."""
    text = value if isinstance(value, str) else ", ".join(v for v in value if v)
    text = text.strip()
    return labels.t(f"view.field.{key}", value=text) if text else ""
