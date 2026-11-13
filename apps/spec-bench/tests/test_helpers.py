"""The pure helpers of the Spec Bench (``apps/spec-bench/lib/*.js``) run under Node.

Each test hands a small ES-module program to ``node --input-type=module -e`` and compares its JSON output. The
helpers are DOM-free (the stream reader, the event reducer, formatters, sanitizers, money states, view models, the
graph layout, the API wrapper with an injected fetch), so they run in milliseconds. Skipped without Node.
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
const ev = (seq, type, data, extra) => Object.assign({{ seq: seq, type: type, at: "2026-11-14T10:00:" + String(seq % 60).padStart(2, "0") + "Z", run_id: "run_1", data: data || {{}} }}, extra || {{}});
const frame = (e) => "id: " + e.seq + "\\nevent: " + e.type + "\\ndata: " + JSON.stringify(e) + "\\n\\n";
"""


def js(body: str) -> Any:
    proc = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", PRELUDE + "\n" + body],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ------------------------------------------------------------------------------------------------ SSE
def test_frame_parser_handles_keepalive_crlf_and_split_chunks() -> None:
    r = js(
        """
const { parseSseFrames, checkSeq, eventOfFrame } = await load("sse");
const text = ": keepalive\\n\\nid: 1\\nevent: run.started\\ndata: {\\"seq\\":1,\\"type\\":\\"run.started\\"}\\n\\nid: 2\\nevent: x\\nda";
const a = parseSseFrames(text);
const b = parseSseFrames(a.rest + "ta: {\\"seq\\":2,\\"type\\":\\"x\\"}\\r\\n\\r\\n");
const multi = parseSseFrames("data: a\\ndata: b\\n\\n");
out({
  first: a.frames.map((f) => [f.id, f.event, f.data === null]),
  rest: a.rest,
  second: b.frames.map((f) => [f.id, f.event, f.data]),
  multi: multi.frames[0].data,
  seq: [checkSeq(0, 1), checkSeq(5, 5), checkSeq(5, 3), checkSeq(5, 6), checkSeq(5, 9), checkSeq(1, "x")],
  ev: [eventOfFrame(a.frames[0]).type, eventOfFrame({data: "not json"}), eventOfFrame({data: "{\\"a\\":1}"}), eventOfFrame(null)],
});
"""
    )
    assert r["first"] == [["1", "run.started", False]]
    assert r["rest"].endswith("da")
    assert r["second"] == [["2", "x", '{"seq":2,"type":"x"}']]
    assert r["multi"] == "a\nb"
    assert r["seq"] == ["ok", "dup", "dup", "ok", "gap", "dup"]
    assert r["ev"] == ["run.started", None, None, None]


def test_read_frames_reads_a_byte_stream_split_anywhere() -> None:
    r = js(
        """
const { readFrames } = await load("sse");
const whole = frame(ev(1, "run.started")) + ": keepalive\\n\\n" + frame(ev(2, "plan.ready"));
const chunks = [];
for (let i = 0; i < whole.length; i += 7) chunks.push(whole.slice(i, i + 7));
const got = [];
await readFrames(streamOf(chunks), (f) => got.push(f));
out(got.filter((f) => f.data !== null).map((f) => JSON.parse(f.data).seq));
"""
    )
    assert r == [1, 2]


def test_run_feed_resumes_after_the_last_seq_and_ends_on_partial() -> None:
    r = js(
        """
const { createRunFeed } = await load("runfeed");
const calls = [], events = [], states = [];
const scripts = [
  () => streamOf([frame(ev(1, "run.started")), frame(ev(2, "plan.ready"))]),
  () => { throw Object.assign(new Error("offline"), { status: 0 }); },
  () => streamOf([": keepalive\\n\\n", frame(ev(3, "step.started", {}, { step: "structure" })), frame(ev(4, "run.partial", { reason: "stop" }))]),
];
const feed = createRunFeed({
  afterSeq: 0,
  open: async (after) => { calls.push(after); return { body: scripts[calls.length - 1]() }; },
  onEvents: (evs) => events.push(...evs.map((e) => e.seq)),
  onState: (name) => states.push(name),
  sleep: async () => {},
  random: () => 0,
});
await feed.start();
out({ calls, events, last: feed.lastSeq, end: states[states.length - 1] });
"""
    )
    assert r["calls"] == [0, 2, 2]
    assert r["events"] == [1, 2, 3, 4]
    assert r["last"] == 4 and r["end"] == "ended"


