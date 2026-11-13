"""Every Spec Bench screen drawn for real, without a browser: a tiny stand-in for the few DOM calls the house helpers
(``h()``, ``svg()``, ``untrusted()``) make, enough to mount each view under Node against an in-memory fake ``fetch``
that plays scripted answers of the API in ``docs/api/spec-agent.md``, and read back what a person would read.
Includes the poisoned-quote tests: markup in pack text must reach the page as text nodes only. Skipped without Node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

PRELUDE = f"""
const ROOT = {json.dumps(ROOT.as_uri())};
const SVGNS = "http:" + "//www.w3.org/2000/svg";
const created = [];
class N {{
  constructor(tag, text, ns) {{ this.tag = tag; this.ns = ns || null; this.kids = []; this.attrs = {{}}; this.on = {{}}; this.className = ""; this.style = {{}}; this.up = null; this.own = text === undefined ? "" : text; this.value = ""; this.checked = false; }}
  get firstChild() {{ return this.kids[0] || null; }}
  get isConnected() {{ return true; }}
  get disabled() {{ return "disabled" in this.attrs; }}
  appendChild(k) {{ if (k.up) k.up.removeChild(k); k.up = this; this.kids.push(k); return k; }}
  removeChild(k) {{ this.kids = this.kids.filter((x) => x !== k); k.up = null; return k; }}
  setAttribute(n, v) {{ this.attrs[n] = String(v); if (n === "class") this.className = String(v); if (n === "value") this.value = String(v); }}
  getAttribute(n) {{ return n in this.attrs ? this.attrs[n] : null; }}
  removeAttribute(n) {{ delete this.attrs[n]; }}
  addEventListener(t, f) {{ (this.on[t] = this.on[t] || []).push(f); }}
  set textContent(t) {{ this.kids = []; this.own = String(t); }}
  get textContent() {{ return this.own + this.kids.map((k) => k.textContent).join(""); }}
  contains(n) {{ return n === this || this.kids.some((k) => k.contains(n)); }}
  focus() {{}}
  querySelectorAll() {{ return []; }}
  async fire(type, extra) {{ for (const f of this.on[type] || []) await f(Object.assign({{ target: this, preventDefault() {{}} }}, extra || {{}})); }}
  click() {{ return this.fire("click"); }}
  change(v) {{ if (typeof v === "boolean") this.checked = v; else if (v !== undefined) this.value = v; return this.fire("change"); }}
}}
globalThis.document = {{
  createElement: (t) => {{ const n = new N(t); created.push(n); return n; }},
  createElementNS: (ns, t) => {{ const n = new N(t, undefined, ns); created.push(n); return n; }},
  createTextNode: (s) => new N("#text", s),
  activeElement: null, hidden: false,
}};
const walk = (n, f) => {{ f(n); n.kids.forEach((k) => walk(k, f)); }};
const find = (n, pred) => {{ let hit = null; walk(n, (x) => {{ if (!hit && pred(x)) hit = x; }}); return hit; }};
const all = (n, pred) => {{ const o = []; walk(n, (x) => {{ if (pred(x)) o.push(x); }}); return o; }};
const has = (n, cls) => (n.className || "").split(" ").includes(cls);
const byClass = (n, cls) => all(n, (x) => has(x, cls));
const byFk = (n, fk) => find(n, (x) => x.attrs["data-fk"] === fk);
const flat = (n) => n.textContent.replace(/\\s+/g, " ").trim();
const cells = (tr) => tr.kids.map(flat);
const rows = (n) => all(n, (x) => x.tag === "tr" && x.kids.some((k) => k.tag === "td")).map(cells);
const out = (v) => process.stdout.write(JSON.stringify(v));
const until = async (pred, ms) => {{ const t = Date.now(); while (Date.now() - t < (ms || 2000)) {{ if (pred()) return true; await new Promise((r) => setTimeout(r, 4)); }} return false; }};
const settle = () => new Promise((r) => setTimeout(r, 30));

const enc = new TextEncoder();
const streamOf = (chunks) => new ReadableStream({{ start(c) {{ for (const ch of chunks) c.enqueue(enc.encode(ch)); c.close(); }} }});
const ev = (seq, type, data, extra) => Object.assign({{ seq, type, at: "2026-11-14T10:00:" + String(seq % 60).padStart(2, "0") + "Z", run_id: "run_01", data: data || {{}} }}, extra || {{}});
const frame = (e) => "id: " + e.seq + "\\nevent: " + e.type + "\\ndata: " + JSON.stringify(e) + "\\n\\n";

// ---- an in-memory fake fetch: routes are "METHOD /path" (no query) -> value | (init, url) => value
//      a value may be {{__status, body}}, {{__text}} or {{__stream: [chunks]}}
function reply(status, body, extra) {{
  const e = extra || {{}};
  return {{ ok: status < 400, status, headers: {{ get: () => (e.text ? "text/plain" : "application/json") }}, json: async () => body, text: async () => (e.text !== undefined ? e.text : JSON.stringify(body)), body: e.stream || null }};
}}
function fakeFetch(routes, log) {{
  return async (url, init) => {{
    const method = (init && init.method) || "GET";
    const path = url.split("?")[0];
    log.push({{ method, url, body: init && init.body ? JSON.parse(init.body) : null }});
    const h = routes[method + " " + path];
    if (h === undefined) return reply(404, {{ error: {{ code: "not_found", message: "" }} }});
    const v = typeof h === "function" ? await h(init, url) : h;
    if (v && v.__status) return reply(v.__status, v.body);
    if (v && v.__text !== undefined) return reply(200, null, {{ text: v.__text }});
    if (v && v.__stream) return reply(200, null, {{ stream: streamOf(v.__stream) }});
    return reply(200, v);
  }};
}}
const BUDGET = {{ period_id: "p1", cap_micros: 5000000, tripwire_micros: 4500000, spent_micros: 1000000, reserved_micros: 0, remaining_micros: 4000000, state: "ok" }};
async function mountView(file, route, routes, opts) {{
  const o = opts || {{}};
  const {{ createApi }} = await import(ROOT + "/lib/api.js");
  const {{ createStore }} = await import(ROOT + "/lib/store.js");
  const log = [], nav = [], mem = new Map();
  const ctx = {{
    api: createApi({{ fetch: fakeFetch(Object.assign({{ "GET /api/budget": BUDGET }}, routes), log) }}),
    store: createStore(() => ({{ getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) }})),
    schedule: (fn) => fn(), announce() {{}}, on() {{ return () => {{}}; }}, setRun() {{}}, refreshBudget() {{}},
    navigate: (r) => nav.push(r), state: {{ budget: o.budget === undefined ? BUDGET : o.budget }},
  }};
  const box = new N("div");
  const mod = await import(ROOT + "/views/" + file + ".js");
  const handle = await (mod.mount ? mod.mount(box, ctx, route) : mod.mountTab(box, ctx, route.env, route.tab));
  return {{ box, ctx, log, nav, handle }};
}}
const posts = (log) => log.filter((c) => c.method === "POST").map((c) => [c.url, c.body]);

// ---- scripted data
const INPUTS = {{
  compass: [
    {{ id: "cmp_yoga", path: "p", format: "compass.v2", tier: "quick", size_band: "compact", profile: "yoga studio", readiness: {{ ready: true, missing: [] }}, drift_flags: ["Brief edited after export"], research_matches: ["rrn_9"] }},
    {{ id: "cmp_freight", format: "compass.v2", tier: "thorough", size_band: "deep", profile: "freight program", readiness: {{ ready: false, missing: ["roles"] }}, drift_flags: [], research_matches: [] }},
  ],
  research: [{{ id: "rrn_9", status: "complete", input_hash: "h" }}],
}};
const AUTO = {{
  compact: {{ compass_id: "cmp_yoga", research_id: "rrn_9", reason: "smallest export of its class, newest complete research", gaps: [] }},
  standard: {{ compass_id: "cmp_yoga", research_id: null, reason: "the only export that fits", gaps: ["no Research run: Spec runs with a thin research lane and says so"] }},
  deep: {{ compass_id: "cmp_freight", research_id: null, reason: "the thorough freight export", gaps: [] }},
}};
const PROFILES = {{ profiles: [{{ id: "haiku", capability_class: "weak", effective_window: 60000, whole_pack_tokens: 50000, k: 2, unset: ["price"] }}, {{ id: "sonnet", capability_class: "strong", effective_window: 200000, whole_pack_tokens: 180000, k: 4, unset: [] }}] }};
const inputsRoutes = (extra) => Object.assign({{
  "GET /api/inputs": INPUTS,
  "GET /api/inputs/auto": (init, url) => {{ const s = /size=(\\w+)/.exec(url)[1]; return AUTO[s] || {{ __status: 404, body: {{ error: {{ code: "not_found", message: "" }} }} }}; }},
  "GET /api/profiles": PROFILES,
  "POST /api/runs": {{ run_id: "run_01" }},
}}, extra || {{}});

