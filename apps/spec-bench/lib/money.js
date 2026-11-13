/* The money state shown in the header at all times. Pure. Micro-USD integers. The state comes from the server
   (ok | tripwire | exhausted); when it is missing it is worked out from the numbers. */
import { usd, usdNum } from "./format.js";

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

/** "ok" | "tripwire" (no new wave starts) | "exhausted" (the cap is reached) | "unknown" (no figures yet). */
export function moneyLevel(b) {
  if (!b || typeof b !== "object" || num(b.cap_micros) === null) return "unknown";
  if (b.state === "exhausted" || b.state === "tripwire" || b.state === "ok") return b.state;
  const used = (num(b.spent_micros) || 0) + (num(b.reserved_micros) || 0);
  if (used >= b.cap_micros) return "exhausted";
  if (num(b.tripwire_micros) !== null && used >= b.tripwire_micros) return "tripwire";
  return "ok";
}
/** "Spent USD 1.23 of the USD 5.00 cap. Tripwire USD 4.50." */
export function moneyText(b) {
  if (moneyLevel(b) === "unknown") return "Money: not known yet";
  const left = num(b.remaining_micros);
  return (
    "Spent " + usd(b.spent_micros) + " of the " + usd(b.cap_micros) + " cap" + (left !== null ? " (" + usd(Math.max(0, left)) + " left)" : "") + ". Tripwire " + usd(b.tripwire_micros) + "." +
    (num(b.reserved_micros) > 0 ? " " + usd(b.reserved_micros) + " is held for calls in progress." : "")
  );
}
/** Positions on the bar in percent of the cap: spent, held and the tripwire. */
export function moneyPercents(b) {
  if (!b || !(num(b.cap_micros) > 0)) return { used: 0, reserved: 0, tripwire: 0 };
  const clamp = (v) => Math.max(0, Math.min(100, v));
  return {
    used: clamp((100 * (num(b.spent_micros) || 0)) / b.cap_micros),
    reserved: clamp((100 * (num(b.reserved_micros) || 0)) / b.cap_micros),
    tripwire: clamp((100 * (num(b.tripwire_micros) || 0)) / b.cap_micros),
  };
}
/** A banner for the header when the state needs words: {level, text}, or null when all is well. */
export function moneyBanner(b) {
  const level = moneyLevel(b);
  if (level === "tripwire") {
    return { level: "tripwire", text: "The money tripwire (" + usd(b.tripwire_micros) + ") was reached. Work already started finishes, but no new step starts. Only the report-writing that costs nothing goes on." };
  }
  if (level === "exhausted") {
    return { level: "exhausted", text: "The money cap (" + usd(b.cap_micros) + ") is used up. Nothing that costs money can start. Only the owner can raise it." };
  }
  return null;
}
/** What to say about a run's estimate against what is left: {refused, lines[]}. Refused when the high end is more than is left. */
export function estimateVerdict(estimate, remainingMicros) {
  const out = { refused: false, lines: [] };
  const low = estimate && num(estimate.low_micros);
  const high = estimate && num(estimate.high_micros);
  if (low === null || high === null) {
    out.lines.push("No cost estimate was given.");
    return out;
  }
  if (num(remainingMicros) === null) {
    out.lines.push("It will cost between " + usdNum(low) + " and " + usdNum(high) + " USD.");
    return out;
  }
  const left = Math.max(0, remainingMicros);
  if (high > left) {
    out.refused = true;
    out.lines.push(
      "This run will not start: the highest it could cost (" + usd(high) + ") is more than the " + usd(left) + " that is left. An offline run costs nothing."
    );
  } else {
    out.lines.push("It fits: it will cost between " + usdNum(low) + " and " + usdNum(high) + " USD, and " + usd(left) + " is left.");
  }
  return out;
}
