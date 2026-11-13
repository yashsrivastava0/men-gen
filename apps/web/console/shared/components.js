/* Mobigen console: small shared helpers for every agent screen. Vanilla JS, no framework, no network.
   Loaded by the shell before any agent script; exposes window.MobigenUI. User content is only ever
   written with textContent / text nodes (h and appendChild); this file never assigns markup strings. */
(function (root) {
  "use strict";

  /** localStorage that never throws (storage may be blocked or full). */
  var store = {
    get: function (k) { try { return root.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { root.localStorage.setItem(k, v); } catch (e) { /* storage may be blocked */ } }
  };

  function $(id) { return document.getElementById(id); }

  /** Create an element. attrs: class, text, on (map of listeners), data (dataset), css (style properties),
      other keys as attributes. Children (strings, numbers, nodes, arrays of them) follow the attrs. */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    var a = attrs || {};
    Object.keys(a).forEach(function (k) {
      var v = a[k];
      if (v === null || v === undefined || v === false) { return; }
      if (k === "class") { el.className = v; }
      else if (k === "text") { el.textContent = String(v); }
      else if (k === "on") { Object.keys(v).forEach(function (ev) { el.addEventListener(ev, v[ev]); }); }
      else if (k === "data") { Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; }); }
      else if (k === "css") { Object.keys(v).forEach(function (p) { el.style[p] = v[p]; }); }
      else if (v === true) { el.setAttribute(k, ""); }
      else { el.setAttribute(k, String(v)); }
    });
    for (var i = 2; i < arguments.length; i++) { appendChild(el, arguments[i]); }
    return el;
  }
  function appendChild(el, c) {
    if (c === null || c === undefined || c === false) { return; }
    if (Array.isArray(c)) { c.forEach(function (x) { appendChild(el, x); }); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  function clear(el) { el.replaceChildren(); }

  /** A short random client id, "ui-" plus 16 hex digits. */
  function randomId() {
    var a = new Uint8Array(8);
    root.crypto.getRandomValues(a);
    var s = "";
    for (var i = 0; i < a.length; i++) { s += ("0" + a[i].toString(16)).slice(-2); }
    return "ui-" + s;
  }

  /** Show a message in the #toasts region for ten seconds, with an optional action button. */
  function toast(text, actionLabel, action) {
    var el = h("div", { class: "toast" }, h("span", { text: text }));
    if (actionLabel) {
      el.appendChild(h("button", { type: "button", text: actionLabel, on: { click: function () { el.remove(); action(); } } }));
    }
    $("toasts").appendChild(el);
    root.setTimeout(function () { el.remove(); }, 10000);
  }

  root.MobigenUI = { store: store, $: $, h: h, appendChild: appendChild, clear: clear, randomId: randomId, toast: toast };
})(window);