const SCREEN = (id, title, extra) => Object.assign({{ id, title, purpose: "Shows the next class", regions: [{{ name: "Next class", importance: "must", text: "The time and place" }}, {{ name: "Offers", importance: "usually", text: "Any offer today", items: ["10% off"] }}, {{ name: "Tips", importance: "could", text: "A breathing tip" }}], actions: [{{ label: "Book", intent: "reserve a seat", effect: "opens the booking" }}], states: [{{ name: "empty", text: "No classes today" }}], copy: [{{ key: "title", text: "Today" }}], hero_records: ["Vinyasa 18:00"] }}, extra || {{}});
const CONFIRM = [];
for (let i = 0; i < 14; i++) CONFIRM.push({{ category: "other", text: "other " + i }});
CONFIRM.push({{ category: "identity", text: "age check" }}, {{ category: "legal", text: "waiver" }}, {{ category: "money", text: "refund rule" }}, {{ category: "money", text: "tax" }}, {{ category: "legal", text: "privacy" }});
for (let i = 14; i < 30; i++) CONFIRM.push({{ category: "other", text: "other " + i }});
const G2 = (extra) => Object.assign({{
  summary: {{ text: "A booking app for yoga classes." }}, content_hash: "sha256:abc123",
  app_map: [{{ name: "Member app", modules: ["Account", "Classes"] }}],
  flows: [{{ id: "f1", title: "Book a class", roles: ["member", "teacher"], steps: ["Home", "Class"] }}],
  content_maps: [SCREEN("scr_home", "Home"), SCREEN("scr_class", "Class")],
  data: [{{ entity: "Booking", plain: "A seat in a class" }}],
  differences_from_brief: ["Added a waitlist"], assumptions: ["One studio only"],
  confirm_list: CONFIRM, capability_tiers: [{{ capability: "camera", tier: "later" }}],
  store_rules: [{{ store: "Apple", plain: "Needs a way to delete the account" }}],
  suggestions: [{{ id: "sg1", text: "Add class packs", why: "competitors have them" }}, {{ id: "sg2", text: "Add gift cards" }}],
  degraded_nodes: ["scr_x"], code_fixes: [{{ code: "R-02", where: "scr_a" }}], flags: [], risks: [{{ text: "Payment review may be slow", severity: "warning" }}],
}}, extra || {{}});
"""


def node(body: str) -> Any:
    proc = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", PRELUDE + "\n" + body],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ------------------------------------------------------------------------------------------------ 1 inputs
def test_inputs_show_proposals_with_reasons_and_start_the_run_with_the_chosen_pair() -> None:
    r = node(
        """
const v = await mountView("inputs", { name: "inputs" }, inputsRoutes());
await until(() => byFk(v.box, "start"));
const proposals = byClass(v.box, "proposal").map(flat);
const compass = all(v.box, (n) => n.tag === "input" && n.attrs.name === "compass").map((n) => n.value);
const drift = flat(v.box).includes("Brief edited after export");
await byFk(v.box, "use-compact").click();
const afterUse = { pressed: byFk(v.box, "use-compact").attrs["aria-pressed"], startOff: byFk(v.box, "start").disabled };
await byFk(v.box, "start").click();
out({ proposals, compass, drift, afterUse, posts: posts(v.log), nav: v.nav, autoCalls: v.log.filter((c) => c.url.startsWith("/api/inputs/auto")).map((c) => c.url) });
"""
    )
    assert len(r["proposals"]) == 4
    assert (
        "smallest export of its class, newest complete research" in r["proposals"][0]
        and "rrn_9" in r["proposals"][0]
    )
    assert "thin research lane" in r["proposals"][1]
    assert "Nothing suitable" in r["proposals"][3]  # the program size has no proposal
    assert r["compass"] == ["cmp_yoga", "cmp_freight"] and r["drift"] is True
    assert r["afterUse"] == {"pressed": "true", "startOff": False}
    assert r["posts"] == [
        [
            "/api/runs",
            {
                "compass_id": "cmp_yoga",
                "offline": False,
                "research_id": "rrn_9",
                "consumer_profile": "haiku",
            },
        ]
    ]
    assert r["nav"] == ["#/preview/run_01"]
    assert sorted(r["autoCalls"]) == [
        f"/api/inputs/auto?size={s}" for s in ("compact", "deep", "program", "standard")
    ]


def test_compass_only_offline_and_not_ready_are_honoured_before_anything_is_sent() -> None:
    r = node(
        """
const v = await mountView("inputs", { name: "inputs" }, inputsRoutes());
await until(() => byFk(v.box, "start"));
await byFk(v.box, "compass-cmp_yoga").change();
await byFk(v.box, "research-rrn_9").change();
await byFk(v.box, "research-none").change();      // Compass only: no research id may be sent
await byFk(v.box, "offline").change(true);
await byFk(v.box, "start").click();
const firstPost = posts(v.log)[0][1];
const w = await mountView("inputs", { name: "inputs" }, inputsRoutes());
await until(() => byFk(w.box, "start"));
const noneYet = byFk(w.box, "start").disabled;
await byFk(w.box, "compass-cmp_freight").change();
const notReady = { off: byFk(w.box, "start").disabled, text: flat(w.box).includes("not ready: roles") };
out({ firstPost, noneYet, notReady });
"""
    )
    assert r["firstPost"] == {
        "compass_id": "cmp_yoga",
        "offline": True,
        "consumer_profile": "haiku",
    }
    assert r["noneYet"] is True
    assert r["notReady"] == {"off": True, "text": True}


def test_a_refused_estimate_is_said_in_plain_words_and_nothing_navigates() -> None:
    r = node(
        """
const routes = inputsRoutes({ "POST /api/runs": { __status: 409, body: { error: { code: "refused_estimate", message: "The highest this run could cost (USD 4.80) is more than the USD 4.00 left.", fix: "Choose a smaller input or run it offline." } } } });
const v = await mountView("inputs", { name: "inputs" }, routes);
await until(() => byFk(v.box, "start"));
await byFk(v.box, "use-compact").click();
await byFk(v.box, "start").click();
const alert = find(v.box, (n) => n.attrs.role === "alert");
out({ text: alert && flat(alert), nav: v.nav, stillOn: !byFk(v.box, "start").disabled });
"""
    )
    assert "USD 4.80" in r["text"] and "Choose a smaller input or run it offline." in r["text"]
    assert r["nav"] == [] and r["stillOn"] is True


# ------------------------------------------------------------------------------------------------ 2 preview
def test_preview_shows_the_cost_range_and_refuses_in_words_when_it_does_not_fit() -> None:
    r = node(
        """
const routes = {
  "GET /api/runs/run_01": { status: "running", estimate: { low_micros: 100000, high_micros: 900000 } },
  "GET /api/runs/run_01/preview": { profile: { id: "standard", label: "Standard app", why: "two roles, one app" }, inventory_count: 42, phases: [{ name: "Core", screens: 30 }, { name: "Later", screens: 12 }], estimate: { low_micros: 100000, high_micros: 900000 }, calls_by_step: { structure: 4, product: 11 } },
};
const tight = await mountView("preview", { name: "preview", id: "run_01" }, routes, { budget: { ...BUDGET, remaining_micros: 500000 } });
await until(() => flat(tight.box).includes("Phases"));
const roomy = await mountView("preview", { name: "preview", id: "run_01" }, routes);
await until(() => flat(roomy.box).includes("Phases"));
const tiles = byClass(tight.box, "tile").map(flat);
out({
  tiles, tightText: flat(tight.box), roomyText: flat(roomy.box),
  tightBanner: byClass(tight.box, "banner").map((b) => b.className),
  roomyBanner: byClass(roomy.box, "banner").map((b) => b.className),
  phases: rows(tight.box).slice(0, 2), links: all(tight.box, (n) => n.tag === "a").map((a) => a.attrs.href),
  gets: tight.log.map((c) => c.url),
});
"""
    )
    assert "Screens and items found42" in r["tiles"] and "Model calls planned15" in r["tiles"]
    assert "Likely costUSD 0.10 to 0.90" in r["tiles"]
    assert (
        "This run will not start" in r["tightText"]
        and "USD 0.90" in r["tightText"]
        and "An offline run costs nothing" in r["tightText"]
    )
    assert "banner banner-bad" in r["tightBanner"] and "banner banner-ok" in r["roomyBanner"]
    assert "It fits" in r["roomyText"]
    assert r["phases"][0][0] == "Core" and r["phases"][1][0] == "Later"
    assert "#/g15/run_01" in r["links"] and "#/run/run_01" in r["links"]
    assert r["gets"][:2] == ["/api/runs/run_01", "/api/runs/run_01/preview"]


# ------------------------------------------------------------------------------------------------ 3 g15
G15 = """
const G15VIEW = { app_map: [{ name: "Customer app", screens: 20, modules: ["Account", "Orders"] }], roles: [{ name: "customer", access: ["Browse", "Order"], home: "Home" }], entities: [{ name: "Order", plain: "Something a customer buys" }],
  inventory: [{ id: "i1", title: "Home", module: "Account", phase: "Core" }, { id: "i2", title: "Reports", module: "Admin", phase: "Core" }], phases: [{ name: "Core", count: 2, why: "must have", pulled_in: ["Home"] }, { name: "Later", count: 0 }] };
const g15Routes = (status, extra) => Object.assign({ "GET /api/runs/run_01": { status, gates: { g15: status === "paused_g15" ? "open" : "closed" } }, "GET /api/runs/run_01/views/g15": G15VIEW, "POST /api/runs/run_01/g15": {} }, extra || {});
"""


def test_g15_shows_the_structure_and_records_the_consent_choices() -> None:
    r = node(
        G15
        + """
