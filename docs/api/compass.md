# Compass HTTP API (`/v1/compass`)

The HTTP contract of [Compass](../packages/compass.md): sessions, events, the Brief, Gate 1 and the event stream. Hand-written from `agents/compass/src/mde_compass/api/` (`routes.py`, `schemas.py`, `auth.py`, `errors.py`, `middleware.py`, `sse.py`) and checked against the app running in mock mode with the in-memory store. The generated [openapi.json](openapi.json) describes the Context API only; Compass serves its own at `GET /v1/compass/openapi.json` (the interactive docs are off). There is no chat UI in the API: a client sends **inbound events** and renders **outbound events**. A test UI ships with it at `/ui/` ([runbook](../runbooks/compass.md)).

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
* Events are idempotent by `event_id` (1 to 64 characters of `A-Za-z0-9_.:-`, chosen by the client): a repeat is acknowledged with `duplicate: true` and ignored.

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
| `upload_added` | `source_id`. Created by the uploads route, not by clients: a bare `upload_added` event with no bytes behind it is answered with an "upload not ready" notice |
| `link_added` | `url` (at most 2048): the page is read by the SSRF-safe reader and ingested like a document; a refused or unreachable link is explained in an `assistant_message` |
| `command` | `name`: `undo`, `back`, `pause`, `resume`, `start_over`, `finish_with_defaults`, `show_what_you_know` |
| `brief_edit` | `item_id` (a path such as `idea.summary`, `access.account_model`, `scope.FTR-004.priority`), `value` |
| `brief_feedback` | `target` (section key, row id or path), `reason` (`not_what_i_meant`, `too_much`, `something_missing`, `wrong_tone`, `dont_want_assumption`, `other`), `text` (at most 4000) |
| `assumption_decision` | `id` (`ASM-###`), `decision` (`accept`/`change`), `value` |
| `lookup_edit` | `action` (`add`/`remove`), `request_id` (`RR-###`) or `text` (at most 500) |
| `contributor_switch` | `contributor_id` (required) |
| `gate_decision` | `decision` (`approve`/`request_changes`), `version`, `brief_hash`, `section_hashes` |

**Outbound events** (the `payload` of a `StoredEvent`, `payload.type` repeats `type`): `assistant_message` (`text`, `language`, `chips`), `card` (`card`: `id`, `frame`, `slots`, `type`, `question`, `why_we_ask`, `options[]` with `id`, `label`, `basis`, `recommended`, `reason`, `sets`, `ticked`, plus `custom_input`, `you_decide`, `skip`, `progress{n, of_about}`, `language`, `must_confirm`, `fallback`), `playback` (`title`, `items[]`, `unclear[]`), `notice` (`code`, `text`, `chips`, `severity`), `progress` (`stage`, `n`, `of_about`, `note`), `brief_patch` (`change_set_id`, `from_version`, `to_version`, `summary`, `ops[]`, `voided_sections[]`, `undoable`), `brief_ready` (`version`, `brief_hash`, `section_hashes`, `approvable`, `blockers[]`), `brief_approved` (`version`, `brief_hash`, `baseline`), `lookup_preview` (`requests[]`), `probe_result` (`kind`, `passed`, `gaps[]`, `round`), `error` (`code`, `message`). Defined but never emitted today: `budget_warning`, `assistant_delta`.

**`BriefViewModel`** (`/brief/view`): `language`, `session_id`, `header` (`title`, `idea_line`, `version`, `status` `draft`/`in_review`/`approved`, `status_label`, `readiness[]` chips for research, blueprint and design, `reviewed`, `of_total`, `degraded`), `sections[]` (14 ids; each with `title`, `rows[]` of `id`, `label`, `value{kind,text,items}`, `badges`, `tier`, `editable`, `history_available`, `needs_review`, `changed`, `group`, `status_chip`, `description`), `footer` (`primary_label`, `secondary_label`, `approvable`, `blockers[]`), `brief_hash`, `section_hashes`, `changed_since_approval`. Row `id`s are paths in the change grammar, so they can be sent straight back in `brief_edit`. Use `header.status` and the `brief_ready` event to decide whether to offer approval; `footer.approvable` is only meaningful in `in_review`.

**`POST /gate`** body is a `gate_decision` event (it needs `type`, `event_id`, `decision`, `version`, `brief_hash`). The reply is `{accepted, state, version, brief_hash, baseline, blockers[]}`; `accepted` is true only when the session is `APPROVED`. Outside `BRIEF_REVIEW` the verdict is `accepted: false` with a `gate_blocked` notice in the stream. In an enterprise session the approval counts only from a contributor with the role `approver` (the session creates `approver` and `commenter` contributors): send `"contributor_id": "approver"` in the body; until then the reply lists `Waiting for approval from: approver`, and approving does not change the hash. Approving the same, unchanged approved Brief again is a no-op.

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
| 404 | `not_found` | unknown session, version, item or language; another tenant's id; `/budget` in token mode |
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

# 3. say what you want: runs understand_turn, situate and write_card, answers with the events it produced
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

(The ids, version 13 and the hash above are from one mock run; yours differ. Every value in `curl` is a placeholder; do not put real secrets in a command line or a document.) If you approve with a wrong version or hash, or while a blocker stands, the reply is `{"accepted": false, ..., "blockers": [...]}` and nothing changes. A later edit voids only the sections it touches and returns the session to `BRIEF_REVIEW`; `POST .../events` with `{"type":"command","event_id":"u1","name":"undo"}` reverts the latest change as a new version.

## Known gaps

* `GET /brief/view?lang=` is validated but the real service ignores it and uses the session locale.
* `question_n` and `question_of_about` on `SessionSummary` are always 0.
* `footer.approvable` in the view model is true outside `in_review`; trust `header.status` and the `brief_ready` event.
* `budget_warning` and `assistant_delta` events exist in the vocabulary but are never emitted; the Compass spend counter is not reconciled with the gateway ledger (read the ledger with `just budget`).
* Fixed since the first version of this page: `GET /budget` now answers 200 (the service attribute was renamed `budget_guard`), and the enterprise multi-approver gate completes (`meta.approvals` is excluded from the hash).
