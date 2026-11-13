"""The static export (no script, no network, everything escaped), determinism, and rebuilding the views from a run folder."""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path

import pytest

from mde_spec_agent import views as V
from mde_spec_agent.compose import build_context
from mde_spec_agent.config import SpecSettings
from mde_spec_agent.run.orchestrator import Orchestrator
from mde_spec_agent.run.pipeline import build_pipeline
from mde_spec_agent.run.store import RunStore
from mde_spec_agent.spend import SpendRecord
from mde_spec_agent.testing import mock_core
from mde_spec_core.graph import SpecGraph

EXPORTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"

IMG = "<img src=x onerror=alert(1)>"
SCRIPT = "<script>alert(1)</script>"


def html_of(b, graph=None, **kw):
    g = graph or b.graph
    g2 = V.g2(g, b.pack, b.check, None, mock=b.mocks)
    return V.preview_html(b.pack, g, g2_view=g2, mock=b.mocks, **kw)


def test_preview_html_has_no_script_no_link_no_external_address(built):
    out = html_of(built)
    low = out.lower()
    assert out.startswith("<!doctype html>")
    for bad in (
        "<script",
        "http://",
        "https://",
        "javascript:",
        "<link",
        "<img",
        "<iframe",
        "src=",
        "@import",
        "url(",
    ):
        assert bad not in low, bad
    for sid, s in built.graph.screens.items():
        if s.status == "in":
            assert f'id="screen-{sid}"' in out
            break
    assert "Illustrative: the final layout is the front end" in out
    assert "<nav" in out and 'href="#screens"' in out


def test_preview_html_escapes_poisoned_text_everywhere(quick):
    g = quick.graph.model_copy(deep=True)
    first = g.screens[sorted(g.screens)[0]]
    first.purpose = f"Shows classes. {IMG} {SCRIPT}"
    first.name = f"Timetable {SCRIPT}"
    feat = next(iter(g.features.values()))
    feat.name = f"Book a class {IMG}"
    feat.status = "later"
    feat.later_reason = "user"
    g.apps[sorted(g.apps)[0]].name = f"App {SCRIPT}"
    ent = next(iter(g.entities.values()))
    ent.name = f"Class {IMG}"
    flow = next(iter(g.flows.values()))
    flow.stages[0].goal = f"browse {SCRIPT}"
    out = html_of(quick, g)
    assert (
        "<img" not in out and "<script" not in out.lower() and "onerror=alert" in out
    )  # the text is shown, as text
    assert html.escape(IMG) in out and html.escape(SCRIPT) in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out
    # the only tags are the ones this module writes
    allowed = {
        "html",
        "head",
        "meta",
        "title",
        "style",
        "body",
        "h1",
        "h2",
        "h3",
        "h5",
        "p",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "div",
        "nav",
        "a",
        "section",
        "article",
        "ul",
        "ol",
        "li",
        "strong",
    }
    tags = set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", out))
    assert tags <= allowed, tags - allowed


def test_preview_html_defangs_urls_in_pack_text(quick):
    g = quick.graph.model_copy(deep=True)
    g.screens[
        sorted(g.screens)[0]
    ].purpose = "See https://example.org/x and http://example.net for details"
    out = html_of(quick, g)
    assert "https://" not in out and "http://" not in out and "example.org" in out


def test_preview_html_is_deterministic(built):
    assert html_of(built) == html_of(built)


def test_views_are_deterministic(built, writer, config):
    g, d = built.graph, built.derived
    sid = sorted(g.screens)[0]
    fid = next(iter(g.flows))
    first = [
        V.g15(g, d),
        V.g2(g, built.pack, built.check, d, mock=built.mocks),
        V.screen_view(g, sid, "busy", mock=built.mocks),
        V.journey_view(g, fid),
        V.nav_graph(g, derived=d),
        V.data_view(g, mock=built.mocks),
        V.needs_view(built.pack, g),
        V.preview(d, d.decision, writer, config, 3_000_000),
        V.pack_tree(built.pack),
        V.budget_view(built.pack),
        V.checks_view(built.check),
    ]
    again = [
        V.g15(g, d),
        V.g2(
            g, built.pack, built.check, d, mock=V.mock_profiles(g)
        ),  # mock profiles rebuilt: same records
        V.screen_view(g, sid, "busy", mock=V.mock_profiles(g)),
        V.journey_view(g, fid),
        V.nav_graph(g, derived=d),
        V.data_view(g, mock=V.mock_profiles(g)),
        V.needs_view(built.pack, g),
        V.preview(d, d.decision, writer, config, 3_000_000),
        V.pack_tree(built.pack),
        V.budget_view(built.pack),
        V.checks_view(built.check),
    ]
    assert first == again


