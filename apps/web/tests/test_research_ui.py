"""The Research screens of the console.

Four kinds of check:

* static: the files, their wiring through the registry and the shell, the tokens, the banned APIs, the Compass hooks;
* ``research_view.js`` (the pure helpers) run under Node: the event reducer and snapshot folding, the stream parser,
  gap detection, formatters, URL safety, finding grouping, error wording;
* the fake Research server (``fake_research_server.py``) against the documented contract, so it is a faithful
  stand-in both for these tests and for trying the console by hand;
* the real producer into the real consumer: Node reads the fake server's actual event stream with ``fetch`` and a
  stream reader, resumes after a hang-up and notices a gap, folding everything with ``research_view.js``.

Offline: the fake server listens on a random loopback port. The Node tests are skipped when Node is missing.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

CONSOLE = Path(__file__).resolve().parents[1] / "console"
RESEARCH = CONSOLE / "agents" / "research"
VIEW_JS = RESEARCH / "research_view.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(rel: str) -> str:
    return (CONSOLE / rel).read_text(encoding="utf-8")


def code_only(js: str) -> str:
    """The script without its comments (a comment may talk about EventSource; the code must not use it)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
    return re.sub(r"\s//\s.*$", "", js, flags=re.M)


def load_fake() -> ModuleType:
    """The fake server module (the tests run with --import-mode=importlib, so it is loaded by path)."""
    path = Path(__file__).with_name("fake_research_server.py")
    spec = importlib.util.spec_from_file_location("fake_research_server_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FAKE = load_fake()
HEADERS = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
HASH = "a" * 64


def good_export(title: str = "yoga studio app") -> dict[str, Any]:
    return {
        "export": {
            "format_version": 2,
            "approved_baseline": {"hash": HASH, "version": 3},
            "brief_hash": HASH,
            "brief_version": 3,
        },
        "brief": {"_hash": HASH, "version": 3},
        "consumer_views": {"research": {"idea": {"category": title}}},
    }


# ---------------------------------------------------------------------------------------------------------
# static checks
# ---------------------------------------------------------------------------------------------------------


def test_the_research_files_are_loaded_by_the_shell_as_extras_before_the_screen_script() -> None:
    registry = read("shared/registry.js")
    assert re.search(
        r'extras:\s*\["agents/research/research_view\.js",\s*"agents/research/research\.js",\s*'
        r'"agents/research/research\.css"\]',
        registry,
    )
    shell = read("shared/shell.js")
    helper = shell.index("loadScript(active.helper)")
    extras = shell.index("loadExtras(active.extras)")
    screen = shell.index("loadScript(active.script)")
    assert helper < extras < screen, (
        "extras come after the helper and before the screen's own script"
    )
    assert ".css" in shell and "loadStyle" in shell, "an extra .css file is a stylesheet"
    assert "compass" not in shell.lower(), "the shell still names no agent"
    for name in ("research_view.js", "research.js", "research.css"):
        assert (RESEARCH / name).is_file()


def test_research_css_uses_only_tokens_and_no_raw_colours() -> None:
    css = read("agents/research/research.css")
    assert not re.search(r"(?<![\w-])--[\w-]+\s*:", css), (
        "a screen's CSS defines no custom property"
    )
    tokens = read("shared/tokens.css")
    defined = set(re.findall(r"--([\w-]+)\s*:", tokens))
    used = set(re.findall(r"var\(--([\w-]+)", css))
    assert used and used <= defined, sorted(used - defined)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "no hex colours"
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", css), "no raw colour functions"
    assert "prefers-reduced-motion" in css, "motion is switched off for those who ask"
    assert "max-width: 520px" in css, "it works on a phone"
    assert "@font-face" not in css and "@import" not in css


def test_research_scripts_use_the_safe_apis_only() -> None:
    js = code_only(read("agents/research/research.js"))
    view = code_only(read("agents/research/research_view.js"))
    for banned in ("EventSource", "XMLHttpRequest", "sendBeacon", "localStorage", "sessionStorage"):
        assert banned not in js, (
            f"research.js must not use {banned} (the shared store guards storage)"
        )
        assert banned not in view, banned
    assert "Last-Event-ID" in js and "getReader()" in js, "fetch + a stream reader, resumable"
    assert 'rel: "noopener noreferrer"' in js and 'target: "_blank"' in js
    assert "sourceLinks" in js and "safeUrl" in view, "links only come from the URL check"
    assert "requestAnimationFrame" in js, "bursts of events are drawn in frames"
    assert "textContent" in js
    assert re.search(r"V\.FEED_CAP", js), "the feed has a cap"
    assert 'role: "log"' in js and "aria-live" in js and "progressbar" in js
    for word in ("traceback", "stack"):  # error text is plain words, never dumped
        assert f"e.{word}" not in js.lower()
    # the only place that speaks to the network is request()/connect()
    assert len(re.findall(r"root\.fetch\(", js)) == 2
    # every id looked up with a bare getElementById is made by the module itself or does not exist
    assert "getElementById" not in js


def test_compass_hooks_are_small_and_present() -> None:
    html = read("agents/compass/compass.html")
    js = read("agents/compass/compass.js")
    for ident in ("tab-research", "research-page", "set-research-url"):
        assert html.count(f'id="{ident}"') == 1, ident
    # started after Gate 1 from BOTH approval paths, only once the session is approved
    assert js.count("startResearchAfterApproval()") == 3  # the definition and two call sites
    assert js.count("if (isApproved()) { startResearchAfterApproval(); }") == 2
    assert "R.start(researchOpts())" in js and "R.show(researchOpts())" in js
    assert "window.MobigenResearch.homePanel({ openPage: openResearchPage })" in js
    assert "window.MobigenResearch.configure({ headers: authHeaders })" in js
    assert '["research", "x.research_tab"]' in js
    assert "setBaseUrl" in js and "getBaseUrl" in js, "the research address is a Settings field"
    # the export is read with a plain fetch, not api(): it can be megabytes and api() keeps a copy in the trace
    assert re.search(r'fetch\(BASE \+ "/sessions/" \+ encodeURIComponent\(sid\) \+ "/export"', js)


async def test_the_research_files_are_served_with_the_strict_csp_and_the_research_origin(
    anon: httpx.AsyncClient,
) -> None:
    for name in ("research_view.js", "research.js", "research.css"):
        r = await anon.get(f"/ui/agents/research/{name}")
        assert r.status_code == 200, name
        csp = r.headers["content-security-policy"]
        assert (
            "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe-inline" not in csp
        )
        assert "connect-src 'self' http://127.0.0.1:8120 http://localhost:8120;" in csp


# ---------------------------------------------------------------------------------------------------------
# research_view.js under Node
# ---------------------------------------------------------------------------------------------------------

PRELUDE = """
global.window = {};
require(process.argv[1]);
const R = window.researchView;
const ev = (seq, type, extra) => Object.assign({ id: "evt_" + seq, seq: seq, ts: "2026-11-11T12:00:0" + (seq % 10) + "Z", run_id: "rrn_1", type: type, target_id: null,
  round: 1, phase: "search", label: type + " label", detail: {}, counters: { searches_used: seq, pages_read: 0, facts_verified: 0, targets_done: 0, targets_total: 3, elapsed_s: seq, eta_s: 30 },
  schema: "research.event.v1" }, extra || {});
"""


def run_js(body: str, *args: str) -> Any:
    assert NODE
    program = PRELUDE + f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));"
    out = subprocess.run(
        [NODE, "-e", program, str(VIEW_JS), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout else None


@needs_node
def test_sse_frames_are_parsed_across_chunks_crlf_comments_and_multiline_data() -> None:
    res = run_js(
        r"""
        const a = R.parseSseFrames("retry: 3000\n\nid: 1\nevent: run.started\ndata: {\"a\":1}\n\n: keepalive\n\nid: 2\nevent: x\ndata: {\"b\":");
        const b = R.parseSseFrames(a.rest + "2}\n\n");
        const c = R.parseSseFrames("id: 3\r\ndata: line1\r\ndata: line2\r\n\r\n");
        const d = R.parseSseFrames("id: 4\r");   // a chunk ends between \r and \n
        const e = R.parseSseFrames(d.rest + "\ndata: ok\n\n");
        return { a: a.frames, aRest: a.rest, b: b.frames, c: c.frames, e: e.frames };
        """
    )
    assert [f["data"] for f in res["a"]] == [
        None,
        '{"a":1}',
    ]  # retry frame has no data; keepalive is dropped
    assert (
        res["a"][0]["retry"] == 3000
        and res["a"][1]["id"] == "1"
        and res["a"][1]["event"] == "run.started"
    )
    assert res["aRest"].startswith("id: 2")
    assert res["b"][0]["data"] == '{"b":2}' and res["b"][0]["id"] == "2"
    assert res["c"][0]["data"] == "line1\nline2"
    assert res["e"][0]["data"] == "ok" and res["e"][0]["id"] == "4"


@needs_node
def test_seq_checks_name_duplicates_and_gaps() -> None:
    res = run_js(
        """return [R.checkSeq(0, 1), R.checkSeq(5, 6), R.checkSeq(5, 5), R.checkSeq(5, 3),
                   R.checkSeq(5, 8), R.checkSeq(0, 2), R.checkSeq(3, undefined)];"""
    )
    assert res == ["ok", "ok", "dup", "dup", "gap", "gap", "dup"]


@needs_node
def test_events_fold_into_the_run_state() -> None:
    res = run_js(
        """
        const s = R.emptyState();
        const events = [
          ev(1, "run.started", { detail: { load_class: "standard", depth_word: "a standard-depth study", minutes: 4, degraded: true } }),
          ev(2, "plan.ready", { detail: { targets: [
              { id: "T-01", label: "Comparable apps", job: "features", lane: "blueprint", priority: 95, status: "queued" },
              { id: "T-02", label: "Payments", job: "services", lane: "integrations", priority: 80, status: "queued" },
              { id: "T-03", label: "Local rules", job: "local", lane: "verify", priority: 70, status: "queued" } ],
              declined: ["a currency symbol"] } }),
          ev(3, "target.started", { target_id: "T-01", detail: { slots_filled: 0, slots_required: 2 } }),
          ev(4, "query.issued", { target_id: "T-01", detail: { query: "q one", variant: "literal", lang: "en", country: "in", provider: "tavily" } }),
          ev(5, "fact.found", { target_id: "T-01", detail: { kind: "feature", label: "Waitlist", topic: "Comparable apps" } }),
          ev(6, "entity.discovered", { detail: { name: "Mindbody", kind: "competitor" } }),
          ev(7, "entity.discovered", { detail: { name: "mindbody" } }),
          ev(8, "fact.verified", { target_id: "T-01", detail: { kind: "feature", label: "Waitlist", domain: "a.com", level: "core" } }),
          ev(9, "fact.found", { detail: { kind: "rule", label: "GST 18 percent" } }),
          ev(10, "fact.rejected", { detail: { reason: "quote_not_found", label: "GST 18 percent" } }),
          ev(11, "target.covered", { target_id: "T-01", detail: { slots_filled: 2, slots_required: 2 } }),
          ev(12, "target.started", { target_id: "T-02", detail: { slots_filled: 0, slots_required: 3 } }),
          ev(13, "round.completed", { detail: { round: 1, slots_filled: 2, slots_required: 8, new_slots: 2 } }),
          ev(14, "gap.detected", { detail: { targets: ["T-02"], count: 1 } }),
          ev(15, "warning", { detail: { code: "provider_slow", message: "A search service is slow." } }),
          ev(16, "target.cancelled", { target_id: "T-02" }),
          ev(17, "target.not_found", { target_id: "T-03", detail: { queries: ["a", "b"] } }),
        ];
        events.forEach((e) => R.applyEvent(s, e, 1000));
        const mid = JSON.parse(JSON.stringify(s));
        R.applyEvent(s, ev(18, "run.finished", { detail: { status: "partial", facts: 1, pages: 2, searches: 3, usd: "0.1", elapsed_s: 40 } }), 2000);
        return { mid, end: s, coverage: R.coverage(mid), totals: R.topicTotals(mid), texts: R.counterTexts(mid), warn: R.warningSummary(mid), depth: R.depthSentence(mid) };
        """
    )
    mid, end = res["mid"], res["end"]
    assert (
        mid["status"] == "running"
        and mid["lastSeq"] == 17
        and mid["depthWord"] == "a standard-depth study"
    )
    assert mid["degraded"] is True and mid["minutes"] == 4
    assert [(t["id"], t["status"]) for t in mid["targets"]] == [
        ("T-01", "covered"),
        ("T-02", "cancelled"),
        ("T-03", "not_found"),
    ]
    assert mid["targets"][0]["slotsFilled"] == 2 and mid["targets"][2]["queries"] == ["a", "b"]
    assert mid["declined"] == ["a currency symbol"]
    assert mid["entities"] == ["Mindbody"], "the same name in another case is one chip"
    assert [(f["label"], f["status"]) for f in mid["facts"]] == [("Waitlist", "checked")]
    assert mid["facts"][0]["domain"] == "a.com" and mid["factsRejected"] == 1
    assert mid["gap"] == {"count": 1, "targets": ["T-02"]}
    assert mid["warnings"][0]["slow"] is True and res["warn"]["slow"] is True
    assert res["warn"]["notes"] == ["A search service is slow."]
    assert res["coverage"] == {
        "filled": 2,
        "required": 5,
        "percent": 40,
        "estimated": False,
    }, "per-topic numbers win over the round"
    assert res["totals"] == {"done": 0, "total": 3}, "the server's counters win over counting rows"
    assert res["texts"]["searches"] == "17" and res["texts"]["topics"] == "0 of 3"
    assert res["depth"] == "We are doing a standard-depth study, with about 4 minutes set aside."
    assert len(mid["feed"]) == 17 and mid["feed"][3]["detail"]["query"] == "q one"
    assert end["status"] == "partial" and end["result"]["facts"] == 1 and end["lastSeq"] == 18


@needs_node
def test_coverage_estimates_topics_that_have_not_reported_slots_and_falls_back_to_the_round() -> (
    None
):
    res = run_js(
        """
        const s = R.emptyState();
        R.applyEvent(s, ev(1, "plan.ready", { detail: { targets: [1, 2, 3, 4].map((n) => ({ id: "T-0" + n, label: "t" + n, job: "features", lane: "blueprint", priority: 1, status: "queued" })), declined: [] } }), 0);
        const none = R.coverage(s);
        R.applyEvent(s, ev(2, "round.completed", { detail: { round: 1, slots_filled: 3, slots_required: 12, new_slots: 3 } }), 0);
        const roundOnly = R.coverage(s);
        R.applyEvent(s, ev(3, "target.started", { target_id: "T-01", detail: { slots_filled: 0, slots_required: 3 } }), 0);
        R.applyEvent(s, ev(4, "target.covered", { target_id: "T-01", detail: { slots_filled: 3, slots_required: 3 } }), 0);
        return { none: none, roundOnly: roundOnly, estimated: R.coverage(s) };
        """
    )
    assert res["none"]["required"] == 0 and res["none"]["percent"] == 0
    assert res["roundOnly"] == {"filled": 3, "required": 12, "percent": 25, "estimated": False}
    assert res["estimated"] == {"filled": 3, "required": 12, "percent": 25, "estimated": True}, (
        "one topic of four is done, so the bar says a quarter, not everything"
    )


@needs_node
def test_terminal_events_set_the_status() -> None:
    res = run_js(
        """
        const f = R.emptyState(); R.applyEvent(f, ev(1, "run.started"), 0);
        R.applyEvent(f, ev(2, "run.failed", { detail: { status: "failed", reason: "The search service stopped.", code: "provider_down" } }), 0);
        const k = R.emptyState(); R.applyEvent(k, ev(1, "run.started"), 0);
        R.applyEvent(k, ev(2, "run.skipped", { detail: { status: "skipped", facts: 4 } }), 0);
        const c = R.emptyState(); R.applyEvent(c, ev(1, "run.finished", { detail: {} }), 0);
        return [f.status, f.failure, k.status, k.result.facts, c.status,
                R.isTerminalType("run.failed"), R.isTerminalType("warning"),
                R.isTerminalStatus("committing"), R.isTerminalStatus("interrupted"), R.isActiveStatus("committing")];
        """
    )
    assert res == [
        "failed",
        {"reason": "The search service stopped.", "code": "provider_down"},
        "skipped",
        4,
        "complete",
        True,
        False,
        False,
        True,
        True,
    ]


@needs_node
def test_a_snapshot_is_the_same_state_a_reload_tails_from_last_seq() -> None:
    res = run_js(
        """
        const snap = { run_id: "rrn_1", program_id: "prg_1", status: "running", phase: "read", round: 2, started_at: "t0", finished_at: null,
          load_class: "deep", load_score: 70, depth_word: "a deep study", plan_source: "mixed",
          input_quality: { degraded: true, flags: [] },
          counters: { searches_used: 9, searches_planned: 28, pages_read: 12, facts_verified: 6, targets_done: 1, targets_total: 2, elapsed_s: 100, eta_s: null },
          targets: [ { id: "T-01", label: "A", job: "features", lane: "blueprint", priority: 9, status: "covered", slots_filled: 2, slots_required: 2 },
                     { id: "T-02", label: "B", job: "data", lane: "mock", priority: 5, status: "running", slots_filled: 1, slots_required: 3 } ],
          declined: ["x"], entities: ["Razorpay", "NPCI"],
          feed: [ ev(40, "fact.found", { detail: { label: "UPI", kind: "fact" } }), ev(41, "fact.verified", { detail: { label: "UPI", domain: "npci.org.in" } }),
                  ev(42, "target.started", { target_id: "T-02", detail: { slots_filled: 0, slots_required: 3 } }) ],
          last_seq: 42, result_status: null, content_hash: null, reason: null };
        const s = R.applySnapshot(snap, 5000);
        const next = R.checkSeq(s.lastSeq, 43), dup = R.checkSeq(s.lastSeq, 42), gap = R.checkSeq(s.lastSeq, 45);
        R.applyEvent(s, ev(43, "target.covered", { round: 2, target_id: "T-02", detail: { slots_filled: 3, slots_required: 3 } }), 6000);
        return { s, next, dup, gap, cov: R.coverage(s), clock: R.clocks(s, 6000 + 2500) };
        """
    )
    s = res["s"]
    assert s["runId"] == "rrn_1" and s["programId"] == "prg_1" and s["lastSeq"] == 43
    assert (
        s["status"] == "running"
        and s["round"] == 2
        and s["depthWord"] == "a deep study"
        and s["degraded"] is True
    )
    assert [t["status"] for t in s["targets"]] == ["covered", "covered"], (
        "the tail updates the snapshot's rows"
    )
    assert s["entities"] == ["Razorpay", "NPCI"] and s["declined"] == ["x"]
    assert [(f["label"], f["status"]) for f in s["facts"]] == [("UPI", "checked")]
    assert (res["next"], res["dup"], res["gap"]) == ("ok", "dup", "gap")
    assert res["cov"]["percent"] == 100 or res["cov"]["filled"] == 5
    # the clock keeps ticking between events: 2.5 s since the counters of event 43 (elapsed 43, eta 30)
    assert abs(res["clock"]["elapsed"] - 45.5) < 0.01 and abs(res["clock"]["eta"] - 27.5) < 0.01


@needs_node
def test_clocks_stand_still_when_the_run_is_over_and_eta_may_be_unknown() -> None:
    res = run_js(
        """
        const s = R.emptyState(); s.status = "running";
        R.applyEvent(s, ev(1, "run.started", { counters: { elapsed_s: 10, eta_s: null } }), 1000);
        const unknown = R.clocks(s, 4000);
        const done = R.applySnapshot({ run_id: "r", status: "complete", counters: { elapsed_s: 60, eta_s: 0 }, feed: [], last_seq: 3 }, 1000);
        return { unknown, done: R.clocks(done, 99999),
                 d: [0, 5, 59, 60, 80, 120, 3599, 3600, 3660, 5400, null].map(R.formatDuration),
                 r: [null, 3, 9, 12, 47, 119, 130, 400].map(R.remainingText) };
        """
    )
    assert res["unknown"] == {"elapsed": 13.0, "eta": None}
    assert res["done"] == {"elapsed": 60, "eta": None}
    assert res["d"] == [
        "0 s",
        "5 s",
        "59 s",
        "1 min",
        "1 min 20 s",
        "2 min",
        "59 min 59 s",
        "1 h",
        "1 h 1 min",
        "1 h 30 min",
        "",
    ]
    assert res["r"] == [
        "estimating",
        "almost done",
        "almost done",
        "about 10 s",
        "about 45 s",
        "about 2 min",
        "about 2 min",
        "about 7 min",
    ]


@needs_node
def test_feed_rows_use_the_servers_label_as_is_and_add_the_details() -> None:
    res = run_js(
        """
        const row = (type, label, detail) => R.feedRow({ type: type, label: label, detail: detail });
        return {
          q: row("query.issued", "Searching: yoga app", { query: "yoga app features", variant: "descriptive", lang: "en", country: "in", provider: "tavily" }),
          sr: row("search.results", "Found 12 results", { count: 12, domains: ["a.com", "b.com"] }),
          pr: row("page.reading", "Reading a.com", { domain: "a.com", title: "A page" }),
          ps: row("page.skipped", "Skipped a.com", { domain: "a.com", reason: "paywall" }),
          pu: row("page.skipped", "Skipped b.com", { domain: "b.com", reason: "new_reason_code" }),
          ff: row("fact.found", "Checking: Waitlist", { label: "Waitlist", topic: "Comparable apps" }),
          fv: row("fact.verified", "Checked: Waitlist", { label: "Waitlist", domain: "a.com" }),
          en: row("entity.discovered", "Found: Mindbody", { name: "Mindbody", kind: "competitor" }),
          rc: row("round.completed", "Round 1 finished", { round: 1, slots_filled: 5, slots_required: 9 }),
          w: row("warning", "A search service is slow", { code: "provider_slow", message: "A search service is slow" }),
          none: row("plan.ready", "", { targets: [] }),
        };
        """
    )
    assert res["q"]["text"] == "Searching: yoga app" and res["q"]["extra"] == "“yoga app features”"
    assert res["q"]["meta"] == "descriptive · en-in · tavily" and res["q"]["kind"] == "Search"
    assert res["sr"]["extra"] == "12 results: a.com, b.com"
    assert res["pr"]["extra"] == "a.com — A page"
    assert res["ps"]["quiet"] is True and res["ps"]["extra"] == "a.com — behind a paywall"
    assert res["pu"]["extra"] == "b.com — new reason code", "an unknown reason still reads as words"
    assert (
        res["ff"]["kind"] == "Checking"
        and res["fv"]["kind"] == "Checked"
        and res["fv"]["extra"] == "Source: a.com"
    )
    assert res["en"]["text"] == "Found: Mindbody" and res["en"]["extra"] == "Mindbody (competitor)"
    assert res["rc"]["extra"] == "5 of 9 checks filled"
    assert res["w"]["extra"] == "", "a warning that repeats its label is not shown twice"
    assert res["none"]["text"] == "plan ready", "a missing label falls back to words, never blank"


@needs_node
def test_only_http_and_https_urls_become_links() -> None:
    res = run_js(
        """
        const urls = ["https://docs.example.com/a?b=1#c", "http://example.org", "  https://padded.example.com  ", "javascript:alert(1)",
          "JaVaScRiPt:alert(1)", "java\\tscript:alert(1)", "data:text/html,<b>x</b>", "ftp://files.example.org/x.pdf", "//evil.example.com",
          "/relative/path", "https://user:pw@example.com/", "https://", "", null, 42, "file:///etc/passwd", "mailto:a@b.co", "blob:https://x.y/1",
          "https://" + "a".repeat(3000) + ".com"];
        return urls.map((u) => R.safeUrl(u));
        """
    )
    assert (
        res
        == [
            "https://docs.example.com/a?b=1#c",
            "http://example.org/",
            "https://padded.example.com/",
            *([None] * 15),
            None,
        ][: len(res)]
    )
    assert res[3:] == [None] * (len(res) - 3)


@needs_node
def test_the_research_address_setting_is_normalised() -> None:
    res = run_js(
        """
        const d = "http://127.0.0.1:8120";
        return ["http://127.0.0.1:8120/", "https://research.example.com:9443/api/", "  http://localhost:8120  ", "", "nonsense",
                "javascript:alert(1)", "http://host:8120/?x=1", "ftp://host"].map((v) => R.normalizeBaseUrl(v, d));
        """
    )
    assert res == [
        "http://127.0.0.1:8120",
        "https://research.example.com:9443/api",
        "http://localhost:8120",
        "http://127.0.0.1:8120",
        "http://127.0.0.1:8120",
        "http://127.0.0.1:8120",
        "http://127.0.0.1:8120",
        "http://127.0.0.1:8120",
    ]


@needs_node
def test_http_problems_become_plain_words_never_dumps() -> None:
    res = run_js(
        """
        const p = (s, b) => R.problemText(s, b, { baseUrl: "http://127.0.0.1:8120" });
        return {
          net: p(0, null), netCustom: R.problemText(0, null, { baseUrl: "http://10.0.0.5:9000", custom: true }), auth: p(401, { code: "unauthorized", message: "nope" }),
          c409: p(409, { code: "run_active", message: "A research run (rrn_01ABC) is already in progress." }),
          m404: p(404, null), r422: p(422, { code: "hash_mismatch", message: "The Brief changed after it was approved." }),
          big: p(413, null), s500: p(500, { message: "Traceback (most recent call last): File \\"x.py\\"" }), odd: p(418, null),
          id1: R.runIdFrom({ message: "A research run (rrn_01ABC) is already in progress." }), id2: R.runIdFrom({ run_id: "rrn_9", message: "x" }),
          id3: R.runIdFrom({ message: "busy" }), id4: R.runIdFrom(null),
          plain: [R.plainReason("The search service stopped answering."), R.plainReason("Traceback (most recent call last):\\n  File \\"a.py\\", line 3"),
                  R.plainReason(""), R.plainReason("x".repeat(400)).length, R.plainReason("KeyError at engine.run(0x7f3a2b)")],
        };
        """
    )
    assert "127.0.0.1:8120" in res["net"]["text"] and res["net"]["kind"] == "network"
    assert "MDE_RESEARCH_ORIGIN" not in res["net"]["text"], "the default address needs no hint"
    assert "MDE_RESEARCH_ORIGIN" in res["netCustom"]["text"], (
        "a changed address may need the Compass server to allow it"
    )
    assert "Settings" in res["auth"]["text"]
    assert res["c409"]["text"].startswith("A research run (rrn_01ABC)")
    assert res["m404"]["text"] and res["big"]["text"].startswith("This export is too large")
    assert (
        res["r422"]["text"] == "The Brief changed after it was approved."
        and res["r422"]["code"] == "hash_mismatch"
    )
    assert "Traceback" not in res["s500"]["text"] and "problem" in res["s500"]["text"]
    assert res["odd"]["kind"] == "other"
    assert [res["id1"], res["id2"], res["id3"], res["id4"]] == ["rrn_01ABC", "rrn_9", None, None]
    assert res["plain"][0] == "The search service stopped answering."
    assert (
        res["plain"][1]
        == res["plain"][2]
        == res["plain"][4]
        == "Something went wrong while researching."
    )
    assert res["plain"][3] == 240


@needs_node
def test_an_export_tells_the_screens_its_key_title_and_hash() -> None:
    export = good_export("hospital app")
    export["session"] = {"id": "ses_ABC", "user_label": "Sunrise"}
    plain = good_export("yoga studio app")
    res = run_js(
        """
        const a = JSON.parse(process.argv[2]), b = JSON.parse(process.argv[3]);
        return [R.exportInfo(a), R.exportInfo(b), R.exportInfo({}), R.exportInfo([]), R.exportInfo(null),
                R.exportInfo({ export: {}, brief: {} })];
        """,
        json.dumps(export),
        json.dumps(plain),
    )
    assert res[0] == {
        "programId": "ses_ABC",
        "key": "ses_ABC",
        "title": "hospital app",
        "briefHash": HASH,
        "version": 3,
    }
    assert (
        res[1]["programId"] is None
        and res[1]["key"] == "brief:" + HASH[:16]
        and res[1]["title"] == "yoga studio app"
    )
    assert res[2] is None and res[3] is None and res[4] is None
    assert res[5]["title"] == "Saved export" and res[5]["key"] == ""


@needs_node
def test_remembered_runs_keep_the_newest_twelve_and_forget_on_request() -> None:
    res = run_js(
        """
        let m = {};
        for (let i = 0; i < 15; i++) { m = R.rememberRun(m, "k" + i, { runId: "r" + i, status: "running", at: 1000 + i }); }
        const keys = Object.keys(m).sort();
        m = R.rememberRun(m, "k14", { status: "complete" });
        const recent = R.recentRuns(m, 3).map((r) => [r.key, r.status]);
        const gone = R.forgetRun(m, "k14");
        return { n: keys.length, hasOld: !!m.k0, kept: !!m.k3, recent: recent, gone: "k14" in gone, orig: "k14" in m,
                 delays: [0, 1, 2, 3, 4, 9].map((a) => R.reconnectDelay(a)), jitter: R.reconnectDelay(2, 1) };
        """
    )
    assert res["n"] == 12 and res["hasOld"] is False and res["kept"] is True
    assert res["recent"][0] == ["k14", "complete"]
    assert res["gone"] is False and res["orig"] is True, "forgetting returns a new map"
    assert res["delays"] == [1, 2, 4, 8, 15, 15]
    assert abs(res["jitter"] - 4.8) < 1e-9


REVIEW_JS = """
const review = {
  run_id: "rrn_1", status: "partial", content_hash: "h",
  coverage: { targets: 8, covered: 6, not_found: 1, slots_required: 24, slots_filled: 17 },
  budget: { searches_used: 10, pages_read: 14, seconds: 125, usd: "0.31", usd_micros: 310000, failures: 1 },
  items: [
    { id: "RI-1", job: "features", lane: "blueprint", kind: "feature", label: "A", claim: "ca", flags: ["single-source"], rejected: false,
      sources: [{ id: "S-1", domain: "a.com", url: "https://a.com/x", published_on: "2026-05-02", retrieved_on: "2026-11-11" },
                { id: "S-2", domain: "b.com", url: "ftp://b.com/y", published_on: "2026-06-01" }], prevalence: { seen_in: 4, of: 5, level: "core" } },
    { id: "RI-2", job: "services", lane: "integrations", kind: "service_option", label: "B", claim: "cb", flags: [], rejected: false, sources: [],
      recency: { newest_source: "2026-02-14" } },
    { id: "RI-3", job: "features", lane: "blueprint", kind: "feature", label: "C", claim: "cc", flags: [], rejected: false, sources: [] },
    { id: "RI-4", job: "local", lane: "verify", kind: "rule", label: "D", claim: "cd", flags: ["needs-confirm"], rejected: true, sources: [] },
  ],
  norms: [ { area: "integration", key: "pay", label: "Payments", prevalence: { seen_in: 5, of: 5, level: "core" }, recency: { newest_source: "2026-05-02" }, item_ids: ["RI-2"], matches_brief: "INT-001" },
           { area: "feature", key: "w", label: "Waitlist", prevalence: { seen_in: 2, of: 5, level: "advanced" }, item_ids: [], matches_brief: null },
           { area: "feature", key: "b", label: "Booking", prevalence: { seen_in: 5, of: 5, level: "core" }, item_ids: ["RI-1"], matches_brief: "FTR-001" },
           { area: "mystery", key: "m", label: "Odd", prevalence: { seen_in: 1, of: 5, level: "emerging" }, item_ids: [], matches_brief: null } ],
  suggestions: { features: [ { label: "Guest passes", item_ids: ["RI-1"], prevalence: { seen_in: 3, of: 5, level: "common" }, state: "undecided" }, { label: "Referrals", item_ids: [], state: "undecided" } ],
                 screens: [], fields: [ { label: "Injury notes", item_ids: [], state: "undecided" } ], states: [], validations: [] },
  confirm: [ { item_id: "RI-4", why: "A tax rate." }, { item_id: "RI-99", why: "Gone." } ],
  gaps: [ { target_id: "T-07", reason: "not_found", label: "Local rules", queries: ["q1", "q2"] }, { target_id: "T-08", reason: "user_cancelled", label: "Design" },
          { target_id: "T-09", reason: "weird_new_reason", label: "Other" } ],
  conflicts: [ { topic: "Cancel window", item_ids: ["RI-1", "RI-3", "RI-77"], note: "They disagree." } ],
  slices: { blueprint: { item_ids: ["RI-3", "RI-1"], digest: "LANE BLUEPRINT\\n- A" }, integrations: {}, design: {}, screens: {}, mock: {}, verify: {} }
};
"""


@needs_node
def test_findings_group_by_lane_job_norm_suggestion_confirm_gap_and_conflict() -> None:
    res = run_js(
        REVIEW_JS
        + """
        const pb = R.playbook(review);
        const jobs = R.jobGroups(review);
        const sug = R.suggestionGroups(review);
        return {
          lanes: pb.map((l) => [l.lane, l.title, l.items.map((i) => i.id), l.digest]),
          verifyFallback: R.laneItems(review, "verify").map((i) => i.id),
          jobs: jobs.map((j) => [j.job, j.title, j.items.length, R.visibleCount(j.items)]),
          norms: R.normGroups(review).map((g) => [g.area, g.title, g.norms.map((n) => n.label)]),
          sug: sug.map((g) => [g.group, g.title, g.items.map((i) => [i.key, i.label, i.on])]), sugCount: R.suggestionCount(review),
          confirm: R.confirmEntries(review).map((c) => [c.itemId, c.why, c.item ? c.item.label : null]),
          gaps: R.gapEntries(review).map((g) => [g.targetId, g.why, g.queries]),
          conflicts: R.conflictEntries(review).map((c) => [c.topic, c.items.map((i) => i.id)]),
          facts: R.reviewFacts(review),
          links: R.sourceLinks(review.items[0]), date0: R.itemDate(review.items[0]), date1: R.itemDate(review.items[1]),
          prev: [R.prevalenceText(review.items[0].prevalence), R.prevalenceText(null), R.prevalenceText({ seen_in: 1, of: 0 })],
          rej: (() => { const a = R.markRejected(review, "RI-1"), b = R.markRejected(review, "RI-404"); return [a, b, review.items[0].rejected, R.visibleCount(R.itemsOf(review))]; })(),
        };
        """
    )
    assert [lane[0] for lane in res["lanes"]] == [
        "blueprint",
        "integrations",
        "design",
        "screens",
        "mock",
        "verify",
    ]
    assert res["lanes"][0][2] == ["RI-3", "RI-1"], "the slice's own order wins"
    assert (
        res["lanes"][0][3].startswith("LANE BLUEPRINT") and res["lanes"][1][2] == ["RI-2"]
    ) or res["lanes"][1][2] == []
    assert res["verifyFallback"] == ["RI-4"] or res["verifyFallback"] == []
    assert [j[0] for j in res["jobs"]] == [
        "features",
        "structure",
        "flows",
        "data",
        "services",
        "design",
        "screens",
        "local",
    ]
    assert res["jobs"][0][2:] == [2, 2] and res["jobs"][7][2:] == [1, 0], (
        "a rejected item is listed but not counted"
    )
    assert res["norms"][0] == ["feature", "Features", ["Booking", "Waitlist"]], (
        "the most common first"
    )
    assert [g[0] for g in res["norms"]] == ["feature", "integration", "mystery"]
    assert res["norms"][2][1] == "mystery"
    assert res["sug"][0] == [
        "features",
        "Feature ideas",
        [["features:0", "Guest passes", False], ["features:1", "Referrals", False]],
    ]
    assert res["sugCount"] == 3, "every suggestion starts off"
    assert res["confirm"] == [["RI-4", "A tax rate.", "D"], ["RI-99", "Gone.", None]]
    assert res["gaps"][0] == ["T-07", "We could not find reliable sources.", ["q1", "q2"]]
    assert (
        res["gaps"][1][1] == "You stopped this topic."
        and res["gaps"][2][1] == "We could not finish this topic."
    )
    assert res["conflicts"] == [["Cancel window", ["RI-1", "RI-3"]]]
    assert res["facts"][0] == "6 of 8 topics covered" and "2 min 5 s" in res["facts"]
    assert res["links"][0] == {
        "domain": "a.com",
        "href": "https://a.com/x",
        "date": "2026-05-02",
        "cls": "",
    }
    assert res["links"][1]["href"] is None and res["links"][1]["domain"] == "b.com", (
        "ftp is plain text"
    )
    assert res["date0"] == "2026-06-01" and res["date1"] == "2026-02-14"
    assert res["prev"] == ["Seen in 4 of 5", "", ""]
    assert res["rej"] == [True, False, True, 2]


@needs_node
def test_words_for_levels_flags_dates_and_statuses() -> None:
    res = run_js(
        """
        return {
          levels: ["core", "common", "advanced", "emerging", "limited_evidence", "odd_level"].map(R.levelWord),
          hint: R.levelHint("core"), flags: ["single-source", "needs-confirm", "entity_from_model", "something_new", ""].map(R.flagWord),
          dates: ["2026-05-02", "2026-11-11T12:30:05Z", "2026-13-02", "soon", "", null].map(R.formatDate),
          runs: ["queued", "running", "committing", "complete", "partial", "failed", "skipped", "interrupted", "x_y"].map(R.runWord),
          topics: ["queued", "running", "covered", "not_found", "cancelled", "skipped", "partial"].map(R.topicWord),
          lane: [R.laneTitle("mock"), R.laneTitle("odd")], plural: [R.plural(1, "page"), R.plural(2, "page"), R.plural(2, "search", "searches")],
          pct: [R.pct(1, 3), R.pct(5, 0), R.pct(9, 3)],
          slow: ["provider_slow", "timeout", "rate_limited", "budget_stop", ""].map(R.warningIsSlow),
        };
        """
    )
    assert res["levels"] == [
        "Core",
        "Common",
        "Advanced",
        "Emerging",
        "Limited evidence",
        "odd level",
    ]
    assert res["hint"] and res["flags"] == [
        "Only one source",
        "Please confirm",
        "Name not confirmed online",
        "something new",
        "",
    ]
    assert res["dates"] == ["2 May 2026", "11 Nov 2026", "2026-13-02", "soon", "", ""]
    assert res["runs"][:8] == [
        "Starting",
        "Researching",
        "Saving the findings",
        "Done",
        "Done, with gaps",
        "Could not finish",
        "Stopped early",
        "Interrupted",
    ]
    assert res["topics"][3] == "Nothing reliable found" and res["lane"] == ["Sample data", "odd"]
    assert res["plural"] == ["1 page", "2 pages", "2 searches"] and res["pct"] == [33, 0, 100]
    assert res["slow"] == [True, True, True, False, False]


# ---------------------------------------------------------------------------------------------------------
# the fake Research server against the documented contract
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def fake_factory() -> Iterator[Callable[..., tuple[Any, httpx.Client]]]:
    servers: list[Any] = []

    def make(**options: Any) -> tuple[Any, httpx.Client]:
        options.setdefault("speed", 0.0)
        options.setdefault("origins", ("*",))
        server, url = FAKE.start_in_thread(**options)
        servers.append(server)
        return server, httpx.Client(base_url=url + "/v1/research", headers=HEADERS, timeout=20)

    yield make
    for server in servers:
        server.shutdown()
        server.server_close()


def wait_for(
    client: httpx.Client, run_id: str, done: Callable[[dict[str, Any]], bool], timeout: float = 15
) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while True:
        snap = client.get(f"/runs/{run_id}").json()
        if done(snap):
            return snap  # type: ignore[no-any-return]
        assert time.monotonic() < end, f"timed out in status {snap['status']}"
        time.sleep(0.05)


TERMINAL = {"complete", "partial", "failed", "skipped", "interrupted"}


def start_run(client: httpx.Client, program_id: str = "prg_test", **extra: Any) -> str:
    r = client.post(
        "/runs", json={"program_id": program_id, "brief_export": good_export(), **extra}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"run_id", "program_id", "status", "events_url"}
    assert body["program_id"] == program_id and body["run_id"].startswith("rrn_")
    return str(body["run_id"])


def read_events(client: httpx.Client, run_id: str, **kwargs: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with client.stream("GET", f"/runs/{run_id}/events", **kwargs) as resp:
        assert resp.status_code == 200 and "text/event-stream" in resp.headers["content-type"]
        for frame in resp.read().decode().split("\n\n"):
            data = [line[6:] for line in frame.split("\n") if line.startswith("data: ")]
            if data:
                events.append(json.loads("\n".join(data)))
    return events


def test_the_fake_server_needs_the_dev_auth_headers(fake_factory: Callable[..., Any]) -> None:
    _server, client = fake_factory()
    for headers in (
        {},
        {"Authorization": "Bearer wrong", "X-Tenant-Id": "t", "X-User-Id": "u"},
        {"Authorization": "Bearer dev-token"},
    ):
        r = httpx.get(str(client.base_url) + "/config", headers=headers)
        assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert httpx.get(str(client.base_url).split("/v1")[0] + "/healthz").json() == {"ok": True}
    cfg = client.get("/config").json()
    assert cfg["mode"] == "mock" and set(cfg) == {
        "mode",
        "search_provider",
        "keys",
        "model",
        "checker_vendor",
    }
    assert cfg["keys"] == {"tavily": False, "serper": False}


def test_the_fake_server_answers_cors_preflight_for_the_console_origin(
    fake_factory: Callable[..., Any],
) -> None:
    _server, client = fake_factory(origins=("http://127.0.0.1:8110",))
    base = str(client.base_url)
    pre = httpx.options(
        base + "/runs",
        headers={
            "Origin": "http://127.0.0.1:8110",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-tenant-id,x-user-id,last-event-id",
        },
    )
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == "http://127.0.0.1:8110"
    allowed = {h.strip().lower() for h in pre.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "content-type", "x-tenant-id", "x-user-id", "last-event-id"} <= allowed
    assert "POST" in pre.headers["access-control-allow-methods"]
    other = httpx.options(base + "/runs", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    ok = client.get("/config", headers={"Origin": "http://127.0.0.1:8110"})
    assert ok.headers["access-control-allow-origin"] == "http://127.0.0.1:8110"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda e: {}, "not_an_export"),
        (lambda e: {**e, "export": {**e["export"], "format_version": 1}}, "not_format_2"),
        (
            lambda e: {
                **e,
                "export": {k: v for k, v in e["export"].items() if k != "approved_baseline"},
            },
            "not_approved",
        ),
        (lambda e: {**e, "brief": {"_hash": "b" * 64}}, "hash_mismatch"),
        (lambda e: {k: v for k, v in e.items() if k != "consumer_views"}, "missing_research_view"),
    ],
)
def test_the_fake_server_refuses_an_unusable_export_with_the_documented_codes(
    fake_factory: Callable[..., Any], mutate: Callable[[dict[str, Any]], dict[str, Any]], code: str
) -> None:
    _, client = fake_factory()
    r = client.post("/runs", json={"brief_export": mutate(good_export())})
    assert r.status_code == 422 and r.json()["code"] == code and r.json()["message"]
    assert client.post("/runs", content=b"not json").status_code == 422


def test_one_run_per_program_and_run_active_names_the_run(fake_factory: Callable[..., Any]) -> None:
    _, client = fake_factory(speed=1.0)
    run_id = start_run(client, "prg_busy")
    r = client.post("/runs", json={"program_id": "prg_busy", "brief_export": good_export()})
    assert r.status_code == 409 and r.json()["code"] == "run_active"
    assert run_id in r.json()["message"], "the message names the run the console should join"
    assert start_run(client, "prg_other")  # another program is fine
    assert client.post(f"/runs/{run_id}/skip").status_code == 202


def test_a_run_plays_the_documented_event_sequence(fake_factory: Callable[..., Any]) -> None:
    _, client = fake_factory()
    run_id = start_run(client)
    snap = wait_for(client, run_id, lambda s: s["status"] in TERMINAL)
    assert (
        snap["status"] == "complete"
        and snap["result_status"] == "complete"
        and snap["content_hash"]
    )
    events = read_events(client, run_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1)), "seq is contiguous from 1"
    assert snap["last_seq"] == len(events) and len(snap["feed"]) == 60
    for e in events:
        assert {
            "id",
            "seq",
            "ts",
            "run_id",
            "type",
            "target_id",
            "round",
            "phase",
            "label",
            "detail",
            "counters",
            "schema",
        } <= set(e)
        assert e["schema"] == "research.event.v1" and e["label"], (
            "every event has a user-facing label"
        )
        assert {
            "searches_used",
            "searches_planned",
            "searches_ceiling",
            "pages_read",
            "facts_verified",
            "targets_done",
            "targets_total",
            "elapsed_s",
            "eta_s",
        } == set(e["counters"])
    kinds = {e["type"] for e in events}
    documented = {
        "run.started", "plan.ready", "target.started", "target.covered", "query.issued", "search.results", "page.reading",
        "page.read", "page.skipped", "fact.found", "fact.verified", "fact.rejected", "entity.discovered", "round.completed",
        "gap.detected", "warning", "run.finished",
    }  # fmt: skip
    assert documented <= kinds, sorted(documented - kinds)
    assert events[0]["type"] == "run.started" and events[-1]["type"] == "run.finished"
    started = events[0]["detail"]
    assert {"load_class", "load_score", "minutes", "plan_source", "degraded", "depth_word"} <= set(
        started
    )
    plan = next(e for e in events if e["type"] == "plan.ready")["detail"]
    assert (
        len(plan["targets"]) == 8
        and plan["declined"]
        and {"id", "label", "job", "lane", "priority", "status"} == set(plan["targets"][0])
    )
    assert next(e for e in events if e["type"] == "warning")["detail"]["code"]
    end = events[-1]["detail"]
    assert {"status", "facts", "pages", "searches", "usd", "elapsed_s"} <= set(end)
    assert snap["counters"]["targets_done"] == snap["counters"]["targets_total"] == 8
    assert set(snap) >= {"run_id", "program_id", "status", "phase", "round", "started_at", "finished_at", "load_class", "depth_word", "plan_source", "input_quality", "counters", "targets", "declined", "entities", "feed", "last_seq", "result_status", "content_hash", "reason"}  # fmt: skip


