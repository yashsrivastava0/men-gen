"""Question bank validation (brief 8.6): coverage, ids, sets, ask_if, i18n, chips, knowledge,
language fallback, and a simulation of the Guided interview for every archetype."""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from importlib import resources

import pytest
import yaml

from mde_content.catalogs import archetype_ids, load_catalogs, slot_def, slots_for_mode
from mde_content.expr import (
    BoolOp,
    Compare,
    Expr,
    ListLit,
    Lit,
    Not,
    SlotRef,
    holds,
    parse,
    referenced_slots,
    unknown_slots,
)
from mde_content.questions import (
    QuestionBanks,
    lang_chain,
    load_question_banks,
    localize,
    resolve_text,
)
from mde_contracts.cards import Card
from mde_contracts.common import Mode
from mde_contracts.content import QuestionBankFile
from mde_contracts.slots import SLOT_IDS, SlotData, SlotDef

BANKS = load_question_banks()
ARCHETYPES = archetype_ids()
MODES: tuple[Mode, ...] = ("guided", "enterprise")
THEMES = {
    "clean-bright",
    "bold-dark",
    "warm-friendly",
    "minimal-mono",
    "vibrant-playful",
    "corporate-calm",
}
ALL_KINDS = ("standard", "compliance", "resolution", "help", "inspiration", "nudge")
NEVER_ORDERED = {"help", "inspiration", "nudge"}
OPTION_TYPES = {"choice3", "visual3", "confirm"}
LANGS = ("hi", "hi-Latn")

# Slot value vocabulary (documented in catalogs.yaml and docs/specs/question-cards.md).
VOCAB: dict[str, set[str]] = {
    "auth.method": {"email_phone_code", "social", "password", "guest_first", "sso", "none"},
    "pay.model": {"online", "on_delivery", "both", "none"},
    "pay.provider": {"mock", "company_provider", "later"},
    "data.source": {"generated", "existing_api", "sample"},
    "behavior.offline": {"basic", "full", "none"},
    "auth.sso": {"required", "not_needed", "ask_it"},
    "data.classification": {"public", "internal", "confidential"},
    "release.channel": {"internal", "testers", "public"},
    "design.direction": THEMES,
    "idea.archetype": set(ARCHETYPES),
    "scope.apps": {"main", "customer", "business", "courier", "admin", "employee"},
    "regions": {"in", "eu", "us", "other"},
}
COMPLIANCE_EXTRA_FLAGS = {"needs_expert_review"}
JARGON = re.compile(
    r"\b(API|APIs|SDK|JSON|schema|database|OAuth|token|endpoint|backend|frontend|regex|YAML)\b",
    re.IGNORECASE,
)
ENTERPRISE_JARGON_OK = {"Q-ent-01", "Q-ent-08"}  # single sign-on / backend are IT-audience words
DEVANAGARI = re.compile("[ऀ-ॿ]")


def raw_files() -> Iterator[tuple[str, QuestionBankFile]]:
    folder = resources.files("mde_content") / "data" / "questions"
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".yaml"):
            data = yaml.safe_load(entry.read_text(encoding="utf-8"))
            yield entry.name, QuestionBankFile.model_validate(data)


def feature_ids(archetype: str) -> list[str]:
    """Option ids of the archetype's scope.features card."""
    cards = BANKS.cards_for_slot("scope.features", archetype, "guided")
    assert len(cards) == 1, archetype
    return [o.id for o in cards[0].options]


def walk(node: Expr) -> Iterator[Expr]:
    yield node
    if isinstance(node, Not):
        yield from walk(node.operand)
    elif isinstance(node, BoolOp):
        for child in node.operands:
            yield from walk(child)
    elif isinstance(node, Compare):
        yield from walk(node.left)
        yield from walk(node.right)


def compared_literals(expr: str, slot: str) -> set[str]:
    """String literals an expression compares ``slot`` against."""
    out: set[str] = set()
    for node in walk(parse(expr)):
        if not isinstance(node, Compare):
            continue
        sides = (node.left, node.right)
        if any(isinstance(s, SlotRef) and s.path == slot for s in sides):
            for s in sides:
                if isinstance(s, Lit) and isinstance(s.value, str):
                    out.add(s.value)
                elif isinstance(s, ListLit):
                    out.update(x for x in s.items if isinstance(x, str))
    return out


def option_values(slot: str, archetype: str, mode: Mode) -> list[SlotData]:
    """Plausible values a slot can take for an archetype: what its cards can write."""
    values: list[SlotData] = [None, slot_def(slot).default]
    for card in BANKS.cards_for_slot(slot, archetype, mode, kinds=ALL_KINDS):
        ids = [o.id for o in card.options]
        if card.type == "multi":
            values += [[i] for i in ids]
            values.append(ids)
        for o in card.options:
            if slot in o.sets:
                values.append(o.sets[slot])
    for card in BANKS.all_cards():
        for o in card.options:
            if slot in o.sets and slot in ("auth.method", "pay.model"):
                values.append(o.sets[slot])
    unique: list[SlotData] = []
    for v in values:
        if v not in unique:
            unique.append(v)
    return unique


