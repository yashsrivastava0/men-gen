/* Filters for lists on the screens (inputs and runs). Pure: arrays in, arrays out. */

export const RUN_GROUPS = [
  { id: "all", label: "All" },
  { id: "active", label: "Running" },
  { id: "done", label: "Finished" },
  { id: "problem", label: "Needs attention" },
];
const GROUP_OF = {
  queued: "active",
  running: "active",
  committing: "active",
  complete: "done",
  partial: "done",
  skipped: "done",
  failed: "problem",
  interrupted: "problem",
  cancelled: "problem",
};
export const statusGroup = (status) => GROUP_OF[status] || "done";

/** Case-insensitive "every word appears" match. An empty query matches everything. */
export function textMatch(haystack, query) {
  const words = String(query || "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return true;
  const h = String(haystack || "").toLowerCase();
  return words.every((w) => h.indexOf(w) !== -1);
}
export function filterRuns(runs, f) {
  const group = (f && f.group) || "all";
  const q = (f && f.q) || "";
  return runs.filter(
    (r) =>
      (group === "all" || statusGroup(r.status) === group) &&
      textMatch([r.input_name, r.idea, r.status, r.size_class, r.run_id].join(" "), q)
  );
}
export function runGroupCounts(runs) {
  const c = { all: runs.length, active: 0, done: 0, problem: 0 };
  for (const r of runs) c[statusGroup(r.status)] += 1;
  return c;
}
/** Keep one size ("all" keeps everything). The server's order (Tiny, Small, Medium, Big) is kept. */
export function filterInputs(inputs, size) {
  if (!size || size === "all") return inputs;
  return inputs.filter((i) => i.preview && i.preview.size_class === size);
}
export function sizeCounts(inputs) {
  const c = { all: inputs.length, tiny: 0, small: 0, medium: 0, big: 0 };
  for (const i of inputs) {
    const s = i.preview && i.preview.size_class;
    if (s in c) c[s] += 1;
  }
  return c;
}
