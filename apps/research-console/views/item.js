/* One finding, drawn in two sizes: a fixed-height row for lists, and the full item view in a dialog (claim, data in
   readable form, THE BEST QUOTE, the source with date and link, evidence badge, flags, what it applies to, other
   sources, and the Reject control). Every string from a web page or a model goes in as text; links only through link(). */
import { h, clear, untrusted, link, put } from "../lib/dom.js";
import { modal } from "./dialogs.js";
import { ApiError } from "../lib/api.js";
import { evidenceWord, evidenceTone, EVIDENCE, flagWord, FLAG_TONE, impactWord, kindWord, appliesText, prevalenceText, levelWord, recencyText, dataRows, laneTitle } from "../lib/results.js";
import { jobTitle, formatDate, humanize, plural } from "../lib/format.js";
import { problemBanner } from "./shared.js";

export const ROW_HEIGHT = 104;

export function evidenceBadge(evidence) {
  return h("span", { class: "chip chip-" + (evidenceTone(evidence) || "plain"), title: EVIDENCE[evidence] ? EVIDENCE[evidence].hint : "" }, evidenceWord(evidence));
}
export function flagChips(flags, limit) {
  const shown = (flags || []).filter((f) => flagWord(f));
  return shown.slice(0, limit || 99).map((f) => h("span", { class: "chip chip-" + (FLAG_TONE[f] || "plain") }, flagWord(f)));
}

/** A list row of fixed height (the virtual list needs it). `onOpen(item)` runs on click or Enter. */
export function itemRow(item, index, total, onOpen) {
  const rejected = !!item.rejected;
  return h(
    "li",
    { class: "vrow" + (rejected ? " is-rejected" : ""), "data-id": item.id, "aria-posinset": index + 1, "aria-setsize": total },
    h(
      "button",
      { type: "button", class: "vrow-btn", onclick: () => onOpen(item), "data-fk": "item:" + item.id },
      h("span", { class: "vrow-title" }, untrusted(item.label || item.id, "item-label")),
      h("span", { class: "vrow-claim" }, untrusted(item.claim || "")),
      h(
        "span",
        { class: "vrow-chips" },
        evidenceBadge(item.evidence),
        item.impact === "high" ? h("span", { class: "chip chip-warn" }, "High impact") : null,
        rejected ? h("span", { class: "chip chip-bad" }, "Rejected by you") : null,
        h("span", { class: "chip chip-plain" }, jobTitle(item.job)),
        flagChips(item.flags, 2)
      )
    )
  );
}

/** Readable key/value rows (nested lists and groups), never a JSON dump. */
export function dataView(rows) {
  const dl = h("dl", { class: "data-rows" });
  for (const r of rows) {
    dl.appendChild(h("dt", null, r.label));
    const dd = h("dd", null);
    if (r.rows) dd.appendChild(dataView(r.rows));
    else if (r.list) dd.appendChild(h("ul", { class: "data-list" }, r.list.map((t) => h("li", null, untrusted(t)))));
    else dd.appendChild(untrusted(r.value));
    dl.appendChild(dd);
  }
  return dl;
}

export function quoteBlock(quote) {
  if (!quote || !quote.text) return h("p", { class: "muted" }, "No quote was kept for this item.");
  return h(
    "figure",
    { class: "quote" },
    h("blockquote", null, untrusted(quote.text)),
    h("figcaption", null, quote.domain ? untrusted(quote.domain) : null, quote.published ? " · " + formatDate(quote.published) : "", quote.url ? [" · ", link(quote.url, "Open the page")] : null)
  );
}

export function sourceRows(item) {
  return h(
    "ul",
    { class: "plain-list sources-list" },
    (item.sources || []).map((s) =>
      h("li", null, untrusted(s.domain || ""), " ", h("span", { class: "chip chip-plain" }, humanize(s["class"] || s.source_class || "")), s.published || s.published_on ? " · " + formatDate(s.published || s.published_on) : "", s.title ? h("div", { class: "muted small" }, untrusted(s.title)) : null, s.url ? h("div", { class: "small" }, link(s.url, "Open the page")) : null)
    )
  );
}