def possibly_asked(sd: SlotDef, archetype: str, mode: Mode) -> bool:
    """Can the slot's precondition hold for some plausible answers? (brute force, small)"""
    if sd.ask_if is None:
        return True
    names = sorted(referenced_slots(sd.ask_if) - {"idea.archetype"})
    pools: list[list[SlotData]] = []
    for name in names:
        if name == "sensitive.domains":
            domains = [d.id for d in load_catalogs().sensitive_domains]
            pools.append([None, *[[d] for d in domains]])
        else:
            pools.append(option_values(name, archetype, mode))
    for combo in itertools.product(*pools):
        ctx: dict[str, SlotData] = {
            "idea.archetype": archetype,
            **dict(zip(names, combo, strict=True)),
        }
        if holds(sd.ask_if, ctx):
            return True
    return False


def as_list(value: SlotData) -> list[str]:
    assert isinstance(value, list)
    return value


def can_be_asked(card: Card) -> bool:
    return card.ask_if is None or parse(card.ask_if) != Lit(False)


# --------------------------------------------------------------------------- structure


def test_one_file_per_archetype_plus_shared() -> None:
    files = dict(raw_files())
    assert set(files) == {f"{a}.yaml" for a in ARCHETYPES} | {"shared.yaml"}
    for name, f in files.items():
        assert name == f"{f.archetype}.yaml"
    assert BANKS.archetypes == sorted(ARCHETYPES)  # loaded in file-name order after shared


def test_files_round_trip_through_the_models() -> None:
    for name, f in raw_files():
        assert QuestionBankFile.model_validate(f.model_dump()) == f, name


def test_archetype_files_only_hold_their_own_cards() -> None:
    for name, f in raw_files():
        for card in [*f.cards, *f.inspiration_cards]:
            if f.archetype == "shared":
                assert set(card.archetypes) <= set(ARCHETYPES), card.id
            else:
                assert card.archetypes in ([], [f.archetype]), (name, card.id)
        for chip in f.starter_chips:
            assert chip.archetype in (None, f.archetype) if f.archetype != "shared" else True
        if f.archetype != "shared":
            assert f.starter_chips and all(c.archetype == f.archetype for c in f.starter_chips)


def test_card_ids_are_unique_and_well_formed() -> None:
    ids = [c.id for c in BANKS.all_cards()]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 60
    for cid in ids:
        assert re.fullmatch(r"Q-[a-z]+-\d{2}", cid), cid
    for card in BANKS.all_cards():
        opt_ids = [o.id for o in card.options]
        assert len(opt_ids) == len(set(opt_ids)), card.id
        assert BANKS.card(card.id) == card
    with pytest.raises(KeyError):
        BANKS.card("Q-nope-00")


def test_duplicate_card_ids_are_rejected_at_load() -> None:
    files = [f for _, f in raw_files()]
    commerce = next(f for f in files if f.archetype == "commerce")
    clone = commerce.model_copy(update={"archetype": "custom"})
    with pytest.raises(ValueError, match="duplicate card id"):
        QuestionBanks([commerce, clone])


def test_cards_use_real_slots_and_ids_match_their_area() -> None:
    for card in BANKS.all_cards():
        assert card.slot in SLOT_IDS, card.id
        assert card.version == 1


# --------------------------------------------------------------------------- options and sets


def _check_value(slot: str, value: SlotData, card: Card, archetype: str | None) -> None:
    kind = slot_def(slot).value_kind
    where = f"{card.id} sets {slot}"
    if kind == "bool":
        assert isinstance(value, bool), where
    elif kind == "list":
        assert isinstance(value, list), where
        assert all(isinstance(x, str) and x for x in value), where
        assert len(value) == len(set(value)), where
    else:
        assert isinstance(value, str) and value, where
    if slot in VOCAB:
        items = value if isinstance(value, list) else [value]
        assert set(items) <= VOCAB[slot], (where, value)
    if slot == "scope.features" and archetype:
        assert isinstance(value, list)
        assert set(value) <= set(feature_ids(archetype)), (where, value)


def test_every_option_declares_sets_with_valid_slots_and_values() -> None:
    for card in BANKS.all_cards():
        archetype = card.archetypes[0] if len(card.archetypes) == 1 else None
        if card.type in ("text", "upload"):
            assert card.options == [], card.id
            continue
        assert card.options, card.id
        for opt in card.options:
            if card.kind == "nudge":
                assert opt.sets == {}, (card.id, opt.id)
                continue
            assert opt.sets, (card.id, opt.id)
            for slot, value in opt.sets.items():
                assert slot in SLOT_IDS, (card.id, slot)
                _check_value(slot, value, card, archetype)


def test_card_shapes_by_type() -> None:
    for card in BANKS.all_cards():
        if card.type in ("choice3", "visual3"):
            allowed = (2, 3) if card.kind == "nudge" else (3,)
            assert len(card.options) in allowed, card.id
        if card.type == "confirm":
            assert len(card.options) == 2, card.id
        if card.type == "visual3":
            assert card.slot == "design.direction", card.id
        if card.type == "multi":
            assert len(card.options) >= 4, card.id
            assert card.other.enabled, card.id
            for opt in card.options:
                assert opt.sets == {card.slot: [opt.id]}, (card.id, opt.id)
        if card.type in OPTION_TYPES and card.kind != "nudge":
            assert card.other.enabled, card.id  # "Something else" box


def test_exactly_one_recommended_option_except_multi() -> None:
    for card in BANKS.all_cards():
        rec = [o for o in card.options if o.recommended]
        if card.type in ("text", "upload"):
            assert not rec, card.id
        elif card.type == "multi":
            if card.slot == "scope.features":
                assert len(rec) >= 3, card.id  # a sensible pre-ticked set
        else:
            assert len(rec) == 1, card.id


