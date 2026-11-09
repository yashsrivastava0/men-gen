"""Code validators for planner questions (build prompt 02, sections 2.5 and 2.6; ADR 0015 point 6).

The model proposes questions; this module decides whether each may be shown. Every rule is a named issue code, so a
rejection can be explained, repaired once (the planner is told the codes) or dropped, but a rejected question is never
replaced by a canned one. Pure functions over ``Card`` and a frozen ``ValidationContext``; no I/O, no model.

Rules (code: meaning)

* ``empty_question`` / ``missing_intent``: text and intent are required (the intent is the duplicate key).
* ``no_project_term``: a non-universal question must contain a project term. Exempt: confirmation cards, and cards whose
  needs are all flagged ``universal`` in the catalog (for example "which languages").
* ``duplicate_id`` / ``duplicate_intent`` / ``duplicate_text``: against the whole ledger and the earlier questions of the
  batch. Same normalized intent is the primary key; then an overlapping need with normalized text similarity at or above
  ``InterviewPolicy.duplicate_similarity_min``; identical normalized text always. A follow-up is not a duplicate of its
  parent.
* ``unmapped_need``: every need id must be a catalog slot or ``domain:<topic>``.
* ``question_too_long`` / ``option_too_long``: policy word limits (Hindi gets the extra words card validation allows).
* ``options_not_distinct``, ``choose_one_options`` (exactly three distinct options, otherwise use an open prompt or a
  quick form), ``options_count`` (other card types), ``missing_examples`` (open prompts and quick forms need hints).
* ``option_basis`` (typical, document and own-words claims must be backed; reuses card validation), ``option_sets``.
* ``wrong_language`` (declared language and script), ``technology_question`` (``no_ask`` topics; a word the user used
  themselves is allowed), ``declined_option`` (a declined category or tier C capability offered as an option),
  ``leading_wording``, ``invented_number``, ``brand_name``, ``multiple_questions``, ``not_a_question`` (a choice card's
  hypothesis must be phrased as a question).
* ``too_many_recommended`` / ``recommended_not_allowed`` / ``recommended_without_reason``.
* Batch only: ``over_batch_size``, ``over_group_size``.

Batch level: ``validate_batch`` also reports; ``project_term_share``, ``cross_idea_overlap`` measure specificity and
diversity; ``coverage_issues`` checks under-asking (every must or high-impact need is covered, satisfied or assumed).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from itertools import combinations
from typing import Literal

from mde_compass.catalog.loader import Catalog
from mde_compass.config import CompassConfig, InterviewPolicy, QuestionPolicy
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import AssumedInstead, NeedStatus, RejectedQuestion
from mde_compass.engine import cards as card_rules
from mde_compass.engine.language import normalize
from mde_compass.engine.needs import expert_gap_obligations
from mde_compass.engine.ports import CardDraft, CardRequest
from mde_compass.engine.terms import contains_project_term, extract_terms, jaccard, tokenize
from mde_compass.safety.screen import declined_category, phrase_hits

IssueCode = Literal[
    "empty_question",
    "missing_intent",
    "no_project_term",
    "duplicate_id",
    "duplicate_intent",
    "duplicate_text",
    "unmapped_need",
    "question_too_long",
    "option_too_long",
    "options_not_distinct",
    "choose_one_options",
    "options_count",
    "missing_examples",
    "option_basis",
    "option_sets",
    "wrong_language",
    "technology_question",
    "declined_option",
    "leading_wording",
    "invented_number",
    "brand_name",
    "multiple_questions",
    "not_a_question",
    "too_many_recommended",
    "recommended_not_allowed",
    "recommended_without_reason",
    "over_batch_size",
    "over_group_size",
]

CHOICE_TYPES: frozenset[str] = frozenset({"choose_one", "look_signals", "confirm"})
"""Card types whose options are alternatives: only these may carry a recommended option."""
QUESTION_MARK_TYPES: frozenset[str] = frozenset(
    {"choose_one", "pick_many", "priority", "confirm", "look_signals"}
)
EXAMPLE_TYPES: frozenset[str] = frozenset({"open_prompt", "quick_form"})
DOMAIN_PREFIX = "domain:"
TEXT_COMPARE_CHARS = 300
_QUESTION_MARKS = ("?", "？", "؟")

# Leading wording beyond card_rules.yaml: the question pushes the user toward an answer.
_LEADING_EXTRA: tuple[str, ...] = (
    "dont you think",
    "wouldnt you agree",
    "wouldnt you say",
    "isnt it true",
    "isnt it better",
    "everyone knows",
    "you should",
    "you must",
    "you surely",
    "no doubt",
    "undoubtedly",
    "without a doubt",
    "the only way",
    "the right way",
    "the proper way",
    "popular choice",
    "आपको चाहिए कि",
    "ज़ाहिर है",
)

# Technology and stack questions (catalog ``no_ask``): topic id -> trigger phrases. The catalog decides which
# topics are active; a topic without an entry here falls back to its own id as a phrase. A phrase made only of
# words the user used themselves is allowed (a "server" in a restaurant app, a "Python" course).
_TOPIC_PHRASES: dict[str, tuple[str, ...]] = {
    "stack_and_framework": (
        "tech stack",
        "technology stack",
        "framework",
        "react native",
        "flutter",
        "swiftui",
        "kotlin",
        "javascript",
        "typescript",
        "programming language",
        "cross platform",
        "native or hybrid",
        "टेक स्टैक",
        "फ्रेमवर्क",
    ),
    "hosting_and_database": (
        "database",
        "hosting",
        "cloud provider",
        "aws",
        "azure",
        "google cloud",
        "firebase",
        "supabase",
        "mysql",
        "postgres",
        "postgresql",
        "mongodb",
        "backend",
        "डेटाबेस",
        "होस्टिंग",
    ),
    "hex_colors": (
        "hex code",
        "hex color",
        "hex colour",
        "hex value",
        "color code",
        "colour code",
        "rgb",
    ),
    "legal_wording": (
        "terms and conditions",
        "privacy policy wording",
        "legal text",
        "legal wording",
        "disclaimer text",
        "terms of service wording",
    ),
    "database_tables_and_api_design": (
        "database schema",
        "table structure",
        "api design",
        "api endpoint",
        "api endpoints",
        "rest api",
        "graphql",
        "data model",
        "schema design",
    ),
}
_HEX_COLOR = re.compile(r"(?<![\w#])#[0-9A-Fa-f]{3,8}(?![\w])")


# ------------------------------------------------------------------------------------------ data types
@dataclass(frozen=True)
class Issue:
    code: IssueCode
    message: str
    ref: str = ""  # the conflicting question id, for duplicates

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class LedgerEntry:
    """What the validators remember of an earlier question."""

    card_id: str
    intent: str = ""
    need_ids: tuple[str, ...] = ()
    text: str = ""

    @classmethod
    def from_card(cls, card: Card) -> LedgerEntry:
        return cls(card.id, card.intent, tuple(card.need_ids), card.question)


@dataclass(frozen=True)
class ValidationContext:
    """Everything a question is checked against."""

    terms: frozenset[str]
    catalog: Catalog
    language: str = "en"
    ledger: tuple[LedgerEntry, ...] = ()  # every question asked so far
    planned: tuple[LedgerEntry, ...] = ()  # accepted questions of the batch being validated
    policy: InterviewPolicy = field(default_factory=InterviewPolicy)
    questions: QuestionPolicy = field(default_factory=QuestionPolicy)
    user_text: str = ""  # the user's own words (own-words claims, language and brand checks)
    evidence: tuple[str, ...] = ()  # document quotes: what a ``your_document`` option may rest on
    hints: tuple[
        str, ...
    ] = ()  # typical labels from families.yaml: what a ``typical`` option may rest on
    batch_limit: int | None = (
        None  # most questions this batch may hold (None: the largest policy batch)
    )
    max_per_group: int | None = None  # None: the policy's group size

    @classmethod
    def build(
        cls,
        catalog: Catalog,
        terms: Iterable[str],
        *,
        language: str = "en",
        ledger_cards: Iterable[Card] = (),
        config: CompassConfig | None = None,
        user_text: str = "",
        evidence: Iterable[str] = (),
        hints: Iterable[str] = (),
        batch_limit: int | None = None,
    ) -> ValidationContext:
        cfg = config or CompassConfig()
        return cls(
            terms=frozenset(terms),
            catalog=catalog,
            language=language,
            ledger=tuple(LedgerEntry.from_card(c) for c in ledger_cards),
            policy=cfg.interview,
            questions=cfg.questions,
            user_text=user_text,
            evidence=tuple(evidence),
            hints=tuple(hints),
            batch_limit=batch_limit,
        )

    def effective_batch_limit(self) -> int:
        if self.batch_limit is not None:
            return max(0, self.batch_limit)
        p = self.policy
        return max(p.quick_first_batch[1], p.standard_first_batch[1], p.thorough_batch[1])

    def effective_group_limit(self) -> int:
        return (
            self.max_per_group
            if self.max_per_group is not None
            else self.policy.questions_per_group[1]
        )


@dataclass(frozen=True)
class BatchValidation:
    accepted: list[Card]
    rejected: list[RejectedQuestion]
    issues: dict[str, list[Issue]] = field(
        default_factory=dict
    )  # card id -> its issues (rejected cards only)

    @property
    def accepted_share(self) -> float:
        total = len(self.accepted) + len(self.rejected)
        return len(self.accepted) / total if total else 1.0


# -------------------------------------------------------------------------------------------- helpers
def _words(text: str) -> int:
    return len(text.split())


def _is_known_need(need_id: str, catalog: Catalog) -> bool:
    if need_id in catalog.slots:
        return True
    return need_id.startswith(DOMAIN_PREFIX) and bool(need_id[len(DOMAIN_PREFIX) :].strip())


def is_universal(card: Card, catalog: Catalog) -> bool:
    """Exempt from the project-term rule: a confirmation card, or every need it serves is flagged ``universal``."""
    if card.type == "confirm":
        return True
    needs = [n for n in card.need_ids if n]
    return bool(needs) and all(n in catalog.slots and catalog.slots[n].universal for n in needs)


def _draft(card: Card) -> CardDraft:
    return CardDraft(
        question=card.question,
        why_we_ask=card.why_we_ask,
        options=list(card.options),
        placeholder=card.custom_input.placeholder,
        language=card.language,
    )


def _request(card: Card, ctx: ValidationContext, evidence: Sequence[str]) -> CardRequest:
    slots = [n for n in card.need_ids if n in ctx.catalog.slots] or [
        s for s in card.slots if s in ctx.catalog.slots
    ]
    return CardRequest(
        frame_id=card.frame or "planner",
        slots=slots,
        card_type=card.type,
        hints=list(ctx.hints),
        evidence=list(evidence),
        must_confirm=card.must_confirm,
    )


def _scan_text(card: Card) -> str:
    parts = [card.question, *(f"{o.label} {o.description}" for o in card.options)]
    return normalize(" ".join(parts))


def _similar(a: str, b: str, threshold: float) -> bool:
    a, b = a[:TEXT_COMPARE_CHARS], b[:TEXT_COMPARE_CHARS]
    if not a or not b:
        return False
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
        return False
    return matcher.ratio() >= threshold


# ------------------------------------------------------------------------------------------ the rules
def _check_basics(card: Card, ctx: ValidationContext) -> list[Issue]:
    issues: list[Issue] = []
    if not card.question.strip():
        issues.append(Issue("empty_question", "the question text is empty"))
    if not card.intent.strip():
        issues.append(Issue("missing_intent", "the intent (what we want to learn) is required"))
    unknown = [n for n in card.need_ids if not _is_known_need(n, ctx.catalog)]
    if not card.need_ids:
        issues.append(
            Issue("unmapped_need", "the question serves no need (use a slot id or domain:<topic>)")
        )
    elif unknown:
        issues.append(
            Issue("unmapped_need", f"not a catalog need or domain:<topic>: {', '.join(unknown)}")
        )
    return issues


def _check_project_term(card: Card, ctx: ValidationContext) -> list[Issue]:
    if is_universal(card, ctx.catalog) or contains_project_term(card.question, ctx.terms):
        return []
    return [Issue("no_project_term", "a non-universal question must use a word from the project")]


def _check_duplicates(card: Card, ctx: ValidationContext) -> list[Issue]:
    norm_intent = normalize(card.intent)
    norm_text = normalize(card.question)
    needs = set(card.need_ids)
    threshold = ctx.policy.duplicate_similarity_min
    for entry in (*ctx.ledger, *ctx.planned):
        if entry.card_id == card.id:
            if entry.card_id != card.parent_card_id:
                return [Issue("duplicate_id", f"id {card.id} is already used", entry.card_id)]
            continue
        if card.parent_card_id is not None and entry.card_id == card.parent_card_id:
            continue  # a follow-up refines its parent: it is not a repeat of it
        if norm_intent and norm_intent == normalize(entry.intent):
            return [Issue("duplicate_intent", f"same intent as {entry.card_id}", entry.card_id)]
        entry_text = normalize(entry.text)
        if norm_text and norm_text == entry_text:
            return [Issue("duplicate_text", f"same wording as {entry.card_id}", entry.card_id)]
        if needs & set(entry.need_ids) and _similar(norm_text, entry_text, threshold):
            return [
                Issue(
                    "duplicate_text",
                    f"serves the same need with nearly the same wording as {entry.card_id}",
                    entry.card_id,
                )
            ]
    return []


def _check_lengths(card: Card, ctx: ValidationContext) -> list[Issue]:
    extra_q = extra_o = 0
    if ctx.language == "hi":
        rules = card_rules.load_rules()
        extra_q, extra_o = rules.hi_extra_question, rules.hi_extra_option
    max_q = ctx.questions.max_question_words + extra_q
    max_o = ctx.questions.max_option_words + extra_o
    issues: list[Issue] = []
    if _words(card.question) > max_q:
        issues.append(Issue("question_too_long", f"more than {max_q} words"))
    for option in card.options:
        if _words(option.label) > max_o:
            issues.append(
                Issue("option_too_long", f"option {option.id} has more than {max_o} words")
            )
    return issues


def _check_options(card: Card, ctx: ValidationContext) -> list[Issue]:
    issues: list[Issue] = []
    options = card.options
    labels = [normalize(o.label) for o in options]
    ids = [o.id for o in options]
    if (
        any(not lab for lab in labels)
        or len(set(labels)) != len(labels)
        or any(not i.strip() for i in ids)
        or len(set(ids)) != len(ids)
    ):
        issues.append(
            Issue("options_not_distinct", "option ids and labels must be non-empty and distinct")
        )
    want = ctx.questions.option_count
    if card.type == "choose_one" and len(options) != want:
        issues.append(
            Issue(
                "choose_one_options",
                f"choose_one needs exactly {want} distinct options, got {len(options)}; "
                "otherwise use an open prompt or a quick form with examples",
            )
        )
    elif card.type == "look_signals" and len(options) != want:
        issues.append(Issue("options_count", f"look_signals needs exactly {want} options"))
    elif card.type == "pick_many" and not (
        card_rules.CHECKLIST_MIN <= len(options) <= card_rules.CHECKLIST_MAX
    ):
        issues.append(
            Issue(
                "options_count",
                f"a checklist needs {card_rules.CHECKLIST_MIN}..{card_rules.CHECKLIST_MAX} options",
            )
        )
    elif card.type == "priority" and not (
        card_rules.RANK_MIN <= len(options) <= card_rules.CHECKLIST_MAX
    ):
        issues.append(
            Issue(
                "options_count",
                f"a ranking needs {card_rules.RANK_MIN}..{card_rules.CHECKLIST_MAX} options",
            )
        )
    elif card.type == "confirm" and not options:
        issues.append(Issue("options_count", "a confirmation needs at least one option"))
    if card.type in EXAMPLE_TYPES and not any(e.strip() for e in card.examples):
        issues.append(Issue("missing_examples", f"a {card.type} question needs example hints"))
    return issues


def _check_recommended(card: Card) -> list[Issue]:
    recommended = [o for o in card.options if o.recommended]
    issues: list[Issue] = []
    if len(recommended) > 1:
        issues.append(Issue("too_many_recommended", "at most one option may be recommended"))
    if recommended and card.type not in CHOICE_TYPES:
        issues.append(
            Issue("recommended_not_allowed", f"a {card.type} card cannot recommend an option")
        )
    for option in recommended:
        if not option.reason.strip():
            issues.append(
                Issue(
                    "recommended_without_reason",
                    f"option {option.id} is recommended without a reason",
                )
            )
    return issues


def _from_card_errors(errors: Iterable[str]) -> list[Issue]:
    """Map the error strings of ``engine.cards`` checks onto named issue codes."""
    issues: list[Issue] = []
    for error in errors:
        head, _, detail = error.partition(": ")
        if head == "wording.leading":
            issues.append(Issue("leading_wording", detail))
        elif head == "wording.statistic":
            issues.append(Issue("invented_number", detail))
        elif head == "brand.name":
            issues.append(Issue("brand_name", detail))
        elif head == "one_idea":
            issues.append(Issue("multiple_questions", detail))
        elif head.startswith("language."):
            issues.append(Issue("wrong_language", detail))
        elif head.startswith(("basis.", "families.", "ticked.")):
            issues.append(Issue("option_basis", detail))
        elif head.startswith("sets."):
            issues.append(Issue("option_sets", detail))
    return issues


def _check_wording_and_basis(card: Card, ctx: ValidationContext) -> list[Issue]:
    draft = _draft(card)
    wording_evidence = [*ctx.evidence, *sorted(ctx.terms)]
    errors = [
        *card_rules.check_wording(
            draft, ctx.user_text, _request(card, ctx, wording_evidence), ctx.language
        ),
        *card_rules.check_language(draft, ctx.language, ctx.user_text),
        *card_rules.check_grounding(draft, _request(card, ctx, ctx.evidence), ctx.user_text),
    ]
    request = _request(card, ctx, ctx.evidence)
    for option in card.options:
        errors.extend(card_rules.check_sets(option, request, ctx.catalog, needs_sets=False))
        if option.ticked and option.basis == "suggestion":
            errors.append(
                f"ticked.basis: {option.id} is pre-ticked on a suggestion, not on evidence"
            )
    issues = _from_card_errors(errors)
    norm = _scan_text(card)
    for phrase in phrase_hits(norm, [normalize(p) for p in _LEADING_EXTRA]):
        issues.append(Issue("leading_wording", f"'{phrase}' leads the user"))
    if card.type in QUESTION_MARK_TYPES and not any(m in card.question for m in _QUESTION_MARKS):
        issues.append(Issue("not_a_question", "a choice question must be phrased as a question"))
    return _dedupe(issues)


def _topic_phrases(ctx: ValidationContext) -> list[str]:
    phrases: list[str] = []
    for topic in ctx.catalog.no_ask:
        if topic in _TOPIC_PHRASES:
            phrases.extend(_TOPIC_PHRASES[topic])
        elif not topic.startswith("anything"):
            phrases.append(topic.replace("_", " "))
    return list(dict.fromkeys(phrases))


def _check_technology(card: Card, ctx: ValidationContext) -> list[Issue]:
    raw = " ".join([card.question, *(o.label for o in card.options)])
    norm = normalize(raw)
    user_words = set(ctx.terms) | set(tokenize(ctx.user_text))
    issues: list[Issue] = []
    for phrase in phrase_hits(norm, [normalize(p) for p in _topic_phrases(ctx)]):
        if all(word in user_words for word in phrase.split()):
            continue  # the user said it themselves
        issues.append(
            Issue("technology_question", f"'{phrase}' is a technology choice (the stack is fixed)")
        )
        break
    if "hex_colors" in ctx.catalog.no_ask and _HEX_COLOR.search(raw):
        issues.append(
            Issue("technology_question", "a hex colour code is a design detail, not a question")
        )
    return issues


def _check_declined(card: Card, ctx: ValidationContext) -> list[Issue]:
    for option in card.options:
        for text in (option.label, option.description, *_set_texts(option.sets)):
            match = declined_category(text, ctx.catalog, language=ctx.language)
            if match is not None:
                return [
                    Issue(
                        "declined_option",
                        f"option {option.id} offers {match.label}, which cannot be built",
                    )
                ]
    return []


def _set_texts(sets: Mapping[str, object]) -> list[str]:
    out: list[str] = []
    for value in sets.values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(str(v) for v in item.values() if isinstance(v, str))
    return out


def _dedupe(issues: Iterable[Issue]) -> list[Issue]:
    seen: set[tuple[str, str]] = set()
    out: list[Issue] = []
    for issue in issues:
        key = (issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def validate_question(card: Card, ctx: ValidationContext) -> list[Issue]:
    """Every issue found on one question (empty means it may be shown). Does not look at batch size."""
    issues = [
        *_check_basics(card, ctx),
        *_check_project_term(card, ctx),
        *_check_duplicates(card, ctx),
        *_check_lengths(card, ctx),
        *_check_options(card, ctx),
        *_check_recommended(card),
        *_check_wording_and_basis(card, ctx),
        *_check_technology(card, ctx),
        *_check_declined(card, ctx),
    ]
    return _dedupe(issues)


def validate_batch(cards: Sequence[Card], ctx: ValidationContext) -> BatchValidation:
    """Validate a planner batch in order. A question is accepted if it has no issues, does not repeat an earlier
    accepted question of the batch, and fits the batch size and its group's size (later ones are rejected: the
    engine sorts by priority first, and rejected extras go to the backlog)."""
    accepted: list[Card] = []
    rejected: list[RejectedQuestion] = []
    by_card: dict[str, list[Issue]] = {}
    planned: list[LedgerEntry] = []
    per_group: dict[str, int] = {}
    batch_limit = ctx.effective_batch_limit()
    group_limit = ctx.effective_group_limit()
    for card in cards:
        local = replace(ctx, planned=(*ctx.planned, *planned))
        issues = validate_question(card, local)
        group = card.group_id or ""
        if len(accepted) >= batch_limit:
            issues.append(
                Issue("over_batch_size", f"the batch already holds {batch_limit} questions")
            )
        elif per_group.get(group, 0) >= group_limit:
            issues.append(
                Issue("over_group_size", f"a group holds at most {group_limit} questions")
            )
        if issues:
            by_card.setdefault(card.id, []).extend(issues)
            rejected.append(
                RejectedQuestion(card_id=card.id, source="code", reasons=[str(i) for i in issues])
            )
            continue
        accepted.append(card)
        planned.append(LedgerEntry.from_card(card))
        per_group[group] = per_group.get(group, 0) + 1
    return BatchValidation(accepted=accepted, rejected=rejected, issues=by_card)


# --------------------------------------------------------------------------------------- batch measures
def project_term_share(cards: Iterable[Card], terms: Collection[str], catalog: Catalog) -> float:
    """Share of the non-universal questions that contain a project term (1.0 when there are none)."""
    scored = [c for c in cards if not is_universal(c, catalog)]
    if not scored:
        return 1.0
    return sum(1 for c in scored if contains_project_term(c.question, terms)) / len(scored)


_Signature = tuple[frozenset[str], frozenset[str]]


def _question_signatures(cards: Iterable[Card], catalog: Catalog | None) -> list[_Signature]:
    """(project-like terms, all words) of each question; universal ones are left out when a catalog is given."""
    return [
        (frozenset(extract_terms(c.question)), frozenset(tokenize(c.question)))
        for c in cards
        if catalog is None or not is_universal(c, catalog)
    ]


def _pair_similarity(x: _Signature, y: _Signature) -> float:
    """Jaccard of the content terms; two questions with no content terms at all (pure template wording) are
    compared on every word instead, so identical generic questions score 1.0 rather than 0.0."""
    if not x[0] and not y[0]:
        return jaccard(x[1], y[1])
    return jaccard(x[0], y[0])


def _one_way_overlap(a: Sequence[_Signature], b: Sequence[_Signature]) -> float:
    if not a or not b:
        return 0.0
    return sum(max(_pair_similarity(x, y) for y in b) for x in a) / len(a)


def cross_idea_overlap_pairs(
    batches_by_idea: Mapping[str, Sequence[Card]], catalog: Catalog | None = None
) -> dict[tuple[str, str], float]:
    """Pairwise overlap of the question terms of two ideas' first batches: for each question the best Jaccard of its
    terms against the other idea's questions, averaged both ways. 1.0 means every question has an identical twin."""
    sets = {k: _question_signatures(v, catalog) for k, v in batches_by_idea.items()}
    return {
        (a, b): (_one_way_overlap(sets[a], sets[b]) + _one_way_overlap(sets[b], sets[a])) / 2
        for a, b in combinations(sorted(sets), 2)
    }