# ------------------------------------------------------------------------------------------------ reducer
def test_reducer_folds_a_dense_stream_into_the_run_state() -> None:
    r = js(
        """
const { emptyState, ingest, stepRows, callKinds, runActions } = await load("reducer");
const s = emptyState();
const evs = [
  ev(1, "run.queued"), ev(2, "run.started"), ev(3, "plan.ready", { estimate: { low_micros: 100000, high_micros: 900000 } }),
  ev(4, "step.started", {}, { step: "structure" }),
  ev(5, "call.finished", { kind: "product", tokens: 1200, cost_micros: 40000, repaired: false }, { step: "structure" }),
  ev(6, "call.finished", { kind: "product", tokens: { in: 1000, out: 500 }, cost_micros: 60000, repaired: true }),
  ev(7, "call.finished", { kind: "design", tokens: 300, cost_micros: 10000 }),
  ev(8, "validators.done", { gate: "freeze", blockers: 1, warnings: 4 }),
  ev(9, "repair.rcode", { code: "R-03" }), ev(10, "repair.reask", { node: "scr_a" }),
  ev(11, "banner", { code: "weak_route" }),
  ev(12, "step.finished", {}, { step: "structure" }),
  ev(13, "checkpoint"),
  ev(14, "gate.opened", { gate: "g15" }),
];
const r = ingest(s, evs);
const afterOpen = { status: s.status, g15: s.gates.g15, actions: runActions(s) };
ingest(s, [ev(15, "gate.closed", { gate: "g15" }), ev(16, "run.partial", { reason: "budget_tripwire" })]);
const partial = { status: s.status, reason: s.partial.reason, actions: runActions(s) };
ingest(s, [ev(17, "run.resumed"), ev(18, "gate.opened", { gate: "g2" })]);
const g2 = { status: s.status, partial: s.partial };
ingest(s, [ev(19, "run.finished", { status: "approved" })]);
out({
  r, last: s.lastSeq, received: s.received, estimate: s.estimate,
  calls: [s.calls.count, s.calls.tokens, s.calls.costMicros, s.calls.repaired],
  kinds: callKinds(s).map((k) => [k.kind, k.count, k.costMicros]),
  steps: stepRows(s), validators: s.validators, repairs: [s.repairs.rcode, s.repairs.reask],
  banners: s.banners.map((b) => b.code), checkpoints: s.checkpoints, spend: s.spendMicros,
  afterOpen, partial, g2, final: s.status, resumed: s.resumed,
});
"""
    )
    assert r["r"] == {"applied": 14, "dup": 0, "gap": False}
    assert r["last"] == 19 and r["received"] == 19
    assert r["estimate"] == {"low_micros": 100000, "high_micros": 900000}
    assert r["calls"] == [3, 3000, 110000, 1]
    assert r["kinds"] == [["product", 2, 100000], ["design", 1, 10000]]
    assert r["steps"] == [{"name": "structure", "status": "done", "current": False}]
    assert r["validators"] == [{"seq": 8, "gate": "freeze", "blockers": 1, "warnings": 4}]
    assert r["repairs"] == [1, 1] and r["banners"] == ["weak_route"] and r["checkpoints"] == 1
    assert r["spend"] == 110000
    assert r["afterOpen"]["status"] == "paused_g15" and r["afterOpen"]["g15"] == "open"
    assert (
        r["afterOpen"]["actions"]["openG15"] is True and r["afterOpen"]["actions"]["stop"] is True
    )
    assert r["partial"] == {
        "status": "partial",
        "reason": "budget_tripwire",
        "actions": {"stop": False, "resume": True, "openG15": False, "openG2": False},
    }
    assert r["g2"] == {"status": "awaiting_g2", "partial": None}
    assert r["final"] == "approved" and r["resumed"] == 1