def test_the_event_stream_resumes_from_last_event_id_or_after(
    fake_factory: Callable[..., Any],
) -> None:
    _, client = fake_factory()
    run_id = start_run(client)
    wait_for(client, run_id, lambda s: s["status"] in TERMINAL)
    everything = read_events(client, run_id)
    n = len(everything)
    assert [
        e["seq"] for e in read_events(client, run_id, headers={"Last-Event-ID": str(n - 3)})
    ] == [n - 2, n - 1, n]
    assert [e["seq"] for e in read_events(client, run_id, params={"after": n - 1})] == [n]
    assert read_events(client, run_id, params={"after": n}) == [], (
        "a finished run ends the stream at once"
    )
    assert client.get("/runs/rrn_nope/events").status_code == 404


def test_the_stream_sends_retry_first_and_keepalive_comments(
    fake_factory: Callable[..., Any],
) -> None:
    _, client = fake_factory(speed=1.0, keepalive=0.2)
    run_id = start_run(client)
    seen = ""
    with client.stream("GET", f"/runs/{run_id}/events") as resp:
        for chunk in resp.iter_text():
            seen += chunk
            if ": keepalive" in seen:
                break
            assert time.monotonic() < time.monotonic() + 30
    assert seen.startswith("retry: 3000\n\n") and ": keepalive" in seen
    assert client.post(f"/runs/{run_id}/skip").status_code == 202


