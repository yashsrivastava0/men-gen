/* Step 1, Inputs: what the app found, the proposed pairs for each size (with the reason and a one-click override),
   the Compass-only option, gaps, drift flags and readiness, then "Start" which creates the run. */
import { h, setKids, untrusted, put, keepFocus } from "../lib/dom.js";
import { normalizeInputs, normalizeProposal, normalizeProfiles } from "../lib/model.js";
import { humanize, capitalize } from "../lib/format.js";
import { banner, chip, problemBanner, problemOf, loading, pageHead, card, facts, bullets } from "./shared.js";

export const SIZES = [
  { id: "compact", label: "Compact", note: "a small app" },
  { id: "standard", label: "Standard", note: "a medium app" },
  { id: "deep", label: "Deep", note: "a big app" },
  { id: "program", label: "Program", note: "several apps together" },
];

export function mount(root, ctx) {
  let gone = false;
  const body = h("div", null, loading());
  put(root, pageHead("1. Inputs", "Pick what the Spec should be built from: an export from Compass (the interview) and, if there is one, the Research that matches it. The app proposes a pair for each size; you can change any of it."), body);
  const sel = { compassId: null, researchId: null, compassOnly: false, profileId: "", offline: false };
  let data = null;
  let proposals = {};
  let profiles = [];
  let failure = null;
  let starting = false;
  let startProblem = null;
  let joined = false;

  async function load() {
    try {
      const sizeCalls = SIZES.map((s) => ctx.api.get("/api/inputs/auto" + ctx.api.q({ size: s.id })).then((r) => normalizeProposal(r), () => null));
      const [inputs, prof, ...props] = await Promise.all([ctx.api.get("/api/inputs"), ctx.api.get("/api/profiles").catch(() => null), ...sizeCalls]);
      data = normalizeInputs(inputs);
      profiles = normalizeProfiles(prof);
      SIZES.forEach((s, i) => (proposals[s.id] = props[i]));
      const remembered = ctx.store.get("inputs", null);
      if (remembered && data.compass.some((c) => c.id === remembered.compassId)) {
        sel.compassId = remembered.compassId;
        sel.researchId = remembered.researchId || null;
        sel.compassOnly = !!remembered.compassOnly;
        sel.profileId = remembered.profileId || "";
      }
      if (!sel.profileId && profiles.length) sel.profileId = profiles[0].id;
      failure = null;
    } catch (e) {
      failure = problemOf(e);
    }
    draw();
  }

  function usePair(compassId, researchId) {
    sel.compassId = compassId;
    sel.researchId = researchId;
    sel.compassOnly = !researchId;
    startProblem = null;
    draw();
  }

  async function start() {
    if (starting || !sel.compassId) return;
    starting = true;
    startProblem = null;
    draw();
    const payload = { compass_id: sel.compassId, offline: !!sel.offline };
    if (!sel.compassOnly && sel.researchId) payload.research_id = sel.researchId;
    if (sel.profileId) payload.consumer_profile = sel.profileId;
    try {
      const r = await ctx.api.post("/api/runs", payload);
      ctx.store.set("inputs", { compassId: sel.compassId, researchId: sel.researchId, compassOnly: sel.compassOnly, profileId: sel.profileId });
      joined = !!(r && r.joined);
      if (r && r.run_id) {
        ctx.setRun(r.run_id);
        ctx.refreshBudget();
        ctx.navigate("#/preview/" + r.run_id);
        return;
      }
      startProblem = { text: "The app did not say which run it started. Open the Run history.", fix: "" };
    } catch (e) {
      startProblem = problemOf(e);
    }
    starting = false;
    draw();
  }

  function proposalCard(size) {
    const p = proposals[size.id];
    const node = h("div", { class: "proposal", "data-size": size.id }, h("h4", null, size.label, " ", h("span", { class: "muted" }, "(" + size.note + ")")));
    if (!p) {
      node.appendChild(h("p", { class: "muted" }, "Nothing suitable was found for this size."));
      return node;
    }
    const c = data.compass.find((x) => x.id === p.compassId);
    node.appendChild(facts([["Compass export", c ? c.id + (c.tier ? " (" + c.tier + ")" : "") : p.compassId], ["Research", p.researchId || "none: the Spec runs with a thin research lane and says so"], ["Why this pair", p.reason]]));
    if (p.gaps.length) node.appendChild(h("div", null, h("strong", null, "Gaps: "), bullets(p.gaps)));
    const selected = sel.compassId === p.compassId && (sel.researchId || null) === p.researchId;
    node.appendChild(h("button", { type: "button", class: "btn" + (selected ? " btn-primary" : ""), "data-fk": "use-" + size.id, "aria-pressed": String(selected), onclick: () => usePair(p.compassId, p.researchId) }, selected ? "Selected" : "Use this pair"));
    return node;
  }

  function compassRow(c) {
    const chosen = sel.compassId === c.id;
    return h(
      "label",
      { class: "choice" + (chosen ? " chosen" : "") },
      h("input", { type: "radio", name: "compass", value: c.id, checked: chosen, "data-fk": "compass-" + c.id, onchange: () => { sel.compassId = c.id; startProblem = null; if (!sel.researchId || !c.matches.includes(sel.researchId)) sel.researchId = c.matches[0] || null; draw(); } }),
      h("span", null, untrusted(c.id, "", "strong"), " ", c.tier ? chip(capitalize(c.tier), "busy") : null, " ", c.sizeBand ? chip(c.sizeBand) : null, " ", c.ready ? chip("Ready", "ok") : chip("Not ready", "bad")),
      c.profile ? h("span", { class: "muted" }, "Profile: ", untrusted(c.profile)) : null,
      c.format ? h("span", { class: "muted small" }, "Format ", untrusted(c.format)) : null,
      c.missing.length ? h("span", { class: "tone-bad" }, "Missing: ", untrusted(c.missing.join(", "))) : null,
      c.drift.length ? h("span", { class: "tone-warn" }, "Changed since it was made: ", untrusted(c.drift.join(", "))) : null
    );
  }

  function draw() {
    if (gone) return;
    keepFocus(body, paint);
  }
  function paint() {
    if (failure) {
      setKids(body, problemBanner(failure), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
      return;
    }
    if (!data) return;
    const kids = [];
    if (!data.compass.length) {
      kids.push(h("div", { class: "card empty" }, h("h3", null, "No Compass export found yet"), h("p", null, "Put a Compass export in docs/compass/research-inputs or add one below.")));
    }
    kids.push(card("Proposed pairs", h("p", { class: "muted" }, "One proposal per size of app. Pick one with one click, or choose something else below."), h("div", { class: "proposals" }, SIZES.map(proposalCard))));
    kids.push(
      card(
        "Choose yourself",
        h("fieldset", { class: "choices" }, h("legend", null, "Compass export (the interview result)"), data.compass.map(compassRow)),
        h(
          "fieldset",
          { class: "choices" },
          h("legend", null, "Research (what was found on the web)"),
          h("label", { class: "choice" + (sel.compassOnly ? " chosen" : "") }, h("input", { type: "radio", name: "research", checked: sel.compassOnly, "data-fk": "research-none", onchange: () => { sel.compassOnly = true; draw(); } }), h("span", null, h("strong", null, "Compass only"), " ", h("span", { class: "muted" }, "(no Research: the Spec runs with a thin research lane and says so)"))),
          data.research.map((r) => {
            const chosen = !sel.compassOnly && sel.researchId === r.id;
            return h("label", { class: "choice" + (chosen ? " chosen" : "") }, h("input", { type: "radio", name: "research", checked: chosen, "data-fk": "research-" + r.id, onchange: () => { sel.compassOnly = false; sel.researchId = r.id; draw(); } }), h("span", null, untrusted(r.id, "", "strong"), " ", chip(capitalize(humanize(r.status)), r.status === "complete" ? "ok" : "warn")));
          })
        )
      )
    );
    const chosenCompass = data.compass.find((c) => c.id === sel.compassId);
    const notReady = chosenCompass && !chosenCompass.ready;
    const profileSelect = h("select", { class: "text-input", "data-fk": "profile", onchange: (e) => { sel.profileId = e.target.value; } }, profiles.map((p) => h("option", { value: p.id, selected: p.id === sel.profileId }, p.label + (p.capabilityClass ? " (" + p.capabilityClass + ")" : ""))));
    const offline = h("input", { type: "checkbox", checked: sel.offline, "data-fk": "offline", onchange: (e) => { sel.offline = !!e.target.checked; } });
    kids.push(
      card(
        "Start",
        h("div", { class: "row wrap" }, h("label", null, "Who will read the finished spec: ", profileSelect)),
        h("label", { class: "row" }, offline, h("span", null, "Offline run: no model is called, so it costs nothing. Good for a first look.")),
        notReady ? banner("This export is not ready: " + chosenCompass.missing.join(", ") + ". Starting is turned off until it is complete.", "warn") : null,
        startProblem ? problemBanner(startProblem) : null,
        joined ? banner("This input is already running. You joined the run in progress.", "info") : null,
        h("div", { class: "actions" }, h("button", { type: "button", class: "btn btn-primary", "data-fk": "start", disabled: starting || !sel.compassId || !!notReady, onclick: start }, starting ? "Starting…" : "Create the run and see the preview (free)"), !sel.compassId ? h("p", { class: "why-off" }, "Choose a Compass export first.") : null)
      )
    );
    kids.push(uploadCard());
    setKids(body, kids);
  }

  let uploadNote = null;
  function uploadCard() {
    const note = h("div", { class: "upload-note" });
    if (uploadNote) note.appendChild(uploadNote);
    const input = h("input", { type: "file", accept: ".json,application/json", class: "file-input", "data-fk": "upload", onchange: async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      try {
        await ctx.api.postRaw("/api/inputs/upload", await file.text());
        uploadNote = banner("Added. It is in the list now.", "ok");
        await load();
        return;
      } catch (err) {
        uploadNote = problemBanner(problemOf(err));
      }
      draw();
    } });
    return card("Add a Compass export", h("p", { class: "muted" }, "A JSON file from Compass. It is kept inside the app only."), h("label", { class: "btn" }, "Choose a file…", input), note);
  }

  load();
  return {
    unmount() {
      gone = true;
    },
  };
}