def test_skip_assumption_is_the_recommended_option() -> None:
    for card in BANKS.all_cards():
        rec = [o.id for o in card.options if o.recommended]
        if card.type in OPTION_TYPES and card.skip.enabled:
            assert card.skip.assumes == rec[0], card.id
        if card.skip.assumes is not None:
            assert card.skip.assumes in [o.id for o in card.options], card.id
        if not card.skip.enabled:
            assert card.id in {"Q-idea-01", "Q-help-02"} or card.kind == "nudge", card.id


def test_recommended_scope_features_are_in_the_catalog_of_that_card() -> None:
    for a in ARCHETYPES:
        card = BANKS.cards_for_slot("scope.features", a, "guided")[0]
        assert card.type == "multi"
        assert len(feature_ids(a)) in (9, 10)
        assert len({o.id for o in card.options}) == len(card.options)
        for fid in feature_ids(a):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", fid), fid


def test_feature_id_lists_match_the_documented_ids() -> None:
    expected = {
        "commerce": "catalog_browse search cart_checkout payments order_tracking reviews "
        "favorites push_notifications loyalty support_chat",
        "food_delivery": "restaurant_browse menu_item_options cart_checkout payments "
        "live_order_tracking ratings saved_addresses push_notifications promo_codes support",
        "booking_services": "service_catalog provider_profiles slot_booking payments reminders "
        "reviews reschedule_cancel push_notifications memberships support",
        "internal_workforce": "task_lists forms_checklists approvals timesheets directory "
        "announcements offline_sync push_notifications kiosk_mode reports",
        "custom": "list_detail search forms auth_profile notifications settings media_gallery "
        "maps payments_mock",
    }
    for a, ids in expected.items():
        assert feature_ids(a) == ids.split(), a


# --------------------------------------------------------------------------- ask_if


def test_every_card_has_a_parsable_ask_if_over_real_slots() -> None:
    for card in BANKS.all_cards():
        assert card.ask_if is not None, card.id
        parse(card.ask_if)
        assert not unknown_slots(card.ask_if), (card.id, card.ask_if)


def test_situation_cards_are_never_picked_by_the_order_logic() -> None:
    for card in BANKS.all_cards():
        if card.kind in NEVER_ORDERED:
            assert parse(card.ask_if or "") == Lit(False), card.id


def test_ask_if_literals_name_real_features_and_values() -> None:
    domains = {d.id for d in load_catalogs().sensitive_domains}
    for card in BANKS.all_cards():
        assert card.ask_if
        features = compared_literals(card.ask_if, "scope.features")
        if features:
            assert card.archetypes, card.id  # feature ids only make sense inside one archetype
            for a in card.archetypes:
                assert features <= set(feature_ids(a)), (card.id, a)
        for slot, vocab in VOCAB.items():
            if slot != "design.direction":
                assert compared_literals(card.ask_if, slot) <= vocab, (card.id, slot)
        assert compared_literals(card.ask_if, "sensitive.domains") <= domains, card.id


def test_resolution_cards_fire_only_on_a_contradiction() -> None:
    auth = ("auth.method", "email_phone_code")
    pay = ("pay.model", "online")
    contra: dict[str, tuple[dict[str, SlotData], tuple[str, str]]] = {
        "Q-fix-01": ({"auth.method": "none", "scope.features": ["favorites", "search"]}, auth),
        "Q-fix-02": ({"auth.method": "none", "scope.features": ["ratings"]}, auth),
        "Q-fix-03": ({"auth.method": "none", "scope.features": ["memberships"]}, auth),
        "Q-fix-04": ({"auth.method": "none", "scope.features": ["task_lists"]}, auth),
        "Q-fix-05": ({"auth.method": "none", "scope.features": ["auth_profile", "search"]}, auth),
        "Q-fix-06": ({"pay.model": "none", "scope.features": ["payments", "search"]}, pay),
        "Q-fix-07": ({"pay.model": "none", "scope.features": ["payments"]}, pay),
        "Q-fix-08": ({"pay.model": "none", "scope.features": ["payments"]}, pay),
    }
    cards = {c.id: c for c in BANKS.all_cards() if c.kind == "resolution"}
    assert set(cards) == set(contra)
    for cid, (ctx, (slot, fixed)) in contra.items():
        card = cards[cid]
        assert card.ask_if
        assert holds(card.ask_if, ctx), cid
        assert not holds(card.ask_if, {}), cid
        assert not holds(card.ask_if, {**ctx, slot: fixed}), cid
        assert not holds(card.ask_if, {**ctx, "scope.features": ["search"]}), cid
    for a in ARCHETYPES:
        fix = [c for c in cards.values() if c.archetypes == [a] and c.slot == "auth.method"]
        assert len(fix) == 1, a
        assert BANKS.cards_for_slot("auth.method", a, "guided")[-1] == fix[0]


def test_resolution_options_leave_no_contradiction_behind() -> None:
    for card in BANKS.all_cards():
        if card.kind != "resolution":
            continue
        assert card.ask_if
        for opt in card.options:
            ctx: dict[str, SlotData] = {
                "auth.method": "none",
                "pay.model": "none",
                "scope.features": feature_ids(card.archetypes[0]),
            }
            ctx.update(opt.sets)
            assert not holds(card.ask_if, ctx), (card.id, opt.id)