def test_reducer_ignores_duplicates_and_stops_at_a_gap() -> None:
    r = js(
        """
const { emptyState, ingest } = await load("reducer");
const s = emptyState();
const first = ingest(s, [ev(1, "run.started"), ev(2, "checkpoint"), ev(2, "checkpoint"), ev(1, "run.started"), ev(3, "checkpoint")]);
const gap = ingest(s, [ev(5, "checkpoint"), ev(4, "checkpoint")]);       // 5 is not the next one: stop, apply nothing after it
const after = ingest(s, [ev(4, "checkpoint"), ev(5, "checkpoint")]);     // the missing one arrives (a refetch): both go in
out({ first, gap, after, last: s.lastSeq, checkpoints: s.checkpoints, received: s.received });
"""
    )
    assert r["first"] == {"applied": 3, "dup": 2, "gap": False}
    assert r["gap"] == {"applied": 0, "dup": 0, "gap": True}
    assert r["after"] == {"applied": 2, "dup": 0, "gap": False}
    assert r["last"] == 5 and r["checkpoints"] == 4 and r["received"] == 5


def test_reducer_caps_every_list_but_keeps_the_totals() -> None:
    r = js(
        """
const m = await load("reducer");
const s = m.emptyState();
const evs = [];
let seq = 0;
for (let i = 0; i < 500; i++) evs.push(ev(++seq, "call.finished", { kind: "k" + (i % 3), tokens: 10, cost_micros: 5 }));
for (let i = 0; i < 300; i++) evs.push(ev(++seq, "validators.done", { gate: "g", blockers: 0, warnings: 1 }));
for (let i = 0; i < 300; i++) evs.push(ev(++seq, "repair.rcode", { code: "R-01" }));
for (let i = 0; i < 100; i++) evs.push(ev(++seq, "banner", { code: "b" + i }));
m.ingest(s, evs);
out({ feed: s.feed.length, recent: s.calls.recent.length, count: s.calls.count, tokens: s.calls.tokens, validators: s.validators.length, repairItems: s.repairs.items.length, rcode: s.repairs.rcode, banners: s.banners.length, lastBanner: s.banners[s.banners.length - 1].code, caps: [m.FEED_CAP, m.CALL_CAP, m.VALIDATOR_CAP, m.REPAIR_CAP, m.BANNER_CAP] });
"""
    )
    assert r["feed"] == r["caps"][0] == 150 and r["recent"] == r["caps"][1] == 60
    assert r["count"] == 500 and r["tokens"] == 5000
    assert r["validators"] == 100 and r["repairItems"] == 100 and r["rcode"] == 300
    assert r["banners"] == 30 and r["lastBanner"] == "b99"


def test_reducer_snapshot_wins_and_failure_is_plain() -> None:
    r = js(
        """
const { applySnapshot, emptyState, applyEvent } = await load("reducer");
const s = applySnapshot({ run_id: "run_9", status: "awaiting_g2", step: "package", spend_micros: 1500000, estimate: { low_micros: 1, high_micros: 2 }, gates: { g15: "closed", g2: "open" }, banners: [{ code: "weak_route" }] });
const f = emptyState();
applyEvent(f, ev(1, "run.failed", { reason: "provider unreachable", code: "net" }));
out({ s: [s.runId, s.status, s.step, s.spendMicros, s.gates, s.banners.map((b) => b.code)], f: [f.status, f.failure] });
"""
    )
    assert r["s"] == [
        "run_9",
        "awaiting_g2",
        "package",
        1500000,
        {"g15": "closed", "g2": "open"},
        ["weak_route"],
    ]
    assert r["f"] == ["failed", {"reason": "provider unreachable", "code": "net"}]


def test_event_lines_are_plain_words() -> None:
    r = js(
        """
const { describeEvent } = await load("eventtext");
const kinds = ["run.started", "step.started", "call.finished", "validators.done", "gate.opened", "run.partial", "banner", "weird.thing"];
out(kinds.map((k) => describeEvent(ev(1, k, { gate: "g2", reason: "no_credit", kind: "product", tokens: 1500, cost_micros: 20000, code: "weak_route", blockers: 2, warnings: 3 }, { step: "structure" })).text));
"""
    )
    assert r[0] == "The run started."
    assert "Building the app structure" in r[1]
    assert r[2] == "A model call finished (product): 1,500 tokens, USD 0.02"
    assert "2 must-fix, 3 to check" in r[3] and "the spec (G2)" in r[4]
    assert "no credit left" in r[5] and "weaker model route" in r[6] and r[7] == "weird.thing"


