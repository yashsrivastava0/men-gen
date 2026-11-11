"""The pure helpers of the Research console (``apps/research-console/lib/*.js``) run under Node.

Each test hands a small ES-module program to ``node --input-type=module -e`` and compares its JSON output. The
helpers are DOM-free (the stream reader, the event reducer, formatters, sanitizers, virtual-list math, filters,
budget arithmetic, the API wrapper with an injected fetch), so they run in milliseconds. Skipped without Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

PRELUDE = f"""
const LIB = {json.dumps(LIB.as_uri())};
const load = (name) => import(LIB + "/" + name + ".js");
const out = (v) => process.stdout.write(JSON.stringify(v));
const enc = new TextEncoder();
const streamOf = (chunks) => new ReadableStream({{ start(c) {{ for (const ch of chunks) c.enqueue(enc.encode(ch)); c.close(); }} }});
const ev = (seq, type, extra) => Object.assign({{ id: "evt_" + seq, seq: seq, ts: "2026-11-11T10:00:" + String(seq % 60).padStart(2, "0") + "Z", run_id: "rrn_1", type: type, label: type + " " + seq, detail: {{}}, counters: {{ searches_used: seq, pages_read: 0, facts_verified: 0, targets_done: 0, targets_total: 3, elapsed_s: seq, eta_s: 30 }} }}, extra || {{}});
const frame = (e) => "id: " + e.seq + "\\nevent: " + e.type + "\\ndata: " + JSON.stringify(e) + "\\n\\n";
"""


def js(body: str) -> Any:
    program = PRELUDE + "\n" + body
    proc = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", program],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ------------------------------------------------------------------------------------------------ SSE
def test_frame_parser_handles_retry_keepalive_crlf_and_split_chunks() -> None:
    r = js(
        """
const { parseSseFrames, checkSeq, eventOfFrame } = await load("sse");
const text = "retry: 3000\\n\\n: keepalive\\n\\nid: 1\\nevent: run.started\\ndata: {\\"seq\\":1,\\"type\\":\\"run.started\\"}\\n\\nid: 2\\nevent: x\\nda";
const a = parseSseFrames(text);
const b = parseSseFrames(a.rest + "ta: {\\"seq\\":2,\\"type\\":\\"x\\"}\\r\\n\\r\\n");
const multi = parseSseFrames("data: a\\ndata: b\\n\\n");
out({
  first: a.frames.map((f) => [f.id, f.event, f.retry, f.data === null]),
  rest: a.rest,
  second: b.frames.map((f) => [f.id, f.event, f.data]),
  multi: multi.frames[0].data,
  seq: [checkSeq(0, 1), checkSeq(5, 5), checkSeq(5, 3), checkSeq(5, 6), checkSeq(5, 9), checkSeq(1, "x")],
  ev: [eventOfFrame(a.frames[1]).type, eventOfFrame(a.frames[0]), eventOfFrame({data: "not json"}), eventOfFrame({data: "{\\"a\\":1}"})],
});
"""
    )
    assert r["first"] == [[None, None, 3000, True], ["1", "run.started", None, False]]
    assert r["rest"].endswith("da")
    assert r["second"] == [["2", "x", '{"seq":2,"type":"x"}']]
    assert r["multi"] == "a\nb"
    assert r["seq"] == ["ok", "dup", "dup", "ok", "gap", "dup"]
    assert r["ev"] == ["run.started", None, None, None]


def test_read_frames_reads_a_byte_stream_split_anywhere() -> None:
    r = js(
        """
const { readFrames } = await load("sse");
const e1 = ev(1, "run.started"), e2 = ev(2, "plan.ready");
const whole = "retry: 3000\\n\\n" + frame(e1) + ": keepalive\\n\\n" + frame(e2);
const chunks = [];
for (let i = 0; i < whole.length; i += 7) chunks.push(whole.slice(i, i + 7));   // splits mid-frame and mid-line
const got = [];
await readFrames(streamOf(chunks), (f) => got.push(f));
out(got.filter((f) => f.data !== null).map((f) => JSON.parse(f.data).seq));
"""
    )
    assert r == [1, 2]


def test_run_feed_reconnects_with_last_event_id_and_ignores_keepalive() -> None:
    r = js(
        """
const { createRunFeed } = await load("runfeed");
const calls = [];
const states = [];
const events = [];
const scripts = [
  () => streamOf(["retry: 3000\\n\\n", frame(ev(1, "run.started")), frame(ev(2, "plan.ready"))]),   // ends without a terminal event
  () => { throw Object.assign(new Error("offline"), { status: 0 }); },                               // a network failure
  () => streamOf([": keepalive\\n\\n", frame(ev(3, "query.issued")), ": keepalive\\n\\n", frame(ev(4, "run.finished", { detail: { status: "complete" } }))]),
];
const feed = createRunFeed({
  afterSeq: 0,
  open: async (after) => { calls.push(after); const s = scripts[calls.length - 1](); return { body: s }; },
  onEvents: (evs) => events.push(...evs.map((e) => e.seq)),
  onState: (name, info) => states.push(name + (info && info.seconds !== undefined ? ":" + Math.round(info.seconds) : "")),
  sleep: async () => {},
  random: () => 0,
});
await feed.start();
out({ calls, events, states, last: feed.lastSeq });
"""
    )
    assert r["calls"] == [0, 2, 2]  # resumed after seq 2 both times
    assert r["events"] == [1, 2, 3, 4]
    assert r["last"] == 4
    assert r["states"][-1] == "ended"
    assert r["states"].count("retry:1") == 2  # one short pause after the clean close, one back-off after the failure


def test_run_feed_stops_on_404_and_when_asked() -> None:
    r = js(
        """
const { createRunFeed } = await load("runfeed");
const states = [];
const f1 = createRunFeed({ open: async () => { throw Object.assign(new Error("x"), { status: 404 }); }, onEvents() {}, onState: (n) => states.push(n), sleep: async () => {} });
await f1.start();
let opened = 0;
const f2 = createRunFeed({ open: async (a, signal) => { opened++; return { body: new ReadableStream({ start(c) { signal.addEventListener("abort", () => c.error(new Error("aborted"))); } }) }; }, onEvents() {}, onState() {}, sleep: async () => {} });
const done = f2.start();
await new Promise((r) => setTimeout(r, 20));
f2.stop();
await done;
out({ states, opened });
"""
    )
    assert r["states"] == ["connecting", "fatal"]
    assert r["opened"] == 1


# ------------------------------------------------------------------------------------------------ reducer
def test_reducer_runs_a_whole_run_and_ignores_duplicates() -> None:
    r = js(
        """
