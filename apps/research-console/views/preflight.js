/* The Preflight panel: every check, failing ones first, each with what it found (detail) and what to do (fix), and the
   "Test the connection" button (about USD 0.01: one search and one tiny model call, booked in the spend record). */
import { h, clear, untrusted } from "../lib/dom.js";
import { modal } from "./dialogs.js";
import { usd, humanize } from "../lib/format.js";
import { ApiError } from "../lib/api.js";

function checkRow(c, ready) {
  const optional = ready && /^key:/.test(c.id) && !c.ok;
  const state = c.ok ? "ok" : optional ? "optional" : "bad";
  const word = c.ok ? "Ready" : optional ? "Not needed now" : "Needs fixing";
  return h(
    "li",
    { class: "check check-" + state },
    h("span", { class: "check-state" }, word),
    h("span", { class: "check-label" }, untrusted(c.label)),
    c.detail ? h("span", { class: "check-detail" }, untrusted(c.detail)) : null,
    !c.ok && c.fix ? h("span", { class: "check-fix" }, "What to do: ", untrusted(c.fix)) : null
  );
}

function pingLines(res) {
  const s = res && res.search ? res.search : {};
  const m = res && res.model ? res.model : {};
  const why = (x) => (x.error ? " (" + humanize(x.error) + ")" : "");
  return [
    s.ok ? "Search works" + (s.provider ? " (" + s.provider + (s.results !== undefined ? ", " + s.results + " results" : "") + ")" : "") + "." : "Search did not work" + why(s) + ".",
    m.ok ? "The model works" + (typeof m.cost_micros === "number" ? " (cost " + usd(m.cost_micros) + ")" : "") + "." : "The model did not answer" + why(m) + ".",
  ];
}

export function openPreflight(ctx) {
  const m = modal("Is the app ready?", { wide: true });
  const list = h("ul", { class: "checks" });
  const head = h("p", { class: "dlg-line" });
  const meta = h("p", { class: "muted" });
  const result = h("div", { class: "ping-result", role: "status" });
  const pingBtn = h("button", { type: "button", class: "btn", "data-fk": "ping" }, "Test the connection (about USD 0.01)");
  const recheck = h("button", { type: "button", class: "btn", "data-fk": "recheck" }, "Check again");

  function draw() {
    const pre = ctx.state.preflight;
    clear(list);
    if (!pre) {
      head.textContent = ctx.state.online ? "Checking…" : "We cannot reach the app, so nothing can be checked yet.";
      return;
    }
    head.textContent = pre.ready ? "Everything needed for a live run is ready." : "Not ready. Fix the items marked “Needs fixing”, then check again.";
    head.className = "dlg-line " + (pre.ready ? "tone-ok" : "tone-bad");
    const checks = (pre.checks || []).slice().sort((a, b) => Number(a.ok) - Number(b.ok));
    for (const c of checks) list.appendChild(checkRow(c, pre.ready));
    meta.textContent = [pre.model ? "Model in use: " + pre.model : "", pre.checker_vendor ? "Checker: " + pre.checker_vendor : ""].filter(Boolean).join(" · ");
  }
  const off = ctx.on("preflight", draw);
  m.closed.then(off);
  draw();

  recheck.addEventListener("click", async () => {
    recheck.disabled = true;
    await ctx.refreshPreflight();
    recheck.disabled = false;
    recheck.focus();
  });
  pingBtn.addEventListener("click", async () => {
    pingBtn.disabled = true;
    clear(result);
    result.appendChild(h("p", null, "Testing… (one search and one tiny model call)"));
    try {
      const res = await ctx.api.post("/api/preflight/ping", {});
      clear(result);
      for (const line of pingLines(res)) result.appendChild(h("p", null, line));
      ctx.announce("Connection test finished");
    } catch (e) {
      clear(result);
      const p = e instanceof ApiError ? e.problem : null;
      result.appendChild(h("p", { class: "tone-bad" }, p ? p.text : "The test could not run."));
      if (p && p.missing.length) result.appendChild(h("p", null, "Missing: ", h("code", null, p.missing.join(", "))));
    }
    pingBtn.disabled = false;
    ctx.refreshBudget();
  });

  m.body.append(head, list, meta, h("div", { class: "row" }, pingBtn, recheck), result);
  m.foot.appendChild(h("button", { type: "button", class: "btn btn-primary", "data-autofocus": "1", onclick: () => m.close("done") }, "Close"));
  m.focusFirst();
}