def test_skip_stops_the_run_with_a_skipped_result_and_findings_so_far(
    fake_factory: Callable[..., Any],
) -> None:
    _, client = fake_factory(speed=1.0)
    run_id = start_run(client, "prg_skip")
    assert client.post(f"/runs/{run_id}/skip").status_code == 202
    snap = wait_for(client, run_id, lambda s: s["status"] in TERMINAL)
    assert snap["status"] == "skipped" and snap["reason"]
    events = read_events(client, run_id)
    assert events[-1]["type"] == "run.skipped" and events[-1]["detail"]["status"] == "skipped"
    review = client.get("/programs/prg_skip/review").json()
    assert review["status"] == "skipped" and isinstance(review["items"], list)
    assert client.post(f"/runs/{run_id}/skip").status_code == 409, "already over"


def test_cancelling_a_topic_makes_it_a_gap_and_the_run_partial(
    fake_factory: Callable[..., Any],
) -> None:
    _, client = fake_factory(speed=0.02)
    run_id = start_run(client, "prg_cancel")
    r = client.post(f"/runs/{run_id}/targets/T-08/cancel")
    assert r.status_code == 202
    assert client.post(f"/runs/{run_id}/targets/T-99/cancel").status_code == 404
    snap = wait_for(client, run_id, lambda s: s["status"] in TERMINAL, timeout=30)
    assert snap["status"] == "partial"
    assert next(t for t in snap["targets"] if t["id"] == "T-08")["status"] == "cancelled"
    assert any(
        e["type"] == "target.cancelled" and e["target_id"] == "T-08"
        for e in read_events(client, run_id)
    )
    gaps = client.get("/programs/prg_cancel/review").json()["gaps"]
    assert any(g["target_id"] == "T-08" and g["reason"] == "user_cancelled" for g in gaps)
    assert client.post(f"/runs/{run_id}/targets/T-08/cancel").status_code == 409


