"""The Budget page's "Search credits" section drawn for real, without a browser: a tiny stand-in for the few DOM calls the
house helper ``h()`` makes (createElement, text nodes, attributes, listeners), enough to mount ``views/credits.js`` under
Node with a fake API and read back what a person would read. Skipped without Node."""

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
class N {{
  constructor(tag, text) {{ this.tag = tag; this.kids = []; this.attrs = {{}}; this.on = {{}}; this.className = ""; this.style = {{}}; this.up = null; this.own = text === undefined ? "" : text; this.disabled = false; this.value = ""; }}
  get firstChild() {{ return this.kids[0] || null; }}
  appendChild(k) {{ if (k.up) k.up.removeChild(k); k.up = this; this.kids.push(k); return k; }}
  append(...ks) {{ for (const k of ks) this.appendChild(typeof k === "string" ? new N("#text", k) : k); }}
  removeChild(k) {{ this.kids = this.kids.filter((x) => x !== k); k.up = null; return k; }}
  setAttribute(n, v) {{ this.attrs[n] = String(v); if (n === "disabled") this.disabled = true; }}
  getAttribute(n) {{ return n in this.attrs ? this.attrs[n] : null; }}
  addEventListener(t, f) {{ (this.on[t] = this.on[t] || []).push(f); }}
  set textContent(t) {{ this.kids = []; this.own = String(t); }}
  get textContent() {{ return this.own + this.kids.map((k) => k.textContent).join(" "); }}
  contains(n) {{ return n === this || this.kids.some((k) => k.contains(n)); }}
  focus() {{}}
  querySelectorAll() {{ return []; }}
  click() {{ return Promise.all((this.on.click || []).map((f) => f())); }}
}}
globalThis.document = {{ createElement: (t) => new N(t), createTextNode: (s) => new N("#text", s), activeElement: null, hidden: false }};
const walk = (n, f) => {{ f(n); n.kids.forEach((k) => walk(k, f)); }};
const find = (n, pred) => {{ let hit = null; walk(n, (x) => {{ if (!hit && pred(x)) hit = x; }}); return hit; }};
const all = (n, pred) => {{ const out = []; walk(n, (x) => {{ if (pred(x)) out.push(x); }}); return out; }};
const byFk = (n, fk) => find(n, (x) => x.attrs["data-fk"] === fk);
const out = (v) => process.stdout.write(JSON.stringify(v));
const flat = (n) => n.textContent.replace(/\\s+/g, " ").trim();
const tick = () => new Promise((r) => setTimeout(r, 5));
const prov = (o) => Object.assign({{ provider: "tavily", label: "Tavily", unit: "credit", used: null, limit: null, remaining: null, level: "unknown", source: "counted", as_of: null, read_at: null, plan: null, note: "", key_present: true, can_check: true, baseline: null }}, o);
const FIXTURE = {{
  enabled: true, primary: "tavily", write_failed: false, check: {{ checked_at: null, error: null, wait_s: 0 }},
  providers: [
    prov({{ used: 93, limit: 1000, remaining: 907, level: "ok", source: "provider", as_of: "2026-11-13T09:00:00+00:00", read_at: "2026-11-13T09:00:00+00:00", plan: "Researcher", note: "Tavily itself reported this figure." }}),
    prov({{ provider: "serper", label: "Serper", remaining: 2389, level: "ok", can_check: false, as_of: "2026-11-13T09:00:00+00:00", note: "Serper does not report its balance." }}),
  ],
  per_run: [{{ run_id: "rrn_1", at: "2026-11-13T09:30:00+00:00", credits: {{ tavily: 33, serper: 2 }}, total: 35 }}],
  estimate_hint: {{ small: {{ tavily: {{ low: 28, mid: 32, high: 41 }}, serper: {{ low: 22, mid: 26, high: 41 }} }} }},
}};
function make(api) {{
  const calls = [];
  const ctx = {{
    api: {{
      get: async (p) => {{ calls.push(["GET", p]); return (api && api.get ? api.get(p) : FIXTURE); }},
      post: async (p, b) => {{ calls.push(["POST", p, b]); return api && api.post ? api.post(p, b) : FIXTURE; }},
    }},
    announce() {{}}, on() {{ return () => {{}}; }}, refreshCredits() {{}}, state: {{ credits: null }},
  }};
  return {{ ctx, calls }};
}}
"""


def node(body: str) -> Any:
    proc = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", PRELUDE + "\n" + body],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_section_shows_each_provider_in_plain_words() -> None:
    r = node(
        """