# --------------------------------------------------------------------------- visual cards


def test_visual_cards_use_only_the_six_themes() -> None:
    seen: dict[str, set[str]] = {}
    for card in BANKS.all_cards():
        if card.type != "visual3":
            for opt in card.options:
                assert opt.preview is None, (card.id, opt.id)
            continue
        ids: set[str] = set()
        for opt in card.options:
            assert opt.preview is not None
            kind, _, theme = opt.preview.partition(":")
            assert kind == "theme" and theme in THEMES, (card.id, opt.preview)
            assert opt.sets == {"design.direction": theme}
            ids.add(theme)
        assert len(ids) == 3
        seen[card.id] = ids
    assert len(seen) == 10  # a primary and a "three more looks" card per archetype


def test_each_archetype_shows_three_looks_then_the_other_three() -> None:
    for a in ARCHETYPES:
        first, more = BANKS.cards_for_slot("design.direction", a, "guided")
        assert can_be_asked(first) and not can_be_asked(more)
        ids_first = {str(o.sets["design.direction"]) for o in first.options}
        ids_more = {str(o.sets["design.direction"]) for o in more.options}
        assert ids_first | ids_more == THEMES and not ids_first & ids_more, a
        assert first.options[0].recommended


def test_at_most_two_visual_cards_are_reachable_in_guided() -> None:
    for a in ARCHETYPES:
        visual = [
            c
            for slot in SLOT_IDS
            for c in BANKS.cards_for_slot(slot, a, "guided")
            if c.type == "visual3" and can_be_asked(c)
        ]
        assert 1 <= len(visual) <= 2, a


# --------------------------------------------------------------------------- copy quality


def test_text_lengths_are_sane() -> None:
    for card in BANKS.all_cards():
        assert 10 <= len(card.text) <= 240, card.id
        if card.help:
            assert len(card.help) <= 200, card.id
        for opt in card.options:
            assert 2 <= len(opt.label) <= 50, (card.id, opt.id)
            if opt.description:
                assert len(opt.description) <= 120, (card.id, opt.id)
        if card.other.placeholder:
            assert len(card.other.placeholder) <= 100, card.id
        for lang, tr in card.i18n.items():
            assert 5 <= len(tr.text) <= 300, (card.id, lang)
            assert all(2 <= len(v) <= 60 for v in tr.options.values()), (card.id, lang)
            if tr.help:
                assert len(tr.help) <= 260, (card.id, lang)


def test_copy_is_plain_language() -> None:
    for card in BANKS.all_cards():
        if card.id in ENTERPRISE_JARGON_OK:
            continue
        parts = [card.text, card.help, card.other.placeholder]
        for opt in card.options:
            parts += [opt.label, opt.description]
        blob = " ".join(p for p in parts if p)
        assert not JARGON.search(blob), (card.id, JARGON.search(blob))
        assert "  " not in blob and "—" not in blob, card.id
        assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", blob), card.id  # no emoji
        for opt in card.options:
            assert "(recommended)" not in opt.label, (card.id, opt.id)  # the flag does that


def test_no_invented_prices_or_times_in_cards() -> None:
    for card in BANKS.all_cards():
        blob = " ".join([card.text, card.help or ""] + [o.description or "" for o in card.options])
        assert not re.search(r"[$₹€]|\bRs\.?\b|\bUSD\b|\bINR\b", blob), card.id
        assert not re.search(r"\b\d+\s*(minutes?|hours?|days?|weeks?|months?)\b", blob), card.id


# --------------------------------------------------------------------------- i18n


def test_every_card_has_hindi_and_hinglish_that_match_its_options() -> None:
    for card in BANKS.all_cards():
        assert set(card.i18n) == set(LANGS), card.id
        option_ids = {o.id for o in card.options}
        for lang, tr in card.i18n.items():
            assert set(tr.options) == option_ids, (card.id, lang, set(tr.options) ^ option_ids)
            if card.help:
                assert tr.help, (card.id, lang)
            if card.other.placeholder:
                assert tr.other_placeholder, (card.id, lang)
        assert DEVANAGARI.search(card.i18n["hi"].text), card.id
        assert all(DEVANAGARI.search(v) for v in card.i18n["hi"].options.values()), card.id
        hinglish = card.i18n["hi-Latn"]
        assert hinglish.text.isascii(), card.id
        assert all(v.isascii() for v in hinglish.options.values()), card.id
        assert not DEVANAGARI.search(hinglish.text)


def test_localize_uses_translation_and_falls_back_field_by_field() -> None:
    card = BANKS.card("Q-auth-01")
    hi = localize(card, "hi")
    assert hi.text == card.i18n["hi"].text and hi.id == card.id
    assert [o.label for o in hi.options] == [card.i18n["hi"].options[o.id] for o in card.options]
    assert [o.sets for o in hi.options] == [o.sets for o in card.options]
    assert hi.other.placeholder == card.i18n["hi"].other_placeholder
    assert localize(card, "en") is card
    assert localize(card, "fr") is card
    assert localize(card, None) is card
    assert localize(card, "hi-IN").text == card.i18n["hi"].text
    assert localize(card, "hi-Latn-IN").text == card.i18n["hi-Latn"].text
    partial = card.model_copy(
        update={
            "i18n": {"hi": card.i18n["hi"].model_copy(update={"help": None, "options": {"A": "x"}})}
        }
    )
    got = localize(partial, "hi")
    assert got.help == card.help
    assert [o.label for o in got.options] == ["x", card.options[1].label, card.options[2].label]


