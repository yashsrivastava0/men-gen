"""The whole content set loaded together, plus the cross-file checks no single loader can do.

Written by the lead after the two content authors' branches merged (D-027). ``validate_bundle``
returns a list of human-readable problems (empty = consistent); a test asserts it is empty, and
``mde doctor`` / CI can call it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from mde_content import catalogs as _catalogs
from mde_content import packs as _packs
from mde_content import policies as _policies
from mde_content import questions as _questions
from mde_content import recipes as _recipes
from mde_content import stack as _stack
from mde_content import themes as _themes
from mde_contracts.cards import Card
from mde_contracts.content import Catalogs, DomainPack, RecipeLibrary, StackProfile, ThemeLibrary
from mde_contracts.policy import Policy


@dataclass(frozen=True)
class ContentBundle:
    catalogs: Catalogs
    questions: _questions.QuestionBanks
    packs: dict[str, DomainPack]
    recipes: RecipeLibrary
    themes: ThemeLibrary
    stack: StackProfile
    policies: dict[str, Policy]  # fully resolved (inheritance applied), by policy id


@lru_cache(maxsize=1)
def load_bundle() -> ContentBundle:
    return ContentBundle(
        catalogs=_catalogs.load_catalogs(),
        questions=_questions.load_question_banks(),
        packs=_packs.load_packs(),
        recipes=_recipes.load_recipes(),
        themes=_themes.load_themes(),
        stack=_stack.load_stack_profile(),
        policies={p.id: _policies.resolve_policy(p) for p in _policies.list_policies()},
    )


def _theme_of(preview: str | None) -> str | None:
    return preview.split(":", 1)[1] if preview and preview.startswith("theme:") else None


def _feature_card(cards: list[Card], archetype: str) -> Card | None:
    """The visible ``scope.features`` card of an archetype (multi checklist)."""
    for card in cards:
        visible = card.slot == "scope.features" and card.type == "multi" and card.ask_if != "false"
        if visible and (not card.archetypes or archetype in card.archetypes):
            return card
    return None


def validate_bundle(bundle: ContentBundle | None = None) -> list[str]:
    """Cross-file consistency checks; returns problems (empty list = consistent)."""
    b = bundle or load_bundle()
    problems: list[str] = []
    archetypes = {a.id for a in b.catalogs.archetypes}
    theme_ids = {t.id for t in b.themes.themes}
    domains = {d.id for d in b.catalogs.sensitive_domains}
    cards = b.questions.all_cards()
    card_ids = {c.id for c in cards}

    # 1. archetypes agree across catalogs, packs and question banks
    if set(b.packs) != archetypes:
        problems.append(f"packs {sorted(b.packs)} != catalog archetypes {sorted(archetypes)}")
    bank_archetypes = {a for a in b.questions.archetypes if a != "shared"}
    if bank_archetypes != archetypes:
        problems.append(
            f"question banks {sorted(bank_archetypes)} != archetypes {sorted(archetypes)}"
        )

    # 2. visual cards point at real themes; direction values are themes
    for card in cards:
        for opt in card.options:
            theme = _theme_of(opt.preview)
            if theme is not None and theme not in theme_ids:
                problems.append(f"card {card.id} option {opt.id}: unknown theme {theme!r}")
            if card.slot == "design.direction":
                value = opt.sets.get("design.direction")
                if isinstance(value, str) and value not in theme_ids:
                    problems.append(
                        f"card {card.id} option {opt.id}: direction {value!r} not a theme"
                    )

    # 3. per archetype: feature checklist == pack features; theme candidates are real themes
    for aid, pack in b.packs.items():
        pack_features = {f.id for f in pack.features}
        card = _feature_card(cards, aid)
        if card is None:
            problems.append(f"{aid}: no visible scope.features card")
        else:
            card_features = {o.id for o in card.options}
            if card_features != pack_features:
                only_card = sorted(card_features - pack_features)
                only_pack = sorted(pack_features - card_features)
                problems.append(
                    f"{aid}: feature card vs pack differ (card only {only_card}, pack only {only_pack})"
                )
        for t in pack.theme_candidates:
            if t not in theme_ids:
                problems.append(f"{aid}: theme candidate {t!r} is not a theme")
        if pack.default_theme and pack.default_theme not in theme_ids:
            problems.append(f"{aid}: default theme {pack.default_theme!r} is not a theme")
        for recipe_screen in pack.screens:
            if recipe_screen.recipe not in {r.id for r in b.recipes.recipes}:
                problems.append(f"{aid}: screen {recipe_screen.key} uses unknown recipe")

        # 4. compliance triggers reference real domains and real cards
        for trig in pack.compliance_triggers:
            if trig.sensitive_domain not in domains:
                problems.append(
                    f"{aid}: trigger {trig.id} names unknown domain {trig.sensitive_domain!r}"
                )
            for cid in trig.cards:
                if cid not in card_ids:
                    problems.append(f"{aid}: trigger {trig.id} names unknown card {cid!r}")

    # 5. every sensitive domain has at least one must-confirm compliance card
    compliance_cards = [c for c in cards if c.kind == "compliance"]
    if not compliance_cards:
        problems.append("no compliance cards in the question bank")

    # 6. stack profile and policies are usable
    if not b.stack.capabilities:
        problems.append("stack profile has no capabilities")
    for needed in ("guided-default", "platform-default"):
        if needed not in b.policies:
            problems.append(f"missing policy {needed!r}")
    return problems
