/* Report: the readable Markdown report, drawn as a safe preview (text nodes only; see lib/markdown.js) with a download of
   the .md file. A long report is drawn in steps so the page stays quick. */
import { h, clear, setKids, link, downloadBlob } from "../lib/dom.js";
import { parseMarkdown } from "../lib/markdown.js";
import { ApiError } from "../lib/api.js";
import { problemBanner } from "./shared.js";
import { bytesText } from "../lib/format.js";

const STEP = 250;

function spans(list) {
  return list.map((s) => {
    if (s.t === "bold") return h("strong", { dir: "auto" }, s.text);
    if (s.t === "code") return h("code", { dir: "auto" }, s.text);
    if (s.t === "link") return link(s.url, s.text);
    return s.text;
  });
}
export function renderBlock(b) {
  if (b.type === "h") return h("h" + Math.min(6, b.level + 1), { class: "md-h", dir: "auto" }, spans(b.spans));
  if (b.type === "p") return h("p", { dir: "auto" }, spans(b.spans));
  if (b.type === "ul" || b.type === "ol") return h(b.type, { dir: "auto" }, b.items.map((i) => h("li", null, spans(i))));
  if (b.type === "quote") return h("blockquote", { class: "md-quote", dir: "auto" }, b.lines.map((l, i) => h("div", null, spans(l))));
  if (b.type === "hr") return h("hr", null);
  if (b.type === "table") {
    return h(
      "div",
      { class: "table-wrap" },
      h("table", { class: "table md-table" }, h("thead", null, h("tr", null, b.head.map((c) => h("th", { scope: "col", dir: "auto" }, spans(c))))), h("tbody", null, b.rows.map((r) => h("tr", null, r.map((c) => h("td", { dir: "auto" }, spans(c)))))))
    );
  }
  return h("p", null, "");
}

export async function mountTab(panel, state) {
  const ctx = state.ctx;
  const path = "/v1/research/runs/" + encodeURIComponent(state.runId) + "/report";
  let text;
  try {
    text = await ctx.api.text(path);
  } catch (e) {
    setKids(panel, e instanceof ApiError ? problemBanner(e.problem) : h("p", { class: "tone-bad" }, "The report could not be loaded."));
    return;
  }
  const blocks = parseMarkdown(text);
  const body = h("div", { class: "md", "data-blocks": String(blocks.length) });
  const more = h("div", { class: "row" });
  let shown = 0;
  function draw() {
    const frag = document.createDocumentFragment();
    for (const b of blocks.slice(shown, shown + STEP)) frag.appendChild(renderBlock(b));
    shown = Math.min(blocks.length, shown + STEP);
    body.appendChild(frag);
    clear(more);
    if (shown < blocks.length) more.appendChild(h("button", { type: "button", class: "btn", "data-fk": "report-more", onclick: draw }, "Show more (" + (blocks.length - shown) + " blocks left)"));
  }
  const dl = h("button", { type: "button", class: "btn btn-primary", "data-fk": "report-download", onclick: () => downloadBlob(new Blob([text], { type: "text/markdown;charset=utf-8" }), "research-report-" + state.runId + ".md") }, "Download the report (.md)");
  setKids(panel, h("p", { class: "lead" }, "The whole result as a readable document, one finding after another. This is a preview; the download is the same text as a Markdown file (" + bytesText(text.length) + ")."), h("div", { class: "row" }, dl), h("div", { class: "card report" }, body, more));
  draw();
}
