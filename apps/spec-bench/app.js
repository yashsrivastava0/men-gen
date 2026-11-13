/* The shell: the always-visible header (name, mode, money state, TEST FAKE banner), the journey rail on the left,
   hash routing, the status announcer, and the small amount of shared state the screens use. */
import { createApi } from "./lib/api.js";
import { createStore } from "./lib/store.js";
import { createScheduler, h, clear, setKids, navLink } from "./lib/dom.js";
import { moneyLevel, moneyText, moneyPercents, moneyBanner } from "./lib/money.js";
import { parseRoute, RUN_VIEWS, railItems } from "./lib/routes.js";

const api = createApi();
const store = createStore(() => window.localStorage);
const $ = (id) => document.getElementById(id);
const listeners = {};

const ctx = {
  api: api,
  store: store,
  schedule: createScheduler(),
  state: { budget: null, health: null, online: true, fake: false, runId: store.get("run", null), runStatus: null },
  on(topic, fn) {
    (listeners[topic] = listeners[topic] || new Set()).add(fn);
    return () => listeners[topic].delete(fn);
  },
  emit(topic) {
    for (const fn of Array.from(listeners[topic] || [])) {
      try {
        fn();
      } catch (e) {
        console.error(e);
      }
    }
  },
  announce: announce,
  navigate(route) {
    if (window.location.hash === route) route_();
    else window.location.hash = route;
  },
  setRun(id) {
    ctx.state.runId = id;
    store.set("run", id);
  },
  refreshBudget: refreshBudget,
};

// ---- announcer (the ARIA live region) -------------------------------------------------------------------------------
let lastSaid = "";
function announce(text) {
  if (!text || text === lastSaid) return;
  lastSaid = text;
  const el = $("announcer");
  el.textContent = "";
  setTimeout(() => {
    el.textContent = text;
  }, 60);
}

// ---- the header ----------------------------------------------------------------------------------------------------
function drawMoney() {
  const b = ctx.state.budget;
  const level = ctx.state.online ? moneyLevel(b) : "unknown";
  $("money").className = "money money-" + level;
  $("money-text").textContent = ctx.state.online ? moneyText(b) : "Money: not connected";
  const p = moneyPercents(b);
  $("money-fill").style.width = p.used.toFixed(1) + "%";
  $("money-hold").style.left = p.used.toFixed(1) + "%";
  $("money-hold").style.width = p.reserved.toFixed(1) + "%";
  $("money-trip").style.left = p.tripwire.toFixed(1) + "%";
  const meter = $("money-meter");
  meter.setAttribute("aria-valuenow", String(Math.round(p.used)));
  meter.setAttribute("aria-valuetext", ctx.state.online ? moneyText(b) : "not connected");
}
function drawMode() {
  const hl = ctx.state.health;
  const pill = $("mode-pill");
  if (!ctx.state.online) {
    pill.className = "mode mode-bad";
    pill.textContent = "No connection";
  } else if (!hl) {
    pill.className = "mode mode-wait";
    pill.textContent = "Checking…";
  } else if (hl.mode === "live") {
    pill.className = "mode mode-live";
    pill.textContent = hl.ready ? "LIVE: real models; this costs money" : "LIVE mode, not ready";
  } else {
    pill.className = "mode mode-offline";
    pill.textContent = "Offline only: no model is called, nothing is spent";
  }
}
function drawNotices() {
  const box = $("notices");
  clear(box);
  if (!ctx.state.online) {
    box.appendChild(h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, "We cannot reach the Spec Bench app. Check that it is still running in its terminal window. This page keeps trying and will recover by itself."), h("button", { type: "button", class: "btn", onclick: () => refreshAll() }, "Try now")));
    return;
  }
  const mb = moneyBanner(ctx.state.budget);
  if (mb) box.appendChild(h("div", { class: "banner banner-" + (mb.level === "exhausted" ? "bad" : "warn"), role: "status" }, h("p", null, mb.text)));
  const hl = ctx.state.health;
  if (hl && hl.ready === false) {
    const reasons = Array.isArray(hl.reasons) ? hl.reasons.map(String) : [];
    box.appendChild(h("div", { class: "banner banner-warn", role: "status" }, h("p", null, "The app is not ready for live work yet."), reasons.length ? h("ul", null, reasons.map((r) => h("li", null, r))) : null));
  }
}
function drawRail(route) {
  const run = route.id && RUN_VIEWS.indexOf(route.name) !== -1 ? route.id : ctx.state.runId;
  const status = run === ctx.state.runId ? ctx.state.runStatus : null;
  const rail = $("rail");
  const items = railItems(run, route.name, status);
  setKids(
    rail,
    h("p", { class: "rail-title" }, "Your journey"),
    h(
      "ol",
      { class: "rail-list" },
      items.steps.map((it) => {
        const kids = [h("span", { class: "rail-num" }, String(it.n)), " ", it.label, it.hint ? h("span", { class: "rail-hint" }, " " + it.hint) : null];
        if (!it.route) return h("li", { class: "rail-off" }, h("span", { class: "rail-item", "aria-disabled": "true" }, kids, h("span", { class: "sr-only" }, " (choose or start a run first)")));
        const a = navLink(it.route, undefined, "rail-item" + (it.hint ? " rail-attention" : ""));
        if (it.current) a.setAttribute("aria-current", "page");
        for (const k of kids.filter(Boolean)) a.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
        return h("li", null, a);
      })
    ),
    h("ul", { class: "rail-list rail-more" }, items.more.map((it) => h("li", null, navLink(it.route, it.label, "rail-item" + (it.current ? " current" : "")))))
  );
  for (const a of rail.querySelectorAll("a")) {
    if (a.className.indexOf("current") !== -1 && !a.getAttribute("aria-current")) a.setAttribute("aria-current", "page");
  }
}