# ------------------------------------------------------------------------------------------------ safe
def test_safe_urls_refuse_scripts_data_and_credentials() -> None:
    r = js(
        """
const { safeUrl, safeApiPath, safeRoute, looksLikeMarkup } = await load("safe");
out({
  url: ["https://example.org/a?b=1", "http://example.org", "javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html,<script>1</script>", "vbscript:x", "file:///etc/passwd", "ftp://example.org", "https://user:pw@example.org", "https://", "/relative", "//evil.example", "", null, 5].map(safeUrl),
  api: ["/api/runs/run_01/export/preview.html", "/api/../etc", "//evil.example/api/x", "/api//x", "http://x/api/a", "/apix", "/api/a b", "/api/a?x=1", null].map(safeApiPath),
  route: ["#/run/run_01", "#/handoff/run_01/screens/scr_a", "#/", "#/a b", "javascript:1", "#/x?y", 4].map(safeRoute),
  markup: [looksLikeMarkup("<img src=x onerror=1>"), looksLikeMarkup("plain")],
});
"""
    )
    assert r["url"] == ["https://example.org/a?b=1", "http://example.org/"] + [None] * 13
    assert r["api"] == ["/api/runs/run_01/export/preview.html"] + [None] * 8
    assert r["route"] == [
        "#/run/run_01",
        "#/handoff/run_01/screens/scr_a",
        "#/",
        None,
        None,
        None,
        None,
    ]
    assert r["markup"] == [True, False]


def test_problem_text_has_a_plain_sentence_for_every_api_error_code() -> None:
    r = js(
        """
const { problemText, plainReason } = await load("safe");
const codes = ["bad_request", "not_found", "conflict", "run_active", "not_ready", "budget_blocked", "refused_estimate", "gate_required", "bad_origin"];
const body = (code, message, fix) => ({ error: { code, message, fix } });
out({
  codes: codes.map((c) => { const p = problemText(c === "not_found" ? 404 : c === "bad_origin" ? 403 : 409, body(c, "", "")); return [c, p.kind, p.text.length > 20]; }),
  withMessage: problemText(409, body("refused_estimate", "The highest it could cost (USD 4.00) is more than the USD 1.00 left.", "Run it offline.")),
  network: problemText(0, null).text,
  server: problemText(500, null).text,
  flat: problemText(409, { code: "run_active", message: "Run run_01ABC is active." }).runId,
  trace: plainReason('Traceback (most recent call last): File "x.py"', "fallback"),
  cls: plainReason("KeyError", "fallback"),
  long: plainReason("w ".repeat(400)).length,
});
"""
    )
    assert all(ok for _c, _k, ok in r["codes"])
    assert [k for _c, k, _ok in r["codes"]] == [
        "refused",
        "missing",
        "conflict",
        "active",
        "not_ready",
        "budget",
        "refused_estimate",
        "gate",
        "origin",
    ]
    assert r["withMessage"]["text"].startswith("The highest it could cost")
    assert r["withMessage"]["fix"] == "Run it offline."
    assert "could not reach" in r["network"] and "problem" in r["server"]
    assert r["flat"] == "run_01ABC"
    assert r["trace"] == "fallback" and r["cls"] == "fallback" and r["long"] <= 320


# ------------------------------------------------------------------------------------------------ money
def test_the_money_banner_states() -> None:
    r = js(
        """
const m = await load("money");
const base = { cap_micros: 5000000, tripwire_micros: 4500000, reserved_micros: 0, remaining_micros: 3000000 };
const states = {
  ok: { ...base, spent_micros: 2000000, state: "ok" },
  tripwire: { ...base, spent_micros: 4600000, remaining_micros: 400000, state: "tripwire" },
  exhausted: { ...base, spent_micros: 5000000, remaining_micros: 0, state: "exhausted" },
  derivedTrip: { ...base, spent_micros: 4400000, reserved_micros: 200000 },
  derivedOut: { ...base, spent_micros: 4000000, reserved_micros: 1000000 },
  none: null,
};
const o = {};
for (const k of Object.keys(states)) o[k] = [m.moneyLevel(states[k]), m.moneyText(states[k]), m.moneyBanner(states[k])];
o.pct = m.moneyPercents(states.ok);
o.v = [m.estimateVerdict({ low_micros: 100000, high_micros: 900000 }, 500000), m.estimateVerdict({ low_micros: 100000, high_micros: 400000 }, 500000), m.estimateVerdict(null, 5)];
out(o);
"""
    )
    assert r["ok"][0] == "ok" and r["ok"][2] is None
    assert r["ok"][1] == "Spent USD 2.00 of the USD 5.00 cap (USD 3.00 left). Tripwire USD 4.50."
    assert r["tripwire"][0] == "tripwire" and "no new step starts" in r["tripwire"][2]["text"]
    assert r["exhausted"][0] == "exhausted" and "used up" in r["exhausted"][2]["text"]
    assert r["derivedTrip"][0] == "tripwire" and r["derivedOut"][0] == "exhausted"
    assert r["none"] == ["unknown", "Money: not known yet", None]
    assert r["pct"] == {"used": 40, "reserved": 0, "tripwire": 90}
    assert r["v"][0]["refused"] is True and "will not start" in r["v"][0]["lines"][0]
    assert r["v"][1]["refused"] is False and r["v"][2]["lines"] == ["No cost estimate was given."]


