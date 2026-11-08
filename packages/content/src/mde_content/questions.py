"""Loader for the question bank (``data/questions/*.yaml``): cards, chips, copy, knowledge.

One YAML file per archetype plus ``shared.yaml``; each validates as a ``QuestionBankFile``.
Files are discovered by listing the folder, so a new archetype is a new file, not new code.
Cards in an archetype file default to that archetype (``Card.archetypes`` empty means "all" only
in the shared file).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from functools import lru_cache
from importlib import resources

import yaml

from mde_contracts.calls import KnowledgeLite
from mde_contracts.cards import Card
from mde_contracts.common import Mode
from mde_contracts.content import KnowledgeSnippet, QuestionBankFile, StarterChip, UiCopy

SHARED = "shared"
BASE_LANG = "en"
# Kinds the normal order logic may pick; help, inspiration and nudge cards are shown by situation.
ORDERED_KINDS: tuple[str, ...] = ("standard", "compliance", "resolution")
_KIND_RANK = {"standard": 0, "compliance": 1, "resolution": 2}

_LANG_TAG = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*")


# --------------------------------------------------------------------------- language


def _canonical(tag: str) -> list[str]:
    parts = tag.replace("_", "-").split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            out.append(part.title())
        elif len(part) == 2 and part.isalpha():
            out.append(part.upper())
        else:
            out.append(part)
    return out


def lang_chain(lang: str | None) -> list[str]:
    """Languages to try for ``lang``, most specific first, always ending in ``en``.

    ``hi-Latn`` falls back to ``en`` (not to Devanagari ``hi``); ``hi-IN`` falls back to ``hi``.
    """
    if not lang or not _LANG_TAG.fullmatch(lang):
        return [BASE_LANG]
    parts = _canonical(lang)
    has_script = any(len(p) == 4 and p.isalpha() for p in parts[1:])
    chain = ["-".join(parts)]
    region_free = [p for p in parts if not (len(p) == 2 and p.isalpha() and p != parts[0])]
    if region_free != parts:
        chain.append("-".join(region_free))
    if not has_script:
        chain.append(parts[0])
    chain.append(BASE_LANG)
    return list(dict.fromkeys(chain))


def resolve_text(texts: Mapping[str, str], lang: str | None, fallback: str | None = None) -> str:
    """The text for ``lang`` following :func:`lang_chain`; ``fallback`` if none is present."""
    for tag in lang_chain(lang):
        if tag in texts:
            return texts[tag]
    if fallback is not None:
        return fallback
    raise KeyError(f"no text for {lang!r} and no English text")


def localize(card: Card, lang: str | None) -> Card:
    """A copy of ``card`` with text, option labels, help and placeholder in ``lang``.

    Each field falls back to English independently, so a partly translated card still works.
    """
    for tag in lang_chain(lang):
        if tag == BASE_LANG:
            return card
        tr = card.i18n.get(tag)
        if tr is None:
            continue
        options = [
            o.model_copy(update={"label": tr.options.get(o.id, o.label)}) for o in card.options
        ]
        other = card.other
        if tr.other_placeholder is not None:
            other = other.model_copy(update={"placeholder": tr.other_placeholder})
        return card.model_copy(
            update={
                "text": tr.text,
                "options": options,
                "other": other,
                "help": tr.help if tr.help is not None else card.help,
            }
        )
    return card


# --------------------------------------------------------------------------- the bank


def _for_file(card: Card, archetype: str) -> Card:
    if archetype != SHARED and not card.archetypes:
        return card.model_copy(update={"archetypes": [archetype]})
    return card


class QuestionBanks:
    """All question-bank files, indexed. Build with :func:`load_question_banks`."""

    def __init__(self, files: Iterable[QuestionBankFile]) -> None:
        self._files: tuple[QuestionBankFile, ...] = tuple(files)
        cards: list[Card] = []
        inspiration: list[Card] = []
        self._insp_files: dict[str, list[Card]] = {}
        for f in self._files:
            cards.extend(_for_file(c, f.archetype) for c in f.cards)
            insp = [_for_file(c, f.archetype) for c in f.inspiration_cards]
            inspiration.extend(insp)
            self._insp_files.setdefault(f.archetype, []).extend(insp)
        self._cards: tuple[Card, ...] = tuple(cards)
        self._inspiration: tuple[Card, ...] = tuple(inspiration)
        self._by_id: dict[str, Card] = {}
        for card in (*self._cards, *self._inspiration):
            if card.id in self._by_id:
                raise ValueError(f"duplicate card id {card.id}")
            self._by_id[card.id] = card
        self._chips: dict[str, list[StarterChip]] = {}
        self._copy: dict[str, UiCopy] = {}
        self._snippets: list[KnowledgeSnippet] = []
        for f in self._files:
            self._chips.setdefault(f.archetype, []).extend(f.starter_chips)
            for item in f.ui_copy:
                if item.id in self._copy:
                    raise ValueError(f"duplicate ui copy id {item.id}")
                self._copy[item.id] = item
            self._snippets.extend(f.knowledge)

    # -- files and cards
    @property
    def files(self) -> tuple[QuestionBankFile, ...]:
        return self._files

    @property
    def archetypes(self) -> list[str]:
        """Archetype ids that have a file (``shared`` excluded), in load order."""
        return [f.archetype for f in self._files if f.archetype != SHARED]

    def card(self, card_id: str) -> Card:
        """One card by id (inspiration cards included); ``KeyError`` if unknown."""
        return self._by_id[card_id]

    def all_cards(self) -> list[Card]:
        """Every card including inspiration cards, in file order."""
        return [*self._cards, *self._inspiration]

    def cards_for_slot(
        self,
        slot: str,
        archetype: str | None,
        mode: Mode,
        kinds: Collection[str] = ORDERED_KINDS,
    ) -> list[Card]:
        """Cards for ``slot`` that apply to ``archetype`` and ``mode``, in a stable order.

        Order: standard, then compliance, then resolution cards; archetype-specific cards before
        shared ones; file order inside a group. ``ask_if`` is *not* evaluated here.
        """
        picked: list[tuple[int, int, int, Card]] = []
        for index, card in enumerate(self._cards):
            if card.slot != slot or card.kind not in kinds:
                continue
            if card.archetypes and archetype not in card.archetypes:
                continue
            if card.modes and mode not in card.modes:
                continue
            picked.append((_KIND_RANK.get(card.kind, 9), 0 if card.archetypes else 1, index, card))
        return [c for *_, c in sorted(picked, key=lambda t: t[:3])]

    def inspiration(self, archetype: str | None) -> list[Card]:
        """Help-mode inspiration cards: the archetype's own first, then the shared ones."""
        own = self._insp_files.get(archetype, []) if archetype and archetype != SHARED else []
        return [*own, *self._insp_files.get(SHARED, [])]

    # -- chips, copy, knowledge
    def chips(self, archetype: str | None, lang: str | None = BASE_LANG) -> list[StarterChip]:
        """Starter chips with labels in ``lang`` (fallback ``en``). ``None``: the greeting chips."""
        key = archetype if archetype in self._chips else SHARED
        return [
            c.model_copy(update={"label": resolve_text(c.i18n, lang, c.label)})
            for c in self._chips.get(key, [])
        ]

    def ui_copy_ids(self) -> list[str]:
        return list(self._copy)

    def ui_copy(self, copy_id: str) -> UiCopy:
        return self._copy[copy_id]

    def snippets(self) -> list[KnowledgeSnippet]:
        return list(self._snippets)

    def knowledge(self, lang: str | None = BASE_LANG) -> list[KnowledgeLite]:
        """Knowledge snippets as C3 input, in ``lang`` (fallback ``en``)."""
        return [KnowledgeLite(id=s.id, text=resolve_text(s.text, lang)) for s in self._snippets]


def _read_files() -> list[QuestionBankFile]:
    folder = resources.files("mde_content") / "data" / "questions"
    names = sorted(
        (p.name for p in folder.iterdir() if p.name.endswith(".yaml")),
        key=lambda n: (n != f"{SHARED}.yaml", n),
    )
    return [
        QuestionBankFile.model_validate(yaml.safe_load((folder / name).read_text(encoding="utf-8")))
        for name in names
    ]


@lru_cache(maxsize=1)
def load_question_banks() -> QuestionBanks:
    """Read and validate every question file (cached; the models are frozen)."""
    return QuestionBanks(_read_files())