def test_lang_chain() -> None:
    assert lang_chain("en") == ["en"]
    assert lang_chain("EN") == ["en"]
    assert lang_chain(None) == ["en"]
    assert lang_chain("") == ["en"]
    assert lang_chain("hi") == ["hi", "en"]
    assert lang_chain("hi-Latn") == ["hi-Latn", "en"]  # Hinglish never falls back to Devanagari
    assert lang_chain("hi-latn") == ["hi-Latn", "en"]
    assert lang_chain("hi-IN") == ["hi-IN", "hi", "en"]
    assert lang_chain("hi_IN") == ["hi-IN", "hi", "en"]
    assert lang_chain("hi-Latn-IN") == ["hi-Latn-IN", "hi-Latn", "en"]
    assert lang_chain("pt-BR") == ["pt-BR", "pt", "en"]
    assert lang_chain("xx") == ["xx", "en"]
    assert lang_chain("not a language!") == ["en"]
    assert lang_chain("en-US") == ["en-US", "en"]


def test_resolve_text() -> None:
    texts = {"en": "hello", "hi": "namaste"}
    assert resolve_text(texts, "hi") == "namaste"
    assert resolve_text(texts, "hi-Latn") == "hello"
    assert resolve_text(texts, "fr") == "hello"
    assert resolve_text(texts, "hi-IN") == "namaste"
    assert resolve_text({"hi": "x"}, "fr", "fallback") == "fallback"
    with pytest.raises(KeyError):
        resolve_text({"hi": "x"}, "fr")


# --------------------------------------------------------------------------- coverage


def _has_usable_card(slot: str, archetype: str, mode: Mode, ctx: dict[str, SlotData]) -> bool:
    return any(
        c.kind in ("standard", "compliance") and can_be_asked(c) and holds(c.ask_if, ctx)
        for c in BANKS.cards_for_slot(slot, archetype, mode)
    )


def test_every_must_slot_has_a_card_for_each_archetype_and_mode() -> None:
    domains = [d.id for d in load_catalogs().sensitive_domains]
    for mode in MODES:
        for a in ARCHETYPES:
            for sd in slots_for_mode(mode, ("must",)):
                if not possibly_asked(sd, a, mode):
                    continue
                if sd.slot_id == "idea.summary":
                    assert BANKS.cards_for_slot(sd.slot_id, a, mode), (mode, a)
                elif sd.slot_id == "compliance.flags":
                    for d in domains:
                        ctx: dict[str, SlotData] = {"idea.archetype": a, "sensitive.domains": [d]}
                        assert _has_usable_card(sd.slot_id, a, mode, ctx), (mode, a, d)
                else:
                    assert BANKS.cards_for_slot(sd.slot_id, a, mode), (mode, a, sd.slot_id)


def test_every_enterprise_should_slot_has_an_enterprise_card() -> None:
    for a in ARCHETYPES:
        for sd in slots_for_mode("enterprise", ("should",)):
            if not possibly_asked(sd, a, "enterprise"):
                continue
            cards = BANKS.cards_for_slot(sd.slot_id, a, "enterprise")
            assert cards, (a, sd.slot_id)
            assert any("enterprise" in c.modes for c in cards), (a, sd.slot_id)


def test_enterprise_cards_are_enterprise_only() -> None:
    for card in BANKS.all_cards():
        enterprise_slot = slot_def(card.slot).group == "enterprise"
        if card.id.startswith("Q-ent-") or card.id == "Q-brand-02":
            assert card.modes == ["enterprise"], card.id
        if enterprise_slot:
            assert card.modes == ["enterprise"], card.id
    for a in ARCHETYPES:
        for slot in ("auth.sso", "regions", "release.channel", "approvers", "data.classification"):
            assert BANKS.cards_for_slot(slot, a, "guided") == []


def test_each_sensitive_domain_has_a_must_confirm_card() -> None:
    domains = load_catalogs().sensitive_domains
    cards = [c for c in BANKS.all_cards() if c.kind == "compliance"]
    assert [c.id for c in cards] == [f"Q-comp-0{i}" for i in range(1, 6)]
    for domain, card in zip(domains, cards, strict=True):
        assert card.slot == "compliance.flags" and card.archetypes == [] and card.modes == []
        assert compared_literals(card.ask_if or "", "sensitive.domains") == {domain.id}
        a, b, c = card.options
        assert a.recommended and card.skip.assumes == "A"
        assert a.sets["compliance.flags"] == domain.compliance_flags
        for slot, value in domain.conservative_defaults.items():
            assert a.sets[slot] == value and c.sets[slot] == value
        assert {k: v for k, v in a.sets.items() if k != "compliance.flags"} == (
            domain.conservative_defaults
        )
        assert b.sets == {"compliance.flags": []}
        assert set(as_list(c.sets["compliance.flags"])) == {
            *domain.compliance_flags,
            *COMPLIANCE_EXTRA_FLAGS,
        }
        for slot in domain.conservative_defaults:
            assert slot_def(slot).level["guided"] == "may"
        assert (
            "not legal advice"
            in " ".join([card.help or "", *(o.description or "" for o in card.options)]).lower()
        )


# --------------------------------------------------------------------------- cards_for_slot, inspiration