# ------------------------------------------------------------------------------------------------ api and store
def test_the_api_wrapper_turns_errors_into_plain_problems_and_builds_queries() -> None:
    r = js(
        """
const { createApi, ApiError } = await load("api");
const seen = [];
const reply = (status, body, type) => ({ ok: status < 400, status, headers: { get: () => type || "application/json" }, json: async () => body, text: async () => "T" });
const routes = {
  "GET /api/ok": () => reply(200, { a: 1 }),
  "GET /api/refused": () => reply(409, { error: { code: "refused_estimate", message: "Too dear.", fix: "Go offline." } }),
  "GET /api/down": () => { throw new TypeError("net"); },
  "GET /api/page": () => reply(500, null, "text/html"),
};
const api = createApi({ fetch: async (url, init) => { seen.push([init.method, url, init.headers["Last-Event-ID"] || null]); const f = routes[init.method + " " + url]; return f ? f() : reply(200, {}); } });
const fail = async (p) => { try { await api.get(p); return null; } catch (e) { return [e instanceof ApiError, e.status, e.problem.kind, e.problem.text, e.problem.fix]; } };
await api.post("/api/runs", { x: 1 });
await api.stream("run_1", 7);
out({ ok: await api.get("/api/ok"), refused: await fail("/api/refused"), down: await fail("/api/down"), page: await fail("/api/page"), q: [api.q({ a: "x y", b: "", c: null, d: 0 }), api.q({})], seen: seen.slice(0, 2), probe: await api.probe("/api/down"), text: await api.text("/api/ok") });
"""
    )
    assert r["ok"] == {"a": 1}
    assert r["refused"] == [True, 409, "refused_estimate", "Too dear.", "Go offline."]
    assert r["down"][:3] == [True, 0, "network"] and "could not reach" in r["down"][3]
    assert r["page"][2] == "server"
    assert r["q"] == ["?a=x%20y&d=0", ""]
    assert r["seen"] == [
        ["POST", "/api/runs", None],
        ["GET", "/api/runs/run_1/events?after=7", "7"],
    ]
    assert r["probe"] is None and r["text"] == "T"


def test_the_store_survives_blocked_or_broken_storage() -> None:
    r = js(
        """
const { createStore } = await import(LIB + "/store.js");
const mem = new Map();
const good = createStore(() => ({ getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) }));
const blocked = createStore(() => { throw new Error("blocked"); });
const full = createStore(() => ({ getItem: () => "{broken", setItem: () => { throw new Error("quota"); }, removeItem: () => { throw new Error("x"); } }));
out({
  good: [good.set("a", { n: 1 }), good.get("a", 0), good.get("b", "dflt"), (good.remove("a"), good.get("a", "gone"))],
  blocked: [blocked.get("a", "f"), blocked.set("a", 1), (blocked.remove("a"), "ok")],
  full: [full.get("a", "f"), full.set("a", 1), (full.remove("a"), "ok")],
});
"""
    )
    assert r["good"] == [True, {"n": 1}, "dflt", "gone"]
    assert r["blocked"] == ["f", False, "ok"] and r["full"] == ["f", False, "ok"]


