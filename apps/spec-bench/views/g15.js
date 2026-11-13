/* Step 3, G1.5: the structure in plain words, and the owner's consent to the phase split. The consent buttons write
   the I-4 override record: approve the split, move things between phases, or put everything in one release. */
import { h, setKids, untrusted, navLink, put, keepFocus } from "../lib/dom.js";
import { arr, textOf, texts, listOf } from "../lib/model.js";
import { statusWord } from "../lib/format.js";
import { banner, statusChip, problemBanner, problemOf, loading, pageHead, card, bullets, genericTable, facts } from "./shared.js";

export function mount(root, ctx, route) {
  const id = route.id;
  let gone = false;
  const body = h("div", null, loading());
  put(root, pageHead("3. Structure check (G1.5)", "This is the shape of the app before the detail is written: its parts, who can do what, what it keeps track of, and what comes first. Your OK here records that you accept the split into releases."), body);
  let snap = null;
  let view = null;
  const moves = new Map(); // item id -> phase name
  let result = null;
  let busy = false;

  async function load() {
    try {
      const [s, v] = await Promise.all([ctx.api.get(ctx.api.runPath(id)), ctx.api.get(ctx.api.runPath(id) + "/views/g15")]);
      snap = s || {};
      view = v || {};
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
      return;
    }
    draw();
  }

  async function decide(decision, everything) {
    if (busy) return;
    busy = true;
    result = null;
    draw();
    const payload = { decision: decision };
    if (decision === "change") payload.moves = Array.from(moves.entries()).map(([item, phase]) => ({ item: item, to: phase }));
    if (everything) payload.everything_in_one_release = true;
    try {
      await ctx.api.post(ctx.api.runPath(id) + "/g15", payload);
      result = { ok: true, text: decision === "change" ? "Your changes were recorded. The split is being redone." : everything ? "Recorded: everything goes in one release." : "Recorded: you accepted the split." };
      moves.clear();
      ctx.refreshBudget();
      if (decision === "approve") {
        ctx.navigate("#/run/" + id);
        return;
      }
      await load();
    } catch (e) {
      result = { ok: false, problem: problemOf(e) };
    }
    busy = false;
    draw();
  }

  function phasesOf() {
    const raw = listOf(view.phases || view.phase_split, ["phases"]);
    return raw.map((p) => (p && typeof p === "object" ? { name: textOf(p.name || p.label || p.id), items: texts(p.items || p.screens), count: typeof p.count === "number" ? p.count : typeof p.screens === "number" ? p.screens : null, why: textOf(p.why || p.reason || p.note), pulled: texts(p.pulled_in || p.pull_ins) } : { name: textOf(p), items: [], count: null, why: "", pulled: [] }));
  }

  function draw() {
    if (gone) return;
    const kids = [];
    const open = snap.status === "paused_g15" || (snap.gates && snap.gates.g15 === "open");
    kids.push(h("p", null, statusChip(snap.status)));
    if (!open) {
      kids.push(banner(snap.gates && snap.gates.g15 === "closed" ? "You have already decided on the structure for this run." : "This run is at: " + statusWord(snap.status) + ". The structure check opens when the structure is ready. You can look at what is here, but the buttons stay off until then.", "info"));
    }
    const apps = arr(view.app_map || view.apps);
    kids.push(card("App map", apps.length ? h("div", { class: "appmap" }, apps.map((a) => h("div", { class: "appbox" }, h("h4", null, untrusted(textOf(a && typeof a === "object" ? a.name || a.title || a.id : a))), a && typeof a === "object" ? [a.screens !== undefined ? h("p", { class: "muted" }, untrusted(String(a.screens) + " screens")) : null, bullets(texts(a.modules), "No parts listed.")] : null))) : h("p", { class: "muted" }, "No app map yet.")));
    const roles = arr(view.roles || view.roles_and_access);
    kids.push(card("Who can do what", roles.length ? genericTable(roles.map((r) => ({ role: textOf(r && r.name !== undefined ? r.name : r && r.id !== undefined ? r.id : r), can: texts(r && (r.access || r.can || r.screens)).join("; "), home: textOf(r && (r.home || r.home_screen)) })), { cols: ["role", "can", "home"], labels: { can: "Can see or do", home: "Starts at" } }) : h("p", { class: "muted" }, "No roles yet.")));
    const ents = arr(view.entities);
    kids.push(card("What the app keeps track of", ents.length ? h("div", { class: "appmap" }, ents.map((e) => h("div", { class: "appbox" }, h("h4", null, untrusted(textOf(e && typeof e === "object" ? e.name || e.title : e))), h("p", null, untrusted(textOf(e && typeof e === "object" ? e.plain || e.description || e.text : ""))), e && e.fields ? h("p", { class: "muted small" }, untrusted(texts(e.fields).join(", "))) : null))) : h("p", { class: "muted" }, "Nothing listed yet.")));
    const phases = phasesOf();
    const names = phases.map((p) => p.name);
    const inventory = arr(view.inventory);
    kids.push(
      card(
        "Everything that will be built (" + inventory.length + ")",
        inventory.length
          ? h(
              "div",
              { class: "table-wrap" },
              h(
                "table",
                { class: "table" },
                h("thead", null, h("tr", null, ["Item", "Part", "Release", open ? "Move to" : ""].filter((x, i) => i < 3 || open).map((t) => h("th", { scope: "col" }, t)))),
                h(
                  "tbody",
                  null,
                  inventory.slice(0, 400).map((it) => {
                    const iid = String(it && it.id !== undefined ? it.id : textOf(it));
                    const cur = textOf(it && (it.phase || it.release));
                    const target = moves.get(iid);
                    return h(
                      "tr",
                      null,
                      h("td", { "data-label": "Item" }, untrusted(textOf(it && typeof it === "object" ? it.title || it.name || it.id : it))),
                      h("td", { "data-label": "Part" }, untrusted(textOf(it && it.module))),
                      h("td", { "data-label": "Release" }, untrusted(target || cur), target && target !== cur ? h("span", { class: "tone-warn" }, " (moved)") : null),
                      open
                        ? h("td", { "data-label": "Move to" }, h("select", { class: "text-input", "data-fk": "move-" + iid, onchange: (e) => { const v = e.target.value; if (!v || v === cur) moves.delete(iid); else moves.set(iid, v); draw(); } }, h("option", { value: "" }, "Leave"), names.filter((n) => n !== cur).map((n) => h("option", { value: n, selected: n === target }, n))))
                        : null
                    );
                  })
                )
              ),
              inventory.length > 400 ? h("p", { class: "muted" }, "Showing the first 400 of " + inventory.length + ".") : null
            )
          : h("p", { class: "muted" }, "No list yet.")
      )
    );
    kids.push(
      card(
        "Split into releases",
        phases.length
          ? h("div", { class: "appmap" }, phases.map((p) => h("div", { class: "appbox" }, h("h4", null, untrusted(p.name)), h("p", null, untrusted(String(p.count !== null ? p.count : p.items.length) + " items")), p.why ? h("p", { class: "muted" }, untrusted(p.why)) : null, p.pulled.length ? h("p", { class: "tone-warn" }, "Pulled in so the app still works: ", untrusted(p.pulled.join(", "))) : null)))
          : h("p", { class: "muted" }, "No split yet."),
        facts([["Rule", textOf(view.pull_in_rule || view.rule)]])
      )
    );
    if (result) kids.push(result.ok ? banner(result.text, "ok") : problemBanner(result.problem));
    const off = !open || busy;
    kids.push(
      card(
        "Your decision",
        h("p", null, "Approving records that you saw the split and accept it. Nothing is built from a part that you move to a later release."),
        h(
          "div",
          { class: "actions row" },
          h("button", { type: "button", class: "btn btn-primary", "data-fk": "g15-approve", disabled: off, onclick: () => decide("approve", false) }, "Approve this split"),
          h("button", { type: "button", class: "btn", "data-fk": "g15-one", disabled: off, onclick: () => decide("approve", true) }, "Everything in one release"),
          h("button", { type: "button", class: "btn", "data-fk": "g15-change", disabled: off || !moves.size, onclick: () => decide("change", false) }, "Apply my " + moves.size + " move" + (moves.size === 1 ? "" : "s"))
        ),
        off && !busy ? h("p", { class: "why-off" }, "Turned off until the structure check is open.") : null,
        navLink("#/run/" + id, "Back to the live run", "btn btn-small")
      )
    );
    keepFocus(body, () => setKids(body, kids));
  }
  load();
  return { unmount() { gone = true; } };
}
