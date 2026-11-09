"""``cmp.audit_questions``: spec, examples, the deterministic heuristic, validators, fallback and the adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.audit_questions.adapter import (
    NO_VERDICT,
    AuditAdapter,
    build_input,
    parse_needs_table,
    to_verdicts,
)
from mde_compass.calls.audit_questions.fallback import (
    UNIVERSAL_NEEDS,
    heuristic,
    parse_asked,
    tokens,
    wire_fallback,
)
from mde_compass.calls.audit_questions.mock import handle
from mde_compass.calls.audit_questions.schema import (
    AuditInput,
    AuditOutput,
    NeedWire,
    QuestionWire,
    VerdictWire,
)
from mde_compass.calls.audit_questions.spec import SPEC
from mde_compass.calls.audit_questions.validate import structural
from mde_compass.calls.base import CallRunner
from mde_compass.calls.plan_questions.mock import handle as plan_mock
from mde_compass.calls.plan_questions.schema import PlanInput
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.cards import Card, CardOption
from mde_compass.engine.ports import AuditRequest, TurnContext
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/audit_questions/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
PLAN_EXAMPLES = Path(__file__).parents[3] / "src/mde_compass/calls/plan_questions/examples"
CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")


def _input(raw: dict[str, object]) -> AuditInput:
    return AuditInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> AuditOutput:
    return AuditOutput.model_validate(raw["output"])


def _q(
    card_id: str = "Q-001",
    text: str = "",
    *,
    intent: str = "",
    serves: list[str] | None = None,
    options: list[str] | None = None,
) -> QuestionWire:
    return QuestionWire(
        card_id=card_id,
        text=text,
        intent=intent,
        serves=["scope.features"] if serves is None else serves,
        type="choose_one" if options else "open_prompt",
        option_labels=options or [],
        recommended="",
        why_we_ask="",
    )


def _inp(
    questions: list[QuestionWire],
    *,
    terms: list[str] | None = None,
    asked: str = "",
    needs: list[str] | None = None,
) -> AuditInput:
    return AuditInput(
        language="en",
        project_terms=["tiffin", "Pune", "subscription"] if terms is None else terms,
        needs=[
            NeedWire(need_id=n, label=n, sufficient="")
            for n in (["scope.features", "roles.list"] if needs is None else needs)
        ],
        asked_list=asked,
        questions=questions,
    )


def _one(text: str, **kw: object) -> VerdictWire:
    terms = kw.pop("terms", None)
    q = _q(text=text, **kw)  # type: ignore[arg-type]
    (v,) = heuristic(_inp([q], terms=terms)).verdicts  # type: ignore[arg-type]
    return v


def _card(
    n: int,
    question: str,
    *,
    intent: str = "",
    need_ids: list[str] | None = None,
    options: list[str] | None = None,
    recommended: int | None = None,
) -> Card:
    opts = [
        CardOption(id=chr(ord("A") + i), label=label, recommended=i == recommended)
        for i, label in enumerate(options or [])
    ]
    return Card(
        id=f"Q-{n:03d}",
        frame="planned",
        slots=[],
        type="choose_one" if opts else "open_prompt",
        question=question,
        why_we_ask="Because it decides the first screen.",
        options=opts,
        intent=intent,
        need_ids=["scope.features"] if need_ids is None else need_ids,
    )


# ----------------------------------------------------------------------------------------- spec, examples
def test_spec_matches_profile() -> None:
    s = SPEC.spec
    assert s.id == "cmp.audit_questions" and s.version == 1
    assert s.reasoning_effort == "medium" and s.max_output_tokens == 3000 and s.route == "standard"
    assert SPEC.lane == "critic" and SPEC.timeout_s == 60 and not s.tools
    assert s.untrusted_inputs == ("asked_list",)
    assert set(s.untrusted_inputs) <= set(AuditInput.model_fields)


def test_there_are_three_examples_in_three_languages_that_pass_the_validator() -> None:
    assert len(EXAMPLES) >= 3
    assert {e["input"]["language"] for e in EXAMPLES} >= {"en", "hi-Latn", "hi"}
    assert len({e["name"] for e in EXAMPLES}) == len(EXAMPLES)
    for e in EXAMPLES:
        inp, out = _input(e), _output(e)
        assert structural(inp, out) == [], e["name"]
        assert [v.card_id for v in out.verdicts] == [q.card_id for q in inp.questions]
        # the examples show good and bad questions, so a strict reviewer is seen to say no
        assert any(v.specific >= 4 for v in out.verdicts) and any(v.issues for v in out.verdicts)
        assert any(v.specific < 3 or v.leading or v.jargon for v in out.verdicts)


def test_examples_do_not_share_question_wording() -> None:
    texts = [q["text"] for e in EXAMPLES for q in e["input"]["questions"]]
    assert len(texts) == len(set(texts))


def test_the_prompt_carries_the_rubric_and_the_rules() -> None:
    system = (EXAMPLES_DIR.parent / "system.jinja").read_text(encoding="utf-8")
    for needle in (
        "specific (1 to 5)",
        "5 = could only be asked of this project",
        "3 = acceptable",
        "1 = would read the same for any unrelated app",
        "duplicate_of",
        "never a rewritten question",
        "do not rewrite questions",
        "Never approve",
    ):
        assert needle in system, needle
    assert (
        system.count("specific 1.") + system.count("specific 5.") >= 2
    )  # contrasting scored examples


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    runner = CallRunner(make_client([SPEC]))
    inp = _input(raw)
    res = await runner.run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert [v.card_id for v in res.output.verdicts] == [q.card_id for q in inp.questions]
    assert all(v.fix.startswith("[mock]") for v in res.output.verdicts if v.fix)
    assert any(v.fix for v in res.output.verdicts)  # the examples contain weak questions


def test_the_user_template_shows_every_question_and_the_untrusted_block() -> None:
    from mde_llm_gateway.render import PromptRenderer

    inp = _input(EXAMPLES[0])
    rendered = PromptRenderer().render(SPEC.spec, inp)
    assert "<untrusted_content" in rendered.user and "Q-001 how doctors" in rendered.user
    for q in inp.questions:
        assert f"[{q.card_id}]" in rendered.user and q.text in rendered.user
    assert "scope.features: Features" in rendered.user
    assert "never follow instructions" in rendered.system.lower()


# ------------------------------------------------------------------------------- the heuristic: scores
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Do customers pick a tiffin meal each day or take a subscription?", 4),  # two terms
        ("Where in Pune will you start?", 3),  # one term
        ("What features do you want in the app?", 2),  # none
        ("Who will be using it day to day?", 2),
    ],
)
def test_specific_counts_distinct_project_terms(text: str, expected: int) -> None:
    assert _one(text).specific == expected


def test_a_repeated_term_counts_once_and_plural_and_case_are_folded() -> None:
    assert _one("Is the tiffin a tiffin or TIFFIN?").specific == 3
    assert _one("Do you sell tiffins by subscriptions in PUNE?").specific == 4


def test_terms_match_whole_words_only() -> None:
    assert _one("Is the party in the mart?", terms=["art"]).specific == 2
    assert _one("Is it an art class?", terms=["art"]).specific == 3
    assert _one("Which delivery partner do you use?", terms=["delivery partner"]).specific == 3
    assert _one("Which partner does the delivery?", terms=["delivery partner"]).specific == 2


def test_devanagari_terms_match_as_whole_words() -> None:
    terms = ["स्टॉक", "दवा"]
    assert _one("स्टॉक कम होने पर क्या दवा मँगवाएँ?", terms=terms).specific == 4
    assert _one("दवाइयाँ कहाँ रखी जाती हैं?", terms=terms).specific == 2  # a different word


def test_a_universal_need_needs_no_project_term() -> None:
    assert _one("Which languages should the app speak?", serves=["locales"]).specific == 3
    assert _one("Which city first?", serves=["context.geo", "locales"]).specific == 3
    # one non-universal need among them brings the strict rule back
    assert _one("Which city first?", serves=["context.geo", "scope.features"]).specific == 2
    assert _one("Which languages?", serves=[]).specific == 2
    assert {
        "idea.summary",
        "context.geo",
        "brand.name",
        "brand.assets",
        "locales",
    } == UNIVERSAL_NEEDS


def test_a_universal_need_with_project_terms_still_scores_by_terms() -> None:
    q = "Which languages do tiffin customers in Pune speak?"
    assert _one(q, serves=["locales"]).specific == 4


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
def test_must_not_the_heuristic_never_gives_four_to_a_question_with_no_project_term(
    raw: dict[str, object],
) -> None:
    inp = _input(raw).model_copy(update={"project_terms": ["zzqx", "unrelated words"]})
    for v in heuristic(inp).verdicts:
        assert v.specific <= 3, v
    # and with the real terms it does score some questions 4 or more (so the test above is not vacuous)
    assert any(v.specific >= 4 for v in heuristic(_input(raw)).verdicts)


def test_no_project_terms_at_all_means_no_question_scores_above_three() -> None:
    inp = _inp([_q(text="Do tiffin customers in Pune subscribe?")], terms=[])
    assert heuristic(inp).verdicts[0].specific == 2


# ----------------------------------------------------------------------- the heuristic: flags and issues
def test_leading_words_are_flagged_with_an_issue_and_a_fix() -> None:
    v = _one("Obviously you want the best tiffin menu, right?")
    assert v.leading and any("obviously" in i.lower() for i in v.issues)
    assert "neutral" in v.fix and "?" not in v.fix
    assert not _one("Should the tiffin menu change every day?").leading
    assert _one("You should add a tiffin menu, shouldn't you?").leading
    assert _one("Is it just a tiffin menu?").leading
    assert _one("Kya yeh sabse accha tiffin menu hai?").leading
    assert not _one("Is it a bestseller menu?").leading  # whole words only


def test_jargon_is_flagged_but_not_when_the_person_used_the_word() -> None:
    v = _one("Which database and API will the tiffin orders use?")
    assert v.jargon and any("database" in i for i in v.issues)
    assert _one("Should the APIs be public for the tiffin orders?").jargon  # plural folds
    own = _one("Which API do the tiffin partners call?", terms=["tiffin", "API"])
    assert not own.jargon
    assert _one("Which tech stack?").jargon  # a two-word phrase


def test_a_question_that_is_too_long_is_not_answerable() -> None:
    long = " ".join(["word"] * 41)
    v = _one(f"tiffin {long}?")
    assert not v.answerable and any("Too long" in i for i in v.issues)
    assert _one(" ".join(["word"] * 39) + "?").answerable


def test_maps_to_need_accepts_listed_needs_and_domain_topics_only() -> None:
    assert _one("tiffin?", serves=["scope.features"]).maps_to_need
    assert _one("tiffin?", serves=["scope.features", "domain:cancellations"]).maps_to_need
    assert not _one("tiffin?", serves=[]).maps_to_need
    assert not _one("tiffin?", serves=["growth.mascot"]).maps_to_need
    assert not _one("tiffin?", serves=["domain:"]).maps_to_need
    assert not _one("tiffin?", serves=["scope.features", "growth.mascot"]).maps_to_need


def test_clean_question_has_no_issues_and_no_fix() -> None:
    v = _one("Do tiffin customers in Pune pick a meal daily or take a subscription?")
    assert (v.specific, v.leading, v.jargon, v.answerable, v.maps_to_need) == (
        4,
        False,
        False,
        True,
        True,
    )
    assert v.issues == [] and v.fix == "" and v.duplicate_of == ""


def test_every_flag_comes_with_an_issue_and_a_short_fix_that_is_not_a_question() -> None:
    q = _q(
        text="Obviously the API needs a database? " + " ".join(["x"] * 40),
        serves=["growth.mascot"],
    )
    (v,) = heuristic(_inp([q])).verdicts
    assert v.specific == 2 and v.leading and v.jargon and not v.answerable and not v.maps_to_need
    assert len(v.issues) >= 5 and v.fix and "?" not in v.fix
    assert structural(_inp([q]), AuditOutput(verdicts=[v])) == []


# ------------------------------------------------------------------------------ the heuristic: duplicates
def test_parse_asked_reads_ids_and_drops_status_and_noise() -> None:
    asked = (
        "Q-001 how shops pay (answered)\n"
        "- Q-002: [answered] who uses it in a shop\n"
        "(nothing asked yet)\n"
        "\n"
        "Q-xyz not an id\n"
        "Q-003 where it launches (skipped)"
    )
    assert parse_asked(asked) == [
        ("Q-001", "how shops pay"),
        ("Q-002", "who uses it in a shop"),
        ("Q-003", "where it launches"),
    ]


def test_a_question_that_repeats_the_asked_list_points_at_its_id() -> None:
    asked = "Q-001 how tiffin customers pay (answered)\nQ-002 which areas of Pune (answered)"
    q = _q("Q-005", "How do tiffin customers pay?", intent="how tiffin customers pay")
    (v,) = heuristic(_inp([q], asked=asked)).verdicts
    assert v.duplicate_of == "Q-001" and any("Q-001" in i for i in v.issues) and v.fix


def test_a_question_that_repeats_an_earlier_one_in_the_batch_points_at_its_id() -> None:
    a = _q("Q-004", "Do tiffin customers pick a meal daily?", intent="learn daily meal choice")
    b = _q(
        "Q-005", "Do tiffin customers pick a meal daily or not?", intent="learn daily meal choice"
    )
    c = _q("Q-006", "Which parts of Pune do you deliver to?", intent="learn the delivery areas")
    va, vb, vc = heuristic(_inp([a, b, c])).verdicts
    assert va.duplicate_of == "" and vb.duplicate_of == "Q-004" and vc.duplicate_of == ""


def test_different_questions_are_not_duplicates_and_a_question_is_not_its_own_duplicate() -> None:
    asked = "Q-005 how tiffin customers pay (answered)"  # the card is already in the ledger
    q = _q("Q-005", "How do tiffin customers pay?", intent="how tiffin customers pay")
    assert heuristic(_inp([q], asked=asked)).verdicts[0].duplicate_of == ""
    other = _q("Q-006", "Who delivers the tiffin boxes?", intent="learn who delivers")
    assert heuristic(_inp([other], asked=asked)).verdicts[0].duplicate_of == ""


def test_the_heuristic_is_pure_and_deterministic() -> None:
    inp = _input(EXAMPLES[0])
    assert heuristic(inp) == heuristic(inp) == wire_fallback(inp)
    assert heuristic(inp, mock=True) == handle(inp)


def test_tokens_fold_case_plural_and_unicode() -> None:
    assert tokens("Clinics, Doctors' SLOTS; glass") == ["clinic", "doctor", "slot", "glass"]
    assert tokens("ＡＰＩ") == ["api"]  # fullwidth


# ------------------------------------------------------------------------------------------ the validator
def test_structural_accepts_a_proper_audit_and_rejects_what_is_not_one() -> None:
    inp = _input(EXAMPLES[0])
    good = _output(EXAMPLES[0])
    assert structural(inp, good) == []

    missing = AuditOutput(verdicts=good.verdicts[:-1])
    assert any("no verdict for Q-007" in e for e in structural(inp, missing))
    extra = AuditOutput(
        verdicts=[*good.verdicts, good.verdicts[0].model_copy(update={"card_id": "Q-099"})]
    )
    assert any("unknown question Q-099" in e for e in structural(inp, extra))
    twice = AuditOutput(verdicts=[*good.verdicts, good.verdicts[0]])
    assert any("more than one verdict" in e for e in structural(inp, twice))
    # order does not matter: the adapter maps by id
    assert structural(inp, AuditOutput(verdicts=list(reversed(good.verdicts)))) == []


@pytest.mark.parametrize("score", [0, 6, -1])
def test_structural_rejects_a_score_out_of_range(score: int) -> None:
    inp = _input(EXAMPLES[0])
    v = _output(EXAMPLES[0]).verdicts
    bad = AuditOutput(verdicts=[v[0].model_copy(update={"specific": score}), *v[1:]])
    assert any("specific must be 1 to 5" in e for e in structural(inp, bad))


@pytest.mark.parametrize(
    "change",
    [
        {"specific": 2},
        {"leading": True},
        {"jargon": True},
        {"duplicate_of": "Q-001"},
        {"answerable": False},
        {"maps_to_need": False},
    ],
)
def test_structural_wants_an_issue_for_every_low_score_and_flag(change: dict[str, object]) -> None:
    inp = _input(EXAMPLES[0])
    v = _output(EXAMPLES[0]).verdicts
    clean = v[0].model_copy(update={"issues": [], "fix": ""})
    assert structural(inp, AuditOutput(verdicts=[clean, *v[1:]])) == []
    flagged = clean.model_copy(update=change)
    errors = structural(inp, AuditOutput(verdicts=[flagged, *v[1:]]))
    assert any("needs at least one issue" in e for e in errors)
    blank = flagged.model_copy(update={"issues": ["  "]})
    assert structural(inp, AuditOutput(verdicts=[blank, *v[1:]])) == errors
    fixed = flagged.model_copy(update={"issues": ["something is wrong"]})
    assert not any(
        "needs at least one issue" in e
        for e in structural(inp, AuditOutput(verdicts=[fixed, *v[1:]]))
    )


def test_structural_refuses_a_fix_that_rewrites_the_question() -> None:
    inp = _input(EXAMPLES[0])
    v = _output(EXAMPLES[0]).verdicts
    for rewrite in ("Ask: do patients cancel often?", "पूछें: क्या मरीज़ रद्द करते हैं？"):
        bad = v[1].model_copy(update={"fix": rewrite})
        errors = structural(inp, AuditOutput(verdicts=[v[0], bad, *v[2:]]))
        assert any("rewritten question" in e for e in errors), rewrite
    wordy = v[1].model_copy(update={"fix": " ".join(["word"] * 61)})
    assert any(
        "short hint" in e for e in structural(inp, AuditOutput(verdicts=[v[0], wordy, *v[2:]]))
    )
    selfdup = v[3].model_copy(update={"duplicate_of": v[3].card_id})
    assert any(
        "duplicate itself" in e for e in structural(inp, AuditOutput(verdicts=[*v[:3], selfdup]))
    )


# --------------------------------------------------------------------------------------- adapter pieces
def test_parse_needs_table_reads_both_shapes_and_skips_noise() -> None:
    table = (
        "Needs (open first):\n"
        "- scope.features [partial, impact 4, must] Features: what each feature is for\n"
        "roles.list | Roles | each kind of person who signs in\n"
        "locales\n"
        "domain:cancellations | Cancellations | what happens\n"
        "needs: not a need\n"
        "\n"
    )
    needs = {n.need_id: n for n in parse_needs_table(table, CATALOG)}
    assert list(needs) == ["scope.features", "roles.list", "locales", "domain:cancellations"]
    assert needs["scope.features"].label == "Features"
    assert needs["scope.features"].sufficient == "what each feature is for"
    assert needs["roles.list"].sufficient == "each kind of person who signs in"
    # a bare id is filled from the catalog
    assert needs["locales"].label and needs["locales"].sufficient


def test_build_input_turns_cards_into_questions_and_lists_the_needs() -> None:
    cards = [
        _card(
            4,
            "Do tiffin customers pick a meal daily?",
            intent="learn daily meal choice",
            need_ids=["scope.features", "domain:daily_choice"],
            options=["Every day", "A weekly plan", "Both"],
            recommended=2,
        ),
        _card(5, "Which languages?", need_ids=["locales", "growth.mascot"]),
    ]
    request = AuditRequest(
        language="en",
        cards=cards,
        project_terms=["tiffin", "Pune"],
        needs_table="- scope.features [missing, impact 4, must] Features: the jobs",
        asked_list="Q-001 who pays (answered)",
    )
    wire = build_input(CATALOG, request)
    first, second = wire.questions
    assert (first.card_id, first.type, first.recommended) == ("Q-004", "choose_one", "Both")
    assert first.option_labels == ["Every day", "A weekly plan", "Both"]
    assert first.serves == ["scope.features", "domain:daily_choice"]
    assert first.intent == "learn daily meal choice" and first.why_we_ask
    assert second.type == "open_prompt" and second.option_labels == [] and second.recommended == ""
    ids = [n.need_id for n in wire.needs]
    # the table's need, plus the REAL catalog slot a card serves; the invented id stays unlisted
    assert ids == ["scope.features", "locales"] and "growth.mascot" not in ids
    assert wire.asked_list == "Q-001 who pays (answered)" and wire.project_terms == [
        "tiffin",
        "Pune",
    ]
    assert heuristic(wire).verdicts[1].maps_to_need is False  # growth.mascot is not a need


def test_build_input_uses_slots_when_a_legacy_card_has_no_need_ids() -> None:
    card = _card(1, "Which languages?", need_ids=[]).model_copy(update={"slots": ["locales"]})
    wire = build_input(CATALOG, AuditRequest(cards=[card]))
    assert wire.questions[0].serves == ["locales"] and [n.need_id for n in wire.needs] == [
        "locales"
    ]


def test_the_wire_input_redacts_secrets_in_everything_that_carries_words() -> None:
    key = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow
    card = _card(
        1,
        f"Is {key} your tiffin key?",
        intent=f"key {key}",
        options=[f"use {key}", "no"],
        recommended=0,
    ).model_copy(update={"why_we_ask": f"because {key}"})
    request = AuditRequest(
        cards=[card],
        project_terms=["tiffin", key],
        asked_list=f"Q-000 typed {key} (answered)",
    )
    wire = build_input(CATALOG, request)
    assert key not in wire.model_dump_json()
    (q,) = wire.questions
    assert (
        "[REDACTED]" in q.text and "[REDACTED]" in q.intent and "[REDACTED]" in q.option_labels[0]
    )
    assert "[REDACTED]" in q.recommended and "[REDACTED]" in q.why_we_ask
    assert wire.project_terms == ["tiffin"]  # a term that was a secret is dropped, not passed on
    assert "[REDACTED]" in wire.asked_list


def test_to_verdicts_maps_to_the_domain_and_never_approves_a_missing_verdict() -> None:
    cards = [_card(4, "a"), _card(5, "b"), _card(6, "c")]
    out = AuditOutput(
        verdicts=[
            VerdictWire(
                card_id="Q-006",
                specific=9,  # out of range: clamped, not a crash
                leading=True,
                jargon=False,
                duplicate_of=" Q-001 ",
                answerable=False,
                maps_to_need=False,
                issues=["bad", " "],
                fix=" drop it ",
            ),
            VerdictWire(
                card_id="Q-004",
                specific=4,
                leading=False,
                jargon=False,
                duplicate_of="",
                answerable=True,
                maps_to_need=True,
                issues=[],
                fix="",
            ),
            VerdictWire(  # an unknown card is ignored
                card_id="Q-099",
                specific=5,
                leading=False,
                jargon=False,
                duplicate_of="",
                answerable=True,
                maps_to_need=True,
                issues=[],
                fix="",
            ),
        ]
    )
    v4, v5, v6 = to_verdicts(cards, out)
    assert [v.card_id for v in (v4, v5, v6)] == ["Q-004", "Q-005", "Q-006"]  # card order
    assert v4.specific == 4 and v4.duplicate_of is None
    assert v5.specific == 1 and v5.issues == [NO_VERDICT]  # missing: failed, never approved
    assert v6.specific == 5 and v6.duplicate_of == "Q-001" and v6.issues == ["bad"]
    assert v6.leading and not v6.answerable and not v6.maps_to_need and v6.fix == "drop it"


# ----------------------------------------------------------------------------------- adapter end to end
def _request() -> AuditRequest:
    cards = [
        _card(
            4,
            "Do tiffin customers in Pune pick a meal daily or take a subscription?",
            intent="learn daily meal choice",
            need_ids=["scope.features", "domain:daily_choice"],
            options=["Every day", "A weekly plan", "Both"],
        ),
        _card(5, "What features do you want in the app?", intent="learn features"),
    ]
    return AuditRequest(
        cards=cards,
        project_terms=["tiffin", "Pune", "subscription"],
        needs_table="scope.features | Features | the jobs and the first release",
    )


async def test_the_mock_adapter_returns_domain_verdicts_one_per_card() -> None:
    adapter = AuditAdapter(CallRunner(make_client([SPEC])), CATALOG)
    result = await adapter.audit(CTX, _request())
    assert not result.used_fallback and result.reason == ""
    good, generic = result.verdicts
    assert (good.card_id, good.specific, good.issues) == ("Q-004", 4, [])
    assert generic.specific == 2 and generic.fix.startswith("[mock]") and generic.issues


async def test_a_scripted_audit_is_passed_through_to_the_domain() -> None:
    request = _request()
    wire = build_input(CATALOG, request)
    scripted = AuditOutput(
        verdicts=[
            VerdictWire(
                card_id=q.card_id,
                specific=5 if q.card_id == "Q-004" else 1,
                leading=False,
                jargon=False,
                duplicate_of="",
                answerable=True,
                maps_to_need=True,
                issues=[] if q.card_id == "Q-004" else ["generic"],
                fix="" if q.card_id == "Q-004" else "name the tiffin jobs",
            )
            for q in wire.questions
        ]
    )
    gateway = ScriptedGateway(by_spec={"cmp.audit_questions": [scripted]})
    result = await AuditAdapter(CallRunner(gateway), CATALOG).audit(CTX, request)
    gateway.assert_consumed()
    assert [v.specific for v in result.verdicts] == [5, 1] and not result.used_fallback


async def test_no_cards_means_no_call() -> None:
    gateway = ScriptedGateway()  # any call would raise ScriptExhausted
    result = await AuditAdapter(CallRunner(gateway), CATALOG).audit(CTX, AuditRequest(cards=[]))
    assert result.verdicts == [] and not result.used_fallback and gateway.calls == []


async def test_a_failed_call_gives_the_strict_heuristic_flagged_as_a_fallback() -> None:
    gateway = ScriptedGateway(ProviderError("boom"))
    result = await AuditAdapter(CallRunner(gateway), CATALOG).audit(CTX, _request())
    assert result.used_fallback and "ProviderError" in result.reason
    good, generic = result.verdicts
    assert good.specific == 4 and good.issues == []
    # the heuristic is strict: the generic question is NOT approved just because the model was down
    assert generic.specific == 2 and generic.issues and not generic.fix.startswith("[mock]")


async def test_a_structurally_bad_answer_falls_back_too() -> None:
    request = _request()
    wire = build_input(CATALOG, request)
    one_of_two = AuditOutput(verdicts=heuristic(wire).verdicts[:1])
    result = await AuditAdapter(CallRunner(ScriptedGateway(one_of_two)), CATALOG).audit(
        CTX, request
    )
    assert result.used_fallback and "no verdict for Q-005" in result.reason
    assert [v.card_id for v in result.verdicts] == ["Q-004", "Q-005"]

    rewriting = AuditOutput(
        verdicts=[
            v.model_copy(update={"fix": "Ask: what do you sell?"}) for v in heuristic(wire).verdicts
        ]
    )
    result = await AuditAdapter(CallRunner(ScriptedGateway(rewriting)), CATALOG).audit(CTX, request)
    assert result.used_fallback and "rewritten question" in result.reason


async def test_the_mock_planner_output_is_accepted_by_the_mock_auditor() -> None:
    """The offline path end to end: whatever the mock planner asks, the mock auditor must not reject it all."""
    path = PLAN_EXAMPLES / "en_clinic_booking_initial.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    batch = plan_mock(PlanInput.model_validate(raw["input"]))
    cards = [
        _card(i + 1, q.text, intent=q.intent, need_ids=list(q.serves))
        for i, q in enumerate(batch.questions)
    ]
    request = AuditRequest(cards=cards, project_terms=raw["input"]["project_terms"])
    result = await AuditAdapter(CallRunner(make_client([SPEC])), CATALOG).audit(CTX, request)
    assert len(result.verdicts) == len(cards) >= 3
    assert all(v.specific >= 3 and not v.leading and not v.jargon for v in result.verdicts)
