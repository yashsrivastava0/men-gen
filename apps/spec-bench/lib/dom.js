/* DOM helpers. The rules of the house, enforced here so the screens cannot break them:
   - every string is inserted as TEXT (createTextNode / textContent); there is no way to pass HTML through h();
   - there is no style attribute and no inline event attribute (handlers are functions, added with addEventListener);
   - a link exists only through link() (outside address on the scheme allow-list), fileLink() (this app's own API path)
     or navLink() (an in-app route);
   - text that came from a Brief, a web page, a model or a file name goes through untrusted(), which sets dir="auto";
   - drawings (the navigation graph, the timelines) are made with svg(), which is createElementNS plus the same rules. */
import { safeUrl, safeRoute, safeApiPath } from "./safe.js";

const SVG_NS = "http:" + "//www.w3.org/2000/svg";

const PLAIN_ATTRS = new Set([
  "class", "id", "type", "title", "role", "dir", "for", "name", "value", "placeholder", "accept", "tabindex", "min", "max",
  "step", "rows", "autocomplete", "lang", "colspan", "scope", "open", "disabled", "hidden", "checked", "selected", "multiple",
  "required", "readonly", "autofocus", "maxlength", "list", "label",
]);
const BOOL_ATTRS = new Set(["open", "disabled", "hidden", "checked", "selected", "multiple", "required", "readonly", "autofocus"]);

function append(el, kid) {
  if (kid === null || kid === undefined || kid === false || kid === true) return;
  if (Array.isArray(kid)) {
    for (const k of kid) append(el, k);
  } else if (typeof kid === "string" || typeof kid === "number") {
    el.appendChild(document.createTextNode(String(kid)));
  } else {
    el.appendChild(kid);
  }
}

/** h("div", {class: "card", onclick: fn, "data-id": 3}, "text", childNode, [more]) */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const v = attrs[key];
      if (v === undefined || v === null || v === false) continue;
      if (key.slice(0, 2) === "on") {
        if (typeof v !== "function") throw new Error("handlers must be functions: " + key);
        el.addEventListener(key.slice(2), v);
      } else if (key === "class") {
        el.className = String(v);
      } else if (key.slice(0, 5) === "aria-" || key.slice(0, 5) === "data-") {
        el.setAttribute(key, String(v));
      } else if (PLAIN_ATTRS.has(key)) {
        if (BOOL_ATTRS.has(key)) el.setAttribute(key, "");
        else el.setAttribute(key, String(v));
      } else {
        throw new Error("attribute not allowed: " + key);
      }
    }
  }
  append(el, kids);
  return el;
}

/** An SVG element: svg("rect", {x: 1, y: 2, class: "node"}, svg("title", null, "text")). Numbers and strings only;
    no handlers through here except functions on "on*" keys, never a style or an address attribute. */
export function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const v = attrs[key];
      if (v === undefined || v === null || v === false) continue;
      if (key.slice(0, 2) === "on") {
        if (typeof v !== "function") throw new Error("handlers must be functions: " + key);
        el.addEventListener(key.slice(2), v);
      } else if (/^[A-Za-z][A-Za-z-]*$/.test(key) && key !== "style" && key !== "href") {
        el.setAttribute(key, String(v));
      } else {
        throw new Error("attribute not allowed: " + key);
      }
    }
  }
  append(el, kids);
  return el;
}

/** Append children (strings become text nodes; arrays are flattened; null and false are skipped). */
export function put(node, ...kids) {
  append(node, kids);
  return node;
}
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}
export function setKids(node, ...kids) {
  clear(node);
  append(node, kids);
  return node;
}
/** Text from the outside (a Brief, a web page, a model, a file name): a span with dir="auto". */
export function untrusted(text, cls, tag) {
  const el = document.createElement(tag || "span");
  el.setAttribute("dir", "auto");
  if (cls) el.className = cls;
  el.textContent = String(text === undefined || text === null ? "" : text);
  return el;
}
/** A web link on the scheme allow-list. Otherwise the address is shown as plain text and nothing is clickable. */
export function link(url, label, cls) {
  const href = safeUrl(url);
  if (!href) return untrusted(label || url || "", "nolink " + (cls || ""));
  const a = document.createElement("a");
  a.setAttribute("href", href);
  a.setAttribute("rel", "noopener noreferrer");
  a.setAttribute("target", "_blank");
  a.setAttribute("dir", "auto");
  if (cls) a.className = cls;
  a.textContent = label || href;
  return a;
}
/** A link to one of this app's own files, such as the offline preview. `download` makes the browser save it. */
export function fileLink(path, label, cls, filename) {
  const p = safeApiPath(path);
  if (!p) return untrusted(label || "", "nolink " + (cls || ""));
  const a = document.createElement("a");
  a.setAttribute("href", p);
  if (filename) a.setAttribute("download", filename);
  if (cls) a.className = cls;
  a.textContent = label || p;
  return a;
}
/** An in-app link such as "#/run/run_01ABC". */
export function navLink(route, label, cls) {
  const r = safeRoute(route);
  const a = document.createElement(r ? "a" : "span");
  if (r) a.setAttribute("href", r);
  if (cls) a.className = cls;
  if (label !== undefined) a.textContent = label;
  return a;
}

/** One DOM write pass per animation frame (a timer when the tab is hidden, because frames stop there). */
export function createScheduler() {
  const pending = new Set();
  let handle = 0;
  function run() {
    handle = 0;
    const batch = Array.from(pending);
    pending.clear();
    for (const fn of batch) {
      try {
        fn();
      } catch (e) {
        console.error(e);
      }
    }
  }
  return function schedule(fn) {
    pending.add(fn);
    if (handle) return;
    handle = document.hidden ? setTimeout(run, 300) : requestAnimationFrame(run);
  };
}

/** Run render(); afterwards put the keyboard focus back on the element that had it (found by data-fk). */
export function keepFocus(root, render) {
  const active = document.activeElement;
  const key = active && root.contains(active) ? active.getAttribute("data-fk") : null;
  render();
  if (key) {
    for (const el of root.querySelectorAll("[data-fk]")) {
      if (el.getAttribute("data-fk") === key) {
        el.focus({ preventScroll: true });
        break;
      }
    }
  }
}