const R = await load("reducer");
const plan = ev(2, "plan.ready", { detail: { targets: [
  { id: "T-01", label: "Comparable apps", job: "features", lane: "blueprint", priority: 100, status: "queued" },
  { id: "T-02", label: "Payment providers", job: "services", lane: "integrations", priority: 90, status: "queued" },
  { id: "T-03", label: "Look and feel", job: "design", lane: "design", priority: 70, status: "queued" } ], declined: ["a currency symbol"] } });
const events = [
  ev(1, "run.started", { detail: { load_class: "focused", depth_word: "a focused look", minutes: 3, degraded: false } }),
  plan,
  ev(3, "target.started", { target_id: "T-01", detail: { topic: "Comparable apps", job: "features" } }),
  ev(4, "fact.found", { detail: { label: "Pack pricing", kind: "rule", topic: "Comparable apps" } }),
  ev(5, "fact.found", { detail: { label: "Bogus claim", kind: "rule", topic: "x" } }),
  ev(6, "fact.verified", { detail: { label: "Pack pricing", domain: "docs.a.example", level: "official" } }),
  ev(7, "fact.rejected", { detail: { reason: "quote_missing", label: "Bogus claim" } }),
  ev(8, "entity.discovered", { detail: { name: "Razorpay", kind: "provider" } }),
  ev(9, "entity.discovered", { detail: { name: "razorpay" } }),
  ev(10, "warning", { detail: { code: "rate_limited", message: "Waiting a moment for a search service" } }),
  ev(11, "target.covered", { target_id: "T-01", detail: { slots_filled: 2, slots_required: 2 } }),
  ev(12, "target.cancelled", { target_id: "T-02", detail: { topic: "Payment providers" } }),
  ev(13, "run.finished", { detail: { status: "partial", facts: 1, pages: 4, searches: 7, usd: "0.05", elapsed_s: 40 } }),
];
const s = R.emptyState();
const first = R.ingest(s, events.slice(0, 8), 1000);
const again = R.ingest(s, events.slice(0, 8), 1000);        // the same batch twice: nothing applied
const rest = R.ingest(s, events.slice(8), 2000);
const gap = R.ingest(R.emptyState(), [ev(1, "run.started"), ev(3, "warning")], 0);
out({
  first, again, rest, gap, status: s.status, lastSeq: s.lastSeq, received: s.received,
  targets: s.targets.map((t) => [t.id, t.status, t.slotsFilled]), declined: s.declined,
  facts: s.facts.map((f) => [f.label, f.status, f.domain]), rejected: s.factsRejected, entities: s.entities,
  warnings: s.warnings.map((w) => [w.code, w.title]), counters: R.counterTexts(s), groups: R.groupTargets(s).map((g) => g.job),
  depth: R.depthSentence(s), result: s.result.facts, now: s.now,
});
"""
    )
    assert r["first"] == {"applied": 8, "dup": 0, "gap": False}
    assert r["again"] == {"applied": 0, "dup": 8, "gap": False}
    assert r["rest"]["applied"] == 5 and r["gap"] == {"applied": 1, "dup": 0, "gap": True}
    assert r["status"] == "partial" and r["lastSeq"] == 13 and r["received"] == 13
    assert r["targets"] == [["T-01", "covered", 2], ["T-02", "cancelled", 0], ["T-03", "queued", 0]]
    assert r["declined"] == ["a currency symbol"]
    assert r["facts"] == [
        ["Pack pricing", "checked", "docs.a.example"],
        ["Bogus claim", "rejected", ""],
    ]
    assert r["rejected"] == 1 and r["entities"] == ["Razorpay"]
    assert r["warnings"] == [["rate_limited", "Waiting because of a rate limit"]]
    assert r["counters"]["topics"].endswith("of 3") and r["counters"]["rejected"] == "1"
    assert r["groups"] == ["features", "services", "design"]
    assert r["depth"].startswith("We are doing a focused look") and "3 minutes" in r["depth"]
    assert r["result"] == 1 and r["now"].startswith("run.finished")


def test_snapshot_then_tail_matches_a_full_replay() -> None:
    """A reloaded page (snapshot, then events after last_seq) ends in the same place as one that saw everything."""
    r = js(
        """
const R = await load("reducer");
const all = [ev(1, "run.started", { detail: { depth_word: "a focused look" } }),
  ev(2, "plan.ready", { detail: { targets: [{ id: "T-01", label: "A", job: "features", priority: 5 }], declined: [] } }),
  ev(3, "target.started", { target_id: "T-01", detail: {} }), ev(4, "page.read", { detail: { domain: "x.example" } }),
  ev(5, "target.covered", { target_id: "T-01", detail: { slots_filled: 1, slots_required: 1 } }), ev(6, "run.finished", { detail: { status: "complete" } })];
const full = R.emptyState(); R.ingest(full, all, 0);
const snap = { run_id: "rrn_1", program_id: "p", status: "running", last_seq: 4, counters: all[3].counters, feed: all.slice(0, 4),
  targets: [{ id: "T-01", label: "A", job: "features", lane: "blueprint", priority: 5, status: "running", slots_filled: 0, slots_required: 1 }], declined: [], entities: [] };
const reloaded = R.applySnapshot(snap, 0);
const tail = R.ingest(reloaded, all, 0);       // the stream may even replay from 1: only 5 and 6 are new
out({ tail, a: [full.status, full.lastSeq, full.targets[0].status], b: [reloaded.status, reloaded.lastSeq, reloaded.targets[0].status], feed: [full.feed.length, reloaded.feed.length] });
"""
    )
    assert r["tail"] == {"applied": 2, "dup": 4, "gap": False}
    assert r["a"] == r["b"] == ["complete", 6, "covered"]
    assert r["feed"] == [6, 6]


def test_feed_is_capped_and_failures_read_in_plain_words() -> None:
    r = js(
        """
