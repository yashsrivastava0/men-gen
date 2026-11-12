/* The shell: the always-visible bar (name, LIVE banner, Preflight pill, Budget bar, navigation), hash routing, the
   status announcer, and the small amount of shared state the screens use (budget, preflight, online). */
import { createApi } from "./lib/api.js";
import { createStore } from "./lib/store.js";
import { createScheduler, h, clear } from "./lib/dom.js";
import { budgetLevel, budgetText, budgetPercents } from "./lib/budget.js";
import { creditsChipText, creditsLevel } from "./lib/credits.js";
import { openPreflight } from "./views/preflight.js";

const api = createApi();
const store = createStore(() => window.localStorage);
const $ = (id) => document.getElementById(id);
const listeners = {};

const ctx = {
  api: api,
  store: store,
  schedule: createScheduler(),
  state: { budget: null, credits: null, preflight: null, online: true, fake: false },
  /** Subscribe to "budget", "credits" or "preflight" changes; returns the function that unsubscribes. */
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
  refreshBudget: refreshBudget,
  refreshCredits: refreshCredits,
  refreshPreflight: refreshPreflight,
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

// ---- the bar ------------------------------------------------------------------------------------------------------------
function drawBudget() {
  const b = ctx.state.budget;
  const level = budgetLevel(b);
  const box = $("budget");
  box.className = "budget budget-" + level;
  $("budget-text").textContent = ctx.state.online ? budgetText(b) : "Budget: not connected";
  const p = budgetPercents(b);
  $("budget-fill").style.width = p.used.toFixed(1) + "%";
  $("budget-trip").style.left = p.tripwire.toFixed(1) + "%";
  const meter = $("budget-meter");
  meter.setAttribute("aria-valuenow", String(Math.round(p.used)));
  meter.setAttribute("aria-valuetext", budgetText(b));
}
/** The credits chip (advice only): hidden until there is something to say about a provider with a key. */
function drawCredits() {
  const text = ctx.state.online ? creditsChipText(ctx.state.credits) : "";
  $("credits-row").hidden = !text;
  if (!text) return;
  $("credits-chip").className = "credits-chip credits-" + creditsLevel(ctx.state.credits);
  $("credits-chip").textContent = text;
}
function drawPreflight() {
  const pre = ctx.state.preflight;
  const pill = $("preflight-pill");
  if (!ctx.state.online) {
    pill.className = "pill pill-bad";
    pill.textContent = "No connection";
  } else if (!pre) {
    pill.className = "pill pill-wait";
    pill.textContent = "Checking…";
  } else if (pre.ready) {
    pill.className = "pill pill-ok";
    pill.textContent = "Ready";
  } else {
    const n = (pre.checks || []).filter((c) => !c.ok && !/^key:/.test(c.id)).length;
    pill.className = "pill pill-bad";
    pill.textContent = "Not ready" + (n ? " (" + n + ")" : "");
  }
}
function drawNotices() {
  const box = $("notices");
  clear(box);
  if (!ctx.state.online) {
    box.appendChild(
      h(
        "div",
        { class: "banner banner-bad", role: "alert" },
        h("p", null, "We cannot reach the Research app. Check that it is still running in its terminal window. This page keeps trying and will recover by itself."),
        h("button", { type: "button", class: "btn", onclick: () => refreshAll() }, "Try now")
      )
    );
  }
  const b = ctx.state.budget;
  if (b && b.write_failed) {
    box.appendChild(
      h("div", { class: "banner banner-warn", role: "status" }, h("p", null, "The spend record could not be saved just now. Research goes on; the record is rebuilt from the ledger at the next write."))
    );
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
    drawBudget();
    drawCredits();
    drawPreflight();
    drawNotices();
    ctx.emit("budget");
  })();
  return budgetBusy;
}
let creditsBusy = null;
async function refreshCredits() {
  if (creditsBusy) return creditsBusy;
  creditsBusy = (async () => {
    try {
      ctx.state.credits = await api.get("/api/credits");
    } catch (e) {
      // credits are advice: a failed read keeps the last figures and raises no alarm of its own
    }
    creditsBusy = null;
    drawCredits();
    ctx.emit("credits");
  })();
  return creditsBusy;
}
async function refreshPreflight() {
  try {
    ctx.state.preflight = await api.get("/api/preflight");
    ctx.state.online = true;
  } catch (e) {
    if (e && e.status === 0) ctx.state.online = false;
  }
  drawPreflight();
  drawNotices();
  ctx.emit("preflight");
}
function refreshAll() {
  budgetAt = 0;
  return Promise.all([refreshBudget(), refreshCredits(), refreshPreflight()]);
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
  inputs: { title: "Inputs", nav: "inputs", load: () => import("./views/inputs.js") },
  runs: { title: "Runs", nav: "runs", load: () => import("./views/runs.js") },
  run: { title: "Run", nav: "runs", load: () => import("./views/run.js") },
  results: { title: "Results", nav: "runs", load: () => import("./views/results.js") },
  budget: { title: "Budget", nav: "budget", load: () => import("./views/budget.js") },
  guide: { title: "Guide", nav: "guide", load: () => import("./views/guide.js") },
  spotcheck: { title: "Spot-check", nav: "spotcheck", load: () => import("./views/spotcheck.js") },
  tools: { title: "Tools", nav: "tools", load: () => import("./views/tools.js") },
};
function parseRoute(hash) {
  const m = /^#\/([a-z]+)(?:\/([A-Za-z0-9_-]{1,80}))?\/?$/.exec(hash || "");
  if (!m || !VIEWS[m[1]]) return { name: "inputs", id: null };
  return { name: m[1], id: m[2] || null };
}
let current = null;
let routeToken = 0;
let firstRoute = true;
async function route_() {
  const token = ++routeToken;
  const r = parseRoute(window.location.hash);
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
  main.appendChild(h("p", { class: "muted pad" }, "Loading…"));
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.getAttribute("data-route") === spec.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  document.title = spec.title + " · Research";
  try {
    const mod = await spec.load();
    if (token !== routeToken) return;
    clear(main);
    current = (await mod.mount(main, ctx, { id: r.id, name: r.name })) || null;
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
  $("preflight-pill").addEventListener("click", () => openPreflight(ctx));
  window.addEventListener("hashchange", route_);
  window.addEventListener("focus", () => refreshAll());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshAll();
  });
  setInterval(() => {
    if (!document.hidden) {
      refreshBudget();
      refreshCredits();
    }
  }, 15000);
  setInterval(() => {
    if (!document.hidden) refreshPreflight();
  }, 60000);
  api.probe("/api/fake").then((r) => {
    if (r && r.fake) {
      ctx.state.fake = true;
      $("fake-banner").hidden = false;
    }
  });
  route_();
  refreshAll();
}
start();
