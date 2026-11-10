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

The AI-planned interview (build prompt 02, section 2.7) is all front end: the agent sends events and answers `GET /qa` and `GET /budget`; the screens below live in `compass.html`, `compass.js`, `compass.css`, and the plain-data logic lives in `interview.js` (`window.compassInterview`: locating a card in its batch, the progress line, the batch payload, the Q&A editor model, the Brief highlight keys). New UI strings are the `ui.interview.*` keys and, since Compass v2, the `ui.v2.*` keys: embedded in the `FALLBACK` dictionary of `compass.js` and appended to the agent's `en.json` and `hi.json` (tests keep the three equal and fail on a key nothing uses).

| What | Where (ids and classes) | Behaviour |
| --- | --- | --- |
| Mock banner | `#mock-banner` (first element of `<main>`, `.mock-banner`) | shown, with no close button, whenever `GET /budget` says `mode: "mock"`: "MOCK MODE: questions are canned"; sticky, wraps on a phone |
| Themed groups and progress | `.qcard-head`, `.group-chip`, `.progress-line` | a `question_batch` event is remembered (`state.batches`); each card shows its group title and "Group 2 of 3 · question 4 of about 9" (a follow-up borrows its parent's group) |
| "Why am I being asked this?" | `.why`, `.why-btn`, `.why-pop` (`role="note"`) | a button with `aria-expanded` and `aria-controls` toggles `why_we_ask` and "This feeds: ..." (`unlocks`); Escape closes it and returns the focus; built with `h()` and `textContent` only |
| Fallback badge | `.fallback-badge` | a card with `fallback: true` shows "Standard question" with a tooltip and `aria-label` saying the planner did not produce it (also on the Q&A list) |
| Tier first | `.tier`, `.tier-card`, `.tier-chip`, `.tier-change`, `#composer-gate` (welcome block) | three large cards before the idea: Quick (about 7 questions), Standard (about 12), Thorough (25 or more), each a button with `aria-pressed`; the choice is sent as `budget_quick` / `budget_standard` / `budget_thorough` and becomes a chip with a "Change" link until the first batch arrives; the idea box, its send and mic buttons and the four start buttons stay disabled until a tier is chosen, and `#composer-gate` says why; a reopened project restores the chip from the stored log; the server's canned welcome card is not drawn (the welcome block replaces it) |
| Batch form (submit once) | `.batch-form`, `.qgroup`, `.qitem`, `.qnum`, `.toggle`, `.send-all`, `.form-notes`, `.form-actions` | every planned batch, even a batch of one (the closing question), is drawn from the `question_batch` event as one form grouped by theme; nothing is sent until "Send my answers" is pressed; the button is disabled while sending (double-submit guard) and nothing collapses until the server answers; a failed POST keeps every answer and shows a plain message with a Retry button (same event id when nothing changed, so it is never applied twice); a stale batch is replaced by the new one and typed text stays where the question still exists; blank questions are summarised ("Left blank (question 3, 4): we will decide those for you") and sent as skipped; `window.compassSend.batchAnswer(batch_id, answers, {decideRest, eventId, onAck, onError})` |
| Numbers and the last question | `.qnum`, `.closing-note`, `.qitem-closing` | "Question 4 of about 7" from the optional `progress` of the batch (target only; the hard cap is never read; the count is hidden when progress is absent or past the target); a card with `origin: "closing"` (or `progress.closing`) is "Last question" with a one-line explanation and "Send and write my Brief" |
| Several answers, custom answer | `.pick-tools`, `.select-all`, `.select-none`, `.form-other`, `.rank-list` | `pick_many`: "Select all" (toggle), "None of these" (sends `action: "none"`, clears the ticks) and a typed answer sent together with the picks (`value` = ids, `custom_text` = text); `choose_one`: pick an option or type "Something else", the last action wins and the other side is dimmed; `priority` (the closing question): ticks are ranked in the order ticked, with up/down/remove arrows, plus the typed answer; a `quick_form` or `confirm` without fields is drawn as an open question |
| Auto-answer | `.toggle` (You decide), `.you-decide-note`, `.decide-rest`, `.decide-confirm` | per question "You decide" disables its inputs and sends `action: "you_decide"` (not offered on a `must_confirm` question); "Decide the rest for me" asks in one line (every open question is decided and listed under "We assumed" for approval) and sends the filled-in questions plus `decide_rest: true` |
| "Ask me more" | `#interview-bar`, `#btn-ask-more` (just above the composer) | sends `ask_more` once something is answered and the Brief is not approved (the one-at-a-time layout toggle is gone: the form is the only layout; an older server that still plans one-at-a-time is asked once for `mode_answer_all`) |
| Waiting | `#typing`, `#typing-text` (polite live region) | the line under the dots follows the phase of the request (the idea, an answer, the last answer, "decide the rest", a document, an edit; `WAIT` in `interview.js`), says something at once and ends in the same "still working" lines at 45 and 90 s; a `progress` event naming a later stage moves it on |
| Documents | `fileInput()`, `uploadFile()` | every picker offers `.pdf .docx .xlsx .pptx .csv .txt .md .eml`; a file over 15 MB or an empty one is refused before sending; "Reading your document..." while it is read; a 413 or 415 (JSON or not) leaves the page working with a plain message |
| Interview depth (compact) | `.depth`, `.depth-chip` (playback card) | Quick / Standard / Thorough chips remain on the playback card only for an idea that arrived without a tier; the note shows the budget known so far from the progress event |
| Brief row highlight | `.brow.pulse`, `@keyframes row-pulse`, `highlightChanged()` | after each turn the view is refreshed; rows with `changed` pulse once per Brief hash (off under `prefers-reduced-motion`) and the first one is scrolled into view on a wide screen |
| Questions and answers | `#qa-section`, `#qa-list`, `.qa-item`, `.qa-status`, `.qa-editor`; chat history in `#history-chat` (History tab) | `GET /qa` on tab open and after each turn: question, answer in plain words, status chip, follow-up indent, fallback badge, the facts it produced; "Edit" opens an inline editor by question kind and "Save" calls `window.compassSend.answerEdit(cardId, value, custom)`, then the toast "Updated: your Brief and the next questions will follow" |
| Details we captured | the `details` section of the view model | rendered by the generic section renderer: server order and title, rows grouped under their `group` heading, no Edit button because `editable` is false |
| App size on the tier cards | `.tier-size` (inside each `.tier-card`), `loadBands()`, `bandOfTier()`, `sizeText()` | ADR 0017: each tier card adds "Small app: about 8 to 15 screens" / "Medium ..." / "Enterprise app: 40 screens or more"; the numbers come from the public `GET /size-bands` (never typed into the page), the labels from the `size.*` strings; nothing is shown until the request returns |
| Structure tab | `#tab-structure`, `.sbanner`, `.smod`, `.sscreens`, `.sflows`, `.sentities`, `.smatrix`, `.sissues`; `loadStructure()`, `renderStructure()` | ADR 0017: appears when the Brief has a `structure` section; `GET /structure?lang=` shows the size banner (band, counts, "bigger/smaller than most ... apps", areas still to accept), one card per area (collapsed screens with pattern, roles and phase), journeys, the data the app keeps, a role by area matrix inside its own scroll box and the checks that were left; written with `textContent` only |
| Structure actions | `structAct()`, `sendStructureAction()`, `removeControl()`, `renameItem()`, `.sform`, `.sconfirm` | per area: Accept, Rename (a plain `brief_edit` of `<section>.<id>.name`), Redraft with the person's own words, Remove; per screen, journey and data kind: Rename or Remove; "Plan as Small / Medium / Enterprise" redrafts from the same answers. Remove and Plan as ask first (a live alert); one `structure_action` event is one change, so Undo restores it whole. Module rows of the Brief panel have Accept and "See the screens" |
| One-tap notices | `.notice-action` | a `notice` with `command` and `command_label` (the size suggestion) shows a button that sends that command once; the tier is never switched by itself |
| Developer & Diagnostics Drawer | `#dev-drawer`, `#btn-dev`, `#btn-copy-claude` | Developer tools with real-time HTTP network flight recorder (`state.networkTrace`), SSE event timeline (`state.devEvents`), raw state inspector, and LLM calls log. Includes a 1-click "Copy for Claude Code" action that exports a prompt-ready markdown report with environment state, last error details, recent API calls, and recent SSE events for instant debugging by Claude Code or Codex. |

## Guide for Adding Future Agent Static Frontends

When engineering new agents (e.g. Research, Spec Builder, Flow):
1. **Directory**: Create `apps/web/console/agents/<name>/` containing `<name>.html` (fragment with `data-slot="topbar"` elements), `<name>.js` (screen logic), `<name>.css` (styles using tokens), and optional `<name>_helpers.js`.
2. **Registry**: Register the agent in `apps/web/console/shared/registry.js` with `id`, `title`, `base`, `markup`, `script`, `style`, and optional `helper`.
3. **Design Tokens**: Strictly use `shared/tokens.css` variables (`--bg`, `--surface`, `--surface-2`, `--border`, `--text`, `--muted`, `--accent`, `--ok`, `--warn`, `--danger`, `--shadow`, `--font`, `--mono`). No custom color declarations or external web fonts.
4. **Layout Pattern**: Adopt the dual-canvas workspace (interaction stream on the left, live synchronized artifact on the right) and the persistent breakout composer pattern (*"Tell <agent> what to do instead..."*).
5. **Traceability & Diagnostics**: Integrate the network flight recorder and Claude Code diagnostic context generator (`generateClaudeReport()`) so that coding AIs can seamlessly inspect, reproduce, and fix backend/frontend issues.


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