const R = await load("reducer");
const s = R.emptyState();
for (let i = 1; i <= 250; i++) R.applyEvent(s, ev(i, "page.read", { detail: { domain: "d" + i + ".example" } }), 0);
const failed = R.emptyState(); R.applyEvent(failed, ev(1, "run.failed", { detail: { status: "failed", reason: "AttributeError", code: "internal" } }), 0);
const interrupted = R.applySnapshot({ run_id: "r", program_id: "p", status: "interrupted", reason: "The server restarted", last_seq: 0 }, 0);
const cancelled = R.emptyState(); R.applyEvent(cancelled, ev(1, "run.failed", { detail: { status: "cancelled", reason: "x", code: "cancelled" } }), 0);
const row = R.feedRow(ev(1, "query.issued", { label: "Searching: yoga", detail: { query: "yoga <b>x</b>", variant: "local", lang: "en", country: "in", provider: "tavily" } }));
const skipped = R.feedRow(ev(2, "page.skipped", { label: "Could not read a.example (paywall)", detail: { domain: "a.example", reason: "paywall" } }));
const waiting = R.nowLine({ status: "queued" }, 2);
out({ n: s.feed.length, first: s.feed[0].seq, last: s.feed[99].seq, failed: R.failureText(failed), interrupted: R.failureText(interrupted),
  cancelled: R.failureText(cancelled), row, quiet: skipped.quiet, extra: skipped.extra, waiting });
"""
    )
    assert r["n"] == 100 and r["first"] == 151 and r["last"] == 250
    assert "AttributeError" not in r["failed"] and "could not finish" in r["failed"].lower()
    assert "restarted" in r["interrupted"] and "cancelled" in r["cancelled"]
    assert (
        r["row"]["text"] == "Searching: yoga" and r["row"]["extra"] == "“yoga <b>x</b>”"
    )  # markup stays text
    assert r["quiet"] is True and "paywall" in r["extra"]
    assert r["waiting"] == "Waiting for its turn: position 2."


# ------------------------------------------------------------------------------------------------ formatters
def test_money_time_and_dates_read_in_plain_words() -> None:
    r = js(
        """
const F = await load("format");
const now = Date.parse("2026-11-11T12:00:00Z");
out({
  usd: [F.usd(1230000), F.usd(0), F.usd(8000), F.usd(10000), F.usd(4990000), F.usdNum(5000000), F.usdRange(200000, 650000)],
  dur: [F.formatDuration(0), F.formatDuration(45), F.formatDuration(80), F.formatDuration(180), F.formatDuration(3900), F.formatDuration(null)],
  left: [F.remainingText(null), F.remainingText(4), F.remainingText(47), F.remainingText(400)],
  minutes: [F.minutesText({ low: 2, mid: 3, high: 5 }), F.minutesText({ low: 1, mid: 1, high: 1 }), F.minutesText(null)],
  ago: [F.ago("2026-11-11T11:59:50Z", now), F.ago("2026-11-11T11:30:00Z", now), F.ago("2026-11-11T08:00:00Z", now), F.ago("2026-11-10T09:00:00Z", now), F.ago("2026-11-06T09:00:00Z", now), F.ago("nonsense", now)],
  between: [F.secondsBetween("2026-11-11T10:00:00Z", "2026-11-11T10:02:30Z"), F.secondsBetween(null, null)],
  delay: [0, 1, 2, 3, 4, 9].map((a) => F.reconnectDelay(a)),
  words: [F.runWord("partial"), F.runWord("interrupted"), F.runWord("weird_new"), F.jobTitle("services"), F.stepWord("search.tavily"), F.stepWord("extract"), F.stepWord("brand_new_step"), F.warningTitle("budget_stop"), F.kindWord("ping")],
  tone: [F.statusTone("complete"), F.statusTone("running"), F.statusTone("partial"), F.statusTone("failed"), F.statusTone("zzz")],
  plural: [F.plural(1, "page"), F.plural(2, "page"), F.plural(0, "search", "searches")],
  bytes: [F.bytesText(512), F.bytesText(2048), F.bytesText(5 * 1048576)],
});
"""
    )
    assert r["usd"] == [
        "USD 1.23",
        "USD 0.00",
        "USD 0.008",
        "USD 0.01",
        "USD 4.99",
        "5.00",
        "USD 0.20 to 0.65",
    ]
    assert r["dur"] == ["0 s", "45 s", "1 min 20 s", "3 min", "1 h 5 min", ""]
    assert r["left"] == ["estimating", "almost done", "about 45 s", "about 7 min"]
    assert r["minutes"] == ["about 3 minutes (2 to 5)", "about 1 minute", ""]
    assert r["ago"] == ["just now", "30 min ago", "4 h ago", "yesterday", "5 days ago", ""]
    assert r["between"] == [150, None]
    assert r["delay"] == [1, 2, 4, 8, 15, 15]
    assert r["words"] == [
        "Done, with gaps",
        "Interrupted",
        "Weird new",
        "Outside services and providers",
        "Web search (tavily)",
        "Reading pages",
        "Brand new step",
        "The budget stopped the research",
        "Connection test",
    ]
    assert r["tone"] == ["ok", "busy", "warn", "bad", ""]
    assert r["plural"] == ["1 page", "2 pages", "0 searches"]
    assert r["bytes"] == ["512 B", "2 KB", "5.0 MB"]


# ------------------------------------------------------------------------------------------------ sanitizers
def test_safe_url_allows_only_plain_http_and_https() -> None:
    r = js(
        """
