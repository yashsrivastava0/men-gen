/* Hash routes and the journey rail. Pure. Routes look like #/name/<run>/<tab>/<more>. The six steps of the owner's
   journey are the rail; the run history and the money page sit below it. */
import { statusWord } from "./format.js";

export const RUN_VIEWS = ["preview", "g15", "run", "g2", "handoff"];
const NAMES = ["inputs", "preview", "g15", "run", "g2", "handoff", "runs", "budget"];

/** "#/handoff/run_01/screens/scr_a" -> {name, id, tab, extra}; anything unknown is the first step. */
export function parseRoute(hash) {
  const m = /^#\/([a-z0-9]+)(?:\/([A-Za-z0-9_-]{1,80}))?(?:\/([a-z]+))?(?:\/([A-Za-z0-9_-]{1,120}))?\/?$/.exec(hash || "");
  if (!m || NAMES.indexOf(m[1]) === -1) return { name: "inputs", id: null, tab: null, extra: null };
  return { name: m[1], id: m[2] || null, tab: m[3] || null, extra: m[4] || null };
}

/** The rail: six numbered steps (a link when a run exists for steps 2 to 6) and the two pages below.
    `status` is the run's state: the step that needs the owner gets a hint. */
export function railItems(runId, current, status) {
  const to = (name, tail) => (name === "inputs" ? "#/inputs" : runId ? "#/" + name + "/" + runId + (tail || "") : null);
  const steps = [
    { n: 1, name: "inputs", label: "Inputs" },
    { n: 2, name: "preview", label: "Preview (costs nothing)" },
    { n: 3, name: "g15", label: "Structure check (G1.5)", hint: status === "paused_g15" ? "needs your OK" : "" },
    { n: 4, name: "run", label: "Run", hint: status === "partial" ? "stopped early" : "" },
    { n: 5, name: "g2", label: "Spec review (G2)", hint: status === "awaiting_g2" ? "needs your OK" : "" },
    { n: 6, name: "handoff", label: "Hand-off (mock front end)", tail: "/sim" },
  ].map((s) => ({ n: s.n, label: s.label, hint: s.hint || "", route: to(s.name, s.tail), current: s.name === current }));
  const more = [
    { label: "Run history", route: "#/runs", current: current === "runs" },
    { label: "Money", route: "#/budget", current: current === "budget" },
  ];
  return { steps: steps, more: more, statusText: status ? statusWord(status) : "" };
}
