"""Properties of the pure parts of the interview memory: quote verification, supersession, ids and hashing."""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from mde_compass.domain.brief import Brief
from mde_compass.domain.common import BriefItem
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.domain.interview import Fact, InterviewBlock
from mde_compass.engine import facts as facts_mod
from mde_compass.engine import quotes
from mde_compass.engine.interview_state import InterviewMemory
from mde_compass.scenarios.doubles import NOW, make_fact
from mde_compass.scenarios.sessions import PURE_EXAMPLES

SPACES = [" ", "  ", "\t", "\n", " \n ", " ", " ", "\r\n"]
WORD = st.text(
    alphabet=st.sampled_from(
        list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJ0123456789") + list("पुणेहिंदीटिफिन")
    ),
    min_size=2,
    max_size=9,
)


# ---------------------------------------------------------------------------- quote check is case and space blind
@st.composite
def quoted(draw: st.DrawFn) -> tuple[list[str], int, int]:
    words = draw(st.lists(WORD, min_size=3, max_size=12))
    i = draw(st.integers(0, len(words) - 1))
    j = draw(st.integers(i + 1, len(words)))
    return words, i, j


def respace(draw: st.DrawFn, words: list[str]) -> str:
    out = words[0]
    for w in words[1:]:
        out += draw(st.sampled_from(SPACES)) + w
    return out


def recase(draw: st.DrawFn, text: str) -> str:
    flip = draw(st.lists(st.booleans(), min_size=len(text), max_size=len(text)))
    return "".join(c.swapcase() if f else c for c, f in zip(text, flip, strict=True))


@st.composite
def mutated(draw: st.DrawFn) -> tuple[str, str]:
    """(quote, source): the same words, written with other case and other whitespace."""
    words, i, j = draw(quoted())
    quote = recase(draw, respace(draw, words[i:j]))
    source = recase(draw, respace(draw, words))
    return quote, source


@given(mutated())
@settings(max_examples=PURE_EXAMPLES)
def test_a_verbatim_quote_is_verified_whatever_the_case_and_the_whitespace(
    pair: tuple[str, str],
) -> None:
    quote, source = pair
    assume(len(quotes.normalize(quote)) >= quotes.DEFAULT_MIN_CHARS)
    assert quotes.verify_quote(quote, source)


@given(quoted(), WORD, st.data())
@settings(max_examples=PURE_EXAMPLES)
def test_a_quote_with_a_word_the_person_never_said_is_not_verified(
    parts: tuple[list[str], int, int], stranger: str, data: st.DataObject
) -> None:
    words, i, j = parts
    source = " ".join(words)
    assume(quotes.normalize(stranger) not in quotes.normalize(source))
    inner = list(words[i:j])
    inner.insert(data.draw(st.integers(0, len(inner))), stranger)
    assume(len(inner) >= 2)
    assert not quotes.verify_quote(" ".join(inner), source)


@given(st.text(min_size=1, max_size=40))
@settings(max_examples=PURE_EXAMPLES)
def test_normalizing_twice_changes_nothing(text: str) -> None:
    once = quotes.normalize(text)
    assert quotes.normalize(once) == once


# ------------------------------------------------------------------ hash ignores the audit block of the interview
blocks = st.builds(
    InterviewBlock,
    budget=st.sampled_from(["quick", "standard", "thorough"]),
    ui_mode=st.sampled_from(["one_at_a_time", "answer_all"]),
    questions_asked=st.integers(0, 60),
    followups=st.integers(0, 20),
    answered=st.integers(0, 60),
    skipped=st.integers(0, 60),
    assumed=st.integers(0, 60),
    batches=st.integers(0, 30),
    facts_active=st.integers(0, 300),
    fallback_count=st.integers(0, 9),
    coverage=st.floats(0.0, 1.0),
    question_ids=st.lists(st.from_regex(r"Q-\d{3}", fullmatch=True), max_size=10),
    stop_reason=st.text(max_size=30),
)


def content_brief() -> Brief:
    brief = Brief()
    brief.idea["summary"] = BriefItem(value="A tiffin app", status="volunteered", tier="Locked")
    brief.audience["segments"] = BriefItem(value=["Office workers"], status="volunteered")
    return brief


@given(blocks, blocks)
@settings(max_examples=PURE_EXAMPLES)
def test_the_brief_hash_does_not_move_when_the_interview_block_changes(
    a: InterviewBlock, b: InterviewBlock
) -> None:
    brief = content_brief()
    base_hash, base_sections = brief_hash(brief), section_hashes(brief)
    brief.meta.interview = a
    assert brief_hash(brief) == base_hash and section_hashes(brief) == base_sections
    brief.meta.interview = b
    assert brief_hash(brief) == base_hash and section_hashes(brief) == base_sections


@given(blocks, st.text(min_size=1, max_size=30))
@settings(max_examples=PURE_EXAMPLES)
def test_the_hash_still_moves_when_the_content_changes(
    block: InterviewBlock, new_summary: str
) -> None:
    brief = content_brief()
    brief.meta.interview = block
    before, sections = brief_hash(brief), section_hashes(brief)
    assume(new_summary != "A tiffin app")
    brief.idea["summary"] = BriefItem(value=new_summary, status="volunteered", tier="Locked")
    assert brief_hash(brief) != before
    after = section_hashes(brief)
    assert [k for k in after if after[k] != sections.get(k)] == ["idea"]


# ------------------------------------------------------------- supersession never deletes, ids never come back
Op = tuple[str, int, int]
ops = st.lists(
    st.tuples(
        st.sampled_from(["add", "add", "add", "supersede", "dispute", "not_used"]),
        st.integers(0, 30),
        st.integers(0, 30),
    ),
    max_size=40,
)


@given(ops)
@settings(max_examples=PURE_EXAMPLES)
def test_a_ledger_only_grows_ids_run_without_gaps_and_old_facts_keep_their_words(
    operations: list[Op],
) -> None:
    mem = InterviewMemory()
    first_seen: dict[str, Fact] = {}
    ever_superseded: set[str] = set()
    for kind, a, b in operations:
        live = [f for f in mem.facts if f.status in ("active", "disputed")]
        if kind == "add" or len(live) < 2:
            number = int(mem.next_fact_id().removeprefix("FCT-"))
            mem.add_fact(make_fact(number, f"statement {number}", quote=f"quote {number}"))
        elif kind == "supersede":
            old, new = live[a % len(live)], live[b % len(live)]
            if old.id != new.id:
                old2, new2 = facts_mod.supersede(old, new, now=NOW)
                mem.update_fact(old2)
                mem.update_fact(new2)
                ever_superseded.add(old.id)
        elif kind == "dispute":
            x, y = live[a % len(live)], live[b % len(live)]
            if x.id != y.id:
                x2, y2 = facts_mod.dispute(x, y, now=NOW)
                mem.update_fact(x2)
                mem.update_fact(y2)
        else:
            target = live[a % len(live)]
            mem.update_fact(facts_mod.mark_not_used(target, "small talk", NOW))
        ids = [f.id for f in mem.facts]
        assert ids == [f"FCT-{n:03d}" for n in range(1, len(ids) + 1)]  # no gap, no reuse, in order
        assert mem.next_fact_id() not in ids
        for f in mem.facts:
            seen = first_seen.setdefault(f.id, f)
            assert (f.statement, f.quote, f.created_at) == (
                seen.statement,
                seen.quote,
                seen.created_at,
            )
        for gone in ever_superseded:
            current = mem.fact(gone)
            assert current is not None and current.status == "superseded"  # kept, and never revived
    assert set(first_seen) == {f.id for f in mem.facts}