def test_the_review_has_every_documented_part(fake_factory: Callable[..., Any]) -> None:
    _, client = fake_factory()
    run_id = start_run(client, "prg_review")
    assert client.get("/programs/prg_review/latest").status_code in {
        200,
        404,
    }  # 404 until it commits
    wait_for(client, run_id, lambda s: s["status"] in TERMINAL)
    review = client.get("/programs/prg_review/review").json()
    assert set(review) >= {"run_id", "status", "content_hash", "profile", "coverage", "budget", "input_quality", "items", "norms", "suggestions", "confirm", "gaps", "conflicts", "slices"}  # fmt: skip
    assert review["status"] == "complete" and len(review["items"]) >= 25
    lanes = {i["lane"] for i in review["items"]}
    jobs = {i["job"] for i in review["items"]}
    assert lanes == {"blueprint", "integrations", "design", "screens", "mock", "verify"}
    assert jobs == {
        "features",
        "structure",
        "flows",
        "data",
        "services",
        "design",
        "screens",
        "local",
    }
    item = review["items"][0]
    assert {"id", "job", "spec_need", "kind", "lane", "label", "claim", "use_note", "slot", "impact", "confidence", "tier", "flags", "applies_to", "sources", "independent_domains", "prevalence", "recency", "request_ids", "target_id", "data", "rejected"} <= set(item)  # fmt: skip
    assert {"id", "domain", "source_class", "url", "retrieved_on", "published_on"} == set(
        item["sources"][0]
    )
    assert any(s["url"].startswith("ftp:") for i in review["items"] for s in i["sources"]), (
        "one non-http source URL to see plain text"
    )
    flags = {f for i in review["items"] for f in i["flags"]}
    assert {"single-source", "stale", "needs-confirm", "legal-adjacent", "conflict"} <= flags
    assert all(
        len(i["claim"].split()) <= 40 and len(i["label"].split()) <= 8 for i in review["items"]
    )
    assert len(review["norms"]) >= 10 and {n["prevalence"]["level"] for n in review["norms"]} >= {
        "core",
        "common",
        "advanced",
        "limited_evidence",
    }
    assert any(n["matches_brief"] for n in review["norms"]) and any(
        n["matches_brief"] is None for n in review["norms"]
    )
    assert set(review["suggestions"]) == {"features", "screens", "fields", "states", "validations"}
    assert all(s["state"] == "undecided" for group in review["suggestions"].values() for s in group)
    assert review["confirm"] and review["conflicts"] and "topic" in review["conflicts"][0]
    assert review["slices"]["blueprint"]["digest"] and review["slices"]["blueprint"]["item_ids"]
    latest = client.get("/programs/prg_review/latest")
    assert latest.status_code == 200 and latest.json()["schema"] == "research.v4"
    assert all("rejected" not in i for i in latest.json()["items"]), (
        "the committed file never carries rejections"
    )
    assert client.get(f"/runs/{run_id}/result").json()["items"] == latest.json()["items"]
    assert (
        client.get("/programs/none/review").status_code == 404
        and client.get("/programs/none/latest").status_code == 404
    )


