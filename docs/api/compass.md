# Compass HTTP API (`/v1/compass`)

The HTTP contract of [Compass](../packages/compass.md): sessions, events, the Brief, Gate 1 and the event stream. Hand-written from `agents/compass/src/mde_compass/api/` (`routes.py`, `schemas.py`, `auth.py`, `errors.py`, `middleware.py`, `sse.py`) and the event and card models in `agents/compass/src/mde_compass/domain/` (`events.py`, `cards.py`, `interview.py`, `brief.py`), and checked against the app running in mock mode with the in-memory store. The generated [openapi.json](openapi.json) and the JSON Schemas in `schemas/` describe the Context API and `mde_contracts` only; Compass serves its own OpenAPI at `GET /v1/compass/openapi.json` (the interactive docs are off) and its JSON Schemas are not exported yet. There is no chat UI in the API: a client sends **inbound events** and renders **outbound events**. A test UI ships with it at `/ui/` ([runbook](../runbooks/compass.md)).

Two question engines sit behind the same routes (`InterviewPolicy.engine`, [package page](../packages/compass.md#the-ai-planned-interview)): `legacy` (a rollback) asks one frame card at a time; `planner` (the default) asks project-specific questions in batches. Every shape below is the same for both; the parts that only the planner engine produces or reads are marked **planner**. The engine is chosen with `COMPASS_INTERVIEW_ENGINE=planner|legacy` ([runbook](../runbooks/compass.md#3-run-the-app)).

Base URL in development: `http://127.0.0.1:8110`. Run: `just compass-dev` or `uv run python -m mde_compass --port 8110 [--memory]`.

## Authentication

Every route except `/healthz`, `/readyz`, `/v1/compass/strings/{lang}`, the static UI and the OpenAPI document needs a bearer token. Any failure (missing, wrong, malformed) is the same `401 {"code":"unauthorized","message":"Authentication is required."}`.

**Dev headers (the default; local use only).** The token is the shared dev token, compared in constant time; the caller states who it is:

| Header | Meaning | Rule |
| --- | --- | --- |
| `Authorization: Bearer <token>` | `dev-token`, or the value of `COMPASS_DEV_TOKEN` | required |
| `X-Tenant-Id` | the tenant every store transaction is bound to | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, required |
| `X-User-Id` | the caller | `^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$`, required |
| `X-Roles` | optional comma separated roles | each `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, at most 16 |

> These headers are trusted identity: whoever holds the dev token can be any tenant and user. The server therefore refuses to listen on a non-loopback host unless token mode is on (exit code 2).

**Token mode.** `COMPASS_API_TOKENS="tokA=tenant:user[:role+role];tokB=..."`: the token selects a server-side identity (compared as SHA-256 digests in constant time) and the `X-*` headers are ignored. In this mode `GET /v1/compass/budget` is hidden (404).

Tenant isolation: a session id from another tenant behaves exactly like a missing one (`404 not_found`).

## Conventions

* JSON bodies; every error is `{"code": "...", "message": "..."}` (see Errors). `X-Request-Id` is returned on every response (a well-formed client value of 8 to 64 characters is kept, else `req_<ulid>`).
* Security headers on every response, including SSE, static files and errors: `Content-Security-Policy` (`default-src 'self'; script-src 'self'; ...; frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
* Request bodies are limited to 256 KB (`COMPASS_MAX_BODY_BYTES`); larger is `413 payload_too_large`. The `.../uploads` path is exempt from that limit and has its own: 15 MB per file, enforced while the bytes are read (`413 too_large`).
* JSON replies of the Brief and query routes carry `Cache-Control: no-store`.
* Money is a decimal string in USD (`"0.0000"`), never a float; timestamps are timezone-aware UTC ISO strings; session ids are `ses_<ulid>`.
* Events are idempotent by `event_id` (1 to 64 characters of `A-Za-z0-9_.:-`, chosen by the client): a repeat is acknowledged with `duplicate: true` and ignored. See [Idempotency and chained turns](#idempotency-and-chained-turns) for the internal events the engine adds itself.

## Endpoints

| Method and path | Purpose | Success |
| --- | --- | --- |
| `GET /healthz` | liveness, no auth | `200 {"status":"ok"}` |
| `GET /readyz` | the service is available, no auth | `200 {"status":"ready"}` or `503 {"status":"not_ready"}` |
| `POST /v1/compass/sessions` | create a session; the greeting and the welcome card are already in the stream | `201` `SessionSummary` |
| `GET /v1/compass/sessions` | the caller's sessions | `200 {"sessions":[SessionSummary]}` |
| `GET /v1/compass/sessions/{id}` | one session | `200` `SessionSummary` |
| `POST /v1/compass/sessions/{id}/events` | send one inbound event; runs one turn | `200` `EventAck` |
| `GET /v1/compass/sessions/{id}/stream` | server-sent events, replayable | `200 text/event-stream` |
| `POST /v1/compass/sessions/{id}/uploads` | upload one document (multipart, field `file`); runs one turn that reads it | `200` `EventAck`; `413 too_large` above 15 MB |
| `GET /v1/compass/sessions/{id}/brief?version=N` | the canonical `brief.json` of a version (default latest) plus `_hash` and `_section_hashes` | `200` |
| `GET /v1/compass/sessions/{id}/brief/view?version=N&lang=xx` | the display-ready `BriefViewModel` | `200` |
| `GET /v1/compass/sessions/{id}/brief/versions` | every saved version | `200 {"versions":[{version, hash, baseline, change_set_id, created_at}]}` |
| `GET /v1/compass/sessions/{id}/brief/diff?from=A&to=B` | entries between two versions | `200 {from_version, to_version, sections_changed, entries[{path, before, after, kind}]}` |
| `GET /v1/compass/sessions/{id}/history?after=&limit=&slot=&q=` | the transcript, both directions, paged | `200 {items, next_after_idx, highlighted_turn_ids}` |
| `GET /v1/compass/sessions/{id}/qa` | every question put to the person, with the answer in plain words, its status and the facts it produced (the Questions-and-answers list; **planner**) | `200 {"items":[QaItem]}` |
| `GET /v1/compass/sessions/{id}/provenance/{item_id}` | where an item's value came from | `200` |
| `POST /v1/compass/sessions/{id}/gate` | a `gate_decision` event; answers with the verdict | `200 {accepted, state, version, brief_hash, baseline, blockers}` |
| `GET /v1/compass/sessions/{id}/handoff` | the consumer views of the approved version | `200 {version, brief_hash, views}` or `409` before approval |
| `GET /v1/compass/sessions/{id}/calls?limit=` | the call log (dev) | `200 {"calls":[{call_id, spec_id, outcome, tokens_in, tokens_out, cost_usd, latency_ms}]}` |
| `GET /v1/compass/budget` | spend against the Compass cap (dev only; 404 in token mode) | `200 {mode, cap_usd, hard_cap_usd, effective_stop_usd, per_session_cap_usd, spent_usd, remaining_usd, spent_micros, calls}` |
| `GET /v1/compass/strings/{lang}` | UI chrome strings, `en` or `hi`, public, cached 300 s | `200 {language, strings}` |
| `GET /` , `GET /ui/` | redirect to the test UI, the test UI | `307`, `200` |

Query limits: `version` and `from`/`to` at least 1; `history` `limit` 1 to 200 (default 50), `slot` and `q` at most 200 characters; `calls` `limit` 1 to 200 (default 30); `lang` two lowercase letters and one of `en`, `hi` (else `422`); stream `after` 0 to 2,147,483,647.

## Bodies

**`POST /sessions`** request (all optional): `{"mode": "guided" | "enterprise", "locale": "en", "user_label": ""}`. `locale` matches `^[a-z]{2}(-[A-Za-z]{2,4})?$` and falls back to `en` if Compass has no strings for it (`en`, `hi`); `user_label` at most 120 characters. An enterprise session also gets two contributors, `approver` and `commenter`.

**`SessionSummary`:** `id`, `title` (the idea, after the first message), `user_label`, `mode`, `locale`, `state` (see [the state table](../packages/compass.md#state-machine)), `brief_version`, `approved_version`, `question_n` and `question_of_about` (always 0 today), `spent_usd`, `llm_calls`, `created_at`, `updated_at`.

**`EventAck`:** `{"accepted": true, "duplicate": false, "events": [StoredEvent]}`: the outbound events this turn produced, so a simple client needs no stream. **`StoredEvent`:** `idx` (per-session order across both directions), `direction` (`in`/`out`), `type`, `event_id`, `turn_id` (the inbound `event_id` the outbound event answers), `seq` (outbound only: the SSE id), `payload`, `at`.

**Inbound events** (`type` selects the shape; every one has `event_id` and an optional `contributor_id` for enterprise sessions):

| `type` | Fields |
| --- | --- |
| `user_message` | `text` (at most 20,000 characters), `input_mode` (`text`/`voice`), `lang_hint` |
| `card_answer` | `card_id`, `value` (an option id; a list of ids for `pick_many`; ordered ids for `priority`; a form object; text), `custom_text` |
| `card_skip`, `card_you_decide` | `card_id` |
| `card_back` | none |
| `batch_answer` | **planner.** `batch_id`, `answers[]` (at most 12; each `card_id`, `action` `answer` / `skip` / `you_decide`, `value`, `custom_text`): the answers of one batch form, read in one turn ("answer all at once"). An unknown `card_id` is `404`. The legacy engine ignores it |
| `answer_edit` | **planner.** `card_id`, `value`, `custom_text`: edit an earlier answer. A new answer version is stored, the facts of the old one are superseded (never deleted), the slots they fed are rebuilt and the questions still waiting are planned again. Not allowed once the Brief is approved: the reply carries an `edit_unavailable` notice. During review it reopens the review |
| `upload_added` | `source_id`. Created by the uploads route, not by clients: a bare `upload_added` event with no bytes behind it is answered with an "upload not ready" notice |
| `link_added` | `url` (at most 2048): the page is read by the SSRF-safe reader and ingested like a document; a refused or unreachable link is explained in an `assistant_message` |
| `command` | `name`: `undo`, `back`, `pause`, `resume`, `start_over`, `finish_with_defaults`, `show_what_you_know`, and for the **planner** engine `ask_more` ("Ask me more": drop the waiting questions, raise the budget to `thorough` and plan deeper questions on the least certain areas; a `nothing_more` notice if the planner has none), `mode_answer_all` / `mode_one_at_a_time` (show each batch as one form or one question at a time; the choice is kept), `budget_quick` / `budget_standard` / `budget_thorough` (interview depth: up to 5, 12 or 25 questions including follow-ups), and `prepare_questions` (internal, see below; clients do not send it) |
| `brief_edit` | `item_id` (a path such as `idea.summary`, `access.account_model`, `scope.FTR-004.priority`), `value` |
| `brief_feedback` | `target` (section key, row id or path), `reason` (`not_what_i_meant`, `too_much`, `something_missing`, `wrong_tone`, `dont_want_assumption`, `other`), `text` (at most 4000) |
| `assumption_decision` | `id` (`ASM-###`), `decision` (`accept`/`change`), `value` |
| `lookup_edit` | `action` (`add`/`remove`), `request_id` (`RR-###`) or `text` (at most 500) |
| `contributor_switch` | `contributor_id` (required) |
| `gate_decision` | `decision` (`approve`/`request_changes`), `version`, `brief_hash`, `section_hashes` |

**Outbound events** (the `payload` of a `StoredEvent`, `payload.type` repeats `type`): `assistant_message` (`text`, `language`, `chips`), `card` (`card`: a [Card](#the-card), one question), `question_batch` (**planner**: `batch_id`, `mode`, `ui_mode`, `groups[]`, `total`, `answered`; see [Planned batches](#planned-batches)), `playback` (`title`, `items[]` with `key`, `label`, `value`, `basis`, and `unclear[]`), `notice` (`code`, `text`, `chips`, `severity`), `progress` (`stage`, `n`, `of_about`, `note`), `brief_patch` (`change_set_id`, `from_version`, `to_version`, `summary`, `ops[]`, `voided_sections[]`, `undoable`), `brief_ready` (`version`, `brief_hash`, `section_hashes`, `approvable`, `blockers[]`), `brief_approved` (`version`, `brief_hash`, `baseline`), `lookup_preview` (`requests[]`), `probe_result` (`kind`, `passed`, `gaps[]`, `round`), `error` (`code`, `message`). Defined but never emitted today: `budget_warning`, `assistant_delta`.

**`BriefViewModel`** (`/brief/view`): `language`, `session_id`, `header` (`title`, `idea_line`, `version`, `status` `draft`/`in_review`/`approved`, `status_label`, `readiness[]` chips for research, blueprint and design, `reviewed`, `of_total`, `degraded`), `sections[]` (15 ids: `idea`, `who`, `apps_roles`, `features`, `look`, `access`, `integrations_data`, `limits`, `words`, `details`, `assumed`, `unknown`, `lookup`, `sources`, `cannot_build`; each with `title`, `rows[]` of `id`, `label`, `value{kind,text,items}`, `badges`, `tier`, `editable`, `history_available`, `needs_review`, `changed`, `group`, `status_chip`, `description`), `footer` (`primary_label`, `secondary_label`, `approvable`, `blockers[]`), `brief_hash`, `section_hashes`, `changed_since_approval`. Row `id`s are paths in the change grammar, so they can be sent straight back in `brief_edit`. Use `header.status` and the `brief_ready` event to decide whether to offer approval; `footer.approvable` is only meaningful in `in_review`.

**`POST /gate`** body is a `gate_decision` event (it needs `type`, `event_id`, `decision`, `version`, `brief_hash`). The reply is `{accepted, state, version, brief_hash, baseline, blockers[]}`; `accepted` is true only when the session is `APPROVED`. Outside `BRIEF_REVIEW` the verdict is `accepted: false` with a `gate_blocked` notice in the stream. In an enterprise session the approval counts only from a contributor with the role `approver` (the session creates `approver` and `commenter` contributors): send `"contributor_id": "approver"` in the body; until then the reply lists `Waiting for approval from: approver`, and approving does not change the hash. Approving the same, unchanged approved Brief again is a no-op.

## Cards, batches and the Interview Memory

### The card

`card` events carry one `Card` (`domain/cards.py`). Fields every card has:

| Field | Meaning |
| --- | --- |
| `id` | `Q-###`, unique in the session |
| `frame` | the frame id (`welcome`, `change_variants` and the catalog frames) or `planned` for a question the planner wrote |
| `slots` | catalog slot ids the answer fills |
| `type` | `choose_one`, `pick_many`, `priority`, `confirm`, `look_signals`, `quick_form`, `upload_or_link`, `open_prompt` |
| `question`, `why_we_ask` | the wording; a planner question is written in the person's language |
| `options[]` | `id`, `label`, `basis` (`your_document`, `your_words`, `typical`, `suggestion`), `recommended`, `reason`, `sets` (slot values the option sets; empty for planner questions), `ticked`, `description` (a short line under the label; planner questions) |
| `custom_input` | `enabled`, `placeholder`, `voice`: the type-your-own box |
| `you_decide`, `skip` | `enabled`; `you_decide.assumes` is the option id it takes |
| `progress` | `{n, of_about}`: "question n of about N" |
| `language` | `en`, `hi`, `hi-Latn` |
| `must_confirm` | risky slots: "You decide" and Skip are refused |
| `fallback` | **true when code made the card instead of the model** (the catalog's fallback card). A fallback is a failure: show a badge; it must not appear in a live batch |

Added for planned questions (**planner**; all optional or defaulted, so every older card still validates):

| Field | Meaning |
| --- | --- |
| `batch_id`, `group_id` | the batch (`BAT-###`) and the themed group (`G1`, `G2`, ...) it belongs to |
| `need_ids` | the needs it serves: a catalog slot id, or `domain:<topic>` for a project-specific detail |
| `intent` | what the question wants to learn, in plain words |
| `unlocks` | the downstream decisions it feeds (a Research query, Blueprint roles, Design tone ...): the text for a "Why am I being asked this?" popover next to `why_we_ask` |
| `answer_spec`, `followup_hint` | what a good answer contains; what to ask if the answer is vague |
| `examples` | placeholder hints for a free-text question |
| `parent_card_id`, `depth` | a follow-up points at the question it refines; `depth` 0 is a base question, 1 or 2 a follow-up |
| `origin` | `planner`, `followup`, `fallback` (a registry card made by code) or `frame` (the legacy engine) |
| `rephrased` | how many times the question was re-worded after "I do not understand" or "I do not know" |

A free-text planner question is an `open_prompt` with `examples` and no options; a question with options is a `choose_one` with exactly three, or a `pick_many`, `priority`, `confirm` or `quick_form`. A re-worded or simplified question is sent again as a `card` event with the same `id` and a higher `rephrased`.

### Planned batches

**Planner.** A `question_batch` event carries one planned batch:

| Field | Meaning |
| --- | --- |
| `batch_id` | `BAT-###` |
| `mode` | why it was planned: `initial` (the first batch), `replan` (after a batch, an answer edit, a pivot or a hand-off gap), `deepen` ("Ask me more"). `followup` and `simplify` are in the vocabulary but the engine does not emit them as batches today |
| `ui_mode` | `one_at_a_time` or `answer_all`: how the engine suggests showing it (the default follows the Situation Profile; `mode_*` commands change it) |
| `groups[]` | `id`, `title` (written by the model in the person's language), `cards[]` (full cards, in the order to ask them); 2 to 3 groups of 3 to 4 questions |
| `total` | questions in the batch |
| `answered` | 0 when emitted; it is not updated later (count the answers instead) |

* **One at a time.** The engine emits the `question_batch`, then a `progress` and a `card` for the first question; each answer brings the next as a `card`. A client that ignores `question_batch` still works.
* **Answer all.** The engine emits the `question_batch` and a `card` for the first question only; the client renders the form from the batch and sends one `batch_answer` with every answer (`answer`, `skip` or `you_decide` per card).
* **Follow-ups are not batches.** A follow-up arrives as a normal `card` with `origin: "followup"`, `parent_card_id` and `depth`, ahead of the rest of the batch.
* **Progress.** `progress` events carry `stage: "interview"`, `n` (questions asked so far plus one) and `of_about` (at most the interview budget).
* **Domain hypothesis.** There is no separate event. The `playback` of the first turn shows what `cmp.situate` guessed (category, likely roles and features, with a `basis`). The planner's own hypothesis arrives with the first batch and is merged into `meta.hypothesis` of the Brief (`GET /brief`).
* **Notices of the planner engine** (`notice.code`): `picked_up` (the values just read from an answer, as chips the person can correct), `assumed` (a vague or undecided answer became a visible assumption), `simplified_decided` (a question was re-worded as far as it goes, so "You decide" took over), `edit_unavailable` (an answer cannot be edited after approval), `nothing_more` (the planner has nothing to add to "Ask me more"), `pivot` (the idea changed direction; names the old and new category).

### Questions and answers: `GET /sessions/{id}/qa`

**Planner.** The data behind a Questions-and-answers list. `{"items": [...]}`, in the order the questions were asked, only the newest version of each question; questions that were dropped or not yet shown are left out. A session on the legacy engine returns `{"items": []}`. Each item:

| Field | Meaning |
| --- | --- |
| `card_id`, `version` | the question and its answer version (an edit adds the next version) |
| `status` | `open` (shown, no answer yet), `answered`, `skipped`, `you_decide` |
| `batch_id`, `group_id`, `origin`, `depth`, `parent_card_id`, `type`, `fallback` | as on the card |
| `question`, `why_we_ask`, `unlocks[]`, `need_ids[]`, `options[]` (`id`, `label`) | what was asked |
| `answer` | the answer in plain words: the labels of the options picked, then the typed text |
| `value`, `custom_text` | what the client sent (option ids, a form object or text) |
| `facts[]` | the facts this answer produced: `id` (`FCT-###`), `statement`, `status` (`active`, `superseded`, `disputed`, `not_used`) |

To edit an answer, send an `answer_edit` event with the `card_id`.

### What the Brief payload gains

* `GET /brief`: `domain_facts[]` (each fact: `id` `FCT-###`, `type`, `statement`, `topics`, `entities`, `consumers`, `source`, `quote`, plus the usual `tier`, `provenance[]`, `confidence`, `status`, `history[]`); the list is read-only (an edit of it is refused) and is left out of the hash while empty.
* `GET /brief`, `meta.interview`: audit counts that never enter the hash or a consumer view: `budget` (`quick`, `standard`, `thorough`), `ui_mode`, `ui_mode_set`, `questions_asked`, `followups`, `answered`, `skipped`, `assumed`, `batches`, `facts_active`, `facts_not_used`, `facts_unreferenced`, `coverage` (0 to 1), `fallback_count`, `question_ids[]`, `stop_reason` (`fatigue`, `budget_reached`, `coverage_reached` or a sentence). **`fallback_count` must be 0 in a live batch**; it counts the planner, auditor, rephraser, summary and compile fallbacks (other calls' fallbacks are only in `GET .../calls`, as `outcome: "fallback"`).
* `GET /brief`, `meta.plan.interview_budget` (`quick`, `standard`, `thorough` or null) and `meta.hypothesis`.
* `GET /brief/view`: one more section, `details` ("Details we captured"), one read-only row per fact (row ids `domain_facts.FCT-###`, `editable: false`), grouped by topic.
* `GET /handoff` and the consumer views: each view gains a `domain_facts` list (`id`, `type`, `statement`, `topics`, `entities`, plus `kind` and `refs` when the fact has them) with the facts that reach that view; never the quote, the provenance or any chat text. All ten catalog consumer tags reach a view (`catalog/fact_kinds.yaml`, `view_routes`): `research`, `blueprint`, `design` and `verify` reach their own view, `screens` reaches `design` and `blueprint`, `frontend` and `backend` reach `blueprint`, `qa` and `change_control` reach `verify`, `all` reaches every view; a fact with no tag gets the default consumers of its `kind`.
* The `blueprint` view groups the facts that refer to a record: each app, role and feature gains `details` (`{kind: [{id, statement}]}`, only when it has some), and `unlinked_details` (same shape) holds the facts whose `refs` resolve to no app, role or feature (a fact is never dropped). Company (`org.*`) slots appear as `org` (`profile`, `approvers`, `design_system`, `api_catalog`) in the views the catalog gives each slot (consumers `all` reach every view). Drafted details and corrections that are not tied to a slot (assumptions with basis `typical`, `correction` or `review`, unless rejected) appear as `assumed_details` (`id`, `claim`, `basis`, `decision`): `typical` in `blueprint` and `verify`, `correction` and `review` in `verify`.

### Idempotency and chained turns

* Every inbound event has a client-chosen `event_id`; the first copy runs, every repeat is answered with `duplicate: true` and no events. The outbound events a turn produces carry the inbound `event_id` as their `turn_id`.
* **Round 0 is two chained turns.** The first message of a session produces the `notice` and the `playback`; the engine then runs an **internal** turn, a `command` named `prepare_questions` with an id of the form `int_<24 hex>` derived from the first turn's id, which plans and shows the first batch. The internal event is recorded in the event log like any inbound event (`GET .../history` lists it, direction `in`, type `command`), and its outbound events carry that id as `turn_id`. Both turns run inside the one `POST .../events` request, so the reply carries the events of both; on the stream the playback is visible while the planner works. A retried request is a duplicate and plans nothing twice, and a planner run with identical inputs is found in the stored batches instead of being paid for again.
* Clients must not send `prepare_questions`; sent after the first batch exists it does nothing.
* A turn that fails ends with one `error` event, `code: "turn_failed"`, as before.

## Documents and links

`POST /v1/compass/sessions/{id}/uploads` takes `multipart/form-data` with one file in the field `file` (one file per request; PDF, DOCX, XLSX, PPTX, CSV, TXT, MD or EML; the type is decided by extension and magic bytes). It is one turn like any event: the reply is an `EventAck` with the outbound events it produced, which are also on the stream. A file that cannot be used is **not** an HTTP error: the turn answers with a plain `assistant_message` ("x.pdf looks damaged and I could not read it. Please send another copy; I carried on with the rest."; password protected, unsupported type, too many pages, too many files, session total too large, and a duplicate: "... is the same as a file I already have (SRC-001); I kept the first one."). HTTP errors are only `413 too_large` (more than 15 MB, `{"code":"too_large","message":"That file is larger than 15 MB. Please send a smaller one."}`), `422 validation_failed` (no `file` field) and the usual 401 and 404. Limits: 10 files, 60 MB and 300 pages per session.

When a document is read, the stream carries an `assistant_message` with the summary ("I read brief.txt: read 100%; 4 statements; 2 need confirming; 0 conflict; 1 open question."), a `notice` `picked_up` with chips ("From your document I picked up: Features: ..."), a `notice` `document_instruction_ignored` if the text tried to instruct the assistant, and then the normal next step (a `playback` and a `card`, or the review). The claims land in the Brief with `document` provenance and a verbatim quote (so `GET .../provenance/{item_id}` shows the source id and pointer); open issues become non-blocking open questions; the source is listed in the `sources` section. A first document in `WELCOME` moves the session to `INTAKE`.

Links: send `{"type":"link_added","event_id":"...","url":"https://..."}` as an event. A pasted message over 3,000 characters in a `user_message` is read as a document too. The URL reader refuses anything that is not a public web page (private and internal addresses, credentials in the URL, odd ports; the checks repeat at every redirect) and says so plainly: "I cannot open http://127.0.0.1/admin: it is not a public web page."

```bash
curl -s -X POST "$B/sessions/$SID/uploads" "${H[@]}" -F "file=@requirements.pdf"
```

## Server-sent events

`GET /v1/compass/sessions/{id}/stream`, headers `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`.

```
retry: 3000

id: 2
event: card
data: {"idx":2,"direction":"out","type":"card","event_id":null,"turn_id":null,"seq":2,"payload":{"type":"card","card":{...}},"at":"2026-11-09T05:38:54.972482Z"}

: keepalive
```

* One frame per outbound event; `id` is the per-session `seq` (1, 2, 3, ... in commit order), `event` is the type, `data` is the compact `StoredEvent` JSON.
* **Replay.** Reconnect with `Last-Event-ID: <n>` (digits only, at most 12, clamped) or `?after=<n>`: you get everything after `n`, then the live tail. The header wins over `?after`. `?follow=false` replays what exists and closes. An unknown session is a clean 404 before the response starts.
* The server polls the store (poll interval 0.25 s, backing off by half after every empty poll up to 2 s); it sends `: keepalive` after 15 s of silence and ends the stream after 900 s (the client reconnects with `Last-Event-ID`). At most 8 open streams per caller (`429 too_many_streams`).
* An error mid-stream is a final frame `event: stream_error` with `data: {"code","message"}`.
* Browsers cannot set the auth headers on `EventSource`; the test UI reads the stream with `fetch` and a reader.

## Errors

| Status | `code` | When |
| --- | --- | --- |
| 400 | any `MdeError` code | an engine error with no more specific mapping |
| 401 | `unauthorized` | missing or invalid credentials |
| 404 | `not_found` | unknown session, version, item or language; another tenant's id; a card id that was never asked (in `card_answer`, `card_skip`, `card_you_decide` or `batch_answer`); `/budget` in token mode |
| 405 | `method_not_allowed` | wrong method |
| 409 | `conflict` | another turn is already running for the session; a stale Brief version |
| 409 | `gate_error`, `hash_mismatch`, `invalid_transition` | the handoff before approval; an approval for a stale hash; a trigger the state does not allow |
| 413 | `payload_too_large`, `too_large` | JSON body over 256 KB; an uploaded file over 15 MB |
| 422 | `validation_failed` | bad body, unknown event `type` (the message lists the valid tags, never the offending input), bad query |
| 429 | `budget_exceeded`, `too_many_streams` | the Compass or gateway cap; too many open streams |
| 500 | `internal_error` | unexpected; a generic message |
| 502, 503 | `provider_error`, `circuit_open`, `not_ready` | a model provider problem; the service is not composed |

A failure *inside a turn* (for example a misconfigured live mode) does not change the HTTP status: the turn ends and one `error` event with `code: "turn_failed"` is appended to the stream.

## Example session (mock mode)

```bash
H=(-H "Authorization: Bearer dev-token" -H "X-Tenant-Id: demo" -H "X-User-Id: demo-user")   # bash or zsh array
B=http://127.0.0.1:8110/v1/compass

# 1. create a session (the greeting and the welcome card are already stored)
curl -s -X POST $B/sessions "${H[@]}" -H 'Content-Type: application/json' -d '{"mode":"guided","locale":"en"}'
#   {"id":"ses_01...","state":"WELCOME","brief_version":1,...}
SID=ses_01...

# 2. watch the stream in another terminal (add -H "Last-Event-ID: 5" to resume)
curl -N $B/sessions/$SID/stream "${H[@]}"

# 3. say what you want (legacy engine: runs understand_turn, situate and write_card), answers with the events it produced
curl -s -X POST $B/sessions/$SID/events "${H[@]}" -H 'Content-Type: application/json' \
  -d '{"type":"user_message","event_id":"e1","text":"I want a food delivery app for restaurants in Pune, in Hindi and English"}'
#   {"accepted":true,"duplicate":false,"events":[notice, playback, progress, card]}

# 4. answer the card (the recommended option id from the card event, here "A")
curl -s -X POST $B/sessions/$SID/events "${H[@]}" -H 'Content-Type: application/json' \
  -d '{"type":"card_answer","event_id":"e2","card_id":"Q-002","value":"A"}'
#   ... repeat until the answer carries a brief_ready event

# 5. look at the Brief page and the versions
curl -s "$B/sessions/$SID/brief/view" "${H[@]}"
curl -s "$B/sessions/$SID/brief/versions" "${H[@]}"

# 6. decide what blocks approval: each high-impact assumption needs an explicit decision
curl -s -X POST $B/sessions/$SID/events "${H[@]}" -H 'Content-Type: application/json' \
  -d '{"type":"assumption_decision","event_id":"e3","id":"ASM-001","decision":"accept"}'
curl -s -X POST $B/sessions/$SID/events "${H[@]}" -H 'Content-Type: application/json' \
  -d '{"type":"brief_edit","event_id":"e4","item_id":"access.account_model","value":"individual"}'
#   the last brief_ready event carries "approvable": true plus the version and hash to approve

# 7. Gate 1: approve exactly that version and hash
curl -s -X POST $B/sessions/$SID/gate "${H[@]}" -H 'Content-Type: application/json' \
  -d '{"type":"gate_decision","event_id":"g1","decision":"approve","version":13,"brief_hash":"5de8e849..."}'
#   {"accepted":true,"state":"APPROVED","version":13,"brief_hash":"5de8e849...","baseline":"B-G1","blockers":[]}

# 8. the consumer views (values only, no chat) of the approved version
curl -s $B/sessions/$SID/handoff "${H[@]}"
#   {"version":13,"brief_hash":"...","views":{"research":{...},"blueprint":{...},"design":{...},"verify":{...}}}
```

With the planner engine (`COMPASS_INTERVIEW_ENGINE=planner`) step 3 returns the events of both round-0 turns: `[notice, playback, question_batch, progress, card]`. The `question_batch` lists the groups (in mock mode the titles and questions are labelled `[mock]`), the `card` is the first question, and the rest of the interview is: answer each `card` (`card_answer` with an option id or `custom_text`), watch for follow-ups (a `card` with `origin: "followup"`) and further batches, read `GET .../qa` for the list of questions and answers, and approve the way steps 5 to 8 show.

(The ids, version 13 and the hash above are from one mock run; yours differ. Every value in `curl` is a placeholder; do not put real secrets in a command line or a document.) If you approve with a wrong version or hash, or while a blocker stands, the reply is `{"accepted": false, ..., "blockers": [...]}` and nothing changes. A later edit voids only the sections it touches and returns the session to `BRIEF_REVIEW`; `POST .../events` with `{"type":"command","event_id":"u1","name":"undo"}` reverts the latest change as a new version.

## Known gaps

* `GET /brief/view?lang=` is validated but the real service ignores it and uses the session locale.
* `question_n` and `question_of_about` on `SessionSummary` are always 0.
* `footer.approvable` in the view model is true outside `in_review`; trust `header.status` and the `brief_ready` event.
* `budget_warning` and `assistant_delta` events exist in the vocabulary but are never emitted. The Compass spend counter is per process; in live mode it starts from the gateway ledger's `cmp.*` total (so `GET /budget` after a restart already includes earlier batches), but spend by other processes during a run is not seen until the next start (read the ledger with `just budget`).
* The planner engine is not the default yet; without `COMPASS_INTERVIEW_ENGINE=planner` the **planner** events and routes stay unused (`batch_answer` is ignored, `GET /qa` is empty, `answer_edit` answers `edit_unavailable`).
* `question_batch.answered` is always 0, and `mode` values `followup` and `simplify` are never emitted as batches.
* `provenance` entries of a fact-backed value show `ref` = `FCT-###` and the copied `quote`; there is no route that returns one fact by id (the facts of an answer are in `GET /qa`).
* Fixed since the first version of this page: `GET /budget` now answers 200 (the service attribute was renamed `budget_guard`), and the enterprise multi-approver gate completes (`meta.approvals` is excluded from the hash).