let budgetBusy = null;
let budgetAt = 0;
async function refreshBudget() {
  if (budgetBusy) return budgetBusy;
  if (Date.now() - budgetAt < 1500) return null;
  budgetBusy = (async () => {
    try {
      ctx.state.budget = await api.get("/api/budget");
      ctx.state.online = true;
    } catch (e) {
      if (e && e.status === 0) ctx.state.online = false;
    }
    budgetAt = Date.now();
    budgetBusy = null;
    drawMoney();
    drawMode();
    drawNotices();
    ctx.emit("budget");
  })();
  return budgetBusy;
}
async function refreshHealth() {
  try {
    ctx.state.health = await api.get("/api/health");
    ctx.state.online = true;
    if (ctx.state.health && ctx.state.health.fake) {
      ctx.state.fake = true;
      $("fake-banner").hidden = false;
    }
  } catch (e) {
    if (e && e.status === 0) ctx.state.online = false;
  }
  drawMode();
  drawNotices();
}
async function refreshRunStatus() {
  const id = ctx.state.runId;
  if (!id) return;
  try {
    const snap = await api.get(api.runPath(id));
    if (id === ctx.state.runId) ctx.state.runStatus = snap && snap.status ? snap.status : null;
  } catch (e) {
    ctx.state.runStatus = null;
  }
  drawRail(parseRoute(window.location.hash));
}
function refreshAll() {
  budgetAt = 0;
  return Promise.all([refreshBudget(), refreshHealth(), refreshRunStatus()]);
}

// ---- theme ----------------------------------------------------------------------------------------------------------------
const THEMES = ["auto", "light", "dark"];
function applyTheme(name) {
  const root = document.documentElement;
  if (name === "light" || name === "dark") root.setAttribute("data-theme", name);
  else root.removeAttribute("data-theme");
  $("theme-toggle").textContent = "Theme: " + name;
}

// ---- routes ------------------------------------------------------------------------------------------------------------------
const VIEWS = {
  inputs: { title: "1. Inputs", load: () => import("./views/inputs.js") },
  preview: { title: "2. Preview", load: () => import("./views/preview.js") },
  g15: { title: "3. Structure check", load: () => import("./views/g15.js") },
  run: { title: "4. Run", load: () => import("./views/run.js") },
  g2: { title: "5. Spec review", load: () => import("./views/g2.js") },
  handoff: { title: "6. Hand-off", load: () => import("./views/handoff.js") },
  runs: { title: "Run history", load: () => import("./views/runs.js") },
  budget: { title: "Money", load: () => import("./views/budget.js") },
};
let current = null;
let routeToken = 0;
let firstRoute = true;
async function route_() {
  const token = ++routeToken;
  const r = parseRoute(window.location.hash);
  if (RUN_VIEWS.indexOf(r.name) !== -1) {
    if (!r.id) r.id = ctx.state.runId;
    if (r.id && r.id !== ctx.state.runId) {
      ctx.setRun(r.id);
      ctx.state.runStatus = null;
    }
  }
  const spec = VIEWS[r.name];
  if (current && current.unmount) {
    try {
      current.unmount();
    } catch (e) {
      console.error(e);
    }
  }
  current = null;
  const main = $("view");
  clear(main);
  drawRail(r);
  document.title = spec.title + " · Spec Bench";
  if (RUN_VIEWS.indexOf(r.name) !== -1 && !r.id) {
    main.appendChild(h("div", { class: "banner banner-info", role: "status" }, h("p", null, "There is no run yet. Start one from the first step."), navLink("#/inputs", "Go to Inputs", "btn btn-primary")));
    return;
  }
  main.appendChild(h("p", { class: "muted pad" }, "Loading…"));
  try {
    const mod = await spec.load();
    if (token !== routeToken) return;
    clear(main);
    current = (await mod.mount(main, ctx, r)) || null;
  } catch (e) {
    if (token !== routeToken) return;
    console.error(e);
    clear(main);
    main.appendChild(h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, "This page could not be drawn. Reload it; if it happens again, the app may need restarting.")));
  }
  if (token !== routeToken) return;
  if (!firstRoute) {
    announce(spec.title);
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  firstRoute = false;
  refreshRunStatus();
}

// ---- start ------------------------------------------------------------------------------------------------------------------------
function start() {
  applyTheme(store.get("theme", "auto"));
  $("theme-toggle").addEventListener("click", () => {
    const next = THEMES[(THEMES.indexOf(store.get("theme", "auto")) + 1) % THEMES.length];
    store.set("theme", next);
    applyTheme(next);
  });
  $("skip").addEventListener("click", () => $("view").focus());
  window.addEventListener("hashchange", route_);
  window.addEventListener("focus", () => refreshAll());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshAll();
  });
  setInterval(() => {
    if (!document.hidden) {
      refreshBudget();
      refreshRunStatus();
    }
  }, 10000);
  setInterval(() => {
    if (!document.hidden) refreshHealth();
  }, 60000);
  route_();
  refreshAll();
}
start();
