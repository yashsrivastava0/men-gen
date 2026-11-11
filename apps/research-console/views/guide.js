/* Guide (#/guide): a collapsible step list for the owner, in plain words, and what to do when something goes wrong. */
import { h, navLink } from "../lib/dom.js";

const STEPS = [
  ["1. Check that the app is ready", "Look at the pill at the top of the page. It should say Ready. If it says Not ready, click it: each problem is listed with what to do. You can also press “Test the connection” there; it costs about one cent and proves a real search and a real model answer."],
  ["2. Run the Small input", "Go to Inputs. Find the card marked Small and press Run research. A box shows what it will probably cost. Confirm. This is the cheapest real test, so start here."],
  ["3. Watch it", "The run page shows the plan (what will be looked up), a live feed (searches, sites, facts being checked), the time and the cost so far. You can close the tab and come back: the run keeps going and the page catches up. Skip research stops it and keeps what was already checked."],
  ["4. Read the result", "When the run ends, press Open the results. The detailed results view arrives in the next version; until then you can download the whole result as one file."],
  ["5. Run Medium, then Big", "Only after the Small run looks right. The Big input costs much more and takes longer; the confirm box will warn you if it is a large part of what is left."],
  ["6. Spot-check", "The spot-check (sixty facts, checked by you against their sources) arrives in the next version."],
  ["7. Read the budget", "Open Budget. Every search and model call is written down as it happens, with the running total. The bar at the top turns amber at 80% and red at the tripwire."],
];
const IFS = [
  ["The pill says Not ready", "Click it. Each failing item says what to set. The variable names shown are the ones to set in the terminal that starts the app; the app never shows their values. Then press Check again."],
  ["A run says “Done, with gaps” (partial)", "It found some things but not everything, for example because the budget ran out or a site would not open. What it checked is kept. The run page says why; you can run the input again."],
  ["A run says Interrupted", "The app was stopped or restarted while the run was going. What it already spent is recorded. Press Re-run on the run page."],
  ["The budget bar is red, or Run is greyed out", "The tripwire was reached: the run in progress finishes and no new run starts. If you want to continue, restart the budget on the Budget page; history is kept."],
  ["The page says it cannot reach the app", "The app is not running, or was stopped. Start it again in its terminal window. This page reconnects by itself."],
  ["A file is listed under Skipped files", "It is in the folder but is not an approved Compass export. The reason is written next to it. Nothing is wrong with the app."],
];

export function mount(root) {
  root.append(
    h("div", { class: "page-head" }, h("h2", null, "Guide")),
    h("p", { class: "lead" }, "A short walk-through for testing the Research app. Open a step to read it."),
    h("section", { class: "card" }, h("h3", null, "Steps"), STEPS.map(([t, body], i) => h("details", { class: "details", open: i === 0 }, h("summary", null, t), h("p", null, body)))),
    h("section", { class: "card" }, h("h3", null, "What to do if…"), IFS.map(([t, body]) => h("details", { class: "details" }, h("summary", null, t), h("p", null, body)))),
    h("p", null, navLink("#/inputs", "Go to the inputs", "btn btn-primary"))
  );
  return {};
}