def test_cards_for_slot_filters_by_archetype_and_mode() -> None:
    assert [c.id for c in BANKS.cards_for_slot("auth.method", "commerce", "guided")] == [
        "Q-auth-01",
        "Q-fix-01",
    ]
    assert [c.id for c in BANKS.cards_for_slot("auth.method", "internal_workforce", "guided")] == [
        "Q-auth-02",
        "Q-fix-04",
    ]
    assert BANKS.cards_for_slot("auth.method", "unknown_type", "guided") == []
    assert BANKS.cards_for_slot("scope.apps", "custom", "guided") == []
    assert BANKS.cards_for_slot("scope.apps", None, "guided") == []
    assert [c.id for c in BANKS.cards_for_slot("brand.assets", "commerce", "enterprise")] == [
        "Q-brand-02"
    ]
    assert BANKS.cards_for_slot("brand.assets", "commerce", "guided") == []
    assert [c.id for c in BANKS.cards_for_slot("idea.summary", "custom", "guided")] == ["Q-idea-01"]
    # help, inspiration and nudge cards only on request
    helpers = BANKS.cards_for_slot("idea.summary", "custom", "guided", kinds=ALL_KINDS)
    assert {c.kind for c in helpers} == {"standard", "help", "nudge"}


def test_cards_for_slot_order_is_stable_and_specific_first() -> None:
    first = BANKS.cards_for_slot("compliance.flags", "commerce", "guided")
    assert [c.id for c in first] == [f"Q-comp-0{i}" for i in range(1, 6)]
    again = load_question_banks().cards_for_slot("compliance.flags", "commerce", "guided")
    assert first == again
    # standard before resolution, archetype-specific before shared
    got = BANKS.cards_for_slot("auth.method", "custom", "guided")
    assert [c.kind for c in got] == ["standard", "resolution"]
    assert got[0].archetypes == ["commerce", "food_delivery", "booking_services", "custom"]
    assert got[1].archetypes == ["custom"]


def test_inspiration_cards() -> None:
    for a in ARCHETYPES:
        own = [c for c in BANKS.inspiration(a) if c.archetypes == [a]]
        assert len(own) == 1, a
        card = own[0]
        assert card.kind == "inspiration" and len(card.options) == 3
        for opt in card.options:
            assert set(opt.sets) == {"idea.summary", "idea.archetype"}
            assert opt.sets["idea.archetype"] == a
            assert isinstance(opt.sets["idea.summary"], str) and len(opt.sets["idea.summary"]) > 20
        assert BANKS.inspiration(a)[-1].id == "Q-help-01"
    shared = BANKS.inspiration(None)
    assert [c.id for c in shared] == ["Q-help-01"] == [c.id for c in BANKS.inspiration("unknown")]
    help_card = shared[0]
    assert help_card.kind == "help" and len(help_card.options) == 3 and help_card.other.enabled
    assert help_card.skip.assumes == "C"
    assert help_card.options[2].sets["idea.archetype"] == "custom"  # spec 3.3 default
    assert BANKS.card("Q-help-02").type == "text" and BANKS.card("Q-help-02").kind == "help"


def test_nudge_cards_use_semantic_option_ids() -> None:
    # One nudge card: the offer after three skips. The first nudge (silence or off-topic chat) is a
    # plain message (ui_copy "nudge") beside the same card, so there is no card for it.
    nudges = [c for c in BANKS.all_cards() if c.kind == "nudge"]
    assert [c.id for c in nudges] == ["Q-nudge-02"]
    n2 = nudges[0]
    assert [o.id for o in n2.options] == ["finish", "continue"]
    assert n2.options[0].recommended and n2.kind == "nudge"
    assert "Q-nudge-01" not in {c.id for c in BANKS.all_cards()}


# --------------------------------------------------------------------------- chips


def test_starter_chips() -> None:
    greeting = BANKS.chips(None)
    assert len(greeting) == 6
    assert BANKS.chips("not-an-archetype") == greeting
    all_ids: list[str] = []
    for key in (None, *ARCHETYPES):
        chips = BANKS.chips(key)
        assert len(chips) == 6, key
        for chip in chips:
            all_ids.append(chip.id)
            assert set(chip.i18n) == set(LANGS) and chip.prefill and len(chip.label) <= 40
            assert DEVANAGARI.search(chip.i18n["hi"]) and chip.i18n["hi-Latn"].isascii()
            for slot, value in chip.sets.items():
                assert slot in SLOT_IDS
                assert slot == "idea.archetype" and value in ARCHETYPES
            if key is not None:
                assert chip.archetype == key and chip.sets == {"idea.archetype": key}
    assert len(all_ids) == len(set(all_ids)) == 36
    assert {str(c.sets.get("idea.archetype")) for c in greeting} >= set(ARCHETYPES)
    assert greeting[-1].sets == {}  # "I am not sure yet" goes to help mode


def test_chip_language_fallback() -> None:
    en = BANKS.chips(None, "en")
    hi = BANKS.chips(None, "hi")
    hl = BANKS.chips(None, "hi-Latn")
    assert [c.label for c in en] != [c.label for c in hi] != [c.label for c in hl]
    assert hi[0].label == "ऑनलाइन दुकान" and hl[0].label == "Online dukaan"
    assert [c.label for c in BANKS.chips(None, "fr")] == [c.label for c in en]
    assert [c.label for c in BANKS.chips("commerce", "hi-IN")] == [
        c.label for c in BANKS.chips("commerce", "hi")
    ]
    assert [c.prefill for c in hi] == [c.prefill for c in en]