const a = await mountView("g15", { name: "g15", id: "run_01" }, g15Routes("paused_g15"));
await until(() => byFk(a.box, "g15-approve"));
const text = flat(a.box);
await byFk(a.box, "g15-approve").click();
const b = await mountView("g15", { name: "g15", id: "run_01" }, g15Routes("paused_g15"));
await until(() => byFk(b.box, "g15-one"));
await byFk(b.box, "g15-one").click();
const c = await mountView("g15", { name: "g15", id: "run_01" }, g15Routes("paused_g15"));
await until(() => byFk(c.box, "move-i2"));
const changeOffBefore = byFk(c.box, "g15-change").disabled;
await byFk(c.box, "move-i2").change("Later");
const changeOffAfter = byFk(c.box, "g15-change").disabled;
const label = flat(byFk(c.box, "g15-change"));
await byFk(c.box, "g15-change").click();
const closed = await mountView("g15", { name: "g15", id: "run_01" }, g15Routes("running"));
await until(() => byFk(closed.box, "g15-approve"));
out({ text, aPosts: posts(a.log), aNav: a.nav, bPosts: posts(b.log), cPosts: posts(c.log), changeOffBefore, changeOffAfter, label, closedOff: byFk(closed.box, "g15-approve").disabled, closedText: flat(closed.box), hasMove: !!byFk(closed.box, "move-i2") });
"""
    )
    for words in (
        "Customer app",
        "Account",
        "Something a customer buys",
        "Pulled in so the app still works",
        "Home",
        "Reports",
    ):
        assert words in r["text"], words
    assert r["aPosts"] == [["/api/runs/run_01/g15", {"decision": "approve"}]] and r["aNav"] == [
        "#/run/run_01"
    ]
    assert r["bPosts"] == [
        ["/api/runs/run_01/g15", {"decision": "approve", "everything_in_one_release": True}]
    ]
    assert r["cPosts"] == [
        ["/api/runs/run_01/g15", {"decision": "change", "moves": [{"item": "i2", "to": "Later"}]}]
    ]
    assert (
        r["changeOffBefore"] is True
        and r["changeOffAfter"] is False
        and r["label"] == "Apply my 1 move"
    )
    assert r["closedOff"] is True and "already decided" in r["closedText"] and r["hasMove"] is False


# ------------------------------------------------------------------------------------------------ 4 run
def _run_routes() -> str:
    return """
const frames1 = [
  ev(1, "run.started"), ev(2, "plan.ready"), ev(3, "step.started", {}, { step: "structure" }),
  ev(4, "call.finished", { kind: "product", tokens: 1200, cost_micros: 40000 }), ev(5, "call.finished", { kind: "product", tokens: 800, cost_micros: 20000, repaired: true }),
  ev(6, "validators.done", { gate: "freeze", blockers: 1, warnings: 3 }), ev(7, "repair.rcode", { message: "R-03 fixed a label" }), ev(8, "banner", { code: "weak_route" }),
  ev(9, "run.partial", { reason: "budget_tripwire" }),
].map(frame);
const frames2 = [ev(10, "run.resumed"), ev(11, "gate.opened", { gate: "g2" }), ev(12, "run.finished", { status: "approved" })].map(frame);
let snapCalls = 0, streamCalls = 0;
const runRoutes = {
  "GET /api/runs/run_01": () => { snapCalls++; return { status: snapCalls === 1 ? "running" : "running", step: "structure", spend_micros: 0, estimate: { low_micros: 100000, high_micros: 2000000 } }; },
  "GET /api/runs/run_01/events": () => { streamCalls++; return { __stream: streamCalls === 1 ? frames1 : frames2 }; },
  "POST /api/runs/run_01/stop": {}, "POST /api/runs/run_01/resume": {},
};
"""


def test_run_screen_folds_the_live_feed_and_resumes_from_the_saved_point() -> None:
    r = node(
        _run_routes()
        + """
const v = await mountView("run", { name: "run", id: "run_01" }, runRoutes);
await until(() => v.handle.state.status === "partial");
await settle();
const text = flat(v.box);
const tiles = byClass(v.box, "tile").map(flat);
const tables = all(v.box, (n) => n.tag === "table").map((t) => rows(t));
const resumeOn = !byFk(v.box, "resume").disabled, stopOn = !byFk(v.box, "stop").disabled;
await byFk(v.box, "resume").click();
await until(() => v.handle.state.status === "approved");
await settle();
const final = flat(v.box);
out({ text, tiles, tables, resumeOn, stopOn, final, streams: v.log.filter((c) => c.url.includes("/events")).map((c) => c.url), posts: posts(v.log), links: all(v.box, (n) => n.tag === "a").map((a) => a.attrs.href), last: v.handle.state.lastSeq, resumed: v.handle.state.resumed });
"""
    )
    assert "Stopped early (partial)" in r["text"] and "the money tripwire was reached" in r["text"]
    assert "A weaker model route is in use" in r["text"]
    assert "Building the app structure" in r["text"] and "R-03 fixed a label" in r["text"]
    assert (
        "Model calls2" in r["tiles"]
        and "Tokens2.0k tokens" not in r["tiles"]
        and "Tokens2,000 tokens" in r["tiles"]
    )
    assert "Calls repaired1" in r["tiles"] and "Spent on this runUSD 0.06" in r["tiles"]
    assert ["product", "2", "2,000", "USD 0.06"] in r["tables"][0]
    assert ["freeze", "1", "3"] in r["tables"][1]
    assert r["resumeOn"] is True and r["stopOn"] is False
    assert r["posts"] == [["/api/runs/run_01/resume", {}]]
    assert r["streams"] == ["/api/runs/run_01/events?after=0", "/api/runs/run_01/events?after=9"]
    assert r["last"] == 12 and r["resumed"] == 1
    assert "Approved" in r["final"] and "#/handoff/run_01/sim" in r["links"]


def test_run_screen_opens_the_gates_and_stops_the_run() -> None:
    r = node(
        """
const frames = [ev(1, "run.started"), ev(2, "gate.opened", { gate: "g15" })].map(frame);
let calls = 0;
const routes = {
  "GET /api/runs/run_01": () => ({ status: "running", step: "structure" }),
  "GET /api/runs/run_01/events": () => { calls++; return { __stream: calls === 1 ? frames : [frame(ev(3, "run.partial", { reason: "stop" }))] }; },
  "POST /api/runs/run_01/stop": {},
};
const v = await mountView("run", { name: "run", id: "run_01" }, routes);
await until(() => v.handle.state.gates.g15 === "open");
await settle();
const g15Link = all(v.box, (n) => n.tag === "a" && n.attrs.href === "#/g15/run_01").length;
const stopOn = !byFk(v.box, "stop").disabled;
await byFk(v.box, "stop").click();
out({ g15Link, stopOn, posts: posts(v.log) });
"""
    )
    assert r["g15Link"] == 1 and r["stopOn"] is True
    assert r["posts"] == [["/api/runs/run_01/stop", {}]]


def test_run_screen_refetches_when_an_event_is_missing() -> None:
    r = node(
        """
let streams = 0, snaps = 0;
const routes = {
  "GET /api/runs/run_01": () => { snaps++; return { status: "running" }; },
  "GET /api/runs/run_01/events": () => { streams++; return { __stream: streams === 1 ? [frame(ev(1, "run.started")), frame(ev(2, "checkpoint")), frame(ev(4, "checkpoint"))] : [1, 2, 3, 4, 5].map((s) => frame(s === 5 ? ev(5, "run.finished", { status: "approved" }) : ev(s, s === 1 ? "run.started" : "checkpoint"))) }; },
};
const v = await mountView("run", { name: "run", id: "run_01" }, routes);
await until(() => v.handle.state.status === "approved");
out({ streams: v.log.filter((c) => c.url.includes("/events")).map((c) => c.url), snaps, last: v.handle.state.lastSeq, checkpoints: v.handle.state.checkpoints });
"""
    )
    assert r["streams"] == ["/api/runs/run_01/events?after=0", "/api/runs/run_01/events?after=0"]
    assert r["snaps"] == 2 and r["last"] == 5 and r["checkpoints"] == 3


# ------------------------------------------------------------------------------------------------ 5 g2
def test_g2_leads_with_the_summary_ranks_the_confirm_list_and_binds_the_hash() -> None:
    r = node(
        """
