/* Mobigen console shell. Fills the agent switcher from the registry (registry.js), then mounts the chosen
   agent: its stylesheet and HTML fragment first, its optional helper script and its script last (so the
   script finds its elements).
   Agent scripts keep global listeners and cannot be unloaded, so switching agents reloads the page with
   ?agent=<id>. The fragment is the agent's own static file from this origin (code, never user content);
   it is parsed with DOMParser into an inert document, and script/style/link/frame nodes are dropped.
   Exposes window.MobigenAgent (the active registry entry) to the agent script. */
(function (root) {
  "use strict";

  var UI = root.MobigenUI;
  var REGISTRY = root.MobigenRegistry || [];
  var CHOICE_KEY = "console.agent";
  var DROPPED = /^(script|style|link|iframe|object|embed)$/i;

  function byId(id) {
    for (var i = 0; i < REGISTRY.length; i++) { if (REGISTRY[i].id === id) { return REGISTRY[i]; } }
    return null;
  }
  function choose() {
    var wanted = null;
    try { wanted = new root.URLSearchParams(root.location.search).get("agent"); } catch (e) { wanted = null; }
    return byId(wanted) || byId(UI.store.get(CHOICE_KEY)) || REGISTRY[0] || null;
  }

  function showError(text) {
    UI.$("agent-mount").appendChild(UI.h("p", { class: "shell-error", role: "alert", text: text }));
  }

  function fillSwitcher(active) {
    var select = UI.$("agent-select");
    UI.clear(select);
    REGISTRY.forEach(function (entry) {
      select.appendChild(UI.h("option", { value: entry.id, text: entry.title }));
    });
    select.value = active.id;
    select.addEventListener("change", function () {
      UI.store.set(CHOICE_KEY, select.value);
      var url = new root.URL(root.location.href);
      url.searchParams.set("agent", select.value);
      root.location.assign(url.toString());
    });
  }

  function loadStyle(href) {
    return new Promise(function (resolve, reject) {
      var link = UI.h("link", { rel: "stylesheet", href: href });
      link.addEventListener("load", resolve);
      link.addEventListener("error", function () { reject(new Error(href)); });
      document.head.appendChild(link);
    });
  }
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var script = UI.h("script", { src: src });
      script.addEventListener("load", resolve);
      script.addEventListener("error", function () { reject(new Error(src)); });
      document.head.appendChild(script);
    });
  }
  function loadMarkup(url) {
    return root.fetch(url, { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) { throw new Error(url); }
      return res.text();
    });
  }
  function mountMarkup(text) {
    var doc = new root.DOMParser().parseFromString(text, "text/html");
    var bar = UI.$("agent-topbar");
    var mount = UI.$("agent-mount");
    Array.prototype.slice.call(doc.body.children).forEach(function (node) {
      if (DROPPED.test(node.tagName)) { return; }
      var el = document.importNode(node, true);
      (el.getAttribute("data-slot") === "topbar" ? bar : mount).appendChild(el);
    });
  }

  function boot() {
    var active = choose();
    if (!active) { showError("No agent is registered."); return; }
    UI.store.set(CHOICE_KEY, active.id);
    fillSwitcher(active);
    document.title = active.title;
    root.MobigenAgent = active;
    Promise.all([loadStyle(active.style), loadMarkup(active.markup)]).then(function (done) {
      mountMarkup(done[1]);
      return active.helper ? loadScript(active.helper) : null;   // pure helpers first, then the screen
    }).then(function () {
      return loadScript(active.script);
    }).catch(function (err) {
      showError("Could not load " + active.title + " (" + (err && err.message ? err.message : "error") + ").");
    });
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); } else { boot(); }
})(window);
