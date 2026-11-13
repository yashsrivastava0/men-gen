"""COPY-L: locale passes, idempotent, flagged for native review, glossary first."""

from __future__ import annotations

import asyncio
import re

from mde_core.errors import GatewayError
from mde_spec_agent.lanes.screens.copy_l import mock as copyl_mock
from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, CopyLOut, StrIn, StrOut
from mde_spec_agent.lanes.screens.copy_l.step import copyl_step, review_key
from mde_spec_agent.lanes.screens.copy_l.validate import validate
from mde_spec_core.config import default_config
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context

_PH = re.compile(r"\{[^{}]*\}")


def test_every_locale_is_complete_and_flagged_for_review(finished, key):
    g = finished(key).graph
    src = g.meta.source_language
    for loc in g.meta.languages:
        if loc == src:
            continue
        copy = g.locale_copy[loc]
        assert copy[review_key(loc)].startswith("needs_native_review")
        missing = [k for k in g.locale_copy[src] if k not in copy]
        assert missing == []
        for k, v in g.locale_copy[src].items():
            assert v and copy[k]
            assert _PH.findall(copy[k]) == _PH.findall(v) or sorted(_PH.findall(copy[k])) == sorted(
                _PH.findall(v)
            )
    assert not [
        f for f in run_all(make_context(g), gate=3, only={"V-14"}) if f.severity == "blocker"
    ]


def test_already_translated_strings_are_not_translated_again(fresh):
    run = fresh("quick")
    g = run.graph
    src, loc = (
        g.meta.source_language,
        next(x for x in g.meta.languages if x != g.meta.source_language),
    )
    keep = sorted(g.locale_copy[src])[0]
    g.locale_copy.setdefault(loc, {})[keep] = "KEEP ME"
    asyncio.run(copyl_step(run.ctx))
    assert g.locale_copy[loc][keep] == "KEEP ME"
    n = len(run.ctx.runner.calls)
    asyncio.run(copyl_step(run.ctx))  # nothing left to translate: no call at all
    assert len(run.ctx.runner.calls) == n


def test_batches_never_exceed_the_per_call_cap(fresh):
    run = fresh("standard")
    seen: list[int] = []
    inner = run.ctx.runner._gateway
    orig = inner.run

    async def run_(spec, inp, ctx):
        if spec.id == "spec.copy_l":
            seen.append(len(inp.strings))
        return await orig(spec, inp, ctx)

    inner.run = run_  # type: ignore[method-assign]
    asyncio.run(copyl_step(run.ctx))
    assert seen and max(seen) <= default_config().fusion.strings_per_locale_call


def test_truncation_splits_the_pass(fresh):
    run = fresh("quick")
    seen: list[int] = []
    inner = run.ctx.runner._gateway
    orig = inner.run

    async def run_(spec, inp, ctx):
        if spec.id == "spec.copy_l":
            seen.append(len(inp.strings))
            if len(inp.strings) > 40:
                raise GatewayError("output truncated")
        return await orig(spec, inp, ctx)

    inner.run = run_  # type: ignore[method-assign]
    asyncio.run(copyl_step(run.ctx))
    g = run.graph
    loc = next(x for x in g.meta.languages if x != g.meta.source_language)
    assert max(seen) > 40 and min(seen) <= 40
    assert all(k in g.locale_copy[loc] for k in g.locale_copy[g.meta.source_language])
    assert not [f for f in run.ctx.flags if f["kind"] == "degraded"]


def test_lost_placeholders_are_refused_then_the_locale_is_degraded(fresh, monkeypatch):
    run = fresh("quick")
    real = copyl_mock.handle

    def handle(inp):
        out = real(inp)
        return CopyLOut(strings=[StrOut(key=s.key, text=_PH.sub("", s.text)) for s in out.strings])

    monkeypatch.setattr(copyl_mock, "handle", handle)
    asyncio.run(copyl_step(run.ctx))
    g = run.graph
    loc = next(x for x in g.meta.languages if x != g.meta.source_language)
    assert g.locale_copy[loc][review_key(loc)].endswith("degraded")
    assert any(f["kind"] == "degraded" and f["step"] == "copyl" for f in run.ctx.flags)
    # the fallback shows the source text, never invented words
    src = g.locale_copy[g.meta.source_language]
    assert all(g.locale_copy[loc][k] == v for k, v in src.items())


def test_validator_rules():
    inp = CopyLIn(
        language="hi",
        source_language="en",
        glossary="",
        facts="",
        strings=[StrIn(key="a", text="Pay {name}"), StrIn(key="b", text="Done")],
    )
    ok = CopyLOut(strings=[StrOut(key="a", text="x {name}"), StrOut(key="b", text="y")])
    assert validate(inp, ok) == []
    assert validate(inp, CopyLOut(strings=[StrOut(key="a", text="x"), StrOut(key="b", text="y")]))
    assert validate(inp, CopyLOut(strings=[StrOut(key="a", text="x {name}")]))
    assert validate(inp, CopyLOut(strings=[*ok.strings, StrOut(key="zzz", text="q")]))
    assert validate(
        inp,
        CopyLOut(
            strings=[StrOut(key="a", text="x {name}"), StrOut(key="b", text="see https://x.y")]
        ),
    )