const routes = (status, extra) => ({ "GET /api/runs/run_01": { status, gates: { g2: status === "awaiting_g2" ? "open" : "closed" }, checked_by: ["self_check", "verify"], critic_vendor: "same" }, "GET /api/runs/run_01/views/g2": G2(extra), "POST /api/runs/run_01/g2": {} });
const v = await mountView("g2", { name: "g2", id: "run_01" }, routes("awaiting_g2"));
await until(() => byFk(v.box, "g2-approve"));
const cards = byClass(v.box, "card");
const first = flat(cards[0]);
const heads = all(v.box, (n) => n.tag === "h3").map(flat);
const confirm = all(byClass(v.box, "confirm")[0], (n) => n.tag === "li").map(flat);
const text = flat(v.box);
const notes = byClass(v.box, "illustrative").map(flat);
const more = flat(byFk(v.box, "more-maps") || new N("x"));
await byFk(v.box, "sg-sg2").click();
const pressed = [byFk(v.box, "sg-sg1").attrs["aria-pressed"], byFk(v.box, "sg-sg2").attrs["aria-pressed"]];
await byFk(v.box, "g2-approve").click();
// not waiting for approval, a missing builder need, a refusal from the app
const w = await mountView("g2", { name: "g2", id: "run_01" }, routes("running"));
await until(() => byFk(w.box, "g2-approve"));
const x = await mountView("g2", { name: "g2", id: "run_01" }, routes("awaiting_g2", { blocking_needs: ["BN-04 no screen map"] }));
await until(() => byFk(x.box, "g2-approve"));
const y = await mountView("g2", { name: "g2", id: "run_01" }, Object.assign(routes("awaiting_g2"), { "POST /api/runs/run_01/g2": { __status: 409, body: { error: { code: "gate_required", message: "The structure check was not approved.", fix: "Approve G1.5 first." } } } }));
await until(() => byFk(y.box, "g2-approve"));
await byFk(y.box, "g2-approve").click();
out({ first, heads, confirm, text, notes, more, pressed, posts: posts(v.log), nav: v.nav, notWaiting: [byFk(w.box, "g2-approve").disabled, flat(w.box).includes("not waiting for approval")], blocked: [byFk(x.box, "g2-approve").disabled, flat(x.box).includes("BN-04 no screen map")], refused: flat(find(y.box, (n) => n.attrs.role === "alert")), yNav: y.nav });
"""
    )
    assert "A booking app for yoga classes." in r["first"]
    assert r["heads"][0] == "In short" and r["heads"].index("In short") < r["heads"].index(
        "App map"
    )
    assert len(r["confirm"]) == 20
    assert [c.split(" ", 1)[1] for c in r["confirm"][:5]] == [
        "refund rule",
        "tax",
        "waiver",
        "privacy",
        "age check",
    ]
    assert (
        r["confirm"][0].startswith("Money")
        and r["confirm"][2].startswith("Legal")
        and r["confirm"][4].startswith("Identity")
    )
    for words in (
        "self_check, verify",
        "same",
        "scr_x",
        "Added a waitlist",
        "One studio only",
        "Needs a way to delete the account",
        "Add class packs",
        "R-02",
        "Payment review may be slow",
        "sha256:abc123",
    ):
        assert words in r["text"], words
    assert len(r["notes"]) == 3 and all(
        "Illustrative: the final layout is the front end's." in n for n in r["notes"]
    )  # the section and one per screen
    assert r["pressed"] == ["true", "false"]
    assert r["posts"] == [
        [
            "/api/runs/run_01/g2",
            {
                "decision": "approve",
                "content_hash": "sha256:abc123",
                "suggestion_choices": {"sg1": True, "sg2": False},
            },
        ]
    ]
    assert r["nav"] == ["#/handoff/run_01/sim"]
    assert r["notWaiting"] == [True, True] and r["blocked"] == [True, True]
    assert (
        "The structure check was not approved." in r["refused"]
        and "Approve G1.5 first." in r["refused"]
        and r["yNav"] == []
    )


def test_g2_shows_a_few_screens_first_and_more_on_request() -> None:
    r = node(
        """
const maps = []; for (let i = 0; i < 60; i++) maps.push(SCREEN("scr_" + i, "Screen " + i));
const routes = { "GET /api/runs/run_01": { status: "awaiting_g2" }, "GET /api/runs/run_01/views/g2": G2({ content_maps: maps }) };
const v = await mountView("g2", { name: "g2", id: "run_01" }, routes);
await until(() => byFk(v.box, "more-maps"));
const first = byClass(v.box, "contentmap").length;
const label = flat(byFk(v.box, "more-maps"));
await byFk(v.box, "more-maps").click();
out({ first, label, second: byClass(v.box, "contentmap").length });
"""
    )
    assert r == {"first": 25, "label": "Show 25 more (35 left)", "second": 50}


# ------------------------------------------------------------------------------------------------ poisoned text
def test_markup_in_pack_text_reaches_the_page_as_text_only() -> None:
    r = node(
        """
const IMG = "<img src=x onerror=alert(1)>", SCRIPT = "<script>alert(1)</script>", QUOTE = '"><svg onload=alert(2)>';
const bad = (extra) => SCREEN("scr_bad", IMG, { purpose: SCRIPT, regions: [{ name: IMG, importance: "must", text: SCRIPT, items: [QUOTE] }, { name: SCRIPT, importance: "usually", text: IMG }, { name: QUOTE, importance: "could", text: SCRIPT }], actions: [{ label: IMG, intent: SCRIPT, effect: QUOTE }], states: [{ name: IMG, text: SCRIPT }], copy: [{ key: SCRIPT, text: IMG }], hero_records: [QUOTE] });
const g2 = G2({ summary: { text: IMG + " " + SCRIPT }, content_maps: [bad()], flows: [{ id: "f", title: SCRIPT, steps: [IMG] }], assumptions: [SCRIPT], differences_from_brief: [IMG], risks: [{ text: QUOTE }], suggestions: [{ id: "x", text: IMG }], store_rules: [{ store: SCRIPT, plain: IMG }], degraded_nodes: [QUOTE] });
const routes = {
  "GET /api/runs/run_01": { status: "awaiting_g2", checked_by: [IMG], critic_vendor: SCRIPT }, "GET /api/runs/run_01/views/g2": g2, "GET /api/runs/run_01/views/g15": { inventory: [{ id: "scr_bad", title: IMG }] },
  "GET /api/profiles": PROFILES,
  "GET /api/runs/run_01/views/screen/scr_bad": bad(),
  "GET /api/runs/run_01/pack/tree": { files: [{ path: "screens/" + IMG + ".md", tokens: 5 }, { path: "global/" + SCRIPT, tokens: 5 }] },
  "GET /api/runs/run_01/pack/file": { __text: "# " + IMG + "\\n" + SCRIPT },
  "GET /api/runs/run_01/pack/checks": { results: [{ code: "V-01", severity: "error", where: IMG, why: SCRIPT, fix: QUOTE }], builder_needs: [{ id: IMG, status: SCRIPT }] },
  "GET /api/runs/run_01/pack/selfcheck": { flags: [{ what: IMG }], banners: [SCRIPT] },
};
const g2v = await mountView("g2", { name: "g2", id: "run_01" }, routes);
await until(() => byFk(g2v.box, "g2-approve"));
const env = { runId: "run_01", consumer: "haiku", mock: "typical", extra: "scr_bad", profiles: [] };
const scr = await mountView("handoff_screens", { env }, routes);
await until(() => byClass(scr.box, "contentmap").length);
const tree = await mountView("handoff_pack", { env, tab: "files" }, routes);
await until(() => all(tree.box, (n) => n.tag === "button").length);
const fileBtn = all(tree.box, (n) => n.attrs["data-fk"] && n.attrs["data-fk"].startsWith("file-"))[0];
await fileBtn.click();
await until(() => find(tree.box, (n) => n.tag === "pre"));
const checks = await mountView("handoff_pack", { env, tab: "checks" }, routes);
await until(() => rows(checks.box).length);
const self = await mountView("handoff_pack", { env, tab: "selfcheck" }, routes);
await until(() => flat(self.box).length > 10);
const boxes = [g2v.box, scr.box, tree.box, checks.box, self.box];
const forbidden = ["script", "img", "iframe", "object", "embed", "style", "link", "svg", "form", "base", "meta"];
const tags = [...new Set(created.map((n) => n.tag))];
const badAttrs = created.filter((n) => Object.keys(n.attrs).some((a) => /^on/i.test(a) || ["src", "srcdoc", "style", "formaction", "action"].includes(a))).length;
const textSeen = boxes.map((b) => [flat(b).includes(IMG), flat(b).includes(SCRIPT)]);
const leafHits = (b, s) => all(b, (n) => n.kids.length === 0 && n.own.includes(s)).length;
out({ tags, forbidden: tags.filter((t) => forbidden.includes(t)), badAttrs, textSeen, g2Img: leafHits(g2v.box, IMG), g2Script: leafHits(g2v.box, SCRIPT), pre: find(tree.box, (n) => n.tag === "pre").textContent, hrefs: created.filter((n) => n.tag === "a").map((n) => n.attrs.href) });
"""
    )
    assert r["forbidden"] == [], r["tags"]
    assert r["badAttrs"] == 0
    assert all(seen[0] or seen[1] for seen in r["textSeen"])  # the characters are on the page
    assert r["g2Img"] >= 3 and r["g2Script"] >= 3  # as text nodes, many places
    assert r["pre"] == "# <img src=x onerror=alert(1)>\n<script>alert(1)</script>"
    assert all(h is None or h.startswith(("#/", "/api/")) for h in r["hrefs"])


def test_markup_in_journey_probe_data_graph_and_history_text_is_text_too() -> None:
    r = node(
        """