const { mountCredits } = await import(ROOT + "/views/credits.js");
const { ctx, calls } = make();
const box = new N("section");
mountCredits(box, ctx);
await tick();
const cards = all(box, (n) => (n.className || "").split(" ").includes("credit-card"));
const bar = find(box, (n) => n.attrs.role === "progressbar");
out({
  calls,
  cards: cards.map(flat),
  levels: cards.map((c) => c.className),
  bar: bar && [bar.attrs["aria-valuenow"], bar.attrs["aria-label"], bar.kids[0].style.width],
  buttons: all(box, (n) => n.tag === "button").map((b) => b.textContent),
  runs: all(box, (n) => n.tag === "tr").map(flat),
  need: flat(find(box, (n) => n.tag === "details" && n.textContent.includes("What a run usually needs"))),
  all: flat(box),
});
"""
    )
    assert r["calls"] == [["GET", "/api/credits"]]
    tavily, serper = r["cards"]
    assert "Tavily" in tavily and "93 of 1,000 used" in tavily and "907" in tavily
    assert "from Tavily" in tavily and "Plan: Researcher" in tavily and "Plenty left" in tavily
    assert "Serper" in serper and "2,389 left" in serper and "counted by this app" in serper
    assert "does not report its balance" in serper
    assert r["bar"] == ["9", "Tavily credits used", "9.3%"]  # a bar where the allowance is known
    assert r["levels"] == ["credit-card credits-ok", "credit-card credits-ok"]
    assert "Ask Tavily now" in r["buttons"] and "Save this number" in r["buttons"]
    assert any("rrn_1" in t or "Tavily 33 · Serper 2" in t for t in r["runs"])
    assert "Small: Tavily about 32 (up to 41) · Serper about 26 (up to 41)" in r["need"]
    assert "never stops a run" in r["all"] and "not money" in r["all"]
    assert "usd" not in r["all"].lower()


def test_asking_tavily_shows_what_happened_and_never_throws() -> None:
    r = node(
        """
const { mountCredits } = await import(ROOT + "/views/credits.js");
const answers = [
  { ...FIXTURE, refresh: { done: true, throttled: false, error: null } },
  { ...FIXTURE, refresh: { done: false, throttled: true, wait_s: 33, error: null } },
  { ...FIXTURE, refresh: { done: false, throttled: false, error: "auth" } },
];
const seen = [];
const { ctx, calls } = make({ post: async (p) => { seen.push(p); return answers.shift(); } });
const box = new N("section");
mountCredits(box, ctx);
await tick();
const said = [];
for (let i = 0; i < 3; i++) {
  await byFk(box, "cr-refresh").click();
  await tick();
  const banner = find(box, (n) => (n.className || "").includes("banner"));
  said.push([banner ? banner.className : null, banner ? flat(banner) : null]);
}
out({ seen, said, posts: calls.filter((c) => c[0] === "POST").length });
"""
    )
    assert r["seen"] == ["/api/credits/refresh"] * 3 and r["posts"] == 3
    assert r["said"][0][0].endswith("banner-ok") and "up to date" in r["said"][0][1]
    assert r["said"][1][0].endswith("banner-ok") and "33 s" in r["said"][1][1]
    assert r["said"][2][0].endswith("banner-warn") and "did not accept the key" in r["said"][2][1]
    assert "this app has counted" in r["said"][2][1]


def test_the_balance_form_checks_before_it_sends_and_sends_whole_numbers() -> None:
    r = node(
        """
const { mountCredits } = await import(ROOT + "/views/credits.js");
const { ctx, calls } = make();
const box = new N("section");
mountCredits(box, ctx);
await tick();
const set = (fk, v) => { byFk(box, fk).value = v; };
const errorText = () => (find(box, (n) => n.attrs.role === "alert" && n.kids.length) || { textContent: "" }).textContent;
set("cr-provider", "tavily"); set("cr-amount", "93.5");
await byFk(box, "cr-save").click();
const bad = errorText();
const afterBad = calls.filter((c) => c[0] === "POST").length;
set("cr-amount", "93"); set("cr-limit", "1000");
await byFk(box, "cr-save").click();
await tick();
out({ bad, afterBad, posts: calls.filter((c) => c[0] === "POST"), saved: box.textContent.includes("Saved. The app counts from this moment.") });
"""
    )
    assert "whole number" in r["bad"] and r["afterBad"] == 0  # refused in the page: nothing is sent
    assert r["posts"] == [
        [
            "POST",
            "/api/credits/baseline",
            {"provider": "tavily", "kind": "used", "amount": 93, "limit": 1000},
        ]
    ]
    assert r["saved"] is True


def test_a_failed_load_says_so_and_credits_off_says_so() -> None:
    r = node(
        """
const { mountCredits } = await import(ROOT + "/views/credits.js");
const { ApiError } = await import(ROOT + "/lib/api.js");
const a = make({ get: async () => { throw new ApiError(500, null); } });
const boxA = new N("section");
mountCredits(boxA, a.ctx);
await tick();
const b = make({ get: async () => ({ enabled: false, providers: [], per_run: [], estimate_hint: {} }) });
const boxB = new N("section");
mountCredits(boxB, b.ctx);
await tick();
const c = make({ get: async () => ({ ...FIXTURE, per_run: [], providers: [prov({}), prov({ provider: "serper", label: "Serper", key_present: false })] }) });
const boxC = new N("section");
mountCredits(boxC, c.ctx);
await tick();
out({ failed: flat(boxA), off: flat(boxB), fresh: flat(boxC), freshOpen: find(boxC, (n) => n.tag === "details" && n.textContent.includes("Enter or correct")).attrs.open !== undefined });
"""
    )
    assert "could not" in r["failed"].lower() or "wrong" in r["failed"].lower() or r["failed"]
    assert "not being tracked" in r["off"]
    assert "No count yet" in r["fresh"] and "No Serper key is set" not in r["fresh"]
    assert r["freshOpen"] is True  # with nothing known the balance form starts open
