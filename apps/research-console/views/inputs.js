/* Inputs (home): the approved Compass exports found in the inputs folder, each with a free preview, an estimate and a
   Run button behind a confirm dialog. Auto-refreshes every 10 s while the tab is visible and on focus. */
import { h, clear, setKids, untrusted, navLink, keepFocus, put } from "../lib/dom.js";
import { modal } from "./dialogs.js";
import { statusChip, sizeBadge, estimateParts, placeText, countsText, row, problemBanner } from "./shared.js";
import { usd, usdNum, ago, formatDateTime, bytesText, jobTitle, plural, humanize, sizeWord } from "../lib/format.js";
import { startBlock, estimateAdvice } from "../lib/budget.js";
import { filterInputs, sizeCounts } from "../lib/filters.js";
import { ApiError } from "../lib/api.js";
import { isActiveStatus } from "../lib/reducer.js";

const REFRESH_MS = 10000;
const MAX_UPLOAD = 25 * 1024 * 1024;
const SIZES = ["all", "tiny", "small", "medium", "big"];

export function mount(root, ctx) {
  const openDetails = new Set();
  let data = null;
  let lastJson = "";
  let failure = null;
  let sizeFilter = ctx.store.get("inputs.size", "all");
  let updatedAt = 0;
  let timer = 0;
  let gone = false;
  let blockSig = "";

  const noticeBox = h("div", { class: "notice-slot" });
  const failBox = h("div", { class: "notice-slot" });
  const filterBox = h("div", { class: "filters", role: "group", "aria-label": "Show inputs of size" });
  const listBox = h("div", { class: "cards" });
  const skippedBox = h("div", null);
  const updated = h("span", { class: "muted small" }, "");
  const refreshBtn = h("button", { type: "button", class: "btn", "data-fk": "refresh", onclick: () => load(true) }, "Refresh");
  const fileInput = h("input", { type: "file", accept: ".json,application/json", class: "file-input", id: "upload-file", onchange: onFile });

  root.append(
    h("div", { class: "page-head" }, h("h2", null, "Inputs"), h("div", { class: "row" }, updated, refreshBtn)),
    h("p", { class: "lead" }, "Pick an approved Compass export and run research on it. Each card shows, before you spend anything, what would be researched and what it would probably cost."),
    noticeBox,
    failBox,
    filterBox,
    listBox,
    skippedBox,
    h(
      "section",
      { class: "upload card" },
      h("h3", null, "Add a Compass export"),
      h("p", { class: "muted" }, "Choose an approved Compass export (a .json file saved after the Brief was approved). It is checked like any file in the folder and kept in the app's own data folder."),
      h("label", { class: "btn", for: "upload-file" }, "Choose a file…"),
      fileInput
    )
  );

  // ---- loading --------------------------------------------------------------------------------------------------------
  async function load(manual) {
    if (gone) return;
    try {
      const res = await ctx.api.get("/api/inputs");
      failure = null;
      updatedAt = Date.now();
      const json = JSON.stringify(res);
      const changed = json !== lastJson;
      lastJson = json;
      data = res;
      if (changed || manual) draw();
      else drawUpdated();
      if (manual) ctx.announce("Inputs refreshed");
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "The list could not be loaded." };
      draw();
    }
  }
  function drawUpdated() {
    updated.textContent = updatedAt ? "Updated " + ago(new Date(updatedAt).toISOString()) : "";
  }

  // ---- drawing --------------------------------------------------------------------------------------------------------
  function draw() {
    if (gone) return;
    blockSig = JSON.stringify(startBlock(ctx.state.budget, ctx.state.preflight));
    keepFocus(root, () => {
      drawUpdated();
      clear(failBox);
      if (failure) {
        failBox.appendChild(
          h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, failure.text), h("button", { type: "button", class: "btn", onclick: () => load(true) }, "Try again"))
        );
      }
      if (!data) {
        setKids(listBox, h("p", { class: "muted" }, failure ? "" : "Loading the inputs…"));
        clear(filterBox);
        return;
      }
      drawFilters();
      drawList();
      drawSkipped();
    });
  }
  function drawFilters() {
    clear(filterBox);
    const counts = sizeCounts(data.inputs);
    if (data.inputs.length < 2) return;
    filterBox.appendChild(h("span", { class: "muted small" }, "Show:"));
    for (const s of SIZES) {
      if (s !== "all" && !counts[s]) continue;
      filterBox.appendChild(
        h(
          "button",
          {
            type: "button",
            class: "btn btn-chip",
            "aria-pressed": sizeFilter === s ? "true" : "false",
            "data-fk": "size:" + s,
            onclick: () => {
              sizeFilter = s;
              ctx.store.set("inputs.size", s);
              draw();
            },
          },
          (s === "all" ? "All" : sizeWord(s)) + " (" + counts[s] + ")"
        )
      );
    }
  }
  function drawList() {
    const inputs = filterInputs(data.inputs, sizeFilter);
    clear(listBox);
    if (!data.inputs.length) {
      listBox.appendChild(emptyState());
      return;
    }
    if (!inputs.length) {
      listBox.appendChild(h("p", { class: "muted" }, "No input of that size. Choose another size above."));
      return;
    }
    const last = ctx.store.get("lastInput", null);
    for (const input of inputs) listBox.appendChild(card(input, last === input.id));
  }
  function emptyState() {
    return h(
      "section",
      { class: "card empty" },
      h("h3", null, data.folder_exists ? "No approved Compass exports found" : "The inputs folder does not exist yet"),
      h("p", null, data.folder_exists ? "Looked in this folder:" : "The app looks for exports in this folder, which is missing:"),
      h("p", null, h("code", { class: "path", dir: "auto" }, data.folder)),
      h("p", null, "To add one:"),
      h(
        "ol",
        null,
        h("li", null, data.folder_exists ? "Copy an approved Compass export (.json) into that folder." : "Create that folder, then copy an approved Compass export (.json) into it."),
        h("li", null, "Come back to this page. It checks the folder every 10 seconds, so the file appears by itself."),
        h("li", null, "Or use “Add a Compass export” below to pick the file from anywhere.")
      ),
      (data.skipped || []).length ? h("p", { class: "muted" }, "Some files were found but could not be used; see “Skipped files” below.") : null
    );
  }
  function drawSkipped() {
    clear(skippedBox);
    const skipped = data.skipped || [];
    if (!skipped.length) return;
    skippedBox.appendChild(
      h(
        "details",
        { class: "card skipped", open: openDetails.has("skipped"), ontoggle: (e) => toggle("skipped", e) },
        h("summary", { "data-fk": "skipped" }, "Skipped files (" + skipped.length + ")"),
        h("p", { class: "muted" }, "These files are in the folder but cannot be researched. Nothing is wrong with the app."),
        h("ul", { class: "plain-list" }, skipped.map((s) => h("li", null, untrusted(s.name, "fname"), " — ", untrusted(s.text || humanize(s.reason)), s.size ? " (" + bytesText(s.size) + ")" : "")))
      )
    );
  }
  function toggle(key, e) {
    if (e.target.open) openDetails.add(key);
    else openDetails.delete(key);
  }

  function card(input, isLast) {
    const p = input.preview || {};
    const idea = p.idea || {};
    const est = estimateParts(p.estimate);
    const active = input.active_run;
    const title = idea.category || input.name;
    const badges = [sizeBadge(p.size_class, p.size_label)];
    if (p.light) badges.push(h("span", { class: "chip chip-plain" }, "Light mode"));
    if (active) badges.push(statusChip(active.status));
    if (input.changed_since_last_run) badges.push(h("span", { class: "chip chip-warn", title: "The file was edited after the last run. A run uses its own copy of the file as it was when it started." }, "Changed since last run"));
    if (isLast) badges.push(h("span", { class: "chip chip-plain" }, "Last used"));

    const facts = h("dl", { class: "facts" });
    facts.append(
      ...row("Place", placeText(p.place), true),
      ...row("Languages", (p.languages || []).join(", ") || "not stated", true),
      ...row("Contains", countsText(p.counts)),
      ...row("Size", (p.size_label || sizeWord(p.size_class)) + (p.load ? " (load: " + p.load.class + ", score " + p.load.score + ")" : "")),
      ...row("Light mode", p.light ? "yes: a very small app, so only a quick look" : "no"),
      ...row("Trust checks", h("span", null, h("strong", null, p.trust && p.trust.on ? "on" : "off"), " — ", untrusted((p.trust && p.trust.why) || ""))),
      ...row("Topics planned", topicSummary(p.topics))
    );
    if (est) {
      facts.append(
        ...row(
          "Estimate",
          h(
            "span",
            null,
            est.cost,
            est.time ? ", " + est.time : "",
            est.work ? ", " + est.work : "",
            ". ",
            h("em", null, est.note)
          )
        )
      );
    }
    const declined = p.declined || [];
    const warns = p.warnings || [];

    const details = h(
      "details",
      { class: "details", open: openDetails.has(input.id), ontoggle: (e) => toggle(input.id, e) },
      h("summary", { "data-fk": "det:" + input.id }, "Details"),
      detailsBody(input, p)
    );

    return h(
      "article",
      { class: "card input-card", "data-input": input.id },
      h("div", { class: "card-head" }, h("h3", { class: "card-title" }, untrusted(title)), h("div", { class: "badges" }, badges)),
      idea.summary ? h("p", { class: "summary" }, untrusted(idea.summary)) : null,
      h(
        "p",
        { class: "filemeta muted small" },
        "File: ",
        untrusted(input.name, "fname"),
        " · modified ",
        h("span", { title: formatDateTime(input.modified) }, ago(input.modified)),
        " · fingerprint ",
        h("code", null, input.short_hash || input.id),
        (input.also_named || []).length ? h("span", null, " · same content also saved as ", ...(input.also_named || []).map((n, i) => [i ? ", " : "", untrusted(n, "fname")])) : null
      ),
      facts,
      declined.length ? h("p", { class: "declined" }, h("strong", null, "We already know this: "), declined.map((d, i) => [i ? ", " : "", untrusted(d)])) : null,
      warns.length ? h("ul", { class: "warn-list" }, warns.map((w) => h("li", null, h("span", { class: "chip chip-warn" }, "Note"), " ", untrusted(w.text)))) : null,
      details,
      pastRuns(input),
      actions(input, p)
    );
  }
  function topicSummary(topics) {
    if (!topics) return "none";
    const jobs = Object.keys(topics.by_job || {});
    if (!topics.total) return "none: there is nothing to research";
    return topics.total + (jobs.length ? " (" + jobs.map((j) => jobTitle(j) + " " + topics.by_job[j].count).join(", ") + ")" : "");
  }
  function detailsBody(input, p) {
    const idea = p.idea || {};
    const topics = p.topics || { by_job: {} };
    const box = h("div", { class: "details-body" });
    put(
      box,
      h("h4", null, "Brief summary"),
      h("dl", { class: "facts" }, ...row("Idea", idea.summary || "", true), ...row("Category", [idea.category, idea.subcategory].filter(Boolean).join(" — "), true), ...row("Compass interview", p.compass ? "size " + p.compass.tier + (p.compass.degraded ? ", some steps degraded" : "") : "")),
      h("h4", null, "Topics we would research"),
      Object.keys(topics.by_job || {}).length
        ? h(
            "div",
            { class: "jobs" },
            Object.keys(topics.by_job).map((job) =>
              h("div", { class: "job" }, h("h5", null, jobTitle(job) + " (" + topics.by_job[job].count + ")"), h("ul", null, (topics.by_job[job].labels || []).map((l) => h("li", null, untrusted(l)))))
            )
          )
        : h("p", { class: "muted" }, "Nothing to research."),
      (topics.not_run || []).length
        ? h("p", { class: "muted small" }, plural(topics.not_run.length, "more topic") + " were considered but would not run in this size of study.")
        : null,
      (p.dropped || []).length ? h("div", null, h("h4", null, "Left out"), h("ul", null, p.dropped.map((d) => h("li", null, untrusted(d.label), " — ", untrusted(humanize(d.reason)))))) : null,
      h("h4", null, "Trust label"),
      h("p", null, h("strong", null, p.trust && p.trust.on ? "Trust checks are on. " : "Trust checks are off. "), untrusted((p.trust && p.trust.why) || "")),
      p.requests ? h("p", { class: "muted small" }, "Research requests in the Brief: " + p.requests.trusted + " usable, " + p.requests.template + " look like filler, " + p.requests.thin + " too thin.") : null
    );
    return box;
  }
  function pastRuns(input) {
    const runs = input.runs || [];
    if (!runs.length) return h("p", { class: "muted small" }, "No runs of this input yet.");
    return h(
      "div",
      { class: "past" },
      h("h4", null, "Past runs of this input"),
      h(
        "ul",
        { class: "plain-list" },
        runs.map((r) =>
          h(
            "li",
            null,
            statusChip(r.status),
            " ",
            h("span", { title: formatDateTime(r.created_at) }, ago(r.created_at)),
            " · ",
            usd(r.spend_micros || 0),
            " · ",
            navLink(isActiveStatus(r.status) ? "#/run/" + r.run_id : "#/results/" + r.run_id, isActiveStatus(r.status) ? "Watch" : "Open")
          )
        )
      )
    );
  }
  function actions(input, p) {
    if (input.active_run) {
      return h("div", { class: "actions" }, navLink("#/run/" + input.active_run.run_id, "Already running — open it", "btn btn-primary"));
    }
    const block = startBlock(ctx.state.budget, ctx.state.preflight);
    const why = h("p", { class: "why-off", id: "why-" + input.id }, block.reason);
    const btn = h("button", { type: "button", class: "btn btn-primary", "data-fk": "run:" + input.id, disabled: !block.ok, "aria-describedby": block.ok ? null : "why-" + input.id, onclick: (e) => confirmRun(input, e.currentTarget) }, "Run research");
    return h("div", { class: "actions" }, btn, block.ok ? null : why);
  }

  // ---- the confirm dialog ---------------------------------------------------------------------------------------------------
  async function confirmRun(input) {
    await ctx.refreshBudget();
    const p = input.preview || {};
    const b = ctx.state.budget;
    const est = estimateParts(p.estimate);
    const advice = b && b.enabled ? estimateAdvice(p.estimate, b.remaining_micros) : null;
    const m = modal("Start research?");
    const errorBox = h("div", { class: "dlg-error", role: "alert" });
    put(m.body, 
      h("p", { class: "dlg-line" }, h("strong", null, "This will start real searches and real model calls, and it costs money.")),
      h("p", null, untrusted((p.idea && p.idea.category) || input.name, "", "strong"), " — ", sizeWord(p.size_class), " size"),
      h(
        "dl",
        { class: "facts" },
        ...row("Likely cost", est ? est.cost : "unknown"),
        ...row("Likely time", est && est.time ? est.time : "unknown"),
        ...row("Left in the budget", b && b.enabled ? usd(b.remaining_micros) + " of " + usdNum(b.total_micros) : "not tracked")
      ),
      est ? h("p", { class: "muted small" }, est.note.charAt(0).toUpperCase() + est.note.slice(1) + ".") : null
    );
    if (advice) {
      for (const line of advice.lines) m.body.appendChild(h("p", { class: "dlg-line " + (advice.partial || advice.strong ? "tone-" + (advice.strong ? "bad" : "warn") : "") }, line));
    }
    m.body.appendChild(errorBox);
    const cancel = h("button", { type: "button", class: "btn", "data-autofocus": "1", onclick: () => m.close("no") }, "Cancel");
    const go = h("button", { type: "button", class: "btn btn-primary" }, "Start research (costs money)");
    m.foot.append(cancel, go);
    m.focusFirst();
    go.addEventListener("click", async () => {
      go.disabled = true;
      cancel.disabled = true;
      clear(errorBox);
      try {
        const res = await ctx.api.post("/api/runs", { input_id: input.id });
        ctx.store.set("lastInput", input.id);
        m.close("started");
        ctx.announce(res.created === false ? "That input is already running; opened it" : "Research started");
        ctx.navigate("#/run/" + res.run_id);
      } catch (e) {
        const problem = e instanceof ApiError ? e.problem : { text: "The run could not be started.", missing: [] };
        clear(errorBox);
        errorBox.appendChild(problemBanner(problem));
        if (problem.runId) errorBox.appendChild(h("p", null, navLink("#/run/" + problem.runId, "Open the run that is already going", "btn")));
        if (problem.kind === "not_ready") ctx.refreshPreflight();
        if (problem.kind === "budget") ctx.refreshBudget();
        cancel.disabled = false;
        cancel.textContent = "Close";
        cancel.focus();
        load(false);
      }
    });
  }

  // ---- upload ------------------------------------------------------------------------------------------------------------------
  async function onFile() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    clear(noticeBox);
    if (file.size > MAX_UPLOAD) {
      noticeBox.appendChild(h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, "That file is over 25 MB, which is more than the app accepts.")));
      fileInput.value = "";
      return;
    }
    noticeBox.appendChild(h("div", { class: "banner banner-info", role: "status" }, h("p", null, "Checking ", untrusted(file.name, "fname"), "…")));
    try {
      const res = await ctx.api.postRaw("/api/inputs/upload", file);
      clear(noticeBox);
      if (res && res.accepted) {
        noticeBox.appendChild(h("div", { class: "banner banner-ok", role: "status" }, h("p", null, "Accepted: ", untrusted(file.name, "fname"), " is now in the list.")));
        ctx.announce("File accepted");
      } else {
        noticeBox.appendChild(h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, "Refused: ", untrusted((res && res.text) || "this file cannot be used."))));
        ctx.announce("File refused");
      }
      fileInput.value = "";
      await load(false);
    } catch (e) {
      clear(noticeBox);
      noticeBox.appendChild(problemBanner(e instanceof ApiError ? e.problem : { text: "The upload failed.", missing: [] }));
      fileInput.value = "";
    }
  }

  // ---- refresh rules: every 10 s while visible, on focus, and when the budget or preflight changes ------------------------------
  const tick = () => {
    if (!document.hidden) load(false);
  };
  const onFocus = () => load(false);
  timer = setInterval(tick, REFRESH_MS);
  window.addEventListener("focus", onFocus);
  document.addEventListener("visibilitychange", tick);
  const redraw = () => {
    // only when what the Run buttons say has changed (the budget is polled every 15 s, mostly with nothing new)
    const sig = JSON.stringify(startBlock(ctx.state.budget, ctx.state.preflight));
    if (data && sig !== blockSig) draw();
  };
  const off1 = ctx.on("budget", redraw);
  const off2 = ctx.on("preflight", redraw);
  const labelTimer = setInterval(drawUpdated, 15000);
  draw();
  load(false);

  return {
    unmount() {
      gone = true;
      clearInterval(timer);
      clearInterval(labelTimer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", tick);
      off1();
      off2();
    },
  };
}
