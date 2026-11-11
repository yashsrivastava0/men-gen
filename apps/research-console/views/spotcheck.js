/* Spot-check (#/spotcheck, #/spotcheck/<id>): you judge a sample of findings one card at a time, against the quote and the
   source. Keys 1 to 6 answer, S skips, the left arrow goes back. Every answer is saved at once, so a check can be closed and
   resumed. At the end: the result against the thresholds, where it went wrong, and the export. */
import { h, clear, setKids, untrusted, navLink, downloadBlob } from "../lib/dom.js";
import { problemBanner, statusChip, sizeBadge } from "./shared.js";
import { ApiError } from "../lib/api.js";
import { problemText } from "../lib/safe.js";
import { VERDICTS, SKIP, verdictForKey, verdictLabel, nextUnanswered, nextSkipped, progress, strataWord, summaryRows, verdictText, worstText } from "../lib/spotcheck.js";
import { dataRows, kindWord } from "../lib/results.js";
import { dataView, quoteBlock, evidenceBadge } from "./item.js";
import { jobTitle, formatDateTime, ago, plural, usd } from "../lib/format.js";

export function mount(root, ctx, params) {
  return params.id ? mountCheck(root, ctx, params.id) : mountSetup(root, ctx);
}

// ---- choose runs ---------------------------------------------------------------------------------------------------------------
function mountSetup(root, ctx) {
  let gone = false;
  const runsBox = h("div", { class: "card" });
  const pastBox = h("div", { class: "card" });
  const seed = h("input", { type: "number", class: "text-input", id: "sc-seed", "aria-describedby": "sc-seed-help", placeholder: "leave empty" });
  const errorBox = h("div", { role: "alert" });
  const start = h("button", { type: "button", class: "btn btn-primary", "data-fk": "sc-start", disabled: true }, "Start the spot-check");
  const chosen = new Set();
  root.append(
    h("div", { class: "page-head" }, h("h2", null, "Spot-check")),
    h("p", { class: "lead" }, "Judge a sample of findings yourself: 20 per run, one at a time, each with its quote and source. It is the honest test of whether the research can be trusted."),
    runsBox,
    h("section", { class: "card" }, h("h3", null, "The sample"), h("p", { class: "muted" }, "The sample is drawn by a number (the seed): the same number and the same runs always give the same cards. Leave it empty and the app picks one."), h("label", { for: "sc-seed" }, "Seed (a whole number): "), seed, h("p", { class: "muted small", id: "sc-seed-help" }, "Per run: 8 high-impact facts, 6 provider facts, 3 data records, 3 ordinary facts."), errorBox, h("div", { class: "actions" }, start)),
    pastBox
  );
  async function load() {
    try {
      const [runs, past] = await Promise.all([ctx.api.get("/api/runs"), ctx.api.get("/api/spot-checks")]);
      if (gone) return;
      const done = (runs.runs || []).filter((r) => ["complete", "partial", "skipped"].includes(r.status));
      drawRuns(done);
      drawPast(past.checks || []);
    } catch (e) {
      setKids(runsBox, problemBanner(e instanceof ApiError ? e.problem : problemText(0, null)));
    }
  }
  function drawRuns(done) {
    if (!done.length) {
      setKids(runsBox, h("h3", null, "Pick the runs to check"), h("p", null, "There is no finished run with results yet. Run an input first, then come back."), navLink("#/inputs", "Go to the inputs", "btn"));
      return;
    }
    setKids(
      runsBox,
      h("h3", null, "Pick the runs to check"),
      h(
        "ul",
        { class: "plain-list runs-pick" },
        done.map((r) => {
          const id = "pick-" + r.run_id;
          const box = h("input", { type: "checkbox", id: id, "data-fk": id });
          box.addEventListener("change", () => {
            if (box.checked) chosen.add(r.run_id);
            else chosen.delete(r.run_id);
            start.disabled = !chosen.size;
          });
          return h("li", null, box, " ", h("label", { for: id }, untrusted(r.idea || r.input_name || r.run_id), " ", r.size_class ? sizeBadge(r.size_class) : null, " ", statusChip(r.status), h("span", { class: "muted small" }, " · " + ago(r.finished_at || r.created_at) + " · " + usd(r.spend_micros || 0))));
        })
      )
    );
  }
  function drawPast(checks) {
    if (!checks.length) {
      setKids(pastBox, h("h3", null, "Earlier spot-checks"), h("p", { class: "muted" }, "None yet."));
      return;
    }
    setKids(
      pastBox,
      h("h3", null, "Earlier spot-checks"),
      h("ul", { class: "plain-list" }, checks.map((c) => h("li", null, h("span", { title: formatDateTime(c.created_at) }, ago(c.created_at)), " · seed " + c.seed + " · " + plural((c.run_ids || []).length, "run") + " · answered " + c.answered + " of " + c.total + (c.answered >= c.total ? (c.passed ? " · passed" : " · not passed") : ""), " ", navLink("#/spotcheck/" + c.id, c.answered >= c.total ? "See the result" : "Resume", "btn btn-small"))))
    );
  }
  start.addEventListener("click", async () => {
    start.disabled = true;
    clear(errorBox);
    const body = { run_ids: Array.from(chosen) };
    if (seed.value.trim() !== "") {
      const n = Number(seed.value);
      if (!Number.isInteger(n)) {
        errorBox.appendChild(h("p", { class: "tone-bad" }, "The seed must be a whole number, or empty."));
        start.disabled = false;
        return;
      }
      body.seed = n;
    }
    try {
      const check = await ctx.api.post("/api/spot-checks", body);
      ctx.announce("Spot-check created");
      ctx.navigate("#/spotcheck/" + check.id);
    } catch (e) {
      errorBox.appendChild(problemBanner(e instanceof ApiError ? e.problem : problemText(0, null)));
      start.disabled = false;
    }
  });
  load();
  return {
    unmount() {
      gone = true;
    },
  };
}