def test_every_screen_of_the_export_shows_in_the_page(thorough):
    out = html_of(thorough)
    live = [sid for sid, s in thorough.graph.screens.items() if s.status == "in"]
    assert all(f'id="screen-{sid}"' in out for sid in live)


# ---------------------------------------------------------------- rebuilding the views from a run folder


def make_run(tmp_path, stem):
    store = RunStore(tmp_path / "rt")
    j = store.create_run(
        "prog", "h1", {"offline": True, "compass_path": str(EXPORTS / f"{stem}.json")}
    )
    s = SpecSettings()
    rec = SpendRecord(
        tmp_path / "spend.json", cap_micros=s.cap_micros, tripwire_micros=s.tripwire_micros
    )
    ctx = build_context(
        store=store,
        run_id=j.run_id,
        program_id="prog",
        input_hash="h1",
        graph=SpecGraph(),
        core=mock_core(),
        settings=s,
        record=rec,
        offline=True,
    )
    orch = Orchestrator(store, build_pipeline())
    out = asyncio.run(orch.run(ctx))
    while out.status == "paused":
        orch.approve_gate(ctx.run_id, "g15", {"consent": "test"})
        out = asyncio.run(orch.run(ctx))
    ctx.save_graph()
    return store, ctx, out


@pytest.mark.parametrize("stem", ["quick-yoga-studio-bengaluru", "thorough-freight-fleet-india"])
def test_views_rebuild_from_a_run_folder(tmp_path, stem):
    store, ctx, out = make_run(tmp_path, stem)
    assert out.status == "awaiting_g2"
    loaded = V.load_run(store, ctx.run_id)
    assert loaded.pack is not None and loaded.check is not None and loaded.derived is not None
    assert loaded.graph.canonical_text() == ctx.graph.canonical_text()
    assert loaded.pack.files == ctx.pack.files and loaded.pack.tasks == ctx.pack.tasks
    assert (
        loaded.pack.content_hash == ctx.pack.content_hash and loaded.pack.blocks == ctx.pack.blocks
    )
    mocks = V.mock_profiles(loaded.graph)
    live = V.g2(
        loaded.graph,
        loaded.pack,
        loaded.check,
        loaded.derived,
        flags=loaded.flags,
        banners=loaded.banners,
        rcode_rows=loaded.rcode_rows,
        research=loaded.research,
        mock=mocks,
    )
    memory = V.g2(
        ctx.graph,
        ctx.pack,
        ctx.check,
        ctx.derived,
        flags=ctx.flags,
        banners=ctx.banners,
        rcode_rows=ctx.rcode.rows(),
        research=ctx.research,
        mock=mocks,
    )
    assert (
        live["content_hash"] == memory["content_hash"] == store.get_run(ctx.run_id)["content_hash"]
    )
    assert live["confirm_list"] == memory["confirm_list"]
    assert live["differences_from_brief"] == memory["differences_from_brief"]
    assert V.g15(loaded.graph, loaded.derived) == V.g15(ctx.graph, ctx.derived)
    assert V.pack_tree(loaded.pack) == V.pack_tree(ctx.pack)
    assert V.budget_view(loaded.pack)["total_tokens"] == ctx.pack.budget_report["total_tokens"]
    assert V.checks_view(loaded.check) == V.checks_view(ctx.check)
    page = V.preview_html(
        loaded.pack, loaded.graph, g2_view=live, derived=loaded.derived, mock=mocks
    )
    assert "<script" not in page.lower()


def test_a_synthetic_run_has_no_derived_plan_but_still_has_views(tmp_path):
    store = RunStore(tmp_path / "rt")
    j = store.create_run("prog", "h1", {"offline": True, "synth": "tiny"})
    s = SpecSettings()
    rec = SpendRecord(
        tmp_path / "spend.json", cap_micros=s.cap_micros, tripwire_micros=s.tripwire_micros
    )
    ctx = build_context(
        store=store,
        run_id=j.run_id,
        program_id="prog",
        input_hash="h1",
        graph=SpecGraph(),
        core=mock_core(),
        settings=s,
        record=rec,
        offline=True,
    )
    asyncio.run(Orchestrator(store, build_pipeline()).run(ctx))
    ctx.save_graph()
    loaded = V.load_run(store, ctx.run_id)
    assert loaded.derived is None
    assert V.g15(loaded.graph, None)["phases"][0]["count"] >= 1
    assert V.g2(loaded.graph, loaded.pack, loaded.check, None)["content_maps"]