# ------------------------------------------------------------------------------------------------ view models
def test_the_graph_layout_puts_unreachable_screens_apart_and_flags_dead_ends() -> None:
    r = js(
        """
const { layoutGraph } = await load("model");
const g = layoutGraph({
  nodes: [{ id: "home" }, { id: "list" }, { id: "detail" }, { id: "orphan" }, { id: "later1", in: "later" }],
  edges: [{ from: "home", to: "list" }, { from: "list", to: "detail" }, { from: "detail", to: "list" }, { from: "home", to: "ghost" }],
  unreachable: ["orphan"], dead_ends: ["detail"], entries: ["home"],
});
const by = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
out({ cols: Object.fromEntries(g.nodes.map((n) => [n.id, n.col])), flags: [by.orphan.unreachable, by.detail.deadEnd, by.later1.later, by.home.entry], edges: g.edges.map((e) => [e.from, e.to, e.back]), counts: g.counts, wide: g.width > 400 && g.height > 20, none: layoutGraph(null).nodes.length });
"""
    )
    assert r["cols"] == {"home": 0, "list": 1, "detail": 2, "orphan": 3, "later1": 3}
    assert r["flags"] == [True, True, True, True]
    assert r["edges"] == [
        ["home", "list", False],
        ["list", "detail", False],
        ["detail", "list", True],
    ]  # the edge to a missing node is dropped
    assert r["counts"] == {"total": 5, "unreachable": 1, "deadEnds": 1, "later": 1}
    assert r["wide"] is True and r["none"] == 0


def test_the_confirm_list_is_ranked_money_legal_identity_first_and_capped_at_20() -> None:
    r = js(
        """
const { rankConfirm } = await load("model");
const list = [];
for (let i = 0; i < 12; i++) list.push({ category: "other", text: "other " + i });
list.push({ category: "identity", text: "id 1" }, { category: "legal", text: "legal 1" }, { category: "money", text: "money 1" }, { category: "Money", text: "money 2" }, { category: "legal", text: "legal 2" });
for (let i = 12; i < 20; i++) list.push({ category: "other", text: "other " + i });
const r = rankConfirm(list);
out({ n: r.length, first: r.slice(0, 5).map((x) => x.text), ranks: [r[0].rank, r[19].rank], noOther: r.filter((x) => x.text === "other 19").length, short: rankConfirm(["a", { text: "b" }]).map((x) => x.text) });
"""
    )
    assert r["n"] == 20
    assert r["first"] == ["money 1", "money 2", "legal 1", "legal 2", "id 1"]
    assert r["ranks"] == [1, 20] and r["noOther"] == 0
    assert r["short"] == ["a", "b"]


def test_the_pack_tree_sums_tokens_and_the_models_read_what_the_api_gives() -> None:
    r = js(
        """
const m = await load("model");
const tree = m.buildTree([{ path: "global/tokens.md", tokens: 100 }, { path: "screens/a.md", tokens: 40 }, { path: "screens/b.md", tokens: 60 }, { path: "MANIFEST.json", tokens: 5 }]);
const shape = (ns) => ns.map((n) => [n.name, n.dir, n.tokens, shape(n.children)]);
const screen = m.normalizeScreen({ id: "s1", title: "Home", regions: [{ name: "Hero", importance: "must", text: "Today" }, { name: "Tip", importance: "could" }, { name: "Feed", importance: "usually", items: ["a", "b"] }, { name: "Odd" }], actions: [{ label: "Go", intent: "start", effect: "opens" }], hero_records: ["r1"] });
const flat = m.normalizeScreen({ id: "s2", must: [{ name: "M" }], usually: [], could: [{ name: "C" }] });
const prev = m.normalizePreview({ inventory_count: 12, phases: [{ name: "Core", screens: 8 }], estimate: { low_micros: 1, high_micros: 2 }, calls_by_step: { structure: 4, product: { calls: 6 } } });
const prev2 = m.normalizePreview({ calls_by_step: [{ step: "a", calls: 2 }] });
const waves = m.normalizeWaves({ tasks: [{ id: "t1", wave: "W1" }, { id: "t2", wave: "W0" }, { id: "t3", wave: "W1" }] });
const waves2 = m.normalizeWaves({ waves: [{ wave: "W0", tasks: [{ id: "x" }] }] });
const totals = m.normalizeTaskTotals({ task_read_budget: 28000, tasks: [{ id: "t1", read_tokens: 20000 }, { id: "t2", read_tokens: 30000 }] });
out({ shape: shape(tree), screen: [screen.must.length, screen.usually.length, screen.could.length, screen.usually[0].items, screen.usually[1].name, screen.actions], flat: [flat.must.length, flat.could.length], prev: [prev.inventoryCount, prev.calls, prev.totalCalls, prev.phases], prev2: prev2.calls, waves: waves.map((w) => [w.wave, w.tasks.map((t) => t.id)]), waves2: waves2.map((w) => [w.wave, w.tasks.length]), totals: totals.map((t) => [t.id, t.over]), cols: m.columnsOf([{ a: 1, b: { c: 1 } }, { a: 2, z: "q", n: null }], 3), text: [m.textOf(null), m.textOf({ title: "T" }), m.textOf(["a", { name: "b" }]), m.textOf(7)] });
"""
    )
    assert r["shape"] == [
        ["global", True, 100, [["tokens.md", False, 100, []]]],
        ["screens", True, 100, [["a.md", False, 40, []], ["b.md", False, 60, []]]],
        ["MANIFEST.json", False, 5, []],
    ]
    assert r["screen"][:3] == [1, 2, 1] and r["screen"][3] == ["a", "b"] and r["screen"][4] == "Odd"
    assert r["screen"][5] == [{"label": "Go", "intent": "start", "effect": "opens"}]
    assert r["flat"] == [1, 1]
    assert r["prev"][0] == 12 and r["prev"][1] == [
        {"step": "structure", "calls": 4},
        {"step": "product", "calls": 6},
    ]
    assert r["prev"][2] == 10 and r["prev2"] == [{"step": "a", "calls": 2}]
    assert r["waves"] == [["W1", ["t1", "t3"]], ["W0", ["t2"]]] and r["waves2"] == [["W0", 1]]
    assert r["totals"] == [["t1", False], ["t2", True]]
    assert r["cols"] == ["a", "b", "z"]
    assert r["text"] == ["", "T", "a, b", "7"]


