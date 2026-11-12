/* The "Search credits" section of the Budget page: one card per provider (used or left, a bar where the allowance is
   known, where the figure came from, when Tavily was last asked), a Refresh button, a small form to enter a balance,
   and the credits each run was billed. A credit is what a provider counts for a search, NOT money. Everything here is
   advice: nothing on this page can stop a run. */
import { h, clear, setKids, untrusted, navLink, keepFocus } from "../lib/dom.js";
import { problemBanner } from "./shared.js";
import { formatDateTime, SIZE_ORDER, sizeWord } from "../lib/format.js";
import { ApiError } from "../lib/api.js";
import { figureText, levelWords, creditPercent, sourceWords, checkedText, runCreditsText, baselineForm, refreshWords, needText, grouped } from "../lib/credits.js";

const MAX_ROWS = 15;
const NAMES = { tavily: "Tavily", serper: "Serper" };

export function mountCredits(box, ctx) {
  let data = null;
  let failure = null;
  let outcome = null; // the words for the last Refresh
  let saved = ""; // the words for the last saved balance
  let busy = false;
  let gone = false;
  let formReady = false; // the form is drawn once, so the 15 s refresh never wipes what is being typed
  const noteBox = h("div", { class: "notice-slot" });
  const cards = h("div", { class: "credit-cards" });
  const formBox = h("div", null);
  const runsBox = h("div", null);
  const needBox = h("div", null);
  box.append(
    h("h3", null, "Search credits"),
    h(
      "p",
      { class: "muted" },
      "A credit is what a search provider counts for each search; it is not money. A free allowance is counted in credits and can run out while the budget above still has room. This is advice only: when credits run low the app tells you, and it never stops a run."
    ),
    noteBox,
    cards,
    formBox,
    h("h4", null, "Credits used by each run"),
    runsBox,
    needBox
  );

  async function load() {
    try {
      data = await ctx.api.get("/api/credits");
      failure = null;
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "The search credits could not be loaded.", missing: [] };
    }
    draw();
  }

  async function refresh() {
    if (busy) return;
    busy = true;
    draw();
    try {
      const res = await ctx.api.post("/api/credits/refresh");
      data = res;
      outcome = refreshWords(res.refresh);
      failure = null;
      ctx.announce(outcome.text);
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "Tavily could not be asked just now.", missing: [] };
    }
    busy = false;
    draw();
    ctx.refreshCredits();
  }

  function card(p) {
    const pct = creditPercent(p);
    const figure = figureText(p);
    const kids = [
      h("h4", null, p.label, " ", h("span", { class: "chip chip-" + (p.level === "red" ? "bad" : p.level === "amber" ? "warn" : p.level === "ok" ? "ok" : "plain") }, levelWords(p.level))),
      h("p", { class: "big" }, figure || "No count yet"),
    ];
    if (pct !== null) {
      const fill = h("div", { class: "meter-fill" });
      fill.style.width = pct.toFixed(1) + "%";
      kids.push(
        h("div", { class: "meter credit-meter", role: "progressbar", "aria-label": p.label + " credits used", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(pct)) }, fill)
      );
      if (typeof p.remaining === "number") kids.push(h("p", null, h("strong", null, grouped(p.remaining)), " left"));
    }
    kids.push(h("p", { class: "muted small" }, "Source: ", sourceWords(p), p.as_of ? " · as of " + formatDateTime(p.as_of) : ""));
    kids.push(h("p", { class: "muted small" }, checkedText(p)));
    if (p.plan) kids.push(h("p", { class: "muted small" }, "Plan: ", untrusted(p.plan)));
    if (p.note) kids.push(h("p", { class: "small" }, untrusted(p.note)));
    if (p.key_present === false) kids.push(h("p", { class: "muted small" }, "No " + p.label + " key is set, so this app is not using it."));
    return h("div", { class: "credit-card credits-" + p.level, "data-provider": p.provider }, kids);
  }

  function form() {
    const provider = h("select", { id: "cr-provider", class: "text-input", "data-fk": "cr-provider" }, Object.keys(NAMES).map((k) => h("option", { value: k }, NAMES[k])));
    const kind = h("select", { id: "cr-kind", class: "text-input", "data-fk": "cr-kind" }, h("option", { value: "used" }, "Credits used so far"), h("option", { value: "remaining" }, "Credits left"));
    const amount = h("input", { id: "cr-amount", class: "text-input", type: "number", min: "0", step: "1", autocomplete: "off", "data-fk": "cr-amount" });
    const limit = h("input", { id: "cr-limit", class: "text-input", type: "number", min: "0", step: "1", autocomplete: "off", "data-fk": "cr-limit" });
    const err = h("div", { role: "alert" });
    const save = h("button", { type: "button", class: "btn btn-primary", "data-fk": "cr-save" }, "Save this number");
    provider.addEventListener("change", () => {
      kind.value = provider.value === "tavily" ? "used" : "remaining"; // how each account page words it
    });
    kind.value = "used";
    const submit = async () => {
      clear(err);
      const check = baselineForm({ provider: provider.value, kind: kind.value, amount: amount.value, limit: limit.value });
      if (!check.ok) {
        err.appendChild(h("div", { class: "banner banner-bad" }, h("p", null, check.error)));
        return;
      }
      save.disabled = true;
      try {
        data = await ctx.api.post("/api/credits/baseline", check.body);
        saved = "Saved. The app counts from this moment.";
        ctx.announce(saved);
        amount.value = "";
        limit.value = "";
        ctx.refreshCredits();
        formReady = false;
        draw();
        return;
      } catch (e) {
        err.appendChild(problemBanner(e instanceof ApiError ? e.problem : { text: "The number could not be saved.", missing: [] }));
      }
      save.disabled = false;
    };
    save.addEventListener("click", submit);
    return h(
      "details",
      { class: "details", open: !data || data.providers.every((p) => p.used === null && p.remaining === null) },
      h("summary", null, "Enter or correct a balance"),
      h("p", { class: "muted" }, "Type what your account page shows today. Tavily says how many credits you have used this month; Serper says how many are left. The app counts from this moment. Tavily's free allowance starts again each month: after it does, press the Ask Tavily button, or type the new number here."),
      h(
        "div",
        { class: "credit-form" },
        h("label", { for: "cr-provider" }, "Provider", provider),
        h("label", { for: "cr-kind" }, "The number is", kind),
        h("label", { for: "cr-amount" }, "Number", amount),
        h("label", { for: "cr-limit" }, "Allowance (optional)", limit),
        save
      ),
      err,
      saved ? h("p", { class: "tone-ok", role: "status" }, saved) : null
    );
  }

  function runsTable(rows) {
    if (!rows.length) return h("p", { class: "muted" }, "No run has been counted yet. Credits are counted as soon as a run searches.");
    const shown = rows.slice(0, MAX_ROWS);
    const trs = shown.map((r) =>
      h(
        "tr",
        { "data-run": r.run_id },
        h("td", { "data-label": "When" }, formatDateTime(r.at)),
        h("td", { "data-label": "Run" }, navLink("#/results/" + r.run_id, "Open")),
        h("td", { class: "num", "data-label": "Credits" }, runCreditsText(r))
      )
    );
    return h(
      "div",
      { class: "table-wrap" },
      h("table", { class: "table responsive" }, h("thead", null, h("tr", null, ["When", "Run", "Credits billed"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", null, trs)),
      rows.length > shown.length ? h("p", { class: "muted small" }, "Showing the latest " + shown.length + " of " + rows.length + " runs.") : null
    );
  }

  function draw() {
    if (gone) return;
    if (failure) {
      setKids(noteBox, problemBanner(failure));
      return;
    }
    if (!data) {
      setKids(cards, h("p", { class: "muted" }, "Loading…"));
      return;
    }
    if (!data.enabled) {
      clear(noteBox);
      setKids(cards, h("p", null, "Search credits are not being tracked by this app."));
      clear(formBox);
      clear(runsBox);
      clear(needBox);
      formReady = false;
      return;
    }
    setKids(noteBox, outcome && outcome.text ? h("div", { class: "banner banner-" + (outcome.tone === "warn" ? "warn" : "ok"), role: "status" }, h("p", null, outcome.text)) : null);
    const asking = h("button", { type: "button", class: "btn", "data-fk": "cr-refresh", disabled: busy, onclick: refresh }, busy ? "Asking Tavily…" : "Ask Tavily now");
    const list = data.providers.filter((p) => p.key_present !== false || p.used !== null || p.remaining !== null);
    keepFocus(box, () => setKids(cards, list.map(card), h("div", { class: "actions" }, asking)));
    if (!formReady) {
      setKids(formBox, form());
      formReady = true;
    }
    setKids(runsBox, runsTable(data.per_run || []));
    const hint = data.estimate_hint || {};
    const rows = SIZE_ORDER.filter((k) => hint[k]).map((k) => h("li", null, h("strong", null, sizeWord(k) + ": "), needText(hint[k])));
    setKids(needBox, rows.length ? h("details", { class: "details" }, h("summary", null, "What a run usually needs"), h("p", { class: "muted small" }, "Expected credits by size; the high figure leaves room for searches that are billed twice. Measured numbers replace these as runs finish."), h("ul", { class: "plain-list" }, rows)) : null);
  }

  load();
  const off = ctx.on("credits", () => {
    if (data && !busy) {
      data = ctx.state.credits || data;
      draw();
    }
  });
  return {
    unmount() {
      gone = true;
      off();
    },
  };
}
