/* Dialogs on the real <dialog> element: showModal() traps the keyboard focus and Escape closes. Focus goes to the
   element marked data-autofocus (or the first button) and returns to where it came from when the dialog closes. */
import { h } from "../lib/dom.js";

let counter = 0;

/** modal(title) -> {dlg, body, foot, close(value), closed: Promise<returnValue>}. Fill body and foot, then it is open. */
export function modal(title, opts) {
  const o = opts || {};
  const opener = document.activeElement;
  const openerKey = opener && opener.getAttribute ? opener.getAttribute("data-fk") : null;
  const id = "dlg-title-" + ++counter;
  const body = h("div", { class: "dlg-body" });
  const foot = h("div", { class: "dlg-foot" });
  const dlg = h("dialog", { class: "dlg" + (o.wide ? " dlg-wide" : ""), "aria-labelledby": id }, h("div", { class: "dlg-in" }, h("h2", { id: id }, title), body, foot));
  document.body.appendChild(dlg);
  const closed = new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      const value = dlg.returnValue;
      dlg.remove();
      let back = opener && opener.isConnected ? opener : null;
      if (!back && openerKey) back = document.querySelector('[data-fk="' + CSS.escape(openerKey) + '"]');
      if (back && back.focus) back.focus();
      resolve(value);
    });
  });
  dlg.showModal();
  return {
    dlg: dlg,
    body: body,
    foot: foot,
    close: (value) => dlg.close(value === undefined ? "" : value),
    closed: closed,
    /** Call once the content is in place. */
    focusFirst() {
      const el = dlg.querySelector("[data-autofocus]") || dlg.querySelector("button, input, select, textarea, a[href]");
      if (el) el.focus();
    },
  };
}

/** A yes/no question. Resolves true only for the confirm button. `lines` are plain sentences; each may be {text, tone}. */
export function confirmDialog(o) {
  const m = modal(o.title, { wide: !!o.wide });
  for (const line of o.lines || []) {
    const text = typeof line === "string" ? line : line.text;
    const tone = typeof line === "string" ? "" : line.tone || "";
    m.body.appendChild(h("p", { class: "dlg-line" + (tone ? " tone-" + tone : "") }, text));
  }
  if (o.extra) m.body.appendChild(o.extra);
  m.foot.append(
    h("button", { type: "button", class: "btn", "data-autofocus": "1", onclick: () => m.close("no") }, o.cancelText || "Cancel"),
    h("button", { type: "button", class: "btn " + (o.danger ? "btn-danger" : "btn-primary"), onclick: () => m.close("yes") }, o.confirmText || "OK")
  );
  m.focusFirst();
  return m.closed.then((v) => v === "yes");
}