def test_the_journey_model_keeps_declared_entries_and_exits() -> None:
    r = js(
        """
const { normalizeJourney } = await load("model");
const j = normalizeJourney({ title: "Order", role: "customer", stories: ["s1"], emotion_line: [{ label: "start", level: 3 }, { label: "pay", level: 1, feeling: "tense" }], moments_that_matter: ["pay"], unhappy_paths: [{ what: "card fails", recovery: "retry" }], steps: [{ screen_id: "a", title: "A", exits: [{ label: "next", to: "b" }] }, { screen_id: "b", entries: ["a"], exits: [] }] });
out({ title: j.title, emotion: j.emotion, unhappy: j.unhappy, steps: j.steps.map((s) => [s.id, s.entries, s.exits]), moments: j.moments });
"""
    )
    assert r["title"] == "Order"
    assert r["emotion"][1] == {"label": "pay", "feeling": "tense", "level": 1}
    assert r["unhappy"] == [{"text": "card fails", "recovery": "retry"}]
    assert r["steps"] == [["a", [], [{"label": "next", "to": "b"}]], ["b", ["a"], []]]


def test_routes_and_the_journey_rail() -> None:
    r = js(
        """
const { parseRoute, railItems, RUN_VIEWS } = await load("routes");
const rail = railItems("run_01", "g15", "paused_g15");
const bare = railItems(null, "inputs", null);
out({
  parse: ["#/inputs", "#/preview/run_01", "#/handoff/run_01/screens/scr_a", "#/handoff/run_01/diff/run_02", "#/nonsense", "", "#/run/bad id", "#/g15/run_01/"].map(parseRoute),
  rail: rail.steps.map((s) => [s.n, s.route, s.current, s.hint]),
  more: rail.more.map((m) => m.route),
  bare: bare.steps.map((s) => [s.n, s.route]),
  runViews: RUN_VIEWS,
});
"""
    )
    assert r["parse"][0] == {"name": "inputs", "id": None, "tab": None, "extra": None}
    assert r["parse"][2] == {"name": "handoff", "id": "run_01", "tab": "screens", "extra": "scr_a"}
    assert r["parse"][3]["extra"] == "run_02"
    assert (
        r["parse"][4]["name"] == "inputs"
        and r["parse"][5]["name"] == "inputs"
        and r["parse"][6]["name"] == "inputs"
    )
    assert r["parse"][7] == {"name": "g15", "id": "run_01", "tab": None, "extra": None}
    assert r["rail"] == [
        [1, "#/inputs", False, ""],
        [2, "#/preview/run_01", False, ""],
        [3, "#/g15/run_01", True, "needs your OK"],
        [4, "#/run/run_01", False, ""],
        [5, "#/g2/run_01", False, ""],
        [6, "#/handoff/run_01/sim", False, ""],
    ]
    assert r["more"] == ["#/runs", "#/budget"]
    assert r["bare"] == [[1, "#/inputs"]] + [[n, None] for n in range(2, 7)]