/** The full item view. opts: {runId, ctx, onRejected(item)} */
export function openItem(item, opts) {
  const ctx = opts.ctx;
  const m = modal("A finding", { wide: true });
  const rejectedNote = h("p", { class: "banner banner-bad", role: "status" }, "Rejected by you. It is withdrawn from what the next step reads. The committed research file is not changed.");
  rejectedNote.hidden = !item.rejected;
  const title = h("h3", { class: "item-title", tabindex: "-1", "data-autofocus": "1" }, untrusted(item.label || item.id));
  const claimEl = h("p", { class: "item-claim" }, untrusted(item.claim || ""));
  const refresh = () => {
    rejectedNote.hidden = !item.rejected;
    title.className = "item-title" + (item.rejected ? " struck" : "");
    claimEl.className = "item-claim" + (item.rejected ? " struck" : "");
  };
  refresh();
  const rows = dataRows(item.data);
  const flags = flagChips(item.flags);
  const meta = h(
    "dl",
    { class: "facts" },
    h("dt", null, "Kind"),
    h("dd", null, kindWord(item.kind) || humanize(item.kind)),
    h("dt", null, "Topic area"),
    h("dd", null, jobTitle(item.job) + (item.lane ? " · feeds the " + laneTitle(item.lane) + " lane" : "")),
    item.prevalence && item.prevalence.of ? [h("dt", null, "How common"), h("dd", null, prevalenceText(item.prevalence) + (levelWord(item.prevalence.level) ? " (" + levelWord(item.prevalence.level) + ")" : ""))] : null,
    item.recency && item.recency.newest_source ? [h("dt", null, "Freshness"), h("dd", null, recencyText(item.recency))] : null,
    item.independent_domains !== undefined ? [h("dt", null, "Independent sites"), h("dd", null, String(item.independent_domains))] : null,
    item.store === "appendix" ? [h("dt", null, "Where it is kept"), h("dd", null, "Appendix (ranked below the main store)")] : null
  );
  put(m.body, 
    h("div", { class: "badges" }, evidenceBadge(item.evidence), item.impact ? h("span", { class: "chip chip-" + (item.impact === "high" ? "warn" : "plain") }, impactWord(item.impact)) : null, flags),
    rejectedNote,
    title,
    claimEl,
    item.use_note ? h("p", { class: "muted small" }, "How it is used: ", untrusted(item.use_note)) : null,
    h("h4", null, "The quote it rests on"),
    quoteBlock(item.best_quote),
    h("h4", null, "About this finding"),
    meta,
    rows.length ? [h("h4", null, "Details"), dataView(rows)] : null,
    (item.applies_to || []).length ? [h("h4", null, "What it applies to"), h("ul", { class: "plain-list" }, item.applies_to.map((a) => h("li", null, untrusted(appliesText(a)))))] : null,
    (item.sources || []).length ? [h("h4", null, plural(item.sources.length, "source")), sourceRows(item)] : null
  );

  // ---- the Reject control ------------------------------------------------------------------------------------------------------
  const rejectBox = h("div", { class: "reject-box" });
  const status = h("div", { role: "status" });
  function drawReject() {
    clear(rejectBox);
    if (item.rejected) return;
    const reason = h("input", { type: "text", class: "text-input", id: "reject-reason", maxlength: "200", "aria-label": "Why (optional)", placeholder: "Why? (optional)" });
    const confirm = h("button", { type: "button", class: "btn btn-danger", "data-fk": "reject-confirm" }, "Yes, reject it");
    const cancel = h("button", { type: "button", class: "btn", onclick: drawFirst }, "No, keep it");
    confirm.addEventListener("click", async () => {
      confirm.disabled = true;
      clear(status);
      try {
        await ctx.api.post("/v1/research/runs/" + encodeURIComponent(opts.runId) + "/items/" + encodeURIComponent(item.id) + "/reject", { reason: reason.value });
        item.rejected = true;
        refresh();
        drawReject();
        ctx.announce("Item rejected");
        if (opts.onRejected) opts.onRejected(item);
        status.appendChild(h("p", { class: "tone-ok" }, "Rejected."));
      } catch (e) {
        confirm.disabled = false;
        status.appendChild(problemBanner(e instanceof ApiError ? e.problem : { text: "The rejection could not be saved.", missing: [] }));
      }
    });
    put(rejectBox, h("p", null, "This withdraws the finding from what the next step reads. The committed research file does not change, and it cannot be undone here."), reason, h("div", { class: "row" }, confirm, cancel));
    reason.focus();
  }
  function drawFirst() {
    clear(rejectBox);
    if (item.rejected) {
      rejectBox.appendChild(h("p", { class: "muted" }, "This finding is rejected."));
      return;
    }
    rejectBox.appendChild(h("button", { type: "button", class: "btn btn-danger", "data-fk": "reject", onclick: drawReject }, "Reject this finding…"));
  }
  drawFirst();
  put(m.body, h("h4", null, "Your decision"), rejectBox, status);
  m.foot.appendChild(h("button", { type: "button", class: "btn btn-primary", onclick: () => m.close("done") }, "Close"));
  m.focusFirst();
  return m;
}