const S = await load("safe");
const bad = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:x", "file:///etc/passwd", "//evil.example/x", "/relative/path", "ftp://x.example", "https://user:pw@x.example/", "http://", "", "   ", null, undefined, 42, "https://" + "a".repeat(3000), "blob:https://x.example/uuid"];
const good = ["https://docs.example.com/a?b=1#c", "http://127.0.0.1:8120/x", "  https://x.example/pad  "];
out({
  bad: bad.map((u) => S.safeUrl(u)),
  good: good.map((u) => S.safeUrl(u)),
  routes: ["#/run/rrn_01ABC", "#/inputs", "#/", "javascript:alert(1)", "#/x/<b>", "#/a b", 5].map((x) => S.safeRoute(x)),
  markup: [S.looksLikeMarkup("<img src=x onerror=alert(1)>"), S.looksLikeMarkup("plain")],
});
"""
    )
    assert r["bad"] == [None] * 17
    assert r["good"] == [
        "https://docs.example.com/a?b=1#c",
        "http://127.0.0.1:8120/x",
        "https://x.example/pad",
    ]
    assert r["routes"] == ["#/run/rrn_01ABC", "#/inputs", "#/", None, None, None, None]
    assert r["markup"] == [True, False]


def test_problems_become_plain_sentences_with_the_missing_names() -> None:
    r = js(
        """
const S = await load("safe");
out({
  net: S.problemText(0, null),
  auth: S.problemText(401, { code: "unauthorized" }).kind,
  origin: S.problemText(403, { code: "bad_origin" }).kind,
  notReady: S.problemText(409, { code: "not_ready", message: "Research cannot start yet. Set: A, B.", missing: ["A", "B"] }),
  budget: S.problemText(409, { code: "budget_tripwire", message: "The budget tripwire was reached." }),
  active: S.problemText(409, { code: "run_active", message: "busy", run_id: "rrn_9" }).runId,
  refused: S.problemText(422, { code: "hash_mismatch", message: "The Brief changed after it was approved." }).text,
  server: S.problemText(500, { code: "internal_error", message: "Traceback (most recent call last): File \\"x.py\\"" }).text,
  notFound: S.problemText(404, { code: "not_found", message: "Not found." }).text,
  reasons: [S.plainReason("AttributeError"), S.plainReason("Traceback (most recent call last)"), S.plainReason("The budget ran out."), S.plainReason(""), S.plainReason("x".repeat(500)).length],
});
"""
    )
    assert r["net"]["kind"] == "network" and "could not reach" in r["net"]["text"]
    assert r["auth"] == "auth" and r["origin"] == "origin"
    assert r["notReady"]["missing"] == ["A", "B"] and r["notReady"]["kind"] == "not_ready"
    assert r["budget"]["kind"] == "budget" and r["active"] == "rrn_9"
    assert r["refused"] == "The Brief changed after it was approved."
    assert "Traceback" not in r["server"] and "problem" in r["server"]
    assert r["notFound"].startswith("We could not find")
    assert r["reasons"][:4] == [
        "Something went wrong while researching.",
        "Something went wrong while researching.",
        "The budget ran out.",
        "Something went wrong while researching.",
    ]
    assert r["reasons"][4] == 240


# ------------------------------------------------------------------------------------------------ budget
def test_budget_levels_text_and_advice_before_a_run() -> None:
    r = js(
        """
const B = await load("budget");
const mk = (spent, state) => ({ enabled: true, total_micros: 5000000, tripwire_micros: 4500000, spent_micros: spent, remaining_micros: 5000000 - spent, state: state || "ok" });
const est = (low, mid, high) => ({ usd_micros: { low, mid, high } });
out({
  levels: [B.budgetLevel(mk(0)), B.budgetLevel(mk(3999999)), B.budgetLevel(mk(4000000)), B.budgetLevel(mk(4499999)), B.budgetLevel(mk(4500000, "tripwire")), B.budgetLevel(mk(5000000, "exhausted")), B.budgetLevel({ enabled: false }), B.budgetLevel(null)],
  text: [B.budgetText(mk(1230000)), B.budgetText(null)],
  pct: B.budgetPercents(mk(2500000)),
  calm: B.estimateAdvice(est(100000, 380000, 650000), 4620000),
  strong: B.estimateAdvice(est(1200000, 2000000, 3200000), 4000000),
  partial: B.estimateAdvice(est(200000, 380000, 650000), 500000),
  exact: B.estimateAdvice(est(1, 400000, 500000), 1000000),
  none: B.estimateAdvice(null, 1000000),
  block: [B.startBlock(mk(0), { ready: true, checks: [] }).ok, B.startBlock(mk(0), { ready: false, checks: [{ id: "switch:X", label: "Live switch X", ok: false }, { id: "key:tavily", label: "Search key", ok: false }] }),
          B.startBlock(mk(4500000, "tripwire"), { ready: true }), B.startBlock(mk(5000000, "exhausted"), null).reason, B.startBlock(null, null).ok],
});
"""
    )
    assert r["levels"] == ["ok", "ok", "amber", "amber", "red", "red", "off", "off"]
    assert r["text"] == ["USD 1.23 of 5.00 used; tripwire 4.50", "Budget is not tracked"]
    assert r["pct"] == {"used": 50, "tripwire": 90}
    assert (
        r["calm"]["strong"] is False
        and r["calm"]["partial"] is False
        and "%" in r["calm"]["lines"][0]
    )
    assert r["strong"]["strong"] is True and any("40%" in line for line in r["strong"]["lines"])
    assert r["partial"]["partial"] is True and any(
        "stop early (partial)" in line for line in r["partial"]["lines"]
    )
    assert r["exact"]["strong"] is False  # exactly 40% of what is left is not above it
    assert r["none"]["lines"] == []
    assert r["block"][0] is True
    assert r["block"][1] == {
        "ok": False,
        "reason": "Not ready: Live switch X.",
    }  # a missing optional key is not named
    assert r["block"][2]["ok"] is False and "tripwire" in r["block"][2]["reason"]
    assert "used up" in r["block"][3] and r["block"][4] is True


# ------------------------------------------------------------------------------------------------ vlist, filters, store
def test_virtual_list_math_fixed_and_measured_rows() -> None:
    r = js(
        """
const V = await load("vlist");
const top = V.fixedWindow({ count: 600, itemH: 40, scrollTop: 0, viewportH: 400, overscan: 3 });
const mid = V.fixedWindow({ count: 600, itemH: 40, scrollTop: 4000, viewportH: 400, overscan: 3 });
const end = V.fixedWindow({ count: 600, itemH: 40, scrollTop: 999999, viewportH: 400, overscan: 3 });
const empty = V.fixedWindow({ count: 0, itemH: 40, scrollTop: 0, viewportH: 400 });
const heights = [30, 100, 30, 60, 200, 30];
const off = V.buildOffsets(heights);
const w = V.offsetWindow(off, 120, 100, 0);
out({ top, mid, end, empty, off, rows: [0, 29, 30, 129, 130, 160, 220, 999].map((y) => V.rowAt(off, y)), w,
  scrollTo: V.scrollTopFor(off, 3), emptyOff: V.offsetWindow(V.buildOffsets([]), 0, 100) });
