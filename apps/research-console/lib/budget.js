/* Budget and estimate arithmetic for the bar, the Run button and the confirm dialog. Pure. Micro-USD integers. */
import { usd, usdNum } from "./format.js";

/** "ok" | "amber" (80% of the total used) | "red" (the tripwire reached or the money used up) | "off" (not tracked). */
export function budgetLevel(b) {
  if (!b || !b.enabled) return "off";
  if (b.state === "tripwire" || b.state === "exhausted") return "red";
  if (b.spent_micros >= b.tripwire_micros || b.spent_micros >= b.total_micros) return "red";
  if (b.spent_micros >= 0.8 * b.total_micros) return "amber";
  return "ok";
}
/** "USD 1.23 of 5.00 used; tripwire 4.50" */
export function budgetText(b) {
  if (!b || !b.enabled) return "Budget is not tracked";
  return usd(b.spent_micros) + " of " + usdNum(b.total_micros) + " used; tripwire " + usdNum(b.tripwire_micros);
}
/** Positions on the bar in percent of the total. */
export function budgetPercents(b) {
  if (!b || !b.enabled || !(b.total_micros > 0)) return { used: 0, tripwire: 0 };
  return {
    used: Math.max(0, Math.min(100, (100 * b.spent_micros) / b.total_micros)),
    tripwire: Math.max(0, Math.min(100, (100 * b.tripwire_micros) / b.total_micros)),
  };
}
/** Whether a new run may start now: {ok, reason}. */
export function startBlock(budget, preflight) {
  if (budget && budget.enabled && (budget.state === "tripwire" || budget.state === "exhausted")) {
    return {
      ok: false,
      reason:
        budget.state === "exhausted"
          ? "The budget is used up. Restart it on the Budget page to continue."
          : "The budget tripwire (" + usd(budget.tripwire_micros) + ") was reached, so no new run starts. Restart the budget on the Budget page to continue.",
    };
  }
  if (preflight && preflight.ready === false) {
    const bad = (preflight.checks || []).filter((c) => !c.ok && !/^key:/.test(c.id)).map((c) => c.label);
    return { ok: false, reason: "Not ready: " + (bad.length ? bad.join(", ") : "see the Preflight check at the top") + "." };
  }
  return { ok: true, reason: "" };
}
/** What to say before a run, from its estimate and what is left. {strong, partial, share, lines[]} */
export function estimateAdvice(estimate, remainingMicros) {
  const out = { strong: false, partial: false, share: null, lines: [] };
  const u = estimate && estimate.usd_micros;
  if (!u || typeof remainingMicros !== "number") return out;
  const left = Math.max(0, remainingMicros);
  out.share = left > 0 ? u.mid / left : Infinity;
  if (u.high > left) {
    out.partial = true;
    out.lines.push(
      "This may stop early (partial): the highest estimate (" + usd(u.high) + ") is more than the " + usd(left) + " that is left, so some topics may stay unfinished."
    );
  }
  if (u.mid > 0.4 * left) {
    out.strong = true;
    out.lines.push("Heads up: the expected cost (" + usd(u.mid) + ") is more than 40% of the " + usd(left) + " that is left.");
  }
  if (!out.partial && !out.strong) {
    out.lines.push("Expected to use about " + Math.max(1, Math.round(out.share * 100)) + "% of the " + usd(left) + " that is left.");
  }
  return out;
}