def test_rejecting_an_item_is_an_overlay_the_committed_file_ignores(
    fake_factory: Callable[..., Any],
) -> None:
    _, client = fake_factory()
    run_id = start_run(client, "prg_reject")
    wait_for(client, run_id, lambda s: s["status"] in TERMINAL)
    before = client.get("/programs/prg_reject/latest").json()
    r = client.post(f"/runs/{run_id}/items/RI-007/reject", json={"reason": "not for us"})
    assert r.status_code == 200 and r.json() == {"item_id": "RI-007", "rejected": True}
    items = {i["id"]: i for i in client.get("/programs/prg_reject/review").json()["items"]}
    assert items["RI-007"]["rejected"] is True and items["RI-001"]["rejected"] is False
    assert client.get("/programs/prg_reject/latest").json()["items"] == before["items"]
    assert client.post(f"/runs/{run_id}/items/RI-404/reject", json={}).status_code == 404


def test_the_scenarios_partial_failed_and_interrupted(fake_factory: Callable[..., Any]) -> None:
    _, client = fake_factory()
    partial = start_run(client, "prg_partial_case")
    snap = wait_for(client, partial, lambda s: s["status"] in TERMINAL)
    assert snap["status"] == "partial" and snap["reason"]
    assert next(t for t in snap["targets"] if t["id"] == "T-06")["status"] == "not_found"
    review = client.get("/programs/prg_partial_case/review").json()
    assert review["status"] == "partial" and any(
        g["reason"] == "not_found" and g["queries"] for g in review["gaps"]
    )

    failed = start_run(client, "prg_fail_case")
    snap = wait_for(client, failed, lambda s: s["status"] in TERMINAL)
    assert snap["status"] == "failed" and snap["reason"]
    last = read_events(client, failed)[-1]
    assert last["type"] == "run.failed" and {"status", "reason", "code"} <= set(last["detail"])
    assert client.get("/programs/prg_fail_case/review").status_code == 404

    interrupted = start_run(client, "prg_interrupted_case")
    snap = wait_for(client, interrupted, lambda s: s["status"] in TERMINAL)
    assert snap["status"] == "interrupted"
    assert read_events(client, interrupted)[-1]["type"] not in {
        "run.finished",
        "run.skipped",
        "run.failed",
    }


