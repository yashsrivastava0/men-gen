/* Spot-check and Tools come in the next version (stage 2): the bar shows them as "soon" and this page explains. */
import { h, navLink } from "../lib/dom.js";

export function mount(root, ctx, params) {
  const name = params.name === "tools" ? "Tools" : "Spot-check";
  const text =
    params.name === "tools"
      ? "Tools (comparing search providers and choosing one) comes in the next version."
      : "The spot-check (sixty facts for you to check against their sources) comes in the next version.";
  root.append(h("div", { class: "page-head" }, h("h2", null, name)), h("section", { class: "card" }, h("p", null, text), h("p", null, navLink("#/inputs", "Back to the inputs", "btn"))));
  return {};
}
