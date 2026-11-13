/* One event as a plain line for the live feed: {kind, text}. Pure. The server's words are shown as they are
   (as text); the line only adds the plain label in front. */
import { stepWord, reasonWord, bannerWord, usd, tokensText } from "./format.js";
import { tokensOf } from "./reducer.js";

const str = (v) => (v === undefined || v === null ? "" : String(v));

export function describeEvent(ev) {
  const d = ev.data && typeof ev.data === "object" ? ev.data : {};
  const step = ev.step ? stepWord(ev.step) : "";
  switch (ev.type) {
    case "run.queued": return { kind: "run", text: "The run is waiting to start." };
    case "run.started": return { kind: "run", text: "The run started." };
    case "plan.ready": return { kind: "plan", text: "The plan is ready." + (d.summary ? " " + str(d.summary) : "") };
    case "step.started": return { kind: "step", text: "Started: " + (step || str(d.step)) };
    case "step.finished": return { kind: "step", text: "Finished: " + (step || str(d.step)) };
    case "call.finished":
      return { kind: "call", text: "A model call finished (" + str(d.kind || "call") + "): " + tokensText(tokensOf(d.tokens)) + ", " + usd(d.cost_micros) + (d.repaired ? ", repaired" : "") };
    case "validators.done": return { kind: "check", text: "Checks done at " + str(d.gate || "a gate") + ": " + (d.blockers || 0) + " must-fix, " + (d.warnings || 0) + " to check." };
    case "repair.rcode": return { kind: "repair", text: "A mechanical fix was made. " + str(d.message || d.code) };
    case "repair.reask": return { kind: "repair", text: "A part was asked again. " + str(d.message || d.node) };
    case "banner": return { kind: "banner", text: bannerWord(d.code || d.kind || "banner") };
    case "gate.opened": return { kind: "gate", text: "Waiting for your OK: " + (d.gate === "g15" ? "the structure (G1.5)" : "the spec (G2)") };
    case "gate.closed": return { kind: "gate", text: "Decision recorded: " + (d.gate === "g15" ? "the structure (G1.5)" : "the spec (G2)") };
    case "checkpoint": return { kind: "save", text: "Progress saved." };
    case "run.partial": return { kind: "run", text: "Stopped early: " + reasonWord(d.reason) + "." };
    case "run.resumed": return { kind: "run", text: "Continuing from the last saved point." };
    case "run.finished": return { kind: "run", text: "The run finished." };
    case "run.failed": return { kind: "run", text: "The run failed. " + str(d.reason || d.message) };
    default: return { kind: "event", text: str(ev.type) };
  }
}