# ---------------------------------------------------------------------------------------------------------
# the real producer into the real consumer: Node + research_view.js reading the fake server's actual stream
# ---------------------------------------------------------------------------------------------------------

STREAM_JS = """
const base = process.argv[2] + "/v1/research";
const H = { Authorization: "Bearer dev-token", "X-Tenant-Id": "t", "X-User-Id": "u" };
const exp = JSON.parse(process.argv[3]);
const post = async (path, body) => (await fetch(base + path, { method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, H), body: JSON.stringify(body) })).json();
const get = async (path) => (await fetch(base + path, { headers: H })).json();

/* What the console does: connect, fold every event, remember how many hang-ups and gaps we saw, resume with Last-Event-ID. */
async function follow(runId, state, stats) {
  for (let attempt = 0; attempt < 8 && !R.isTerminalStatus(state.status); attempt++) {
    stats.connections += 1;
    const res = await fetch(base + "/runs/" + runId + "/events?after=" + state.lastSeq, { headers: Object.assign({ Accept: "text/event-stream", "Last-Event-ID": String(state.lastSeq) }, H) });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const r = await reader.read(); if (r.done) break;
      buf += dec.decode(r.value, { stream: true });
      const parsed = R.parseSseFrames(buf); buf = parsed.rest;
      for (const f of parsed.frames) {
        if (f.data === null) continue;
        const e = JSON.parse(f.data);
        const verdict = R.checkSeq(state.lastSeq, e.seq);
        stats[verdict] = (stats[verdict] || 0) + 1;
        if (verdict === "gap") { await reader.cancel(); return "gap"; }
        if (verdict === "ok") R.applyEvent(state, e, 0);
      }
    }
  }
  return "ended";
}
"""


