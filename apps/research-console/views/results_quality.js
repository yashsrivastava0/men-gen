/* Quality: how good is this run? Lane quality, builder-need coverage, validation results, the metrics page with each measure
   against its target (OK, Not OK, or Not measured), extraction first try versus final, checker rates, fetching, timings,
   cost by step and the fallbacks used. Everything is read from the overview; nothing is computed here but wording. */
import { h, setKids, untrusted } from "../lib/dom.js";
import { LANES, laneTitle, QUALITY_WORDS, QUALITY_TONE, BN_WORDS, acceptanceRows, acceptState, fmtPct, costSteps, stageRows } from "../lib/results.js";
import { usd, formatDuration, jobTitle, humanize, capitalize, plural, stepWord, SKIP_REASONS } from "../lib/format.js";

function table(head, rows, cls) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table responsive " + (cls || "") }, h("thead", null, h("tr", null, head.map((t) => h("th", { scope: "col" }, t)))), h("tbody", null, rows)));
}
const td = (label, ...kids) => h("td", { "data-label": label }, ...kids);
const card = (title, ...kids) => h("section", { class: "card" }, h("h3", null, title), ...kids);

export function mountTab(panel, state) {
  const ov = state.overview;
  const m = ov.metrics || {};
  const out = [h("p", { class: "lead" }, "How much to trust this run, and where it was weak. Targets come from the evaluation plan; “Not measured” means the run had nothing to measure.")];

  // lane quality
  const lq = ov.lane_quality || {};
  out.push(
    card(
      "Lane quality",
      table(
        ["Lane", "Quality", "Required facts found", "Findings", "From strong sources"],
        LANES.filter((l) => lq[l]).map((l) =>
          h("tr", { "data-lane": l }, td("Lane", laneTitle(l)), td("Quality", h("span", { class: "chip chip-" + (QUALITY_TONE[lq[l].quality] || "plain") }, QUALITY_WORDS[lq[l].quality] || lq[l].quality)), td("Required facts found", lq[l].slots_filled + " of " + lq[l].slots_required), td("Findings", String(lq[l].items)), td("From strong sources", fmtPct(lq[l].strong_share)))
        )
      )
    )
  );

  // builder-need coverage
  const bn = ov.bn_coverage || {};
  const needs = Object.keys(bn);
  out.push(card("What the builder needs", h("p", { class: "muted" }, "Each need is something the next step needs to know. “Only a default” means research found nothing and a standard answer will be used."), needs.length ? table(["Need", "Coverage"], needs.map((n) => h("tr", null, td("Need", n), td("Coverage", h("span", { class: "chip chip-" + (bn[n] === "researched" ? "ok" : "warn") }, BN_WORDS[bn[n]] || capitalize(humanize(bn[n]))))))) : h("p", { class: "muted" }, "Not reported for this run.")));

  // validation
  const v = ov.validation || {};
  const rules = Object.keys(v.by_rule || {});
  const evv = ov.evidence_validation || [];
  out.push(
    card(
      "Validation checks",
      h("p", null, h("span", { class: "chip chip-" + (v.errors ? "bad" : "ok") }, v.errors ? plural(v.errors, "problem") : "No problems"), " ", h("span", { class: "chip chip-" + (v.warnings ? "warn" : "ok") }, v.warnings ? plural(v.warnings, "warning") : "No warnings")),
      rules.length ? h("ul", { class: "plain-list" }, rules.map((r) => h("li", null, humanize(r) + ": " + v.by_rule[r]))) : h("p", { class: "muted" }, "Every rule the result is checked against passed."),
      evv.length ? h("ul", { class: "plain-list" }, evv.slice(0, 20).map((e) => h("li", null, untrusted(typeof e === "string" ? e : JSON.stringify(e))))) : null
    )
  );

  // acceptance
  const rows = acceptanceRows(m);
  out.push(
    card(
      "Measures against their targets",
      rows.length
        ? table(["Measure", "This run", "Target", "Result"], rows.map((r) => h("tr", { "data-measure": r.key }, td("Measure", r.label), td("This run", r.value), td("Target", r.target), td("Result", h("span", { class: "chip chip-" + (r.state === "ok" ? "ok" : r.state === "bad" ? "bad" : "plain") }, acceptState[r.state])))))
        : h("p", { class: "muted" }, "No measures were reported.")
    )
  );

  // extraction
  const ex = m.extraction || {};
  out.push(
    card(
      "Reading the pages",
      h(
        "dl",
        { class: "facts" },
        h("dt", null, "Facts the model returned"),
        h("dd", null, String(ex.extracted || 0)),
        h("dt", null, "Quote right first time"),
        h("dd", null, fmtPct(ex.quote_pass_first_try)),
        h("dt", null, "Right after one retry"),
        h("dd", null, fmtPct(ex.quote_pass_final) + (ex.recovered_by_retry ? " (" + plural(ex.recovered_by_retry, "fact") + " recovered by the retry)" : "")),
        h("dt", null, "Dropped: number not in quote"),
        h("dd", null, String(ex.number_not_in_quote || 0)),
        h("dt", null, "Dropped: unusable or too long"),
        h("dd", null, String((ex.invalid || 0) + (ex.claim_too_long || 0)))
      )
    )
  );
  // checker
  const ck = m.checker || {};
  out.push(
    card(
      "The checker",
      h(
        "dl",
        { class: "facts" },
        h("dt", null, "Findings checked"),
        h("dd", null, String(ck.items_checked || 0)),
        h("dt", null, "Supported"),
        h("dd", null, fmtPct(ck.supported)),
        h("dt", null, "Partly supported"),
        h("dd", null, fmtPct(ck.partial)),
        h("dt", null, "Not supported (rejected)"),
        h("dd", null, fmtPct(ck.not_supported)),
        h("dt", null, "Not checked"),
        h("dd", null, fmtPct(ck.unchecked))
      ),
      ck.rubber_stamp_signal ? h("p", { class: "banner banner-warn" }, "The checker rejected almost nothing. That can mean it is too lenient, so read a sample yourself: use the Spot-check.") : null
    )
  );
  // fetch and searches
  const fe = m.fetch || {};
  const skips = Object.keys(fe.skipped_by_reason || {});
  out.push(
    card(
      "Searching and fetching",
      h(
        "dl",
        { class: "facts" },
        h("dt", null, "Pages read"),
        h("dd", null, (fe.read || 0) + " of " + (fe.selected || 0) + " (" + fmtPct(fe.success) + ")"),
        h("dt", null, "Searches that mattered"),
        h("dd", null, (m.searches_contributing || 0) + " of " + (m.searches || 0) + " (" + fmtPct(m.searches_contributing_ratio) + ")"),
        h("dt", null, "Findings in the appendix"),
        h("dd", null, (m.appendix_items || 0) + " (" + fmtPct(m.appendix_share) + ")"),
        h("dt", null, "Pages skipped"),
        h("dd", null, skips.length ? skips.map((r) => (SKIP_REASONS[r] || humanize(r)) + ": " + fe.skipped_by_reason[r]).join("; ") : "none")
      )
    )
  );
  // slots by job
  const sj = m.slots_by_job || {};
  const jobs = Object.keys(sj);
  if (jobs.length) out.push(card("Required facts by topic area", table(["Topic area", "Found", "Share"], jobs.map((j) => h("tr", null, td("Topic area", jobTitle(j)), td("Found", sj[j].filled + " of " + sj[j].required), td("Share", fmtPct(sj[j].ratio)))))));
  const failed = m.failed_slots || [];
  if (failed.length) out.push(card("Topics with missing required facts", h("ul", { class: "plain-list" }, failed.map((f) => h("li", null, untrusted(f.label), " — " + f.filled + " of " + f.required + " found")))));
  // timings, cost, fallbacks
  const stages = stageRows(m.stages || (ov.run && ov.run.stages));
  const steps = costSteps(m.cost_by_step || (ov.run && ov.run.cost_by_step));
  const fb = m.fallbacks || ov.fallbacks || {};
  const fbKeys = Object.keys(fb);
  out.push(
    card(
      "Time, cost and fallbacks",
      h("h4", null, "Time by stage"),
      stages.length ? h("ul", { class: "steps" }, stages.map((s) => h("li", null, s.label + ": ", h("strong", null, formatDuration(s.seconds))))) : h("p", { class: "muted" }, "No timings were recorded."),
      h("h4", null, "Cost by step"),
      steps.length ? h("ul", { class: "steps" }, steps.map((s) => h("li", null, stepWord(s.step) + ": ", h("strong", null, usd(s.micros))))) : h("p", { class: "muted" }, "No cost was recorded."),
      h("h4", null, "Fallbacks used"),
      fbKeys.length ? h("ul", { class: "plain-list" }, fbKeys.map((k) => h("li", null, capitalize(humanize(k)) + ": " + (typeof fb[k] === "object" ? JSON.stringify(fb[k]) : fb[k])))) : h("p", { class: "muted" }, "None: every model step answered normally, so no safe fallback was needed.")
    )
  );
  setKids(panel, out);
}
