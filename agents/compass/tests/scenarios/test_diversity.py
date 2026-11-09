"""The cross-idea diversity corpus (build prompt 02, sections 2.6 and 6).

Five hand-written first batches for five unlike ideas must pass the code validators, use project words (at least
90% of the non-universal questions), differ from each other (pairwise term overlap below the policy threshold)
and share no wording verbatim. The same measures must flag a generic corpus (the same questions for every idea)
and the old engine's real cards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mde_compass.catalog import load_catalog
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.engine.question_validators import cross_idea_overlap, is_universal
from mde_compass.scenarios import corpus

DATA = Path(__file__).parent / "data" / "corpus"
REPO = Path(__file__).resolve().parents[4]
EVIDENCE = REPO / "docs" / "compass" / "evidence" / "before-first-questions.json"
POLICY = DEFAULT_CONFIG.interview
IDEAS = {
    "tiffin_pune",
    "clinic_appointments",
    "pharmacy_inventory_b2b",
    "kids_language_learning",
    "senior_ride_sharing",
}

GOOD = corpus.load_corpus(DATA / "first_batches.yaml")
GENERIC = corpus.load_corpus(DATA / "generic_batches.yaml")
CATALOG = load_catalog()


def test_the_corpus_covers_the_five_ideas_of_the_before_evidence() -> None:
    assert set(GOOD) == IDEAS == set(GENERIC)
    if EVIDENCE.exists():
        before = corpus.load_before_evidence(EVIDENCE)
        assert {k: v.idea for k, v in before.items()} == {k: v.idea for k, v in GOOD.items()}


@pytest.mark.parametrize("key", sorted(IDEAS))
def test_each_batch_has_the_shape_the_planner_prompt_asks_for(key: str) -> None:
    cards = GOOD[key].cards
    assert 5 <= len(cards) <= 7
    groups: dict[str, int] = {}
    for c in cards:
        groups[c.group_id or ""] = groups.get(c.group_id or "", 0) + 1
    assert 2 <= len(groups) <= 3 and all(2 <= n <= 4 for n in groups.values())
    assert any(c.type == "text" or c.type == "open_prompt" for c in cards)
    assert all(c.need_ids and c.intent and c.why_we_ask for c in cards)


@pytest.mark.parametrize("key", sorted(IDEAS))
def test_every_hand_written_question_passes_the_code_validators(key: str) -> None:
    result = corpus.validate_idea(GOOD[key], CATALOG)
    assert not result.rejected, [(r.card_id, r.reasons) for r in result.rejected]


def test_at_least_ninety_percent_of_the_non_universal_questions_use_a_project_term() -> None:
    shares = corpus.term_share(GOOD, CATALOG)
    assert all(s >= POLICY.project_term_share_min for s in shares.values()), shares
    pooled = [c for item in GOOD.values() for c in item.cards if not is_universal(c, CATALOG)]
    assert len(pooled) >= 20  # the share is measured over real questions, not over nothing


def test_the_batches_differ_materially_between_ideas() -> None:
    pairs = corpus.overlap_pairs(GOOD, CATALOG)
    assert len(pairs) == 10
    worst_pair, worst = max(pairs.items(), key=lambda kv: kv[1])
    assert worst < POLICY.cross_idea_overlap_max, (worst_pair, worst)
    assert (
        worst < 0.1
    )  # measured: the corpus is far below the 0.5 threshold, so the threshold has headroom


def test_no_wording_is_shared_verbatim_between_ideas() -> None:
    total, distinct, shared = corpus.verbatim_sharing(GOOD)
    assert shared == 0 and distinct == total


def test_the_questions_use_words_only_their_own_idea_has() -> None:
    from mde_compass.engine import terms

    for key, item in GOOD.items():
        mine = " ".join(c.question for c in item.cards)
        others = " ".join(c.question for k, v in GOOD.items() if k != key for c in v.cards)
        own = {t for t in terms.extract_terms(mine) if t not in set(terms.extract_terms(others))}
        assert len(own) >= 8, (key, sorted(own))


# ------------------------------------------------------------------------------------ the negative corpus
def test_generic_batches_are_flagged_by_the_validators() -> None:
    flagged = 0
    for key, item in GENERIC.items():
        result = corpus.validate_idea(item, CATALOG)
        codes = [r for rej in result.rejected for r in rej.reasons]
        assert any("no_project_term" in c for c in codes), (key, codes)
        flagged += len(result.rejected)
    assert flagged >= 5 * 4  # at least four of the six generic questions per idea cannot be shown


def test_generic_batches_have_a_low_project_term_share() -> None:
    shares = corpus.term_share(GENERIC, CATALOG)
    assert all(s < POLICY.project_term_share_min for s in shares.values()), shares


def test_generic_batches_overlap_completely_across_ideas() -> None:
    overlap = cross_idea_overlap({k: v.cards for k, v in GENERIC.items()}, CATALOG)
    assert overlap > POLICY.cross_idea_overlap_max and overlap == pytest.approx(1.0)
    total, distinct, shared = corpus.verbatim_sharing(GENERIC)
    assert distinct == 6 and shared == total  # six wordings, every one of them given to every idea


@pytest.mark.skipif(not EVIDENCE.exists(), reason="the before-evidence file lives in docs/")
def test_the_old_engines_real_cards_are_flagged_the_same_way() -> None:
    before = corpus.load_before_evidence(EVIDENCE, CATALOG)
    total, distinct, shared = corpus.verbatim_sharing(before)
    assert (total, distinct, shared) == (
        30,
        9,
        28,
    )  # the owner's numbers: 9 wordings, 28 of 30 shared
    assert all(
        s < POLICY.project_term_share_min for s in corpus.term_share(before, CATALOG).values()
    )
    assert (
        cross_idea_overlap({k: v.cards for k, v in before.items()}, CATALOG)
        > POLICY.cross_idea_overlap_max
    )