// ---- one check -----------------------------------------------------------------------------------------------------------------
function mountCheck(root, ctx, checkId) {
  let check = null;
  let summary = null;
  let idx = 0;
  let mode = "card"; // card | end
  let saving = false;
  let gone = false;
  const body = h("div", null);
  const live = h("div", { role: "status", class: "sr-only" });
  root.append(h("div", { class: "page-head" }, h("h2", null, "Spot-check"), navLink("#/spotcheck", "All spot-checks", "btn btn-small")), body, live);

  async function load() {
    try {
      check = await ctx.api.get("/api/spot-checks/" + encodeURIComponent(checkId));
      summary = check.summary;
      const first = nextUnanswered(check, 0);
      if (first === -1) mode = "end";
      else {
        idx = first;
        mode = "card";
      }
      draw();
    } catch (e) {
      setKids(body, problemBanner(e instanceof ApiError ? e.problem : problemText(0, null)), navLink("#/spotcheck", "Back", "btn"));
    }
  }
  function draw() {
    if (gone || !check) return;
    if (mode === "end") drawEnd();
    else drawCard();
  }

  function drawCard() {
    const card = check.cards[idx];
    const p = progress(check);
    const current = check.verdicts[card.key];
    const prog = h("progress", { max: String(p.total), value: String(p.answered + p.skipped), "aria-label": "Spot-check progress" });
    const title = h("h3", { class: "card-title", tabindex: "-1", id: "sc-title" }, "Card " + (idx + 1) + " of " + p.total);
    const rows = dataRows(card.data);
    const verdictButtons = VERDICTS.map((v) => h("button", { type: "button", class: "btn verdict" + (current === v.id ? " verdict-on" : ""), "data-verdict": v.id, "data-fk": "v:" + v.id, title: v.hint, "aria-keyshortcuts": v.key, onclick: () => answer(v.id) }, h("kbd", null, v.key), " ", v.label));
    setKids(
      body,
      h("div", { class: "row" }, prog, h("span", { class: "muted small", "data-progress": "1" }, p.answered + " answered" + (p.skipped ? ", " + p.skipped + " skipped" : "") + ", " + p.left + " to go")),
      h(
        "section",
        { class: "card sc-card", "aria-labelledby": "sc-title" },
        title,
        h("div", { class: "badges" }, h("span", { class: "chip chip-plain" }, strataWord(card.stratum)), evidenceBadge(card.evidence), card.impact === "high" ? h("span", { class: "chip chip-warn" }, "High impact") : null, h("span", { class: "chip chip-plain" }, jobTitle(card.job) + " · " + (kindWord(card.kind) || card.kind))),
        card.topped_up ? h("p", { class: "banner banner-info" }, "This card was drawn from another group because there were too few facts of its own kind.") : null,
        h("h4", null, "The claim"),
        h("p", { class: "item-claim" }, untrusted(card.claim)),
        rows.length ? [h("h4", null, "Its details"), dataView(rows)] : null,
        h("h4", null, "The quote it rests on"),
        quoteBlock({ text: card.quote, domain: (card.source || {}).domain, published: (card.source || {}).published, url: (card.source || {}).url }),
        h("p", { class: "muted small" }, "Does the quote really say the claim, and is it right for this app?")
      ),
      h("div", { class: "verdicts", role: "group", "aria-label": "Your verdict" }, verdictButtons),
      h(
        "div",
        { class: "row" },
        h("button", { type: "button", class: "btn", "data-fk": "v:skipped", "aria-keyshortcuts": SKIP.key, onclick: () => answer("skipped") }, h("kbd", null, SKIP.key.toUpperCase()), " Skip"),
        h("button", { type: "button", class: "btn", "data-fk": "sc-prev", disabled: idx === 0, "aria-keyshortcuts": "ArrowLeft", onclick: () => go(idx - 1) }, "← Previous"),
        current ? h("span", { class: "muted small" }, "Your answer so far: " + verdictLabel(current)) : null
      ),
      h("p", { class: "muted small" }, "Keys: 1 to 6 answer, S skips, the left arrow goes back. Each answer is saved at once."),
      h("div", { role: "alert", id: "sc-error" })
    );
    live.textContent = "Card " + (idx + 1) + " of " + p.total;
    title.focus({ preventScroll: true });
  }
  function go(i) {
    if (i < 0 || i >= check.cards.length) return;
    idx = i;
    mode = "card";
    draw();
  }
  async function answer(verdict) {
    if (saving || mode !== "card" || !check) return;
    saving = true;
    const card = check.cards[idx];
    const err = document.getElementById("sc-error");
    try {
      const res = await ctx.api.put("/api/spot-checks/" + encodeURIComponent(checkId) + "/verdicts/" + encodeURIComponent(card.key), { verdict: verdict });
      check.verdicts[card.key] = verdict;
      summary = res.summary || summary;
      const next = nextUnanswered(check, 0);
      if (next === -1) {
        await finish();
      } else {
        idx = next;
        draw();
      }
    } catch (e) {
      if (err) setKids(err, problemBanner(e instanceof ApiError ? e.problem : problemText(0, null)), h("p", null, "Your answer was not saved. Press the key again to retry."));
    }
    saving = false;
  }
  async function finish() {
    try {
      summary = await ctx.api.get("/api/spot-checks/" + encodeURIComponent(checkId) + "/summary");
    } catch (e) {
      /* the summary from the last save is good enough */
    }
    mode = "end";
    draw();
    ctx.announce("Spot-check complete");
  }

  function drawEnd() {
    const s = summary;
    const rows = summaryRows(s);
    const p = progress(check);
    const skipped = nextSkipped(check);
    const exportBtn = (fmt, label) =>
      h("button", { type: "button", class: "btn", "data-fk": "export-" + fmt, onclick: async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
          downloadBlob(await ctx.api.blob("/api/spot-checks/" + encodeURIComponent(checkId) + "/export?format=" + fmt), "spot-check-" + checkId + "." + fmt);
        } catch (err) {
          setKids(document.getElementById("sc-export-note"), h("p", { class: "tone-bad" }, err instanceof ApiError ? err.problem.text : "The export failed."));
        }
        b.disabled = false;
      } }, label);
    setKids(
      body,
      h("section", { class: "card", "aria-labelledby": "sc-end" }, h("h3", { id: "sc-end", tabindex: "-1" }, "The result"), h("p", { class: "big" }, h("span", { class: "chip chip-" + (s.passed ? "ok" : "bad") }, s.passed ? "Passed" : "Not passed"), " ", verdictText(s)), h("p", { class: "muted" }, "You answered " + s.answered + " of " + s.total + " cards" + (p.skipped ? " (" + p.skipped + " skipped)" : "") + ". The check passes when every row below is met."), h("div", { class: "table-wrap" }, h("table", { class: "table responsive" }, h("thead", null, h("tr", null, ["Measure", "Your result", "Needed", "Met?"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", null, rows.map((r) => h("tr", { "data-row": r.label }, h("td", { "data-label": "Measure" }, r.label), h("td", { "data-label": "Your result" }, r.value), h("td", { "data-label": "Needed" }, r.target), h("td", { "data-label": "Met?" }, h("span", { class: "chip chip-" + (r.ok ? "ok" : "bad") }, r.ok ? "Yes" : "No")))))))),
      h(
        "section",
        { class: "card" },
        h("h3", null, "Where it went wrong"),
        (s.worst || []).length ? h("ul", { class: "plain-list" }, s.worst.map((w) => h("li", null, worstText(w)))) : h("p", { class: "muted" }, "Nothing: every answered card was OK or not relevant."),
        h("h4", null, "All answers"),
        h("ul", { class: "steps" }, VERDICTS.concat([{ id: "skipped", label: "Skipped" }]).map((v) => h("li", null, v.label + ": ", h("strong", null, String((s.counts || {})[v.id] || 0)))))
      ),
      h("section", { class: "card" }, h("h3", null, "Keep the result"), h("div", { class: "row" }, exportBtn("md", "Download as Markdown (.md)"), exportBtn("json", "Download as data (.json)")), h("div", { id: "sc-export-note", role: "status" })),
      h("div", { class: "row" }, skipped !== -1 ? h("button", { type: "button", class: "btn", "data-fk": "review-skipped", onclick: () => go(skipped) }, "Review the " + p.skipped + " skipped") : null, h("button", { type: "button", class: "btn", "data-fk": "change-answers", onclick: () => go(0) }, "Change an answer"), navLink("#/spotcheck", "Start another spot-check", "btn btn-primary"))
    );
    document.getElementById("sc-end").focus({ preventScroll: true });
  }

  // ---- keyboard: 1 to 6 answer, S skips, left arrow goes back ----------------------------------------------------------------------
  function onKey(e) {
    if (gone || mode !== "card" || document.querySelector("dialog[open]")) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if (e.key === "ArrowLeft" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      go(idx - 1);
      return;
    }
    const v = verdictForKey(e);
    if (v) {
      e.preventDefault();
      answer(v);
    }
  }
  document.addEventListener("keydown", onKey);
  load();
  return {
    unmount() {
      gone = true;
      document.removeEventListener("keydown", onKey);
    },
  };
}