"""
    )
    assert r["top"] == {"start": 0, "end": 14, "padTop": 0, "padBottom": 23440, "total": 24000}
    assert r["mid"]["start"] == 97 and r["mid"]["end"] == 114 and r["mid"]["padTop"] == 97 * 40
    assert r["end"]["end"] == 600 and r["end"]["padBottom"] == 0 and r["end"]["start"] < 600
    assert r["empty"] == {"start": 0, "end": 0, "padTop": 0, "padBottom": 0, "total": 0}
    assert r["off"] == [0, 30, 130, 160, 220, 420, 450]
    assert r["rows"] == [0, 0, 1, 1, 2, 3, 4, 5]
    assert r["w"] == {"start": 1, "end": 4, "padTop": 30, "padBottom": 230, "total": 450}
    assert r["scrollTo"] == 160 and r["emptyOff"]["end"] == 0


def test_filters_and_remembered_choices_survive_a_blocked_store() -> None:
    r = js(
        """
const Fl = await load("filters");
const { createStore } = await load("store");
const runs = [
  { run_id: "rrn_1", status: "running", input_name: "yoga.json", idea: "Yoga studio app", size_class: "small" },
  { run_id: "rrn_2", status: "complete", input_name: "produce.json", idea: "Produce marketplace", size_class: "medium" },
  { run_id: "rrn_3", status: "interrupted", input_name: "freight.json", idea: "Freight fleet", size_class: "big" },
  { run_id: "rrn_4", status: "partial", input_name: "yoga.json", idea: "Yoga studio app", size_class: "small" } ];
const inputs = [{ id: "a", preview: { size_class: "small" } }, { id: "b", preview: { size_class: "medium" } }, { id: "c", preview: {} }];
const mem = {}; const good = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = v; }, removeItem: (k) => { delete mem[k]; } };
const s1 = createStore(() => good, "t.");
s1.set("last", { id: "abc" }); s1.set("size", "small");
const blocked = createStore(() => { throw new Error("SecurityError"); });
const full = createStore(() => ({ getItem: () => "{broken", setItem() { throw new Error("QuotaExceeded"); }, removeItem() { throw new Error("x"); } }));
out({
  groups: ["all", "active", "done", "problem"].map((g) => Fl.filterRuns(runs, { group: g }).map((r) => r.run_id)),
  text: [Fl.filterRuns(runs, { q: "yoga" }).length, Fl.filterRuns(runs, { q: "YOGA small" }).length, Fl.filterRuns(runs, { q: "yoga freight" }).length, Fl.filterRuns(runs, { group: "done", q: "yoga" }).map((r) => r.run_id)],
  counts: Fl.runGroupCounts(runs), sizeCounts: Fl.sizeCounts(inputs),
  sizes: [Fl.filterInputs(inputs, "all").length, Fl.filterInputs(inputs, "small").map((i) => i.id), Fl.filterInputs(inputs, "big").length],
  store: [s1.get("last"), s1.get("size"), s1.get("missing", "dflt"), mem["t.last"]],
  blocked: [blocked.get("x", 7), blocked.set("x", 1)], broken: [full.get("x", "fb"), full.set("x", 1)],
});
"""
    )
    assert r["groups"] == [
        ["rrn_1", "rrn_2", "rrn_3", "rrn_4"],
        ["rrn_1"],
        ["rrn_2", "rrn_4"],
        ["rrn_3"],
    ]
    assert r["text"] == [2, 2, 0, ["rrn_4"]]
    assert r["counts"] == {"all": 4, "active": 1, "done": 2, "problem": 1}
    assert r["sizeCounts"] == {"all": 3, "tiny": 0, "small": 1, "medium": 1, "big": 0}
    assert r["sizes"] == [3, ["a"], 0]
    assert r["store"] == [{"id": "abc"}, "small", "dflt", '{"id":"abc"}']
    assert r["blocked"] == [7, False] and r["broken"] == ["fb", False]


# ------------------------------------------------------------------------------------------------ the API wrapper
def test_api_sends_the_token_retries_a_stale_one_and_explains_failures() -> None:
    r = js(
        """
const { createApi, ApiError } = await load("api");
const seen = [];
let token = "tok-1";
const res = (status, body, type) => ({ ok: status < 400, status, headers: { get: () => type || "application/json" }, json: async () => body, blob: async () => "BLOB" });
const fetchFn = async (url, init) => {
  seen.push([init && init.method || "GET", url, init && init.headers && init.headers.Authorization, init && init.headers && init.headers["X-Tenant-Id"], init && init.headers && init.headers["Last-Event-ID"]]);
  if (url === "/api/session") return res(200, { token: token, tenant_id: "demo" });
  if (url === "/api/stale") { if (init.headers.Authorization === "Bearer tok-1") { token = "tok-2"; return res(401, { code: "unauthorized" }); } return res(200, { ok: true }); }
  if (url === "/api/refused") return res(409, { code: "not_ready", message: "Research cannot start yet. Set: X.", missing: ["X"] });
  if (url === "/api/down") throw new TypeError("Failed to fetch");
  if (url === "/api/echo") return res(201, { body: init.body, type: init.headers["Content-Type"] });
  if (url.indexOf("/events") > -1) return res(200, null, "text/event-stream");
  return res(404, { code: "not_found", message: "Not found." });
};
const api = createApi({ fetch: fetchFn });
const stale = await api.get("/api/stale");
const refused = await api.post("/api/refused", {}).catch((e) => ({ name: e.constructor.name, status: e.status, text: e.message, missing: e.problem.missing }));
const down = await api.get("/api/down").catch((e) => ({ status: e.status, kind: e.problem.kind }));
const echo = await api.post("/api/echo", { input_id: "abc" });
const raw = await api.postRaw("/api/echo", "{\\"a\\":1}");
const stream = await api.stream("rrn_1", 41);
const probe = [await api.probe("/api/fake"), await createApi({ fetch: async () => { throw new Error("x"); } }).probe("/api/fake")];
out({ stale, refused, down, echo, raw, seen, streamOk: stream.ok, probe });
"""
    )
    assert r["stale"] == {"ok": True}
    assert (
        r["refused"]["status"] == 409
        and r["refused"]["missing"] == ["X"]
        and r["refused"]["name"] == "ApiError"
    )
    assert r["down"] == {"status": 0, "kind": "network"}
    assert (
        json.loads(r["echo"]["body"]) == {"input_id": "abc"}
        and r["echo"]["type"] == "application/json"
    )
    assert r["raw"]["body"] == '{"a":1}'
    sent = [s for s in r["seen"] if s[1] == "/api/stale"]
    assert [s[2] for s in sent] == [
        "Bearer tok-1",
        "Bearer tok-2",
    ]  # asked once more with the new token
    assert all(s[3] == "demo" for s in r["seen"] if s[1] not in ("/api/session", "/api/fake"))
    assert [s[4] for s in r["seen"] if "/events" in s[1]] == ["41"]  # Last-Event-ID
    assert r["probe"] == [None, None]


# ------------------------------------------------------------------------------------------------ stage 2: results helpers
def test_review_query_paging_debounce_and_stale_requests() -> None:
    r = js(
        """