const IMG = "<img src=x onerror=alert(1)>", SCRIPT = "<script>alert(1)</script>";
const routes = {
  "GET /api/runs/run_01/views/g2": G2({ flows: [{ id: "f1", title: IMG, roles: [SCRIPT] }] }),
  "GET /api/runs/run_01/views/journey/f1": { title: IMG, role: SCRIPT, stories: [SCRIPT], emotion_line: [{ label: IMG, level: 1, feeling: SCRIPT }, { label: "b", level: 4 }], moments_that_matter: [IMG], unhappy_paths: [{ what: SCRIPT, recovery: IMG }], steps: [{ screen_id: "a", title: IMG, says: SCRIPT, exits: [{ label: SCRIPT, to: "a" }] }] },
  "GET /api/runs/run_01/pack/tasks": { waves: [{ wave: "W0", tasks: [{ id: "t1", title: IMG }] }] },
  "POST /api/runs/run_01/probe": { outline: [IMG], blocking_questions: [SCRIPT], assumptions: [IMG], acceptance_coverage: [{ criterion: SCRIPT, satisfiable: false, how: IMG }] },
  "GET /api/runs/run_01/views/graph": { nodes: [{ id: "a", title: IMG }, { id: "b", title: SCRIPT, app: IMG }], edges: [{ from: "a", to: "b", label: SCRIPT }], unreachable: ["b"] },
  "GET /api/runs/run_01/views/data": { entities: [{ name: IMG, plain: SCRIPT, lifecycle: [IMG] }], operations: [{ path: SCRIPT }], integrations: [{ name: IMG }] },
  "GET /api/runs/run_01/views/needs": { needs: [{ text: IMG }], assets: [{ title: SCRIPT }] },
  "GET /api/runs": { runs: [{ id: "run_01", status: "approved", profile: IMG }] },
};
const env = { runId: "run_01", consumer: "haiku", mock: "typical", extra: null, profiles: [] };
const boxes = [];
const j = await mountView("handoff_journey", { env, tab: "journeys" }, routes); await until(() => byFk(j.box, "exit-0")); boxes.push(j.box);
const p = await mountView("handoff_probe", { env, tab: "probe" }, routes); await until(() => byFk(p.box, "probe-run")); await byFk(p.box, "probe-run").click(); await until(() => flat(p.box).includes("could not answer")); boxes.push(p.box);
const g = await mountView("handoff_graph", { env, tab: "graph" }, routes); await until(() => find(g.box, (n) => n.tag === "svg")); boxes.push(g.box);
const d = await mountView("handoff_data", { env, tab: "data" }, routes); await until(() => flat(d.box).includes("Needs") === false && flat(d.box).includes("Integrations") === false && flat(d.box).includes("Outside services")); boxes.push(d.box);
const n = await mountView("handoff_needs", { env, tab: "needs" }, routes); await until(() => flat(n.box).includes("Needs (")); boxes.push(n.box);
const r = await mountView("runs", { name: "runs" }, routes); await until(() => rows(r.box).length); boxes.push(r.box);
const tags = [...new Set(created.map((x) => x.tag))];
const bad = created.filter((x) => Object.keys(x.attrs).some((a) => /^on/i.test(a) || ["src", "srcdoc", "style", "formaction", "action"].includes(a))).length;
const forbidden = tags.filter((t) => ["script", "img", "iframe", "object", "embed", "style", "link", "form", "base", "meta"].includes(t));
const outsideSvg = created.filter((x) => x.ns !== SVGNS && ["svg", "g", "rect", "text", "path", "title"].includes(x.tag) && x.tag !== "title").length;
const textSeen = boxes.map((b) => flat(b).includes("<img src=x onerror=alert(1)>") || flat(b).includes("<script>alert(1)</script>"));
out({ forbidden, bad, textSeen, outsideSvg, hrefs: created.filter((x) => x.tag === "a").map((x) => x.attrs.href) });
"""
    )
    assert r["forbidden"] == [] and r["bad"] == 0 and r["outsideSvg"] == 0
    assert all(r["textSeen"]), r["textSeen"]
    assert all(h is None or h.startswith(("#/", "/api/")) for h in r["hrefs"])


def test_verify_says_so_when_the_app_reports_it_did_not_run() -> None:
    r = node(
        HANDOFF
        + """
const v = await mountView("handoff_pack", { env: env0(), tab: "verify" }, hoRoutes({ "GET /api/runs/run_01/pack/verify": { enabled: false } }));
await until(() => flat(v.box).includes("did not run"));
const w = await mountView("handoff_pack", { env: env0(), tab: "verify" }, hoRoutes({ "GET /api/runs/run_01/pack/verify": { findings: [{ code: "VF-1", note: "two apps disagree" }], rounds: 2, canary: [{ task_id: "t1", blocking_questions: 0 }] } }));
await until(() => flat(w.box).includes("VF-1"));
out({ off: flat(v.box), on: flat(w.box) });
"""
    )
    assert "Verify did not run for this size of app" in r["off"]
    assert "two apps disagree" in r["on"] and "Rounds" in r["on"] and "Canary" in r["on"]


# ------------------------------------------------------------------------------------------------ 6 hand-off
HANDOFF = """
const TASKS = { waves: [{ wave: "W0", tasks: [{ id: "t1", title: "Foundation", tokens: 9000 }, { id: "t2", title: "Tokens", tokens: 4000 }] }, { wave: "W1", tasks: [{ id: "t3", title: "Home screen", tokens: 12000 }, { id: "t4", title: "Class screen", tokens: 11000 }, { id: "t5", title: "Account", tokens: 10000 }] }] };
const hoRoutes = (extra) => Object.assign({
  "GET /api/profiles": PROFILES, "GET /api/runs/run_01/views/g2": G2(), "GET /api/runs/run_01/pack/tasks": TASKS,
  "GET /api/runs": { runs: [{ id: "run_01", status: "approved", spend_micros: 1200000, created_at: "2026-11-14T09:00:00+00:00", pack_hash: "abcdef1234567890" }, { id: "run_02", status: "partial", spend_micros: 300000, created_at: "2026-11-14T10:00:00+00:00" }] },
}, extra || {});
const env0 = (extra) => Object.assign({ runId: "run_01", consumer: "haiku", mock: "typical", extra: null, profiles: [] }, extra || {});
"""


def test_handoff_shell_offers_the_tabs_the_download_and_the_two_switches() -> None:
    r = node(
        HANDOFF
        + """
const v = await mountView("handoff", { name: "handoff", id: "run_01", tab: "data" }, hoRoutes({ "GET /api/runs/run_01/views/data": { entities: [] } }));
await until(() => byFk(v.box, "consumer"));
await settle();
const tabs = byClass(v.box, "tab").map((a) => [flat(a), a.attrs.href, a.attrs["aria-current"] || null]);
const dl = find(v.box, (n) => n.tag === "a" && n.attrs.download);
const consumers = all(byFk(v.box, "consumer"), (n) => n.tag === "option").map(flat);
const mocks = all(byFk(v.box, "mock"), (n) => n.tag === "option").map((o) => o.value);
await byFk(v.box, "mock").change("busy");
out({ tabs, dl: [dl.attrs.href, dl.attrs.download, flat(dl)], consumers, mocks, saved: v.ctx.store.get("mock", null), lead: flat(v.box).includes("illustrative, never the final design") });
"""
    )
    assert len(r["tabs"]) == 14
    assert r["tabs"][0] == ["Reading the pack", "#/handoff/run_01/sim", None]
    assert [t[0] for t in r["tabs"] if t[2] == "page"] == ["Data and contract"]
    assert r["dl"] == [
        "/api/runs/run_01/export/preview.html",
        "preview.html",
        "Download preview.html",
    ]
    assert r["consumers"] == ["haiku (weak)", "sonnet (strong)"]
    assert r["mocks"] == ["empty", "typical", "busy", "extreme", "broken", "offline"]
    assert r["saved"] == "busy" and r["lead"] is True


def test_simulator_all_one_and_parallel_modes() -> None:
    r = node(
        HANDOFF
        + """
const routes = hoRoutes({
  "GET /api/runs/run_01/simulate": (init, url) => {
    const mode = /mode=(\\w+)/.exec(url)[1];
    if (mode === "all") return { allowed: false, reason: "the pack is 400k tokens and this reader holds 50k", pack_tokens: 400000, whole_pack_tokens: 50000 };
    if (mode === "one") return { files: [{ part: "prefix", path: "global/tokens.md", tokens: 3000, bytes: 11000 }, { part: "card", path: "tasks/t3.md", tokens: 5000, bytes: 18000 }, { part: "closure", path: "screens/home.md", tokens: 4000, bytes: 15000 }], total_tokens: 12000, budget_tokens: 28000 };
    return { slots: 2, waves: TASKS.waves };
  },
});
const v = await mountView("handoff_sim", { env: env0(), tab: "sim" }, routes);
await until(() => flat(v.box).includes("Not possible for this reader"));
const all_ = flat(v.box);
await byFk(v.box, "mode-one").click();
await until(() => flat(v.box).includes("Within the reading budget"));
const one = { text: flat(v.box), rows: rows(v.box), url: v.log[v.log.length - 1].url };
await byFk(v.box, "mode-parallel").click();
await until(() => find(v.box, (n) => n.tag === "svg"));
const chart = find(v.box, (n) => n.tag === "svg");
const bars = all(chart, (n) => n.tag === "rect");
out({ all_, one, ns: chart.ns === SVGNS, bars: bars.length, slotsText: byClass(v.box, "tile").map(flat), waves: all(v.box, (n) => n.tag === "details").map((d) => flat(d.kids[0])), urls: v.log.filter((c) => c.url.includes("simulate")).map((c) => c.url), saved: v.ctx.store.get("simmode", null) });
"""
    )
    assert (
        "400,000 tokens" not in r["all_"]
        and "400.0k tokens" in r["all_"]
        and "the pack is 400k tokens" in r["all_"]
    )
    assert r["one"]["rows"] == [
        ["prefix", "global/tokens.md", "11,000", "3,000"],
        ["card", "tasks/t3.md", "18,000", "5,000"],
        ["closure", "screens/home.md", "15,000", "4,000"],
    ]
    assert "Within the reading budget (43% used)" in r["one"]["text"]
    assert r["ns"] is True and r["bars"] == 5
    assert (
        "Task cards5" in r["slotsText"]
        and "Worker slots2" in r["slotsText"]
        and "Waves2" in r["slotsText"]
    )
    assert r["waves"] == ["Wave W0 (2 task cards)", "Wave W1 (3 task cards)"]
    assert r["urls"][0] == "/api/runs/run_01/simulate?mode=all&consumer=haiku"
    assert r["urls"][1].startswith(
        "/api/runs/run_01/simulate?mode=one&consumer=haiku&screen=scr_home"
    )
    assert (
        r["urls"][2] == "/api/runs/run_01/simulate?mode=parallel&consumer=haiku"
        and r["saved"] == "parallel"
    )


def test_the_probe_runner_sends_the_chosen_card_and_shows_what_the_weak_reader_understood() -> None:
    r = node(
        HANDOFF
        + """
