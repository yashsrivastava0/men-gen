/* A small, SAFE Markdown reader for the report preview. It never produces HTML: it returns a plain list of blocks, and
   the screen turns each block into text nodes. Supported: # headings, paragraphs, - and 1. lists, > quotes, | tables,
   --- rules, **bold**, `code` and [text](url) (a link only when safeUrl allows it; otherwise the text and address show as
   plain characters). Raw "<script>" and friends are just characters. Pure. */
import { safeUrl } from "./safe.js";

/** Inline spans: [{t: "text"|"bold"|"code"|"link", text, url?}]. */
export function inlineSpans(text) {
  const out = [];
  const re = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]*)\]\(([^)\s]*)\))/g;
  let last = 0;
  let m;
  const s = String(text === undefined || text === null ? "" : text);
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ t: "text", text: s.slice(last, m.index) });
    if (m[1]) out.push({ t: "bold", text: m[2] });
    else if (m[3]) out.push({ t: "code", text: m[4] });
    else {
      const url = safeUrl(m[7]);
      out.push(url ? { t: "link", text: m[6] || url, url: url } : { t: "text", text: m[0] });
    }
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push({ t: "text", text: s.slice(last) });
  return out;
}

function cells(line) {
  let s = line.trim();
  if (s.charAt(0) === "|") s = s.slice(1);
  if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}
const isRule = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);

/** Blocks: {type: "h", level, spans} | {type: "p", spans} | {type: "ul"|"ol", items: [spans]} | {type: "quote", lines: [spans]}
    | {type: "table", head: [spans], rows: [[spans]]} | {type: "hr"}. */
export function parseMarkdown(text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    let m = /^(#{1,6})\s+(.*)$/.exec(line);
    if (m) {
      blocks.push({ type: "h", level: m[1].length, spans: inlineSpans(m[2]) });
      i += 1;
    } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push({ type: "hr" });
      i += 1;
    } else if (/^\s*>/.test(line)) {
      const q = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        q.push(inlineSpans(lines[i].replace(/^\s*>\s?/, "")));
        i += 1;
      }
      blocks.push({ type: "quote", lines: q });
    } else if (/^\s*\|/.test(line) && i + 1 < lines.length && isRule(lines[i + 1])) {
      const head = cells(line).map(inlineSpans);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(cells(lines[i]).map(inlineSpans));
        i += 1;
      }
      blocks.push({ type: "table", head: head, rows: rows });
    } else if ((m = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line))) {
      const ordered = /\d/.test(m[1]);
      const items = [];
      while (i < lines.length && (m = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i])) && /\d/.test(m[1]) === ordered) {
        items.push(inlineSpans(m[2]));
        i += 1;
      }
      blocks.push({ type: ordered ? "ol" : "ul", items: items });
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*>|\s*\||\s*([-*+]|\d+[.)])\s)/.test(lines[i])) {
        para.push(lines[i].trim());
        i += 1;
      }
      if (!para.length) {
        para.push(line.trim());
        i += 1;
      }
      blocks.push({ type: "p", spans: inlineSpans(para.join(" ")) });
    }
  }
  return blocks;
}