const R = await load("results");
const fake = { handle: 0, jobs: new Map(), now: 0,
  set(fn, ms) { const id = ++this.handle; this.jobs.set(id, { fn, at: this.now + ms }); return id; },
  clear(id) { this.jobs.delete(id); },
  advance(ms) { this.now += ms; for (const [id, j] of Array.from(this.jobs)) if (j.at <= this.now) { this.jobs.delete(id); j.fn(); } } };
const calls = [];
const d = R.debounce((v) => calls.push(v), 150, fake);
d("y"); fake.advance(100); d("yo"); fake.advance(100); d("yog"); fake.advance(149); const before = calls.length; fake.advance(1);
d("later"); d.cancel(); fake.advance(500);
const aborted = [];
const gate = R.latestOnly(() => { const c = { aborted: false, abort() { this.aborted = true; aborted.push(1); }, signal: {} }; return c; });
const a = gate.begin(); const b = gate.begin(); const c = gate.begin();
out({
  q: [R.reviewQuery({ lane: "blueprint", q: "pack & fees", evidence: "" }, "main", 100, 100), R.reviewQuery({}, "", 0, 50), R.reviewQuery({ job: "a b" }, "appendix")],
  active: [R.activeFilterCount({ lane: "x", q: "y", impact: "" }), R.activeFilterCount({}), R.activeFilterCount(null)],
  pages: [R.pagesNeeded(0, 15, 100, new Set()), R.pagesNeeded(95, 205, 100, new Set([1])), R.pagesNeeded(0, 15, 100, new Set([0])), R.pagesNeeded(10, 10, 100, new Set())],
  debounce: { calls, before },
  stale: { aborted: aborted.length, current: [gate.current(a.id), gate.current(b.id), gate.current(c.id)] },
});
"""
    )
    assert r["q"] == [
        "lane=blueprint&q=pack%20%26%20fees&store=main&offset=100&limit=100",
        "offset=0&limit=50",
        "job=a%20b&store=appendix&offset=0&limit=50",
    ]
    assert r["active"] == [2, 0, 0]
    assert r["pages"] == [[0], [0, 2], [], []]
    assert r["debounce"] == {"calls": ["yog"], "before": 0}  # only the last typed value, 150 ms after the last key; cancel drops one
    assert r["stale"] == {"aborted": 2, "current": [False, False, True]}


def test_results_words_metrics_rows_and_readable_data() -> None:
    r = js(
        """
const R = await load("results");
const metrics = { acceptance: {
  extraction_quote_pass_first_try: { value: 1.0, target: 0.9, kind: "min", ok: true },
  number_rejection_rate: { value: 0.05, target: 0.03, kind: "max", ok: false },
  fetch_success: { value: null, target: 0.7, kind: "min", ok: null } } };
const data = { name: "Razorpay", empty: "", none: null, list: [], notes: ["a", "b"], flag: true, fees: { setup: "free", rate: "2%", deep: { a: { b: { c: 1 } } } },
  steps: [{ name: "pay", detail: "tap" }, { name: "done", detail: null }] };
