"""G2-SUMMARY: the tables come from code, the model writes one paragraph, a template stands in when the call fails."""

from __future__ import annotations

import asyncio
import json

from mde_spec_agent.lanes.screens.g2_summary import mock as g2_mock
from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out, NumIn
from mde_spec_agent.lanes.screens.g2_summary.step import FILE, g2summary_step
from mde_spec_agent.lanes.screens.g2_summary.validate import validate


def test_summary_has_code_tables_and_a_model_paragraph(finished, key):
    run = finished(key)
    s = run.ctx.plan["g2_summary"]
    t = s["tables"]
    assert set(t) >= {
        "inventory",
        "phases",
        "confirm",
        "differs",
        "assumptions",
        "degraded",
        "code_fixes",
    }
    assert len(t["confirm"]) <= 20
    ranks = [c["rank"] for c in t["confirm"]]
    assert ranks == list(range(1, len(ranks) + 1))
    assert set(t["differs"]) == {"merged", "split", "added", "phased"}
    assert t["inventory"]["screens"] == sum(
        1 for x in run.graph.screens.values() if x.status == "in"
    )
    assert s["paragraph_source"] == "model" and "\n" not in s["paragraph"]
    assert s["headline"] and s["lines"]
    stored = json.loads(run.ctx.store.read_text(run.ctx.run_id, FILE))
    assert stored["paragraph"] == s["paragraph"] and stored["tables"]["inventory"] == t["inventory"]


def test_the_summary_never_changes_what_gate_two_binds(fresh):
    run = fresh("quick")
    h = run.graph.content_hash()
    asyncio.run(g2summary_step(run.ctx))
    assert run.graph.content_hash() == h


def test_when_the_call_fails_the_template_is_used_and_the_paragraph_omitted(fresh, monkeypatch):
    run = fresh("quick")

    def handle(inp):
        return G2Out(paragraph="We have 987654 screens and the best app.")  # a number nobody gave

    monkeypatch.setattr(g2_mock, "handle", handle)
    asyncio.run(g2summary_step(run.ctx))
    s = run.ctx.plan["g2_summary"]
    assert s["paragraph"] is None and s["paragraph_source"] == "omitted"
    assert (
        s["headline"] and s["tables"]["inventory"]["screens"] > 0
    )  # the template summary still stands


def test_validator_rules():
    inp = G2In(language="en", numbers=[NumIn(name="screens", value=12)], lines=["x 3"])
    assert validate(inp, G2Out(paragraph="Twelve is not a digit but 12 screens and 3 items.")) == []
    assert validate(inp, G2Out(paragraph="a\nb"))
    assert validate(inp, G2Out(paragraph="about 99 screens"))
    assert validate(inp, G2Out(paragraph=""))
    assert (
        validate(inp, G2Out(paragraph=" ".join(["word"] * 200))) == []
    )  # length is trimmed by code, not re-asked


def test_trim_cuts_at_the_last_whole_sentence_within_the_limit():
    from mde_spec_agent.lanes.screens.g2_summary.validate import trim_paragraph

    text = "One two three. Four five six seven. Eight nine ten eleven twelve."
    assert trim_paragraph(text, 20) == text
    assert trim_paragraph(text, 8) == "One two three. Four five six seven."
    assert trim_paragraph(text, 3) == "One two three."
    assert trim_paragraph("word " * 50, 5) == "word word word word word."


def test_a_too_long_paragraph_is_trimmed_not_dropped(fresh, monkeypatch):
    run = fresh("quick")
    sentence = "This release has many screens. "

    def handle(inp):
        return G2Out(paragraph=(sentence * 40).strip())

    monkeypatch.setattr(g2_mock, "handle", handle)
    asyncio.run(g2summary_step(run.ctx))
    s = run.ctx.plan["g2_summary"]
    assert s["paragraph_source"] == "model"
    assert len(s["paragraph"].split()) <= 110 and s["paragraph"].endswith(".")
    assert not [f for f in run.ctx.flags if f["kind"] == "degraded"]