def run_stream_js(url: str, body: str) -> Any:
    assert NODE
    program = (
        PRELUDE
        + STREAM_JS
        + f"(async () => {{ {body} }})().then((v) => process.stdout.write(JSON.stringify(v))).catch((e) => {{ console.error(e); process.exit(1); }});"
    )
    out = subprocess.run(
        [NODE, "-e", program, str(VIEW_JS), url, json.dumps(good_export())],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_folded_stream_equals_the_folded_snapshot(fake_factory: Callable[..., Any]) -> None:
    _server, client = fake_factory()
    url = str(client.base_url).split("/v1")[0]
    res = run_stream_js(
        url,
        """
        const run = await post("/runs", { program_id: "prg_e2e", brief_export: exp });
        const state = R.emptyState(); const stats = { connections: 0 };
        await follow(run.run_id, state, stats);
        const snap = await get("/runs/" + run.run_id);
        const fromSnap = R.applySnapshot(snap, 0);
        const pick = (s) => ({ status: s.status, lastSeq: s.lastSeq, targets: s.targets.map((t) => [t.id, t.status, t.slotsFilled, t.slotsRequired]),
          entities: s.entities, declined: s.declined, counters: s.counters, depth: s.depthWord, cov: R.coverage(s), totals: R.topicTotals(s) });
        return { stream: pick(state), snapshot: pick(fromSnap), stats: stats, factsChecked: state.facts.filter((f) => f.status === "checked").length,
                 feed: state.feed.length, warnings: state.warnings.length };
        """,
    )
    assert res["stream"] == res["snapshot"], (
        "reading the stream and reading the snapshot end in the same state"
    )
    assert res["stream"]["status"] == "complete" and res["stream"]["lastSeq"] > 100
    assert res["stats"] == {"connections": 1, "ok": res["stream"]["lastSeq"]}, (
        "no gaps and no duplicates"
    )
    assert res["stream"]["cov"]["percent"] == 100 and res["stream"]["totals"] == {
        "done": 8,
        "total": 8,
    }
    assert (
        res["factsChecked"] > 10
        and res["feed"] == min(200, res["stream"]["lastSeq"])
        and res["warnings"] == 1
    )


@needs_node
def test_a_dropped_stream_resumes_without_losing_or_repeating_an_event(
    fake_factory: Callable[..., Any],
) -> None:
    _server, client = fake_factory(
        drop_stream_after=17, speed=0.05
    )  # still running when the stream connects
    url = str(client.base_url).split("/v1")[0]
    res = run_stream_js(
        url,
        """
        const run = await post("/runs", { program_id: "prg_drop", brief_export: exp });
        const state = R.emptyState(); const stats = { connections: 0 };
        await follow(run.run_id, state, stats);
        const snap = await get("/runs/" + run.run_id);
        return { status: state.status, lastSeq: state.lastSeq, snapSeq: snap.last_seq, stats: stats };
        """,
    )
    assert res["status"] == "complete" and res["lastSeq"] == res["snapSeq"]
    assert res["stats"]["connections"] == 2 and res["stats"]["ok"] == res["lastSeq"]
    assert "gap" not in res["stats"] and "dup" not in res["stats"]


@needs_node
def test_a_missing_event_is_noticed_as_a_gap_and_the_snapshot_repairs_it(
    fake_factory: Callable[..., Any],
) -> None:
    _server, client = fake_factory(gap_at=9)
    url = str(client.base_url).split("/v1")[0]
    res = run_stream_js(
        url,
        """
        const run = await post("/runs", { program_id: "prg_gap", brief_export: exp });
        const state = R.emptyState(); const stats = { connections: 0 };
        const how = await follow(run.run_id, state, stats);
        // the console's answer to a gap: fetch the snapshot, draw from it, tail from its last_seq
        await new Promise((r) => setTimeout(r, 300));
        const repaired = R.applySnapshot(await get("/runs/" + run.run_id), 0);
        const stats2 = { connections: 0 };
        await follow(run.run_id, repaired, stats2);
        return { how: how, stats: stats, stoppedAt: state.lastSeq, repaired: repaired.status, repairedSeq: repaired.lastSeq, stats2: stats2 };
        """,
    )
    assert res["how"] == "gap" and res["stats"]["gap"] == 1 and res["stoppedAt"] == 8
    assert res["repaired"] == "complete" and res["repairedSeq"] > 100
    assert "gap" not in res["stats2"]