out({
  ev: [R.evidenceWord("official"), R.evidenceWord("corroborated"), R.evidenceWord("single_source"), R.evidenceWord("unverified"), R.evidenceTone("unverified"), R.evidenceWord("odd_new")],
  flags: [R.flagWord("single-source"), R.flagWord("stale"), R.flagWord("duplicate_source"), R.flagWord("brand_new_flag")],
  gaps: ["not_found", "budget_stop", "provider_down", "not_run", "not_worthwhile", "conflict_unresolved", "user_cancelled", "ambiguous_reference", "declined", "zzz"].map(R.gapWord),
  acc: R.acceptanceRows(metrics).map((x) => [x.label, x.value, x.target, x.state]),
  cost: [R.costSteps({ "search.tavily": 232000, plan: 0, extract: 1500 }), R.costSteps({ "search.fake": "0.2320", check: "0.0100" })],
  rows: R.dataRows(data),
  applies: [R.appliesText({ kind: "FTR", id: "FTR-003", relation: "expects" }), R.appliesText({ kind: "NEW", id: null, relation: "suggests" })],
  prevalence: [R.prevalenceText({ seen_in: 3, of: 14 }), R.recencyText({ newest_source: "2026-09-01", age_months: 2 })],
  groups: R.normGroups([{ area: "screen", label: "b", prevalence: { seen_in: 1, of: 4 } }, { area: "feature", label: "a", prevalence: { seen_in: 3, of: 4 } }, { area: "feature", label: "z", prevalence: { seen_in: 4, of: 4 } }]).map((g) => [g.title, g.norms.map((n) => n.label)]),
  gg: R.gapGroups([{ reason: "not_found", label: "x" }, { reason: "budget_stop", label: "y" }, { reason: "not_found", label: "z" }]).map((g) => [g.reason, g.gaps.length]),
  trust: R.trustLines({ metrics: { evidence: { official: 6, single_source: 4 }, checker: { supported: 0.9, unchecked: 0.1, rubber_stamp_signal: true }, flags: { "needs-confirm": 3 } }, profile: { appendix_items: 2 } }),
});
"""
    )
    assert r["ev"] == ["Official source", "Several sources agree", "Only one source", "Not fully verified", "bad", "Odd new"]
    assert r["flags"] == ["Only one source", "May be out of date", "Same text on several sites", "Brand new flag"]
    assert r["gaps"][0].startswith("We could not find") and "budget limit" in r["gaps"][1] and "search service" in r["gaps"][2]
    assert "was not worth" in r["gaps"][4] or "not worth" in r["gaps"][4]
    assert "You stopped" in r["gaps"][6] and r["gaps"][9] == "We could not finish this topic."
    assert r["acc"] == [
        ["Quotes found in the page the first time", "100%", "at least 90%", "ok"],
        ["Findings dropped because a number was not in the quote", "5%", "at most 3%", "bad"],
        ["Pages that could be read", "not measured", "at least 70%", "none"],
    ]
    assert r["cost"][0] == [{"step": "search.tavily", "micros": 232000}, {"step": "extract", "micros": 1500}]
    assert r["cost"][1] == [{"step": "search.fake", "micros": 232000}, {"step": "check", "micros": 10000}]
    rows = {x["label"]: x for x in r["rows"]}
    assert "Empty" not in rows and "None" not in rows and "List" not in rows  # empty values are left out
    assert rows["Name"]["value"] == "Razorpay" and rows["Flag"]["value"] == "yes" and rows["Notes"]["list"] == ["a", "b"]
    assert [x["label"] for x in rows["Fees"]["rows"]] == ["Setup", "Rate", "Deep"]
    assert rows["Steps"]["list"] == ["Name: pay; Detail: tap", "Name: done"]
    assert r["applies"] == ["Feature FTR-003 (expects)", "Not in your Brief (suggests)"]
    assert r["prevalence"] == ["mentioned in 3 of 14 sources", "newest source 1 Sep 2026 (2 months ago)"]
    assert r["groups"] == [["Features", ["z", "a"]], ["Screens", ["b"]]]
    assert r["gg"] == [["not_found", 2], ["budget_stop", 1]]
    assert r["trust"][0].startswith("60% of the 10 findings") and any("too lenient" in t for t in r["trust"]) and any("3 findings need" in t for t in r["trust"])


def test_markdown_preview_never_makes_html_and_only_http_links() -> None:
    r = js(
        """
const M = await load("markdown");
const text = [
  "# Research report <script>alert(1)</script>", "",
  "- Status: **complete** and `RI-001` <img src=x onerror=alert(2)>",
  "- [good](https://docs.example.com/a?b=1) and [bad](javascript:alert(3)) and [data](data:text/html,<script>alert(4)</script>)", "",
  "1. first", "2. second", "",
  "> A quote <b onmouseover=alert(5)>bold</b>", "> second line", "",
  "| Lane | Quality |", "| --- | --- |", "| blueprint | partial |", "| <i>x</i> | **ok** |", "",
  "plain paragraph", "continues here", "", "---", "",
  "<script>alert(6)</script>", "[x](//evil.example/y)",
].join("\\n");
const blocks = M.parseMarkdown(text);
const spans = (b) => JSON.stringify(b);
out({
  types: blocks.map((b) => b.type + (b.level || "")),
  heading: blocks[0].spans[0].text,
  ul: blocks[1].items.map((i) => i.map((s) => s.t + ":" + s.text)),
  links: blocks[1].items[1].map((s) => [s.t, s.url || null]),
  ol: blocks[2].items.length, quote: blocks[3].lines.length, table: [blocks[4].head.length, blocks[4].rows.length, blocks[4].rows[1][1][0].t],
  para: blocks[5].spans[0].text, raw: blocks[7].spans.map((s) => s.t + ":" + s.text), anyHtml: spans(blocks).includes("innerHTML"),
  inline: M.inlineSpans("a **b** c `d` [e](https://x.example/) [f](http://") .map((s) => s.t),
  empty: [M.parseMarkdown(""), M.parseMarkdown(null), M.inlineSpans("")],
});
"""
    )
    assert r["types"] == ["h1", "ul", "ol", "quote", "table", "p", "hr", "p"]
    assert r["heading"] == "Research report <script>alert(1)</script>"  # markup stays the characters it is
    assert r["ul"][0] == ["text:Status: ", "bold:complete", "text: and ", "code:RI-001", "text: <img src=x onerror=alert(2)>"]
    links = dict((str(u), t) for t, u in r["links"])
    assert ("link", "https://docs.example.com/a?b=1") in [tuple(x) for x in r["links"]]
    assert sum(1 for t, _ in r["links"] if t == "link") == 1  # javascript: and data: are plain text, not links
    assert r["ol"] == 2 and r["quote"] == 2 and r["table"] == [2, 2, "bold"]
    assert r["para"] == "plain paragraph continues here"
    assert set(x.split(":")[0] for x in r["raw"]) == {"text"}  # a bare <script> line and an unsafe link are only characters
    assert r["inline"] == ["text", "bold", "text", "code", "text", "link", "text"]
    assert r["empty"] == [[], [], []] and links


def test_spot_check_keys_progress_and_summary_wording() -> None:
    r = js(
        """