def cross_idea_overlap(
    batches_by_idea: Mapping[str, Sequence[Card]], catalog: Catalog | None = None
) -> float:
    """The worst pairwise overlap across ideas (compare with ``InterviewPolicy.cross_idea_overlap_max``);
    0.0 for fewer than two ideas. Pass the catalog to leave out universal questions."""
    pairs = cross_idea_overlap_pairs(batches_by_idea, catalog)
    return max(pairs.values(), default=0.0)


def coverage_issues(
    statuses: Mapping[str, NeedStatus],
    covered_needs: Iterable[str],
    assumed_instead: Iterable[str | AssumedInstead],
    expert_gaps: Iterable[str],
    catalog: Catalog,
    config: QuestionPolicy | CompassConfig,
) -> list[str]:
    """Under-asking check: every must or high-impact need must be covered by a question, already satisfied (or
    visibly assumed), or listed in ``assumed_instead``. ``expert_gaps`` entries that name an open need count the same
    way. Returns human-readable issues (empty means complete)."""
    covered = set(covered_needs)
    assumed = {a.need_id if isinstance(a, AssumedInstead) else a for a in assumed_instead}
    issues: list[str] = []
    for need_id in sorted((*covered, *assumed)):
        if not _is_known_need(need_id, catalog):
            issues.append(f"unknown_need: {need_id} is not a catalog need or domain:<topic>")
    owed = list(expert_gap_obligations(statuses, catalog, config))
    for gap in expert_gaps:
        status = statuses.get(gap)
        if status is not None and status.state not in ("satisfied", "assumed") and gap not in owed:
            owed.append(gap)
    for need_id in owed:
        if need_id in covered or need_id in assumed:
            continue
        status = statuses[need_id]
        issues.append(
            f"uncovered_need: {need_id} ({status.level}, impact {status.impact}, {status.state}) "
            "is not covered by a question, satisfied or listed in assumed_instead"
        )
    return issues
