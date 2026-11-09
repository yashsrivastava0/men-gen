# apps/web: the global front end

One front end for every agent (Global rules B). Today it is a static test console in plain HTML, CSS and vanilla JS with **no build step**; a React app is reserved for later. It is never part of an agent folder: an agent exposes only its HTTP API (and the shared JSON Schemas), and nothing here imports agent code.

## Layout

```
apps/web/
  console/                      what the dev server serves at /ui/
    index.html                  the shell: top bar with the agent switcher, a mount point, the toast region
    shared/
      tokens.css                design tokens only: the :root custom properties, light and dark
      shell.css                 the shell's own rules (switcher, mount points, load error)
      components.js             window.MobigenUI: store, $, h, appendChild, clear, randomId, toast
      registry.js               window.MobigenRegistry: one entry per agent
      shell.js                  fills the switcher, mounts the chosen agent
    agents/compass/
      compass.html             the Compass screens as an HTML fragment
      interview.js             window.compassInterview: the pure, DOM-free helpers of the interview screens
      compass.js               the Compass behaviour (starts with the /*CONTRACT {...} CONTRACT*/ block)
      compass.css              the Compass styles
  app/                          reserved for the later React app (.gitkeep)
  tests/                        UI smoke tests (pytest, offline)
```

## How the shell works

`index.html` loads `registry.js`, `components.js` and `shell.js` with `<script src defer>`. `shell.js` fills `<select id="agent-select">` from the registry, picks the agent (the `?agent=<id>` query, else the last choice kept in `localStorage`, else the first entry), sets the page title, then loads that agent's stylesheet and HTML fragment and only then its script, so the script finds its elements. Elements marked `data-slot="topbar"` in the fragment join the shared top bar; the rest go into `#agent-mount`. Agent scripts keep global listeners and cannot be unloaded, so choosing another agent reloads the page with `?agent=<id>`. If a file cannot be loaded the shell says so in the page instead of staying blank.

A registry entry:

| Field | Meaning |
| --- | --- |
| `id` | short name, the `?agent=` value, the folder `agents/<id>/` |
| `title` | the label in the switcher and the page title |
| `base` | the agent's API prefix (`/v1/compass`); the screens reach the agent only through it |
| `markup`, `script`, `style` | the three files of the screen set, relative to `console/` |
| `helper` | optional: a DOM-free helper script the shell loads just before `script` (Compass: `agents/compass/interview.js`) |

Adding an agent is a new `agents/<id>/` folder with those three files plus one registry entry. A second agent is not built yet.

## The Compass interview screens

The AI-planned interview (build prompt 02, section 2.7) is all front end: the agent sends events and answers `GET /qa` and `GET /budget`; the screens below live in `compass.html`, `compass.js`, `compass.css`, and the plain-data logic lives in `interview.js` (`window.compassInterview`: locating a card in its batch, the progress line, the answer-all payload, the Q&A editor model, the Brief highlight keys). New UI strings are the `ui.interview.*` keys: embedded in the `FALLBACK` dictionary of `compass.js` and appended to the agent's `en.json` and `hi.json` (a test keeps the three equal).

