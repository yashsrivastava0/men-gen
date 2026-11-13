/* The content map of one screen: what it must communicate and do, never where things sit. MUST, USUALLY and COULD
   are kept apart and speak in different voices. Used by the G2 review and by the hand-off screen pages. Everything
   from the pack is text. */
import { h, untrusted } from "../lib/dom.js";
import { mockWord } from "../lib/format.js";

export const ILLUSTRATIVE = "Illustrative: the final layout is the front end's.";

const VOICES = [
  { key: "must", word: "MUST", voice: "The screen fails without these. Always shown." },
  { key: "usually", word: "USUALLY", voice: "Expected here unless there is a reason to leave one out." },
  { key: "could", word: "COULD", voice: "Nice to have. These are the first to go on a small screen." },
];

function regionNode(r) {
  return h(
    "li",
    { class: "region" },
    r.name ? untrusted(r.name, "region-name", "strong") : null,
    r.text ? h("p", null, untrusted(r.text)) : null,
    r.items.length ? h("ul", null, r.items.map((i) => h("li", null, untrusted(i)))) : null
  );
}

/** `screen` is a normalized screen (lib/model.js normalizeScreen); opts.mock names the mock profile shown. */
export function renderContentMap(screen, opts) {
  const o = opts || {};
  const box = h("article", { class: "contentmap", "data-screen": screen.id || null });
  box.appendChild(h("header", null, h("h4", null, untrusted(screen.title || screen.id || "Screen")), screen.purpose ? h("p", { class: "purpose" }, untrusted(screen.purpose)) : null));
  box.appendChild(h("p", { class: "illustrative", role: "note" }, ILLUSTRATIVE, o.mock ? " Showing: " + mockWord(o.mock) + "." : ""));
  for (const v of VOICES) {
    const regions = screen[v.key] || [];
    box.appendChild(
      h(
        "section",
        { class: "voice voice-" + v.key },
        h("h5", null, h("span", { class: "voice-word" }, v.word), " ", h("span", { class: "voice-note" }, v.voice)),
        regions.length ? h("ul", { class: "regions" }, regions.map(regionNode)) : h("p", { class: "muted" }, "None.")
      )
    );
  }
  if (screen.actions.length) {
    box.appendChild(
      h(
        "section",
        { class: "block" },
        h("h5", null, "Actions"),
        h("ul", null, screen.actions.map((a) => h("li", null, untrusted(a.label, "", "strong"), a.intent ? [" - to ", untrusted(a.intent)] : null, a.effect ? [" - then ", untrusted(a.effect)] : null)))
      )
    );
  }
  if (screen.states.length) {
    box.appendChild(h("section", { class: "block" }, h("h5", null, "States"), h("ul", null, screen.states.map((s) => h("li", null, s.name ? [untrusted(s.name, "", "strong"), ": "] : null, untrusted(s.text))))));
  }
  if (screen.copy.length) {
    box.appendChild(h("section", { class: "block" }, h("h5", null, "Words on the screen (source language)"), h("ul", null, screen.copy.map((c) => h("li", null, c.key ? [untrusted(c.key, "muted"), ": "] : null, untrusted(c.text))))));
  }
  if (screen.records.length) {
    box.appendChild(h("section", { class: "block" }, h("h5", null, "Example records"), h("ul", null, screen.records.map((r) => h("li", null, untrusted(r))))));
  }
  return box;
}