# --------------------------------------------------------------------------- knowledge and UI copy


def test_knowledge_base() -> None:
    snippets = BANKS.snippets()
    topics = {s.topic for s in snippets}
    assert {
        "what_we_build",
        "how_it_works",
        "preview",
        "code_download",
        "privacy",
        "after_approval",
        "cost",
        "time",
    } <= topics
    assert len({s.id for s in snippets}) == len(snippets)
    for s in snippets:
        assert set(s.text) == {"en", *LANGS}, s.id
        assert s.keywords, s.id
        assert len(s.text["en"]) <= 420, s.id
        assert not re.search(r"\d", s.text["en"]), s.id  # facts we can state carry no numbers
        assert not re.search(r"[$₹€]", s.text["en"]), s.id
        assert DEVANAGARI.search(s.text["hi"]) and s.text["hi-Latn"].isascii(), s.id
    by_topic = {s.topic: s for s in snippets}
    for topic in ("cost", "time"):  # unknowns say how the user will see it, never a number
        assert (
            "you will see the plan before anything is built" in by_topic[topic].text["en"].lower()
        )
    assert "will not guess" in by_topic["cost"].text["en"]
    assert "will not guess" in by_topic["code_download"].text["en"]
    how = by_topic["how_it_works"].text["en"]
    assert "first review" in how and "second review" in how


def test_knowledge_for_c3_follows_the_language_chain() -> None:
    en = BANKS.knowledge("en")
    assert [k.id for k in en] == [s.id for s in BANKS.snippets()]
    hi = BANKS.knowledge("hi")
    assert hi[0].text != en[0].text and DEVANAGARI.search(hi[0].text)
    assert BANKS.knowledge("fr") == en
    assert BANKS.knowledge("hi-Latn")[0].text == BANKS.snippets()[0].text["hi-Latn"]
    assert BANKS.knowledge(None) == en


# --------------------------------------------------------------------------- Guided simulation


def _answer(card: Card, slots: dict[str, SlotData]) -> None:
    """Tap the recommended option (or tick the pre-ticked set); text cards get a plausible text."""
    if card.type == "multi":
        slots[card.slot] = [o.id for o in card.options if o.recommended]
    elif card.type in ("text", "upload"):
        slots[card.slot] = "Blue Basket" if card.slot == "brand.name" else "answer"
    else:
        rec = next(o for o in card.options if o.recommended)
        slots.update(rec.sets)


def simulate(
    archetype: str, mode: Mode, sensitive: list[str] | None = None
) -> tuple[list[Card], dict[str, SlotData]]:
    """The order logic of spec 3.4 with plausible (recommended) answers.

    The first message supplies the idea and the detected archetype; the archetype is still
    confirmed with a card. Levels: must in Guided, must and should in Enterprise.
    """
    slots: dict[str, SlotData] = {"idea.summary": "first message", "idea.archetype": archetype}
    extra: dict[str, SlotData] = {"sensitive.domains": sensitive}
    asked: list[Card] = []
    levels = ("must",) if mode == "guided" else ("must", "should")
    for sd in slots_for_mode(mode, levels):
        ctx = {**slots, **extra}
        if sd.slot_id == "idea.summary" or not holds(sd.ask_if, ctx):
            continue
        confirm = sd.slot_id == "idea.archetype"
        if slots.get(sd.slot_id) is not None and not confirm:
            continue
        while True:
            ctx = {**slots, **extra}
            options = [
                c
                for c in BANKS.cards_for_slot(sd.slot_id, archetype, mode)
                if c.kind in ("standard", "compliance")
                and c not in asked
                and can_be_asked(c)
                and holds(c.ask_if, ctx)
            ]
            if not options:
                break
            asked.append(options[0])
            _answer(options[0], slots)
            if sd.slot_id != "compliance.flags":  # one card per slot, except one per domain
                break
        assert any(c.slot == sd.slot_id for c in asked), f"no card for {sd.slot_id} ({archetype})"
        # a skip or a typed answer leaves nothing contradictory
        for res in (c for c in BANKS.all_cards() if c.kind == "resolution"):
            assert not holds(res.ask_if, {**slots, **extra}), res.id
    return asked, slots


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_guided_path_asks_about_seven_cards(archetype: str) -> None:
    asked, slots = simulate(archetype, "guided")
    ids = [c.id for c in asked]
    assert len(ids) == len(set(ids))
    assert 6 <= len(asked) <= 8, ids  # about seven, idea card aside
    assert len(asked) + 1 <= 10  # hard cap of ten, counting the idea card
    assert asked[0].slot == "idea.archetype"  # confirm the detected type first
    assert sum(1 for c in asked if c.type == "visual3") <= 2
    # the flow ends with every must slot filled and a look chosen
    for sd in slots_for_mode("guided", ("must",)):
        if sd.slot_id == "compliance.flags":
            continue
        if holds(sd.ask_if, slots):
            assert slots.get(sd.slot_id) is not None, sd.slot_id
    assert slots["design.direction"] in THEMES
    slot_order = [slot_def(c.slot).order for c in asked]
    assert slot_order == sorted(slot_order)