| What | Where (ids and classes) | Behaviour |
| --- | --- | --- |
| Mock banner | `#mock-banner` (first element of `<main>`, `.mock-banner`) | shown, with no close button, whenever `GET /budget` says `mode: "mock"`: "MOCK MODE: questions are canned"; sticky, wraps on a phone |
| Themed groups and progress | `.qcard-head`, `.group-chip`, `.progress-line` | a `question_batch` event is remembered (`state.batches`); each card shows its group title and "Group 2 of 3 · question 4 of about 9" (a follow-up borrows its parent's group) |
| "Why am I being asked this?" | `.why`, `.why-btn`, `.why-pop` (`role="note"`) | a button with `aria-expanded` and `aria-controls` toggles `why_we_ask` and "This feeds: ..." (`unlocks`); Escape closes it and returns the focus; built with `h()` and `textContent` only |
| Fallback badge | `.fallback-badge` | a card with `fallback: true` shows "Standard question" with a tooltip and `aria-label` saying the planner did not produce it (also on the Q&A list) |
| Answer-all form | `.batch-form`, `.qgroup`, `.qitem`, `.toggle`, `.send-all` | `ui_mode: "answer_all"` renders the whole batch as one form grouped by theme (radio, checkbox, text area with the question's `examples` as placeholder, per-question "You decide" and "Skip"); "Send all answers" calls `window.compassSend.batchAnswer(batch_id, answers)`; blanks are sent as skipped except questions that must be confirmed |
| Layout toggle and "Ask me more" | `#interview-bar`, `#btn-mode-toggle`, `#btn-ask-more`, `#mode-note` (just above the composer) | the toggle sends `mode_answer_all` / `mode_one_at_a_time`; the server re-sends the unanswered questions of the current batch in the new layout (`#mode-note` stays hidden); "Ask me more" sends `ask_more` once something is answered and the Brief is not approved |
| Interview depth | `.depth`, `.depth-chip` (welcome block and playback card) | Quick / Standard / Thorough send `budget_quick` / `budget_standard` / `budget_thorough`; the note shows the budget known so far from the progress event |
| Brief row highlight | `.brow.pulse`, `@keyframes row-pulse`, `highlightChanged()` | after each turn the view is refreshed; rows with `changed` pulse once per Brief hash (off under `prefers-reduced-motion`) and the first one is scrolled into view on a wide screen |
| Questions and answers | `#qa-section`, `#qa-list`, `.qa-item`, `.qa-status`, `.qa-editor`; chat history in `#history-chat` (History tab) | `GET /qa` on tab open and after each turn: question, answer in plain words, status chip, follow-up indent, fallback badge, the facts it produced; "Edit" opens an inline editor by question kind and "Save" calls `window.compassSend.answerEdit(cardId, value, custom)`, then the toast "Updated: your Brief and the next questions will follow" |
| Details we captured | the `details` section of the view model | rendered by the generic section renderer: server order and title, rows grouped under their `group` heading, no Edit button because `editable` is false |

## Rules for every file under `console/`

* Vanilla JS, no framework, no build step, no `eval`. User and model text is written with `textContent` or text nodes (`h()`), never as markup (`innerHTML` and friends are banned by a test). The one markup parse is `DOMParser` on the agent's own fragment in `shell.js`, which drops `script`, `style`, `link`, `iframe`, `object` and `embed` nodes.
* The server sends a strict CSP (`script-src 'self'`, `style-src 'self'`): no inline script, no inline style attribute, no inline handler, no external resource, no web fonts. Dynamic values that need a style (a bar width) go through `el.style[...]` (the `css` key of `h()`), which the CSP allows.
* Colours, spacing, radii and fonts are tokens from `shared/tokens.css`; a screen's CSS uses `var(--name)` and defines none.

## Run it

The Compass dev server serves this directory:

```bash
uv run python -m mde_compass --memory --port 8110      # no database; open http://127.0.0.1:8110/ui/
just compass-dev                                       # the same with the database
```

`MDE_WEB_DIR` points the server at another directory (a relative path is resolved against the repository root; the default is `apps/web/console`). `mde_compass.app.web_dir()` reads it each time and `create_app()` raises `FileNotFoundError` at start when the directory or its `index.html` is missing. Dev sign-in (token, tenant, user) is in the Settings dialog of the Compass screen.

## Tests

```bash
uv run pytest apps/web/tests -q
```

Offline and fast. They fetch every console file through the real app (CSP and content types), check that the shell and registry reference only files that exist, that the switcher lists the registered agents, that the tokens define the light and dark themes, that no inline script, style or handler exists anywhere under `console/`, that every script parses (`node --check`, skipped without Node), that every id `compass.js` looks up exists in the markup, and that the keys it reads exist in the agent's view-model, card, batch and event schemas, that the `/qa` item keys and the commands the UI sends are the server's, and that every string the code names exists in both languages (`test_ui_contract.py`, the one test that imports agent models). `test_interview_ui.py` checks that each interview screen above is present by its ids and classes; `test_interview_helpers.py` runs the functions of `interview.js` under Node (`node -e`, skipped without Node). `conftest.py` imports `create_app` and `ScriptedService` to serve the console; the console itself imports nothing.
