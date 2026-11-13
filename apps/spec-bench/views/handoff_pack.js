/* The pack explorer: files with token sizes (per file and per task), the size report, every check, the builder-needs
   and coverage matrices, the self-check report, the Verify report (big apps), a comparison of two runs, and the run
   history. A pack file's text is shown as text in a <pre>, never as markup. */
import { h, setKids, untrusted, navLink, put } from "../lib/dom.js";
import { arr, textOf, texts, buildTree, normalizeTaskTotals, normalizeRuns, listOf } from "../lib/model.js";
import { count, tokensText, severityWord, severityTone } from "../lib/format.js";
import { ApiError } from "../lib/api.js";
import { banner, chip, problemBanner, problemOf, loading, card, genericTable, objectSections } from "./shared.js";
import { runsTable } from "./runs.js";

const MAX_FILE_CHARS = 200000;
const MAX_CHILDREN = 200;

export function mountTab(root, ctx, env, tabId) {
  let gone = false;
  const pane = h("div", null, loading());
  put(root, pane);
  const base = ctx.api.runPath(env.runId);
  const fail = (e) => {
    if (!gone) setKids(pane, problemBanner(problemOf(e)));
  };
  const views = { files, tokens, checks, selfcheck, verify, diff, history };
  (views[tabId] || files)();
  return { unmount() { gone = true; } };

  // ---- files ---------------------------------------------------------------------------------------------------
  async function files() {
    try {
      const body = (await ctx.api.get(base + "/pack/tree")) || {};
      if (gone) return;
      const tree = buildTree(listOf(body.files, ["files", "tree"]));
      const totals = normalizeTaskTotals(body);
      const viewer = h("div", { class: "fileview", "aria-live": "polite" }, h("p", { class: "muted" }, "Choose a file to read it."));
      async function openFile(path) {
        setKids(viewer, loading());
        try {
          let text = await ctx.api.text(base + "/pack/file" + ctx.api.q({ path: path }));
          const cut = text.length > MAX_FILE_CHARS;
          if (cut) text = text.slice(0, MAX_FILE_CHARS);
          setKids(viewer, h("h4", null, untrusted(path, "fname")), cut ? banner("Only the first " + count(MAX_FILE_CHARS) + " characters are shown.", "info") : null, h("pre", { class: "filetext", dir: "auto" }, text));
        } catch (e) {
          setKids(viewer, problemBanner(problemOf(e)));
        }
      }
      const total = tree.reduce((n, t) => n + t.tokens, 0);
      setKids(
        pane,
        h("p", null, "The whole pack is about " + tokensText(total) + ". It is never read whole by anyone; task cards are the way in."),
        h("div", { class: "split" }, h("div", { class: "tree", role: "tree" }, treeNodes(tree, openFile)), viewer),
        card("What each task reads", totals.length ? genericTable(totals.map((t) => ({ task: t.id, title: t.title, reads: t.tokens === null ? "" : count(t.tokens) + " tokens", budget: t.budget === null ? "" : count(t.budget), within_budget: t.over ? "NO: over" : "yes" })), { cols: ["task", "title", "reads", "budget", "within_budget"], labels: { within_budget: "Within budget" } }) : h("p", { class: "muted" }, "No per-task totals were given."))
      );
    } catch (e) {
      fail(e);
    }
  }
  function treeNodes(nodes, openFile) {
    const ul = h("ul", { class: "plain-list treelist" });
    nodes.slice(0, MAX_CHILDREN).forEach((n) => {
      if (n.dir) {
        ul.appendChild(h("li", null, h("details", { class: "folder" }, h("summary", null, untrusted(n.name + "/"), h("span", { class: "muted small" }, " " + tokensText(n.tokens))), treeNodes(n.children, openFile))));
      } else {
        ul.appendChild(h("li", null, h("button", { type: "button", class: "filebtn", "data-fk": "file-" + n.path, onclick: () => openFile(n.path) }, untrusted(n.name), h("span", { class: "muted small" }, " " + tokensText(n.tokens)))));
      }
    });
    if (nodes.length > MAX_CHILDREN) ul.appendChild(h("li", { class: "muted" }, "… and " + (nodes.length - MAX_CHILDREN) + " more in this folder."));
    return ul;
  }

  // ---- size report ----------------------------------------------------------------------------------------------
  async function tokens() {
    try {
      const b = (await ctx.api.get(base + "/pack/budget")) || {};
      if (gone) return;
      setKids(pane, h("p", { class: "muted" }, "How big the pack is, by kind of file and by format, and how each task's reading compares with its budget."), objectSections(b));
    } catch (e) {
      fail(e);
    }
  }

  // ---- checks ---------------------------------------------------------------------------------------------------
  async function checks() {
    try {
      const c = (await ctx.api.get(base + "/pack/checks")) || {};
      if (gone) return;
      const results = arr(c.results || c.validators || c.checks);
      const must = results.filter((r) => r && (r.severity === "blocker" || r.severity === "error")).length;
      const needs = arr(c.builder_needs || c.builder_needs_matrix);
      const cov = c.coverage_matrix || c.coverage;
      setKids(
        pane,
        banner(results.length ? results.length + " checks reported something; " + must + " must be fixed." : "Every check passed.", must ? "bad" : results.length ? "warn" : "ok"),
        card(
          "Every check result",
          results.length
            ? h("div", { class: "table-wrap" }, h("table", { class: "table responsive" }, h("thead", null, h("tr", null, ["How serious", "Check", "Where", "Why", "How to fix"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", null, results.slice(0, 500).map((r) => h("tr", null, h("td", { "data-label": "How serious" }, chip(severityWord(r && r.severity), severityTone(r && r.severity))), h("td", { "data-label": "Check" }, untrusted(textOf(r && r.code))), h("td", { "data-label": "Where" }, untrusted(textOf(r && r.where))), h("td", { "data-label": "Why" }, untrusted(textOf(r && r.why))), h("td", { "data-label": "How to fix" }, untrusted(textOf(r && r.fix))))))))
            : h("p", { class: "muted" }, "Nothing to report.")
        ),
        card("What the front end needs to find (builder-needs matrix)", needs.length ? genericTable(needs, { max: 6 }) : h("p", { class: "muted" }, "No matrix was given.")),
        card("Coverage (is every role, entity and flow covered by a screen?)", matrixOrTable(cov))
      );
    } catch (e) {
      fail(e);
    }
  }
  function matrixOrTable(cov) {
    if (Array.isArray(cov)) return genericTable(cov, { max: 8, empty: "No coverage matrix was given." });
    if (cov && Array.isArray(cov.rows) && Array.isArray(cov.columns)) {
      const cell = (r, c) => {
        const row = cov.cells && (Array.isArray(cov.cells) ? cov.cells[cov.rows.indexOf(r)] : cov.cells[textOf(r)]);
        const v = Array.isArray(row) ? row[cov.columns.indexOf(c)] : row && row[textOf(c)];
        return v === true ? "yes" : v === false || v === undefined || v === null ? "no" : textOf(v);
      };
      return h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", null, h("tr", null, h("th", { scope: "col" }, ""), cov.columns.map((c) => h("th", { scope: "col" }, textOf(c))))), h("tbody", null, cov.rows.map((r) => h("tr", null, h("th", { scope: "row" }, untrusted(textOf(r))), cov.columns.map((c) => h("td", { class: cell(r, c) === "no" ? "tone-bad" : "" }, cell(r, c))))))));
    }
    return h("p", { class: "muted" }, "No coverage matrix was given.");
  }

  // ---- self-check and verify ----------------------------------------------------------------------------------------------
  async function selfcheck() {
    try {
      const s = (await ctx.api.get(base + "/pack/selfcheck")) || {};
      if (gone) return;
      setKids(pane, h("p", { class: "muted" }, "What the automatic self-check changed or flagged: mechanical fixes, parts asked again, flags and banners."), objectSections(s));
    } catch (e) {
      fail(e);
    }
  }
  async function verify() {
    try {
      const v = (await ctx.api.get(base + "/pack/verify")) || {};
      if (gone) return;
      if (v.enabled === false) {
        setKids(pane, banner("Verify did not run for this size of app. It is for big (deep) apps only.", "info"));
        return;
      }
      setKids(pane, h("p", { class: "muted" }, "The independent check for big apps: its findings, the patches the Spec applied, how many rounds it took, and the test reading of every task card."), objectSections(v));
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || (e.problem && e.problem.kind === "missing"))) {
        if (!gone) setKids(pane, banner("There is no Verify report for this run. Verify only runs for big (deep) apps.", "info"));
      } else fail(e);
    }
  }

  // ---- compare and history ----------------------------------------------------------------------------------------------
  async function loadRuns() {
    return normalizeRuns(await ctx.api.get("/api/runs")).filter((r) => r.id);
  }
  async function diff() {
    try {
      const runs = (await loadRuns()).filter((r) => r.id !== env.runId);
      if (gone) return;
      let other = env.extra && runs.some((r) => r.id === env.extra) ? env.extra : runs.length ? runs[0].id : "";
      const out = h("div", null);
      const pick = h("select", { class: "text-input", "data-fk": "diff-other", onchange: (e) => { other = e.target.value; show(); } }, runs.map((r) => h("option", { value: r.id, selected: r.id === other }, r.id + (r.hash ? " (" + r.hash.slice(0, 8) + ")" : ""))));
      async function show() {
        if (!other) {
          setKids(out, banner("There is no other run to compare with yet.", "info"));
          return;
        }
        setKids(out, loading());
        try {
          const d = (await ctx.api.get(base + "/diff/" + encodeURIComponent(other))) || {};
          if (gone) return;
          let rows = arr(d.files).map((f) => ({ file: textOf(f && f.path !== undefined ? f.path : f), change: textOf(f && f.status), }));
          if (!rows.length) {
            rows = [].concat(texts(d.added).map((f) => ({ file: f, change: "added" })), texts(d.removed).map((f) => ({ file: f, change: "removed" })), texts(d.changed).map((f) => ({ file: f, change: "changed" })));
          }
          const changed = rows.filter((r) => r.change && r.change !== "same" && r.change !== "unchanged");
          setKids(out, banner(changed.length ? changed.length + " files differ." : "The two packs are identical.", changed.length ? "warn" : "ok"), genericTable(changed, { cols: ["file", "change"], empty: "No differences." }));
        } catch (e) {
          setKids(out, problemBanner(problemOf(e)));
        }
      }
      setKids(pane, h("label", null, "Compare this run with: ", pick), out);
      show();
    } catch (e) {
      fail(e);
    }
  }
  async function history() {
    try {
      const runs = (await loadRuns()).slice().sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
      if (gone) return;
      setKids(pane, runsTable(runs, (r) => (r.id === env.runId ? h("span", { class: "muted" }, "this run") : navLink("#/handoff/" + env.runId + "/diff/" + r.id, "Compare with this run", "btn btn-small")), "Compare"));
    } catch (e) {
      fail(e);
    }
  }
}
