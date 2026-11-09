"""Properties of whole interviews: random sequences of user actions (answers, taps, skips, "you decide", edits of
earlier answers, pivots, private remarks) played against the real planner engine (mock providers, in-memory store),
then finished until the Brief is in review. Each property checks an invariant that must hold whatever the person did.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.engine import facts as facts_mod
from mde_compass.scenarios import ledger
from mde_compass.scenarios.runner import locked_without_evidence
from mde_compass.scenarios.sessions import (
    CANARY_PREFIX,
    IDEAS,
    SESSION_EXAMPLES,
    Act,
    Played,
    play,
    remark,
)
from mde_compass.views.consumers import build_handoff

WORDS = [
    "Pune", "Mumbai", "Hindi", "English", "riders", "customers", "monthly", "commission", "admin",
    "payments", "ads", "students", "offices", "app", "and", "free", "weekly", "kitchen", "doctor",
]  # fmt: skip
texts = st.lists(st.sampled_from(WORDS), min_size=1, max_size=14).map(" ".join)
tokens = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=6, max_size=12)
acts = st.one_of(
    st.builds(Act, kind=st.just("answer"), text=texts, pick=st.integers(0, 5)),
    st.builds(Act, kind=st.just("tap")),
    st.builds(Act, kind=st.just("skip")),
    st.builds(Act, kind=st.just("you_decide")),
    st.builds(Act, kind=st.just("edit"), text=texts, pick=st.integers(0, 9)),
    st.builds(Act, kind=st.just("pivot")),
    st.builds(Act, kind=st.just("remark"), text=tokens.map(remark)),
)
scripts = st.lists(acts, min_size=0, max_size=9)
ideas = st.sampled_from(IDEAS)
EXAMPLES = settings(max_examples=SESSION_EXAMPLES)


def run(acts_: list[Act], idea: str) -> tuple[Played, ledger.SessionData]:
    async def go() -> tuple[Played, ledger.SessionData]:
        played = await play(acts_, idea=idea)
        return played, await played.data()

    return asyncio.run(go())


@given(scripts, ideas)
@EXAMPLES
def test_no_action_sequence_breaks_a_turn(script: list[Act], idea: str) -> None:
    played, _ = run(script, idea)
    assert played.errors == []


@given(scripts, ideas)
@EXAMPLES
def test_no_question_is_asked_twice_in_a_session(script: list[Act], idea: str) -> None:
    _, data = run(script, idea)
    # a flagged fallback is the one place canned cards may come back (BUG-24 pins its repeats); every question a
    # planner wrote must be new
    planned = [c for c in data.planned_cards() if c.origin != "fallback"]
    assert ledger.repeated_questions(planned) == []
    ids = [q.card_id for q in data.qa]
    assert len(ids) == len(set(ids))  # one current row per question


@given(scripts, ideas)
@EXAMPLES
def test_nothing_is_locked_without_a_verified_quote(script: list[Act], idea: str) -> None:
    _, data = run(script, idea)
    assert ledger.quote_violations(data) == []
    assert locked_without_evidence(ledger.brief_json(data.brief)) == []
    for row in data.brief.domain_facts:  # the same rule for the facts the Brief carries
        fact = next(f for f in data.facts if f.id == row.id)
        assert (row.tier == "Locked") == fact.supports_locked()


@given(scripts, ideas)
@EXAMPLES
def test_every_fact_a_brief_item_cites_exists_and_is_still_in_force(
    script: list[Act], idea: str
) -> None:
    _, data = run(script, idea)
    assert ledger.dangling_refs(data) == []
    cited = ledger.fact_refs(json.loads(data.brief.model_dump_json(exclude={"meta"})))
    by_id = {f.id: f for f in data.facts}
    assert all(by_id[ref].status in ("active", "not_used") for ref in cited), {
        ref: by_id[ref].status for ref in cited if by_id[ref].status not in ("active", "not_used")
    }
    report = facts_mod.fact_coverage(data.facts, cited)
    assert report.stale == () and report.unknown == ()


@given(scripts, ideas)
@EXAMPLES
def test_every_active_fact_is_used_by_the_brief_or_marked_not_used(
    script: list[Act], idea: str
) -> None:
    _, data = run(script, idea)
    cov = ledger.coverage(data)
    assert cov.unreferenced == () and cov.ratio == 1.0


@given(scripts, ideas)
@EXAMPLES
def test_consumer_views_never_carry_the_quote_of_a_private_remark(
    script: list[Act], idea: str
) -> None:
    remarked = [a for a in script if a.kind == "remark"]
    _, data = run([*script, Act("remark", remark("planted"))], idea)
    handoff = json.dumps(build_handoff(data.brief), ensure_ascii=False)
    canaries = {
        m.group(0)
        for a in [*remarked, Act("remark", remark("planted"))]
        for m in re.finditer(CANARY_PREFIX + r"[a-z]+", a.text)
    }
    assert canaries and not any(c in handoff for c in canaries), [
        c for c in canaries if c in handoff
    ]
    assert '"quote"' not in handoff and "evt_" not in handoff
    stored = [f for f in data.facts if f.quote.startswith(CANARY_PREFIX)]
    assert stored and all(
        f.status != "deleted" for f in stored
    )  # the remark was kept as a fact, just not handed on


@given(scripts, ideas)
@EXAMPLES
def test_supersession_keeps_every_fact_and_never_changes_its_words(
    script: list[Act], idea: str
) -> None:
    played, data = run(script, idea)
    first: dict[str, Any] = {}
    revived: list[str] = []
    for snap in played.snapshots:
        for fid, fact in snap.items():
            seen = first.setdefault(fid, fact)
            assert (fact.statement, fact.quote) == (seen.statement, seen.quote)
            if seen.status == "superseded" and fact.status != "superseded":
                revived.append(fid)
    assert revived == []
    # nothing that ever existed is gone from the final ledger
    assert set(first) <= {f.id for f in data.facts}
    sizes = [len(s) for s in played.snapshots]
    assert sizes == sorted(sizes)  # the ledger never shrinks, step after step


@given(scripts, ideas)
@EXAMPLES
def test_fact_ids_run_without_gaps_or_reuse_across_edits_and_pivots(
    script: list[Act], idea: str
) -> None:
    _, data = run(script, idea)
    ids = [f.id for f in data.facts]
    assert ids == [f"FCT-{n:03d}" for n in range(1, len(ids) + 1)]
    versions = [(q.card_id, q.version) for q in data.qa_versions]
    assert len(versions) == len(set(versions))  # an answer version is written once


def test_the_random_sessions_really_edit_pivot_and_remark() -> None:
    """A witness for the strategies above: this fixed script does what the random ones may do, and the engine
    ends up with superseded facts, a second answer version, a stored private remark and a Brief in review."""
    script = [
        Act("answer", "Office workers in Baner who order lunch every weekday"),
        Act("remark", remark("witness")),
        Act("edit", "Students and hostel residents near the college", pick=0),
        Act("pivot"),
        Act("answer", "Patients and a receptionist"),
    ]
    played, data = run(script, IDEAS[0])
    assert played.errors == []
    assert any(f.status == "superseded" for f in data.facts)
    assert any(q.version == 2 for q in data.qa_versions)
    assert any(f.quote.startswith(CANARY_PREFIX) for f in data.facts)
    assert asyncio.run(played.conv.state()) in ("BRIEF_REVIEW", "APPROVED")
    assert len(data.shown_cards()) >= 5