const routes = hoRoutes({ "POST /api/runs/run_01/probe": { outline: ["Build the tokens file", "Wire the theme"], blocking_questions: ["Which font for Hindi?"], assumptions: ["Light theme first"], acceptance_coverage: [{ criterion: "Colours are named", satisfiable: true, how: "tokens.md" }, { criterion: "Hindi is shown", satisfiable: false }] } });
const v = await mountView("handoff_probe", { env: env0(), tab: "probe" }, routes);
await until(() => byFk(v.box, "probe-run"));
const offlineLabel = flat(byFk(v.box, "probe-run"));
await byFk(v.box, "probe-task").change("t3");
await byFk(v.box, "probe-offline").change(false);
const liveLabel = flat(byFk(v.box, "probe-run"));
await byFk(v.box, "probe-run").click();
await until(() => flat(v.box).includes("Which font for Hindi?"));
const chips = byClass(v.box, "chip").map(flat);
out({ offlineLabel, liveLabel, posts: posts(v.log), text: flat(v.box), chips, groups: all(v.box, (n) => n.tag === "optgroup").map((g) => g.attrs.label) });
"""
    )
    assert (
        r["offlineLabel"] == "Run the offline stub"
        and r["liveLabel"] == "Run the real test (uses money)"
    )
    assert r["posts"] == [["/api/runs/run_01/probe", {"task_id": "t3", "offline": False}]]
    for words in (
        "1 question the reader could not answer",
        "Build the tokens file",
        "Light theme first",
        "Hindi is shown",
    ):
        assert words in r["text"], words
    assert r["chips"] == ["Yes", "No"] and r["groups"] == ["Wave W0", "Wave W1"]


def test_screen_pages_keep_must_usually_and_could_apart_and_switch_the_example_data() -> None:
    r = node(
        HANDOFF
        + """
const routes = hoRoutes({ "GET /api/runs/run_01/views/screen/scr_home": (init, url) => SCREEN("scr_home", "Home on " + /profile=(\\w+)/.exec(url)[1]) });
const v = await mountView("handoff_screens", { env: env0({ extra: "scr_home" }), tab: "screens" }, routes);
await until(() => byClass(v.box, "contentmap").length);
const voices = ["must", "usually", "could"].map((k) => { const s = byClass(v.box, "voice-" + k)[0]; return [flat(s.kids[0]), flat(s)]; });
const classes = byClass(v.box, "voice").map((n) => n.className);
const label = flat(byClass(v.box, "illustrative")[0]);
const list = all(v.box, (n) => n.tag === "a").map((a) => [a.attrs.href, flat(a)]);
await byFk(v.box, "mock-empty").click();
await until(() => flat(v.box).includes("Home on empty"));
out({ voices, classes, label, list, urls: v.log.filter((c) => c.url.includes("/views/screen/")).map((c) => c.url), pressed: byFk(v.box, "mock-empty").attrs["aria-pressed"], buttons: all(v.box, (n) => n.attrs["data-fk"] && n.attrs["data-fk"].startsWith("mock-")).length });
"""
    )
    assert (
        r["voices"][0][0].startswith("MUST")
        and r["voices"][1][0].startswith("USUALLY")
        and r["voices"][2][0].startswith("COULD")
    )
    assert (
        "The time and place" in r["voices"][0][1]
        and "10% off" in r["voices"][1][1]
        and "A breathing tip" in r["voices"][2][1]
    )
    assert (
        "A breathing tip" not in r["voices"][0][1] and "The time and place" not in r["voices"][2][1]
    )
    assert r["classes"] == ["voice voice-must", "voice voice-usually", "voice voice-could"]
    assert r["label"].startswith("Illustrative: the final layout is the front end's.")
    assert ["#/handoff/run_01/screens/scr_home", "Home"] in r["list"]
    assert r["urls"] == [
        "/api/runs/run_01/views/screen/scr_home?profile=typical",
        "/api/runs/run_01/views/screen/scr_home?profile=empty",
    ]
    assert r["pressed"] == "true" and r["buttons"] == 6


def test_the_journey_walkthrough_steps_by_declared_exits_and_entries() -> None:
    r = node(
        HANDOFF
        + """
const journey = { title: "Book a class", role: "member", stories: ["As a member I book a class"], emotion_line: [{ label: "browse", level: 3, feeling: "curious" }, { label: "pay", level: 1, feeling: "tense" }, { label: "done", level: 5 }], moments_that_matter: ["The payment step"], unhappy_paths: [{ what: "Card is declined", recovery: "Offer another card" }],
  steps: [{ screen_id: "scr_home", title: "Home", exits: [{ label: "Open a class", to: "scr_class" }, { label: "Leave the app", to: "outside" }] }, { screen_id: "scr_class", title: "Class", entries: ["scr_home"], exits: [] }] };
const v = await mountView("handoff_journey", { env: env0(), tab: "journeys" }, hoRoutes({ "GET /api/runs/run_01/views/journey/f1": journey }));
await until(() => byFk(v.box, "exit-0"));
const first = { text: flat(v.box), back: byFk(v.box, "back").disabled, exits: [byFk(v.box, "exit-0").disabled, byFk(v.box, "exit-1").disabled] };
const roles = all(byFk(v.box, "role"), (n) => n.tag === "option").map(flat);
await byFk(v.box, "exit-0").click();
const second = { text: flat(byClass(v.box, "step-card")[0]), back: byFk(v.box, "back").disabled, noWay: flat(v.box).includes("Nowhere") };
await byFk(v.box, "back").click();
const chart = find(v.box, (n) => n.tag === "svg");
out({ first, roles, second, backAt: flat(byClass(v.box, "step-card")[0]).startsWith("Home"), poly: !!find(chart, (n) => n.tag === "polyline"), dots: all(chart, (n) => n.tag === "circle").length, url: v.log[v.log.length - 1].url });
"""
    )
    for words in (
        "As a member I book a class",
        "The payment step",
        "Card is declined",
        "Offer another card",
        "step 1 of 2",
        "the start of the journey",
    ):
        assert words in r["first"]["text"], words
    assert r["first"]["back"] is True and r["first"]["exits"] == [
        False,
        True,
    ]  # the exit that leaves the journey cannot be followed
    assert r["roles"] == ["member", "teacher"]
    assert (
        r["second"]["text"].startswith("Class") and "Arrived from: scr_home" in r["second"]["text"]
    )
    assert r["second"]["back"] is False and r["second"]["noWay"] is True
    assert r["backAt"] is True and r["poly"] is True and r["dots"] == 3
    assert r["url"] == "/api/runs/run_01/views/journey/f1?role=member&profile=typical"


def test_the_navigation_graph_is_an_svg_with_the_role_filter_and_highlights() -> None:
    r = node(
        HANDOFF
        + """
