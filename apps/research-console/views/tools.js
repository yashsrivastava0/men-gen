/* Tools (#/tools): search provider bake-off and provider settings.
   Non-engineers can compare providers head-to-head and choose which one handles live searches. */
import { h, clear, setKids, untrusted, put } from "../lib/dom.js";
import { modal } from "./dialogs.js";
import { problemBanner } from "./shared.js";
import { ApiError } from "../lib/api.js";

const PROVIDER_NAMES = {
  auto: "Auto (use the measured default)",
  tavily: "Tavily (AI search engine)",
  serper: "Serper (Google search)",
};

const PROVIDER_NOTES = {
  auto: "Uses the measured default provider from providers.yaml.",
  tavily: "Tavily costs about 8,000 micro-USD ($0.008) per credit.",
  serper: "Serper costs about 1,000 micro-USD ($0.001) per credit (up to 8x cheaper).",
};

export function mount(root, ctx) {
  let gone = false;
  let bakeoffData = null;
  let settingsData = null;
  let failure = null;
  let timer = null;

  const bakeoffCard = h("section", { class: "card", "aria-labelledby": "bakeoff-h" });
  const settingsCard = h("section", { class: "card", "aria-labelledby": "settings-h" });

  root.append(
    h("div", { class: "page-head" }, h("h2", null, "Tools")),
    h("p", { class: "lead" }, "Tune how Research searches the web: compare search providers head-to-head, or pick which provider is used first."),
    bakeoffCard,
    settingsCard
  );

  async function load() {
    try {
      const [bake, sett] = await Promise.all([
        ctx.api.get("/api/tools/bakeoff").catch((e) => (e && e.status === 404 ? null : Promise.reject(e))),
        ctx.api.get("/api/settings").catch((e) => (e && e.status === 404 ? null : Promise.reject(e))),
      ]);
      bakeoffData = bake;
      settingsData = sett;
      failure = null;
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "Tools could not be loaded." };
    }
    draw();
  }

  function draw() {
    if (gone) return;
    if (failure) {
      setKids(bakeoffCard, problemBanner(failure), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
      clear(settingsCard);
      return;
    }
    drawBakeoff();
    drawSettings();
  }

  // ---- Bake-off ---------------------------------------------------------------------------------------------------
  function drawBakeoff() {
    clear(bakeoffCard);
    bakeoffCard.appendChild(h("h3", { id: "bakeoff-h" }, "Search provider bake-off"));
    bakeoffCard.appendChild(
      h(
        "p",
        { class: "muted" },
        "Tests Tavily and Serper head-to-head on about 40 generic queries built from your inputs. It measures local results, relevance, speed and cost, then suggests the better choice. It costs about USD 0.40 and needs budget room."
      )
    );

    if (!bakeoffData) {
      bakeoffCard.appendChild(h("p", { class: "muted" }, "The bake-off tool is not enabled or not available in this mode."));
      return;
    }

    const state = bakeoffData.state || "idle";
    const plan = bakeoffData.plan || {};
    const last = bakeoffData.result || bakeoffData.last || null;

    if (state === "running") {
      bakeoffCard.appendChild(
        h(
          "div",
          { class: "banner banner-info", role: "status" },
          h("p", null, h("strong", null, "Bake-off is running… "), "Testing providers concurrently. This page updates as results arrive."),
          h("p", { class: "muted small" }, bakeoffData.progress ? "Progress: " + bakeoffData.progress : "")
        )
      );
      if (!timer) {
        timer = setInterval(() => {
          if (!document.hidden && !gone) load();
        }, 3000);
      }
      return;
    }

    if (timer) {
      clearInterval(timer);
      timer = null;
    }

    if (state === "failed") {
      const msg = bakeoffData.message || bakeoffData.error || "The provider bake-off could not complete.";
      bakeoffCard.appendChild(
        h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, h("strong", null, "Bake-off failed: "), untrusted(msg)))
      );
    }

    // Plan and Start button
    const estCost = plan.total_usd ? "USD " + plan.total_usd : "about USD 0.40";
    const planInfo = h(
      "div",
      { class: "facts-box muted small pad" },
      h(
        "p",
        null,
        "Next run plan: ",
        h("strong", null, String(plan.queries_sent || 80)),
        " queries across ",
        (plan.providers || []).join(", ") || "providers",
        " · estimated cost: ",
        h("strong", null, estCost)
      )
    );

    const startBtn = h(
      "button",
      { type: "button", class: "btn btn-primary", "data-fk": "start-bakeoff", onclick: openConfirmBakeoff },
      state === "done" ? "Run provider bake-off again…" : "Run provider bake-off…"
    );
    bakeoffCard.appendChild(planInfo);
    bakeoffCard.appendChild(h("div", { class: "actions" }, startBtn));

    // Previous results if available
    if (last) {
      const chosen = last.recommended || last.chosen || null;
      const reason = last.why || last.reason || null;
      const summaries = Array.isArray(last.summaries) ? last.summaries : Array.isArray(last.summary) ? last.summary : [];

      bakeoffCard.appendChild(h("h4", null, state === "done" ? "Bake-off result" : "Latest bake-off result"));
      if (chosen) {
        bakeoffCard.appendChild(
          h(
            "div",
            { class: "banner banner-ok" },
            h("p", null, "Recommended provider: ", h("strong", null, untrusted(chosen)), reason ? " — " + untrusted(reason) : "")
          )
        );
      }
      if (summaries.length) {
        bakeoffCard.appendChild(bakeoffSummaryTable(summaries, chosen));
      }
    }
  }

  function bakeoffSummaryTable(summary, chosen) {
    const head = h(
      "thead",
      null,
      h(
        "tr",
        null,
        ["Provider", "Local share", "On-topic", "Average speed", "Total cost", ""].map((t) => h("th", { scope: "col" }, t))
      )
    );
    const body = h(
      "tbody",
      null,
      summary.map((row) => {
        const isChosen = row.provider === chosen;
        const setBtn = h(
          "button",
          {
            type: "button",
            class: "btn btn-small" + (isChosen ? " btn-primary" : ""),
            onclick: () => switchProvider(row.provider),
          },
          "Use " + row.provider
        );
        const ontopic =
          typeof row.on_topic_share === "number"
            ? (row.on_topic_share * 100).toFixed(1) + "%"
            : typeof row.ontopic_share === "number"
            ? (row.ontopic_share * 100).toFixed(1) + "%"
            : "–";
        const speed =
          typeof row.latency_ms_mean === "number"
            ? (row.latency_ms_mean / 1000).toFixed(2) + " s"
            : typeof row.avg_latency_s === "number"
            ? row.avg_latency_s.toFixed(2) + " s"
            : "–";
        const cost = row.total_cost_usd ? "USD " + row.total_cost_usd : row.cost_usd ? "USD " + row.cost_usd : "–";

        return h(
          "tr",
          null,
          h("td", { "data-label": "Provider" }, h("strong", null, untrusted(row.provider)), isChosen ? h("span", { class: "chip chip-ok" }, "Recommended") : null),
          h("td", { "data-label": "Local share" }, typeof row.local_share === "number" ? (row.local_share * 100).toFixed(1) + "%" : "–"),
          h("td", { "data-label": "On-topic" }, ontopic),
          h("td", { "data-label": "Speed" }, speed),
          h("td", { "data-label": "Cost" }, cost),
          h("td", { "data-label": "Action" }, setBtn)
        );
      })
    );
    return h("div", { class: "table-wrap" }, h("table", { class: "table responsive" }, head, body));
  }

  function openConfirmBakeoff() {
    const m = modal("Confirm search bake-off", { wide: false });
    const wordInput = h("input", { type: "text", class: "text-input", id: "bakeoff-confirm-word", placeholder: "RUN", "aria-label": "Type RUN to confirm" });
    const confirmBtn = h("button", { type: "button", class: "btn btn-danger", disabled: true, "data-fk": "bakeoff-confirm" }, "Start bake-off");
    const cancelBtn = h("button", { type: "button", class: "btn", onclick: () => m.close() }, "Cancel");
    const errBox = h("div", { role: "alert" });

    wordInput.addEventListener("input", () => {
      confirmBtn.disabled = wordInput.value.trim() !== "RUN";
    });

    confirmBtn.addEventListener("click", async () => {
      confirmBtn.disabled = true;
      clear(errBox);
      try {
        await ctx.api.post("/api/tools/bakeoff", { confirm: "RUN" });
        m.close();
        ctx.announce("Bake-off started");
        load();
      } catch (e) {
        confirmBtn.disabled = false;
        errBox.appendChild(problemBanner(e instanceof ApiError ? e.problem : { text: "The bake-off could not start." }));
      }
    });

    put(
      m.body,
      h("p", null, "The bake-off sends about 40 searches to each provider on the live web, costing about ", h("strong", null, "USD 0.40"), "."),
      h("p", null, "To start, type the word ", h("strong", null, "RUN"), " below:"),
      wordInput,
      errBox,
      h("div", { class: "row actions" }, confirmBtn, cancelBtn)
    );
    m.focusFirst();
  }

  // ---- Settings ---------------------------------------------------------------------------------------------------
  function drawSettings() {
    clear(settingsCard);
    settingsCard.appendChild(h("h3", { id: "settings-h" }, "Primary search provider"));
    settingsCard.appendChild(
      h(
        "p",
        { class: "muted" },
        "Choose which provider Research uses first. If that provider is down or out of credits, failover automatically asks the secondary provider."
      )
    );

    if (!settingsData) {
      settingsCard.appendChild(h("p", { class: "muted" }, "Settings cannot be loaded."));
      return;
    }

    const current = settingsData.primary_provider || "auto";
    const form = h("form", { class: "form-group" });
    const noteBox = h("div", { role: "status" });

    for (const [key, label] of Object.entries(PROVIDER_NAMES)) {
      const radio = h("input", { type: "radio", name: "provider", id: "p-" + key, value: key, checked: key === current });
      radio.addEventListener("change", () => switchProvider(key, noteBox));
      const row = h(
        "div",
        { class: "radio-row pad-small" },
        radio,
        " ",
        h("label", { for: "p-" + key }, h("strong", null, label)),
        h("p", { class: "muted small" }, PROVIDER_NOTES[key] || "")
      );
      form.appendChild(row);
    }

    settingsCard.appendChild(noteBox);
    settingsCard.appendChild(form);
    if (settingsData.applies) {
      settingsCard.appendChild(h("p", { class: "muted small" }, "Setting applies: " + untrusted(settingsData.applies)));
    }
  }

  async function switchProvider(name, noteBox) {
    if (noteBox) clear(noteBox);
    try {
      await ctx.api.post("/api/settings", { primary_provider: name });
      ctx.announce("Primary provider changed to " + name);
      load();
    } catch (e) {
      const err = e instanceof ApiError ? e.problem : { text: "Could not save provider setting." };
      if (noteBox) {
        setKids(noteBox, problemBanner(err));
      } else {
        setKids(settingsCard, problemBanner(err), h("button", { type: "button", class: "btn", onclick: load }, "Reload settings"));
      }
    }
  }

  load();

  return {
    unmount() {
      gone = true;
      if (timer) clearInterval(timer);
    },
  };
}