def test_guided_paths_in_detail() -> None:
    expected = {
        "commerce": [
            "idea.archetype",
            "scope.apps",
            "scope.features",
            "auth.method",
            "design.direction",
            "brand.name",
            "pay.model",
        ],
        "food_delivery": [
            "idea.archetype",
            "scope.apps",
            "scope.features",
            "auth.method",
            "design.direction",
            "brand.name",
            "pay.model",
        ],
        "booking_services": [
            "idea.archetype",
            "scope.apps",
            "scope.features",
            "auth.method",
            "design.direction",
            "brand.name",
            "pay.model",
        ],
        "internal_workforce": [
            "idea.archetype",
            "scope.features",
            "users.roles",
            "auth.method",
            "design.direction",
            "brand.name",
        ],
        "custom": [
            "idea.archetype",
            "scope.features",
            "users.roles",
            "auth.method",
            "design.direction",
            "brand.name",
        ],
    }
    for a, slots in expected.items():
        asked, _ = simulate(a, "guided")
        assert sorted([c.slot for c in asked], key=lambda s: slot_def(s).order) == slots, a


@pytest.mark.parametrize("domain", [d.id for d in load_catalogs().sensitive_domains])
def test_a_sensitive_domain_adds_one_compliance_card_and_stays_under_the_cap(domain: str) -> None:
    for a in ARCHETYPES:
        base, _ = simulate(a, "guided")
        asked, slots = simulate(a, "guided", sensitive=[domain])
        assert len(asked) == len(base) + 1
        assert [c.kind for c in asked].count("compliance") == 1
        assert len(asked) + 1 <= 10
        assert (
            slots["compliance.flags"]
            == load_catalogs()
            .sensitive_domains[[d.id for d in load_catalogs().sensitive_domains].index(domain)]
            .compliance_flags
        )


def test_all_sensitive_domains_at_once_stay_within_the_card_cap() -> None:
    every = [d.id for d in load_catalogs().sensitive_domains]
    for a in ARCHETYPES:
        asked, _ = simulate(a, "guided", sensitive=every)
        assert [c.kind for c in asked].count("compliance") == len(every)
    # a realistic pair keeps the total at or under ten
    asked, _ = simulate("food_delivery", "guided", sensitive=["precise_location", "finance"])
    assert len(asked) + 1 <= 10


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_enterprise_path_asks_every_must_and_should_slot(archetype: str) -> None:
    asked, slots = simulate(archetype, "enterprise")
    asked_slots = {c.slot for c in asked}
    for sd in slots_for_mode("enterprise", ("must", "should")):
        if sd.slot_id == "idea.summary" or sd.slot_id == "compliance.flags":
            continue
        if holds(sd.ask_if, slots):
            assert sd.slot_id in asked_slots, (archetype, sd.slot_id)
    assert {"auth.sso", "data.classification", "approvers"} <= asked_slots
    assert all(not c.modes or "enterprise" in c.modes for c in asked)
    assert len(asked) <= 24


def test_skipping_every_card_uses_the_documented_defaults() -> None:
    """ "You decide" picks the recommended option, which matches the slot table defaults."""
    for a in ARCHETYPES:
        asked, slots = simulate(a, "guided")
        for card in asked:
            if card.slot == "idea.archetype":
                assert slots["idea.archetype"] == a
            if card.slot == "auth.method":
                assert slots["auth.method"] == slot_def("auth.method").default
            if card.slot == "pay.model":
                assert slots["pay.model"] == slot_def("pay.model").default
            if card.slot == "brand.name":
                assert slots["brand.name"]


def test_enterprise_must_confirm_slots_are_never_silently_assumed() -> None:
    must_confirm = {s.slot_id for s in load_catalogs().slots if "enterprise" in s.must_confirm_in}
    for a in ARCHETYPES:
        asked, _ = simulate(a, "enterprise", sensitive=["health"])
        got = {c.slot for c in asked}
        assert {"auth.sso", "compliance.flags"} <= got
        assert must_confirm - {"pay.model"} <= got
    asked, _ = simulate("commerce", "enterprise")
    assert "pay.model" in {c.slot for c in asked}


def test_no_card_is_dead() -> None:
    """Every orderable card applies somewhere: its slot is asked (must or should) in a mode and
    for an archetype the card allows, and the slot's own precondition can hold there."""
    for card in BANKS.all_cards():
        if card.kind in NEVER_ORDERED or card.kind == "resolution" or not can_be_asked(card):
            continue
        sd = slot_def(card.slot)
        applies = [
            (a, m)
            for a in card.archetypes or ARCHETYPES
            for m in card.modes or MODES
            if sd.level[m] in ("must", "should") and possibly_asked(sd, a, m)
        ]
        assert applies, f"{card.id} is never reachable"


def test_lang_chain_keeps_numeric_subtags() -> None:
    assert lang_chain("es-419") == ["es-419", "es", "en"]


def test_localize_without_a_translated_placeholder_keeps_the_english_one() -> None:
    card = BANKS.card("Q-auth-01")
    tr = card.i18n["hi"].model_copy(update={"other_placeholder": None})
    got = localize(card.model_copy(update={"i18n": {"hi": tr}}), "hi")
    assert got.other.placeholder == card.other.placeholder
    assert got.text == tr.text


def test_duplicate_ui_copy_ids_are_rejected_at_load() -> None:
    files = [f for _, f in raw_files()]
    shared = next(f for f in files if f.archetype == "shared")
    other = next(f for f in files if f.archetype == "commerce")
    clash = other.model_copy(update={"ui_copy": [shared.ui_copy[0]]})
    with pytest.raises(ValueError, match="duplicate ui copy id"):
        QuestionBanks([shared, clash])
