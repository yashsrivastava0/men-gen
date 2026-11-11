/* Budget (#/budget): the spend record as a table (date, what, run, cost by step, running total), the periods
   including the history of restarts, and "Restart budget" behind a dialog that needs the word RESTART. */
import { h, clear, setKids, untrusted, navLink } from "../lib/dom.js";
import { modal } from "./dialogs.js";
import { problemBanner } from "./shared.js";
import { usd, usdNum, formatDateTime, kindWord, stepWord } from "../lib/format.js";
import { budgetLevel, budgetText } from "../lib/budget.js";
import { ApiError } from "../lib/api.js";
import { isActiveStatus } from "../lib/reducer.js";

export function mount(root, ctx) {
  let spend = null;
  let activeRuns = 0;
  let failure = null;
  let gone = false;
  const summary = h("section", { class: "card budget-card" });
  const current = h("section", { class: "card" });
  const history = h("section", { class: "card" });
  root.append(h("div", { class: "page-head" }, h("h2", null, "Budget")), h("p", { class: "lead" }, "Everything the Research app spends on live models and live searches is written down here, as it happens."), summary, current, history);

  async function load() {
    try {
      const [sp, runs] = await Promise.all([ctx.api.get("/api/spend"), ctx.api.get("/api/runs").catch(() => ({ runs: [] }))]);
      spend = sp;
      activeRuns = (runs.runs || []).filter((r) => isActiveStatus(r.status)).length;
      failure = null;
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "The spend record could not be loaded.", missing: [] };
    }
    draw();
  }
  function entriesOf(periodId) {
    return ((spend && spend.record && spend.record.entries) || []).filter((e) => e.period === periodId).slice().reverse();
  }
  function draw() {
    if (gone) return;
    if (failure) {
      setKids(summary, problemBanner(failure), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
      clear(current);
      clear(history);
      return;
    }
    if (!spend) {
      setKids(summary, h("p", { class: "muted" }, "Loading…"));
      return;
    }
    if (!spend.enabled) {
      setKids(summary, h("p", null, "Spending is not being tracked by this app."));
      return;
    }
    const b = spend.budget || ctx.state.budget;
    const periods = (spend.record && spend.record.periods) || [];
    const cur = periods[periods.length - 1];
    const blocked = (b && b.active_runs > 0) || activeRuns > 0;
    const why = blocked ? "A run is going or waiting. Restart the budget after it has finished." : "";
    setKids(
      summary,
      h("h3", null, "This period"),
      h("p", { class: "big budget-" + budgetLevel(b) }, budgetText(b)),
      h("p", { class: "muted" }, "Left to spend: ", h("strong", null, usd(b.remaining_micros)), ". At the tripwire the run in progress finishes and no new run starts; at the total, the next call is refused and the run keeps what it has."),
      h("div", { class: "actions" }, h("button", { type: "button", class: "btn btn-danger", "data-fk": "restart", disabled: blocked, "aria-describedby": blocked ? "restart-why" : null, onclick: openRestart }, "Restart budget…"), blocked ? h("p", { class: "why-off", id: "restart-why" }, why) : null)
    );
    setKids(
      current,
      h("h3", null, "What was spent in this period"),
      cur ? entriesTable(entriesOf(cur.id)) : h("p", { class: "muted" }, "Nothing yet.")
    );
    const old = periods.slice(0, -1).reverse();
    setKids(
      history,
      h("h3", null, "Periods and history"),
      h("p", { class: "muted" }, "A restart closes a period and opens a new one at zero. History is kept; nothing is ever deleted."),
      periodsTable(periods),
      old.map((p) => h("details", { class: "details", "data-period": p.id }, h("summary", null, "Entries of the period that started " + formatDateTime(p.started_at)), entriesTable(entriesOf(p.id))))
    );
  }
  function periodsTable(periods) {
    const rows = periods.slice().reverse().map((p, i) =>
      h("tr", null,
        h("td", { "data-label": "Period" }, i === 0 ? "Current" : "Closed"),
        h("td", { "data-label": "Started" }, formatDateTime(p.started_at)),
        h("td", { "data-label": "Ended" }, p.ended_at ? formatDateTime(p.ended_at) : "still open"),
        h("td", { "data-label": "Why it started" }, untrusted(p.reason || "")),
        h("td", { "data-label": "Total spent" }, p.closed_total_micros === null || p.closed_total_micros === undefined ? usd(((spend.budget && spend.budget.spent_micros) || 0)) + " so far" : usd(p.closed_total_micros)))
    );
    return h("div", { class: "table-wrap" }, h("table", { class: "table responsive" }, h("thead", null, h("tr", null, ["Period", "Started", "Ended", "Why it started", "Total spent"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", null, rows)));
  }
  function entriesTable(entries) {
    if (!entries.length) return h("p", { class: "muted" }, "Nothing was spent in this period.");
    const rows = entries.map((e) => {
      const steps = Object.keys(e.by_step || {}).filter((k) => e.by_step[k] > 0);
      return h(
        "tr",
        { "data-entry": e.id },
        h("td", { "data-label": "Date" }, formatDateTime(e.at)),
        h("td", { "data-label": "What" }, kindWord(e.kind), e.label ? h("div", { class: "muted small" }, untrusted(e.label)) : null, e.status === "open" ? h("div", { class: "muted small" }, "in progress") : null),
        h("td", { "data-label": "Run" }, e.run_id ? navLink((e.status === "open" ? "#/run/" : "#/results/") + e.run_id, "Open") : "–"),
        h("td", { "data-label": "Cost by step" }, steps.length ? h("ul", { class: "steps" }, steps.map((k) => h("li", null, stepWord(k) + ": " + usdNum(e.by_step[k])))) : "–"),
        h("td", { class: "num", "data-label": "Cost" }, h("strong", null, usd(e.total_micros || 0)), e.reconciled === true ? h("div", { class: "muted small" }, "matches the ledger") : e.reconciled === false ? h("div", { class: "muted small" }, "adjusted to the ledger") : null),
        h("td", { class: "num", "data-label": "Running total" }, usd(e.running_total_micros || 0))
      );
    });
    return h("div", { class: "table-wrap" }, h("table", { class: "table responsive" }, h("thead", null, h("tr", null, ["Date", "What", "Run", "Cost by step (USD)", "Cost", "Running total"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", null, rows)));
  }

  function openRestart() {
    const m = modal("Restart the budget?");
    const field = h("input", { type: "text", id: "restart-word", class: "text-input", autocomplete: "off", "aria-describedby": "restart-help", "data-autofocus": "1" });
    const err = h("div", { role: "alert" });
    const go = h("button", { type: "button", class: "btn btn-danger", disabled: true }, "Restart budget");
    m.body.append(
      h("p", { class: "dlg-line" }, "This closes the current period and starts a new one at USD 0.00, with a fresh total."),
      h("ul", null, h("li", null, "History is kept: every earlier entry stays in the spend record."), h("li", null, "The old ledger file is not touched, moved or reset."), h("li", null, "It cannot be done while a run is going or waiting.")),
      h("label", { for: "restart-word", id: "restart-help" }, "Type the word ", h("strong", null, "RESTART"), " to confirm:"),
      field,
      err
    );
    const cancel = h("button", { type: "button", class: "btn", onclick: () => m.close("no") }, "Cancel");
    m.foot.append(cancel, go);
    m.focusFirst();
    const check = () => {
      go.disabled = field.value !== "RESTART";
    };
    field.addEventListener("input", check);
    const submit = async () => {
      if (field.value !== "RESTART") return;
      go.disabled = true;
      clear(err);
      try {
        await ctx.api.post("/api/budget/restart", { confirm: "RESTART" });
        m.close("done");
        ctx.announce("Budget restarted");
        await ctx.refreshBudget();
        await load();
      } catch (e) {
        err.appendChild(problemBanner(e instanceof ApiError ? e.problem : { text: "The restart did not work.", missing: [] }));
        go.disabled = false;
      }
    };
    go.addEventListener("click", submit);
    field.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        submit();
      }
    });
  }

  const off = ctx.on("budget", () => {
    if (spend) load();
  });
  load();
  return {
    unmount() {
      gone = true;
      off();
    },
  };
}
