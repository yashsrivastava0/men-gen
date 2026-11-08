"""Gate 1: blockers, the typed yes, and "no approval without a matching hash"."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import AssumptionRecord, Brief, OpenQuestion
from mde_compass.engine.gate import classify_yes, evaluate, gate_blockers

CATALOG = load_catalog()
PASS = {"research": True, "blueprint": True, "design": True}


def kw(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "must_missing": [],
        "probes": PASS,
        "blocking_questions": 0,
        "mode": "guided",
        "approvals": None,
        "required_roles": ["approver"],
        "supplied_version": 3,
        "supplied_hash": "h3",
        "current_hash": "h3",
        "current_version": 3,
    }
    base.update(over)
    return base


def codes(brief: Brief, **over: object) -> list[str]:
    return [b.code for b in gate_blockers(brief, **kw(**over))]  # type: ignore[arg-type]


def asm(i: str, impact: str, decision: str = "proposed") -> AssumptionRecord:
    return AssumptionRecord(id=i, claim="c", impact=impact, decision=decision)  # type: ignore[arg-type]


def test_clean_brief_has_no_blockers() -> None:
    assert codes(Brief()) == []


def test_must_slots_empty() -> None:
    blockers = gate_blockers(Brief(), **kw(must_missing=["idea.summary", "context.geo"]))  # type: ignore[arg-type]
    assert [b.key for b in blockers] == ["blocker.must_slots_empty"]
    assert blockers[0].values == {"items": "idea.summary, context.geo"}


def test_unreviewed_high_impact_assumption_blocks_but_low_does_not() -> None:
    assert codes(Brief(assumptions=[asm("ASM-001", "low")])) == []
    brief = Brief(
        assumptions=[
            asm("ASM-001", "high"),
            asm("ASM-002", "high", "accepted"),
            asm("ASM-003", "high", "changed"),
        ]
    )
    blockers = gate_blockers(brief, **kw())  # type: ignore[arg-type]
    assert [b.code for b in blockers] == ["high_impact_assumption"]
    assert blockers[0].values == {"count": "1"}


def test_probes_must_all_pass_unless_degraded() -> None:
    assert codes(Brief(), probes={"research": True, "blueprint": False}) == ["probes_not_passed"]
    blockers = gate_blockers(Brief(), **kw(probes={"research": True}))  # type: ignore[arg-type]
    assert blockers[0].values == {"items": "blueprint, design"}
    degraded = Brief()
    degraded.meta.degraded = True
    assert codes(degraded, probes={}) == []


def test_blocking_open_questions() -> None:
    brief = Brief(
        open_questions=[
            OpenQuestion(id="Q-001", text="Which city first?", blocking=True),
            OpenQuestion(id="Q-002", text="later", blocking=False),
        ]
    )
    blockers = gate_blockers(brief, **kw())  # type: ignore[arg-type]
    assert [b.values for b in blockers] == [{"question": "Which city first?"}]
    assert codes(Brief(), blocking_questions=2) == ["blocking_question"]


def test_version_and_hash_must_match() -> None:
    assert codes(Brief(), supplied_hash="old") == ["hash_mismatch"]
    assert codes(Brief(), supplied_version=2) == ["hash_mismatch"]
    assert (
        codes(Brief(), supplied_version=None, supplied_hash=None) == []
    )  # listing blockers, not approving


def test_enterprise_needs_every_required_role_on_the_same_hash() -> None:
    assert codes(
        Brief(),
        mode="enterprise",
        required_roles=["approver", "owner"],
        approvals={"approver": "h3"},
    ) == ["approvers_missing"]
    blockers = gate_blockers(
        Brief(),
        **kw(
            mode="enterprise",
            required_roles=["approver", "owner"],
            approvals={"approver": "h3", "owner": "OLD"},
        ),
    )  # type: ignore[arg-type]
    assert blockers[0].values == {"roles": "owner"}
    assert (
        codes(
            Brief(),
            mode="enterprise",
            required_roles=["approver", "owner"],
            approvals={"approver": "h3", "owner": "h3"},
        )
        == []
    )
    assert codes(Brief(), mode="guided", approvals=None) == []


def test_blocker_keys_exist_in_the_catalog_strings() -> None:
    brief = Brief(
        assumptions=[asm("ASM-001", "high")],
        open_questions=[OpenQuestion(id="Q-001", text="t", blocking=True)],
    )
    blockers = gate_blockers(
        brief,
        **kw(must_missing=["a"], probes={}, mode="enterprise", approvals={}, supplied_hash="x"),
    )  # type: ignore[arg-type]
    assert {b.code for b in blockers} == {
        "must_slots_empty",
        "high_impact_assumption",
        "probes_not_passed",
        "blocking_question",
        "hash_mismatch",
        "approvers_missing",
    }
    for b in blockers:
        assert CATALOG.t(b.key, "en") != b.key
        text = CATALOG.t(b.key, "hi", **b.values)
        assert "{" not in text


def test_evaluate_accepts_only_a_clean_matching_approval() -> None:
    assert evaluate(Brief(), **kw()).accepted  # type: ignore[arg-type]
    assert not evaluate(Brief(), **kw(supplied_hash=None)).accepted  # type: ignore[arg-type]
    assert not evaluate(Brief(), **kw(supplied_version=None)).accepted  # type: ignore[arg-type]
    assert not evaluate(Brief(), **kw(supplied_hash="stale")).accepted  # type: ignore[arg-type]
    assert not evaluate(Brief(), **kw(must_missing=["idea.summary"])).accepted  # type: ignore[arg-type]


@given(
    version=st.one_of(st.none(), st.integers(0, 5)),
    supplied=st.one_of(st.none(), st.sampled_from(["h1", "h2", "h3"])),
    current=st.sampled_from(["h1", "h2", "h3"]),
    current_version=st.integers(0, 5),
    missing=st.lists(st.text(min_size=1, max_size=4), max_size=2),
)
def test_no_approval_without_a_matching_hash(
    version: int | None,
    supplied: str | None,
    current: str,
    current_version: int,
    missing: list[str],
) -> None:
    verdict = evaluate(
        Brief(),
        **kw(
            supplied_version=version,
            supplied_hash=supplied,
            current_hash=current,
            current_version=current_version,
            must_missing=missing,
        ),  # type: ignore[arg-type]
    )
    if verdict.accepted:
        assert supplied == current
        assert version == current_version
        assert not missing
    assert verdict.accepted == (not verdict.blockers)


@given(
    st.lists(st.sampled_from(["approver", "owner", "legal"]), max_size=3),
    st.dictionaries(st.sampled_from(["approver", "owner", "legal"]), st.sampled_from(["h3", "h2"])),
)
def test_enterprise_approval_needs_the_same_hash_from_every_role(
    required: list[str], approvals: dict[str, str]
) -> None:
    verdict = evaluate(
        Brief(), **kw(mode="enterprise", required_roles=required, approvals=approvals)
    )  # type: ignore[arg-type]
    if verdict.accepted:
        assert all(approvals.get(r) == "h3" for r in required)


# --- classify_yes ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "Yes!",
        "ok",
        "okay",
        "looks right",
        "Looks good",
        "yes, looks right",
        "haan theek hai",
        "हाँ",
        "ठीक है",
        "approve",
        "please approve",
        "sure",
    ],
)
def test_a_typed_yes_counts_when_the_gate_is_open(text: str) -> None:
    assert classify_yes(text, gate_open=True, language="en") == "yes"
    assert classify_yes(text, gate_open=False, language="en") == "unclear"  # the gate must be open


@pytest.mark.parametrize(
    "text",
    [
        "ok but change the color",
        "yes but add a chat feature",
        "looks good, remove the reviews",
        "haan lekin color badlo",
        "ok and also add payments",
    ],
)
def test_compound_messages_are_change_requests(text: str) -> None:
    assert classify_yes(text, gate_open=True, language="en") == "compound"


@pytest.mark.parametrize("text", ["no", "not yet", "nahi", "नहीं", "no thanks"])
def test_refusals(text: str) -> None:
    assert classify_yes(text, gate_open=True, language="en") == "no"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "maybe",
        "hmm ok i guess",
        "yes maybe",
        "what does assumed mean",
        "I am not sure",
        "tell me more about the features before I decide anything",
    ],
)
def test_unclear_and_silence_never_approve(text: str) -> None:
    assert classify_yes(text, gate_open=True, language="en") == "unclear"


def test_low_confidence_is_not_a_yes() -> None:
    assert classify_yes("yes i guess", gate_open=True, language="en") == "unclear"
    assert classify_yes("yes", gate_open=True, language="en", confidence_min=0.99) == "unclear"


@given(st.text(max_size=100))
def test_only_exact_approvals_ever_classify_as_yes_and_never_when_closed(text: str) -> None:
    assert classify_yes(text, gate_open=False, language="en") != "yes"
