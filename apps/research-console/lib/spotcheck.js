/* The spot-check: verdict buttons and their keys, which card is next, progress, and the summary in plain words. Pure. */
import { fmtPct, STRATUM_WORDS } from "./results.js";

export const VERDICTS = [
  { id: "ok", key: "1", label: "OK", hint: "The quote says it and it is right for this app." },
  { id: "not_relevant", key: "2", label: "Not relevant", hint: "True, but it does not matter for this app." },
  { id: "claim_not_in_quote", key: "3", label: "Claim not in quote", hint: "The quote does not actually say this." },
  { id: "wrong_or_outdated", key: "4", label: "Wrong or outdated", hint: "The page is wrong or too old to rely on." },
  { id: "wrong_place_or_company", key: "5", label: "Wrong place or company", hint: "It is about another country or another company." },
  { id: "invented_detail", key: "6", label: "Invented detail", hint: "A detail that appears nowhere in the source." },
];
export const SKIP = { id: "skipped", key: "s", label: "Skip" };
const BY_KEY = {};
for (const v of VERDICTS) BY_KEY[v.key] = v.id;
BY_KEY[SKIP.key] = SKIP.id;

/** A pressed key -> a verdict id, or null (modifier keys, other keys, typing in a field are never verdicts). */
export function verdictForKey(e) {
  if (!e || e.ctrlKey || e.metaKey || e.altKey) return null;
  const key = String(e.key || "").toLowerCase();
  return Object.prototype.hasOwnProperty.call(BY_KEY, key) ? BY_KEY[key] : null;
}
export function verdictLabel(id) {
  const v = VERDICTS.find((x) => x.id === id);
  return v ? v.label : id === "skipped" ? "Skipped" : "";
}

/** Index of the first card that has no verdict at all (skipped cards count as answered). -1 when every card has one. */
export function nextUnanswered(check, from) {
  const cards = check.cards || [];
  const verdicts = check.verdicts || {};
  for (let i = from || 0; i < cards.length; i++) if (!verdicts[cards[i].key]) return i;
  return -1;
}
export function nextSkipped(check) {
  const cards = check.cards || [];
  for (let i = 0; i < cards.length; i++) if ((check.verdicts || {})[cards[i].key] === "skipped") return i;
  return -1;
}
export function progress(check) {
  const cards = check.cards || [];
  const v = check.verdicts || {};
  let answered = 0;
  let skipped = 0;
  for (const c of cards) {
    if (v[c.key] === "skipped") skipped += 1;
    else if (v[c.key]) answered += 1;
  }
  return { total: cards.length, answered: answered, skipped: skipped, left: cards.length - answered - skipped };
}
export const strataWord = (s) => STRATUM_WORDS[s] || "A fact";

/** The summary against the thresholds as rows: {label, value, target, ok}. */
export function summaryRows(s) {
  const t = s.thresholds || { supported: 0.95, ok: 0.9, invented_total_below: 3 };
  const p = s.pass || {};
  return [
    { label: "Supported (OK or not relevant)", value: fmtPct(s.supported_pct), target: "at least " + fmtPct(t.supported), ok: !!p.supported },
    { label: "OK", value: fmtPct(s.ok_pct), target: "at least " + fmtPct(t.ok), ok: !!p.ok },
    {
      label: "Invented detail",
      value: s.invented_total + " in all, " + s.invented_high_impact + " on high-impact facts",
      target: "none on high-impact facts and fewer than " + t.invented_total_below + " in all",
      ok: !!p.no_invented,
    },
  ];
}
export function verdictText(s) {
  if (!s.answered) return "Nothing answered yet.";
  return s.passed ? "Passed: the research met every threshold." : "Not passed: at least one threshold was missed.";
}
/** {where: "job:services", count: 3} -> "services (3 not OK)". */
export function worstText(w) {
  const at = String(w.where || "").split(":");
  const name = at.length > 1 ? at.slice(1).join(":").replace(/_/g, " ") : String(w.where || "");
  return name + " (" + w.count + " not OK)";
}