const graph = { roles: ["customer", "courier"], entries: ["home"], nodes: [{ id: "home", title: "Home" }, { id: "orders", title: "Orders" }, { id: "detail", title: "Order detail" }, { id: "ghost", title: "Ghost screen" }, { id: "p2", title: "Gift cards", in: "later" }], edges: [{ from: "home", to: "orders", label: "tap Orders" }, { from: "orders", to: "detail" }], unreachable: ["ghost"], dead_ends: ["detail"] };
const v = await mountView("handoff_graph", { env: env0(), tab: "graph" }, hoRoutes({ "GET /api/runs/run_01/views/graph": graph }));
await until(() => find(v.box, (n) => n.tag === "svg"));
await settle();
const chart = find(v.box, (n) => n.tag === "svg");
const groups = all(chart, (n) => n.tag === "g").map((g) => g.className);
const svgTags = [...new Set(all(chart, (n) => n.tag !== "#text").map((n) => n.tag + (n.ns === SVGNS ? "" : "!")))].sort();
const text = flat(v.box);
await byFk(v.box, "graph-role").change("courier");
await until(() => v.log.filter((c) => c.url.includes("/views/graph")).length === 2);
out({ groups, svgTags, text, paths: all(chart, (n) => n.tag === "path").length, urls: v.log.filter((c) => c.url.includes("/views/graph")).map((c) => c.url), roleOpts: all(byFk(v.box, "graph-role"), (n) => n.tag === "option").map(flat), label: chart.attrs["aria-label"] });
"""
    )
    assert r["svgTags"] == [
        "defs",
        "g",
        "marker",
        "path",
        "rect",
        "svg",
        "text",
        "title",
    ]  # all in the SVG namespace (no "!")
    assert r["groups"][0].startswith("node in")
    assert (
        any("unreachable" in g for g in r["groups"])
        and any("deadend" in g for g in r["groups"])
        and any("later" in g for g in r["groups"])
    )
    assert (
        "Ghost screen" in r["text"]
        and "Nobody can reach these screens" in r["text"]
        and "These screens have no way out" in r["text"]
    )
    assert "5 screens, 1 later, 1 unreachable, 1 dead ends." in r["text"]
    assert r["paths"] >= 2 and "1 unreachable" in r["label"]
    assert r["urls"] == [
        "/api/runs/run_01/views/graph",
        "/api/runs/run_01/views/graph?role=courier",
    ]
    assert r["roleOpts"] == ["Every role", "customer", "courier"]


def test_data_and_needs_tabs_show_every_section() -> None:
    r = node(
        HANDOFF
        + """
const data = { entities: [{ name: "Booking", plain: "A seat in a class", lifecycle: ["requested", "paid", "attended"], fields: ["id", "class"] }], operations: [{ method: "POST", path: "/bookings", summary: "Create a booking" }], fixtures: [{ entity: "Booking", count: 12 }], mock_behaviours: [{ name: "slow pay", text: "Payment takes 5 s" }], integrations: [{ name: "Stripe", interrupted_flow: "resume at the cart", pending_timeout: "10 min" }] };
const needs = { needs: [{ id: "N1", text: "A map" }], blocks: [{ id: "B1", name: "ClassCard" }], assets: [{ id: "A1", title: "Hero photo", sourcing: "licensed" }], motion_intents: [], stories: [{ id: "S1", text: "Book" }], opportunities: [{ id: "O1", text: "Widgets" }] };
const d = await mountView("handoff_data", { env: env0(), tab: "data" }, hoRoutes({ "GET /api/runs/run_01/views/data": data }));
await until(() => flat(d.box).includes("Stripe"));
const n = await mountView("handoff_needs", { env: env0(), tab: "needs" }, hoRoutes({ "GET /api/runs/run_01/views/needs": needs }));
await until(() => flat(n.box).includes("Hero photo"));
out({ d: flat(d.box), n: flat(n.box), dRows: rows(d.box), nHeads: all(n.box, (x) => x.tag === "h3").map(flat) });
"""
    )
    for words in (
        "requested → paid → attended",
        "Create a booking",
        "Payment takes 5 s",
        "resume at the cart",
        "10 min",
    ):
        assert words in r["d"], words
    assert "Hero photo" in r["n"] and "licensed" in r["n"]
    assert r["nHeads"][1].startswith("Needs") and "(0)" in r["nHeads"][4]


def test_pack_explorer_tree_file_checks_matrices_and_reports() -> None:
    r = node(
        HANDOFF
        + """
const routes = hoRoutes({
  "GET /api/runs/run_01/pack/tree": { files: [{ path: "global/tokens.md", tokens: 3000 }, { path: "screens/home.md", tokens: 4000 }, { path: "MANIFEST.json", tokens: 50 }], task_read_budget: 28000, tasks: [{ id: "t3", title: "Home", read_tokens: 20000 }, { id: "t9", title: "Huge", read_tokens: 31000 }] },
  "GET /api/runs/run_01/pack/file": (init, url) => ({ __text: "FILE " + decodeURIComponent(/path=([^&]+)/.exec(url)[1]) }),
  "GET /api/runs/run_01/pack/budget": { by_kind: { screens: 4000, global: 3000 }, tasks: [{ id: "t3", total: 20000, budget: 28000 }] },
  "GET /api/runs/run_01/pack/checks": { results: [{ code: "V-11", severity: "error", where: "scr_a", why: "unknown entity", fix: "rename it" }, { code: "V-30", severity: "warning", where: "scr_b", why: "long label", fix: "shorten" }], builder_needs: [{ id: "BN-01", need: "route map", status: "ok" }, { id: "BN-02", need: "token names", status: "missing" }], coverage: { rows: ["customer", "courier"], columns: ["home", "orders"], cells: [[true, true], [true, false]] } },
  "GET /api/runs/run_01/pack/selfcheck": { rcode_changes: [{ code: "R-03", where: "scr_a" }], reasks: [], flags: [{ what: "thin research" }], banners: ["weak_route"] },
  "GET /api/runs/run_01/pack/verify": { __status: 404, body: { error: { code: "not_found", message: "" } } },
  "GET /api/runs/run_02/pack/verify": { enabled: false },
  "GET /api/runs/run_01/diff/run_02": { files: [{ path: "screens/home.md", status: "changed" }, { path: "MANIFEST.json", status: "same" }, { path: "screens/new.md", status: "added" }] },
});
const mt = (tab, extra) => mountView("handoff_pack", { env: env0({ extra }), tab }, routes);
const files = await mt("files");
await until(() => byFk(files.box, "file-global/tokens.md"));
const treeText = flat(byClass(files.box, "tree")[0]);
await byFk(files.box, "file-global/tokens.md").click();
await until(() => find(files.box, (n) => n.tag === "pre"));
const pre = find(files.box, (n) => n.tag === "pre").textContent;
const taskRows = rows(files.box).filter((c) => c.length === 5);
const tokens = await mt("tokens");
await until(() => flat(tokens.box).includes("Screens"));
const checks = await mt("checks");
await until(() => flat(checks.box).includes("Coverage"));
const self = await mt("selfcheck");
await until(() => flat(self.box).includes("R-03"));
const verify = await mt("verify");
await until(() => flat(verify.box).length > 5);
const diff = await mt("diff");
await until(() => flat(diff.box).includes("files differ"));
const hist = await mt("history");
await until(() => rows(hist.box).length);
out({ treeText, pre, taskRows, tokens: flat(tokens.box), checkText: flat(checks.box), checkRows: rows(checks.box), self: flat(self.box), verify: flat(verify.box), diff: [flat(diff.box), rows(diff.box)], hist: rows(hist.box), histLinks: all(hist.box, (n) => n.tag === "a" && /diff/.test(n.attrs.href || "")).map((a) => a.attrs.href), fileUrl: files.log.filter((c) => c.url.includes("/pack/file"))[0].url });
"""
    )
    assert (
        "global/" in r["treeText"]
        and "7.0k tokens" not in r["treeText"]
        and "3,000 tokens" in r["treeText"]
        and "MANIFEST.json" in r["treeText"]
    )
    assert (
        r["pre"] == "FILE global/tokens.md"
        and r["fileUrl"] == "/api/runs/run_01/pack/file?path=global%2Ftokens.md"
    )
    assert ["t3", "Home", "20,000 tokens", "28,000", "yes"] in r["taskRows"] and [
        "t9",
        "Huge",
        "31,000 tokens",
        "28,000",
        "NO: over",
    ] in r["taskRows"]
    assert "By kind" in r["tokens"] and "4000" in r["tokens"]
    assert "2 checks reported something; 1 must be fixed." in r["checkText"]
    assert ["Must fix", "V-11", "scr_a", "unknown entity", "rename it"] in r["checkRows"] and [
        "Check",
        "V-30",
        "scr_b",
        "long label",
        "shorten",
    ] in r["checkRows"]
    assert ["BN-02", "token names", "missing"] in r["checkRows"]  # the builder-needs matrix
    assert ["courier", "yes", "no"] in r["checkRows"] and ["customer", "yes", "yes"] in r[
        "checkRows"
    ]  # the coverage matrix
    for words in ("R-03", "thin research", "weak_route"):
        assert words in r["self"], words
    assert (
        "no Verify report" in r["verify"] and "Verify only runs for big (deep) apps" in r["verify"]
    )
    assert (
        "2 files differ." in r["diff"][0]
        and ["screens/home.md", "changed"] in r["diff"][1]
        and ["MANIFEST.json", "same"] not in r["diff"][1]
    )
    assert len(r["hist"]) == 2 and r["hist"][0][0] == "run_02"  # newest first
    assert r["histLinks"] == ["#/handoff/run_01/diff/run_02"]


# ------------------------------------------------------------------------------------------------ money page and history
def test_the_money_page_states_the_cap_the_tripwire_and_stops_everything() -> None:
    r = node(
        """