const S = await load("spotcheck");
const key = (k, extra) => S.verdictForKey(Object.assign({ key: k }, extra || {}));
const cards = [{ key: "r:1" }, { key: "r:2" }, { key: "r:3" }, { key: "r:4" }];
const check = { cards, verdicts: { "r:1": "ok", "r:2": "skipped", "r:4": "invented_detail" } };
const passing = { answered: 20, total: 20, passed: true, supported_pct: 0.95, ok_pct: 0.9, invented_total: 0, invented_high_impact: 0, pass: { supported: true, ok: true, no_invented: true }, thresholds: { supported: 0.95, ok: 0.9, invented_total_below: 3 }, worst: [] };
const failing = Object.assign({}, passing, { passed: false, ok_pct: 0.85, invented_total: 3, invented_high_impact: 1, pass: { supported: true, ok: false, no_invented: false }, worst: [{ where: "job:services", count: 4 }, { where: "kind:service_option", count: 3 }] });
out({
  keys: ["1", "2", "3", "4", "5", "6", "s", "S", "7", "0", "a", "Enter"].map((k) => key(k)),
  modifiers: [key("1", { ctrlKey: true }), key("1", { metaKey: true }), key("1", { altKey: true })], none: [S.verdictForKey(null), S.verdictForKey({})],
  order: S.VERDICTS.map((v) => [v.key, v.id]),
  next: [S.nextUnanswered(check, 0), S.nextUnanswered(check, 3), S.nextUnanswered({ cards, verdicts: { "r:1": "ok", "r:2": "ok", "r:3": "ok", "r:4": "ok" } }, 0), S.nextSkipped(check)],
  prog: S.progress(check), label: [S.verdictLabel("invented_detail"), S.verdictLabel("skipped"), S.verdictLabel("zz")],
  rowsPass: S.summaryRows(passing).map((x) => [x.value, x.target, x.ok]), rowsFail: S.summaryRows(failing).map((x) => x.ok),
  text: [S.verdictText(passing), S.verdictText(failing), S.verdictText({ answered: 0 })],
  worst: failing.worst.map(S.worstText), strata: [S.strataWord("high_impact"), S.strataWord("service"), S.strataWord("zz")],
});
"""
    )
    assert r["keys"] == ["ok", "not_relevant", "claim_not_in_quote", "wrong_or_outdated", "wrong_place_or_company", "invented_detail", "skipped", "skipped", None, None, None, None]
    assert r["modifiers"] == [None, None, None] and r["none"] == [None, None]
    assert r["order"] == [["1", "ok"], ["2", "not_relevant"], ["3", "claim_not_in_quote"], ["4", "wrong_or_outdated"], ["5", "wrong_place_or_company"], ["6", "invented_detail"]]
    assert r["next"] == [2, -1, -1, 1]  # the first card with no verdict at all; a skipped card counts as answered
    assert r["prog"] == {"total": 4, "answered": 2, "skipped": 1, "left": 1}
    assert r["label"] == ["Invented detail", "Skipped", ""]
    assert r["rowsPass"][0][:2] == ["95%", "at least 95%"] and r["rowsPass"][1][:2] == ["90%", "at least 90%"] and all(x[2] for x in r["rowsPass"])
    assert r["rowsFail"] == [True, False, False]
    assert r["text"][0].startswith("Passed") and r["text"][1].startswith("Not passed") and r["text"][2] == "Nothing answered yet."
    assert r["worst"] == ["services (4 not OK)", "service option (3 not OK)"]
    assert r["strata"][0].startswith("A fact where a mistake") and r["strata"][2] == "A fact"


def test_run_screen_wiring_facts_from_the_snapshot_and_required_facts() -> None:
    r = js(
        """
const R = await load("reducer");
const plan = ev(2, "plan.ready", { detail: { slots_required: 5, targets: [
  { id: "T-01", label: "A", job: "features", priority: 9, slots_required: 3 }, { id: "T-02", label: "B", job: "services", priority: 8, slots_required: 1 } ] } });
const snap = { run_id: "rrn_1", program_id: "p", status: "running", last_seq: 6, slots_required: 5, counters: { targets_total: 2 },
  feed: [ev(1, "run.started"), plan, ev(3, "target.started", { target_id: "T-01", detail: { slots_filled: 0, slots_required: 3 } }), ev(4, "fact.found", { detail: { fact_id: "atm_1", label: "Pack fees" } })],
  targets: [{ id: "T-01", label: "A", job: "features", status: "running", slots_filled: 1, slots_required: 3 }, { id: "T-02", label: "B", job: "services", status: "queued", slots_filled: 0, slots_required: 1 }],
  facts: [{ fact_id: "atm_1", label: "Pack fees", kind: "rule", topic: "A", state: "checked", level: "supported", reason: null, domain: "docs.a.example" },
          { fact_id: "atm_2", label: "Refunds", kind: "rule", topic: "A", state: "checking", level: null, reason: null, domain: null },
          { fact_id: "atm_3", label: "Bad one", kind: "rule", topic: "A", state: "rejected", level: null, reason: "quote_missing", domain: null }] };
const s = R.applySnapshot(snap, 0);
const before = s.facts.map((f) => [f.id, f.status]);
R.ingest(s, [ev(7, "fact.verified", { detail: { fact_id: "atm_2", label: "Refunds", domain: "x.example", level: "official" } }), ev(8, "fact.found", { detail: { fact_id: "atm_9", label: "New one" } }), ev(9, "fact.found", { detail: { label: "No id fact" } }), ev(10, "fact.rejected", { detail: { label: "No id fact", reason: "x" } })], 0);
const fin = R.emptyState();
R.applyEvent(fin, ev(1, "run.finished", { detail: { status: "complete", facts: 5, pages: 2, searches: 3, usd: "0.2320", elapsed_s: 4, stages: { plan: 1, search: 2 }, cost_by_step: { "search.fake": "0.2320" }, fallbacks: {} } }), 0);
out({ before, after: s.facts.map((f) => [f.id || f.label, f.status, f.reason]), cov: R.coverage(s), req: s.slotsRequired, rejected: s.factsRejected,
  fin: [fin.status, fin.result.stages, fin.result.cost_by_step, fin.result.fallbacks] });
"""
    )
    assert r["before"] == [["atm_1", "checked"], ["atm_2", "checking"], ["atm_3", "rejected"]]  # the whole list, not the last events
    assert r["after"] == [["atm_1", "checked", ""], ["atm_2", "checked", ""], ["atm_3", "rejected", "quote_missing"], ["atm_9", "checking", ""], ["No id fact", "rejected", "x"]]
    assert r["cov"] == {"filled": 1, "required": 5, "percent": 20} and r["req"] == 5
    assert r["rejected"] >= 2
    assert r["fin"] == ["complete", {"plan": 1, "search": 2}, {"search.fake": "0.2320"}, {}]