const exhausted = { ...BUDGET, spent_micros: 5000000, remaining_micros: 0, state: "exhausted" };
const tripwire = { ...BUDGET, spent_micros: 4600000, remaining_micros: 400000, state: "tripwire" };
const mk = (b) => ({ "GET /api/budget": b, "GET /api/config": { budget: { cap_usd: 5, tripwire_usd: 4.5 }, task_read_budget: 28000 }, "GET /api/runs": { runs: [{ id: "run_01", status: "partial", spend_micros: 2500000, created_at: "2026-11-14T09:00:00Z" }] }, "POST /api/stop": {} });
const a = await mountView("budget", { name: "budget" }, mk(exhausted));
await until(() => byFk(a.box, "stop-all"));
const b = await mountView("budget", { name: "budget" }, mk(tripwire));
await until(() => byFk(b.box, "stop-all"));
await byFk(a.box, "stop-all").click();
await until(() => flat(a.box).includes("Asked every running run to stop"));
const { flattenConfig } = await import(ROOT + "/views/budget.js");
out({ a: flat(a.box), b: flat(b.box), banners: [byClass(a.box, "banner")[0].className, byClass(b.box, "banner")[0].className], posts: posts(a.log), cfg: flattenConfig({ x: { y: 1, z: [2] }, k: "v" }), tiles: byClass(a.box, "tile").map(flat) });
"""
    )
    assert (
        "Spent USD 5.00 of the USD 5.00 cap" in r["a"]
        and "The money cap (USD 5.00) is used up" in r["a"]
        and "nothing that costs money can start" in r["a"].lower()
    )
    assert "tripwire" in r["b"].lower() and "no new step starts" in r["b"]
    assert r["banners"] == ["banner banner-bad", "banner banner-warn"]
    assert r["posts"] == [["/api/stop", {}]]
    assert r["cfg"] == [
        {"name": "x.y", "value": "1"},
        {"name": "x.z", "value": "2"},
        {"name": "k", "value": "v"},
    ]
    assert "CapUSD 5.00" in r["tiles"] and "TripwireUSD 4.50" in r["tiles"]


def test_run_history_lists_runs_newest_first_and_says_when_there_are_none() -> None:
    r = node(
        """
const routes = { "GET /api/runs": { runs: [{ id: "run_a", status: "approved", spend_micros: 100000, created_at: "2026-11-13T09:00:00Z" }, { id: "run_b", status: "failed", created_at: "2026-11-14T09:00:00Z" }] } };
const v = await mountView("runs", { name: "runs" }, routes);
await until(() => rows(v.box).length);
const empty = await mountView("runs", { name: "runs" }, { "GET /api/runs": { runs: [] } });
await until(() => flat(empty.box).includes("No runs yet"));
const broken = await mountView("runs", { name: "runs" }, { "GET /api/runs": { __status: 500, body: null } });
await until(() => find(broken.box, (n) => n.attrs.role === "alert"));
out({ rows: rows(v.box), links: all(v.box, (n) => n.tag === "a").map((a) => a.attrs.href), empty: flat(empty.box), broken: flat(broken.box) });
"""
    )
    assert [row[0] for row in r["rows"]] == ["run_b", "run_a"]
    assert r["rows"][0][1] == "Failed" and r["rows"][1][3] == "USD 0.10"
    assert r["links"] == ["#/run/run_b", "#/run/run_a"]
    assert "No runs yet" in r["empty"] and "had a problem" in r["broken"]


# ------------------------------------------------------------------------------------------------ the shell (app.js)
SHELL = """
const ids = ["skip", "fake-banner", "bar", "mode-pill", "theme-toggle", "money", "money-text", "money-meter", "money-fill", "money-hold", "money-trip", "notices", "rail", "view", "announcer"];
const els = Object.fromEntries(ids.map((id) => [id, new N("div")]));
els["fake-banner"].hidden = true;
const listeners = {};
Object.assign(globalThis.document, { getElementById: (id) => els[id], documentElement: new N("html"), addEventListener() {}, title: "", body: new N("body") });
globalThis.window = { location: { hash: "" }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, addEventListener: (t, f) => { listeners[t] = f; }, scrollTo() {} };
globalThis.setInterval = () => 0;
async function startShell(routes, hash) {
  const log = [];
  globalThis.fetch = fakeFetch(Object.assign({ "GET /api/budget": BUDGET }, routes), log);
  window.location.hash = hash || "";
  await import(ROOT + "/app.js");
  await until(() => flat(els.rail).includes("Your journey"));
  await settle();
  return log;
}
const railLinks = () => all(els.rail, (n) => n.tag === "a").map((a) => [a.attrs.href, flat(a), a.attrs["aria-current"] || null]);
const HEALTH = (extra) => Object.assign({ ready: true, mode: "live", reasons: [], fake: false, version: "1" }, extra || {});
"""


def test_shell_shows_test_fake_live_mode_money_and_the_journey_rail() -> None:
    r = node(
        SHELL
        + """
await startShell({ ...inputsRoutes(), "GET /api/health": HEALTH({ fake: true }) }, "#/inputs");
await until(() => !els["fake-banner"].hidden && flat(els["money-text"]).startsWith("Spent"));
out({
  fakeHidden: els["fake-banner"].hidden, mode: [els["mode-pill"].className, flat(els["mode-pill"])],
  money: [els.money.className, flat(els["money-text"]), els["money-fill"].style.width, els["money-trip"].style.left, els["money-meter"].attrs["aria-valuenow"]],
  rail: all(els.rail, (n) => n.tag === "li").map(flat), disabled: all(els.rail, (n) => n.attrs["aria-disabled"] === "true").length,
  links: railLinks(), title: document.title, notices: flat(els.notices),
});
"""
    )
    assert r["fakeHidden"] is False
    assert r["mode"] == ["mode mode-live", "LIVE: real models; this costs money"]
    assert r["money"] == [
        "money money-ok",
        "Spent USD 1.00 of the USD 5.00 cap (USD 4.00 left). Tripwire USD 4.50.",
        "20.0%",
        "90.0%",
        "20",
    ]
    assert [x.split(" (")[0] for x in r["rail"]][:6] == [
        "1 Inputs",
        "2 Preview",
        "3 Structure check",
        "4 Run",
        "5 Spec review",
        "6 Hand-off",
    ]
    assert r["rail"][6:] == ["Run history", "Money"]
    assert r["disabled"] == 5 and r["links"][0] == ["#/inputs", "1 Inputs", "page"]
    assert r["title"] == "1. Inputs · Spec Bench" and r["notices"] == ""


def test_shell_keeps_the_fake_banner_hidden_for_a_real_app_and_warns_at_the_tripwire() -> None:
    r = node(
        SHELL
        + """
const trip = { ...BUDGET, spent_micros: 4600000, remaining_micros: 400000, state: "tripwire" };
await startShell({ ...inputsRoutes(), "GET /api/health": HEALTH({ mode: "offline_only", ready: false, reasons: ["No model key is set"] }), "GET /api/budget": trip }, "#/inputs");
await until(() => flat(els.notices).includes("tripwire"));
out({ fakeHidden: els["fake-banner"].hidden, mode: flat(els["mode-pill"]), moneyClass: els.money.className, notices: flat(els.notices), banners: byClass(els.notices, "banner").map((b) => b.className) });
"""
    )
    assert r["fakeHidden"] is True
    assert r["mode"] == "Offline only: no model is called, nothing is spent"
    assert r["moneyClass"] == "money money-tripwire"
    assert (
        "no new step starts" in r["notices"]
        and "not ready for live work yet" in r["notices"]
        and "No model key is set" in r["notices"]
    )
    assert r["banners"] == ["banner banner-warn", "banner banner-warn"]


def test_shell_says_when_the_app_cannot_be_reached() -> None:
    r = node(
        SHELL
        + """
const down = () => { throw new TypeError("network"); };
await startShell({ "GET /api/health": down, "GET /api/budget": down, "GET /api/inputs": down, "GET /api/profiles": down }, "#/inputs");
await until(() => flat(els.notices).includes("cannot reach"));
out({ money: flat(els["money-text"]), cls: els.money.className, mode: flat(els["mode-pill"]), notices: flat(els.notices), alert: byClass(els.notices, "banner")[0].attrs.role });
"""
    )
    assert r["money"] == "Money: not connected" and r["cls"] == "money money-unknown"
    assert (
        r["mode"] == "No connection"
        and "cannot reach the Spec Bench app" in r["notices"]
        and r["alert"] == "alert"
    )


def test_shell_routes_to_a_run_and_points_the_rail_at_the_step_that_needs_the_owner() -> None:
    r = node(
        SHELL
        + """
const routes = { ...inputsRoutes(), "GET /api/health": HEALTH(), "GET /api/runs/run_01": { status: "paused_g15", estimate: { low_micros: 1, high_micros: 2 } }, "GET /api/runs/run_01/preview": { inventory_count: 3 } };
await startShell(routes, "#/preview/run_01");
await until(() => flat(els.view).includes("Screens and items found"));
await until(() => flat(els.rail).includes("needs your OK"));
out({ links: railLinks(), disabled: all(els.rail, (n) => n.attrs["aria-disabled"] === "true").length, title: document.title, view: flat(els.view).slice(0, 40) });
"""
    )
    assert ["#/preview/run_01", "2 Preview (costs nothing)", "page"] in r["links"]
    assert ["#/g15/run_01", "3 Structure check (G1.5) needs your OK", None] in r["links"]
    assert ["#/handoff/run_01/sim", "6 Hand-off (mock front end)", None] in r["links"]
    assert r["disabled"] == 0 and r["title"] == "2. Preview · Spec Bench"
