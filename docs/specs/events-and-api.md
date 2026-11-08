# Events and the Context API

The HTTP contract of the Context Engine: routes, request and response shapes, the event stream, idempotency, errors, authentication and upload rules. The machine-readable form is [../api/openapi.json](../api/openapi.json) (exported by `uv run python scripts/export_schemas.py`, kept current by a test); the event and card models are in [../api/schemas/](../api/schemas/). How the service is built and run: [../packages/context-api.md](../packages/context-api.md). The rules behind the events (gates, runs, hand-off): [../architecture/flow-and-gates.md](../architecture/flow-and-gates.md).

There is no chat UI here: a client (the CLI, a simulator, later a web or mobile app) sends **inbound events** and renders **outbound events**.

## Overview

```
client                                   Context API                              flow + engines
  | POST /v1/sessions                      |                                          |
  |--------------------------------------->| create_session -------------------------> Discover.start (canned greeting)
  |<--- 201 {session, events: [greeting]}--|                                          |
  | POST /v1/sessions/{id}/events  (tap)   |                                          |
  |--------------------------------------->| handle_event ---------------------------> Discover turn (0 or 1+ LLM calls)
  |<--- 200 {events, state, job, last_seq}-|                                          |
  | POST .../events  (gate_decision approve Gate 1)                                   |
  |--------------------------------------->| handle_event -> enqueue plan job          |
  |<--- 202 {events: [state_changed ...], job}                                        |
  | GET  .../stream   (SSE, Last-Event-ID) |   worker: research -> build -> Gate 2    |
  |<=== progress, plan_ready, gate =========                                          |
  | POST .../events  (gate_decision approve Gate 2)                                   |
  |<--- 200 {events: [state_changed, spec_ready]}   (commit to the project repo first)|
```

## Authentication

Every route except `/healthz` and `/readyz` needs a bearer token. There are two modes, chosen by the deployment (`API_TOKENS`); the client always sends `Authorization: Bearer <token>`.

**Dev headers (the default; local use only).** The token is the one shared `API_DEV_TOKEN` (compared in constant time) and the caller states who it is:

| Header | Meaning |
| --- | --- |
| `X-Tenant-Id` | the tenant every database transaction of the request is bound to; letters, digits, `_` and `-`, at most 64 characters, starting with a letter or digit (it becomes a directory name in the hand-off repository) |
| `X-User-Id` | the caller (letters, digits and `_ . : @ + -`, at most 128) |
| `X-Roles` | optional, comma separated gate roles the caller acts under, for example `client_product_owner,delivery_lead` (at most 20) |

> **These headers are trusted identity.** Whoever holds the dev token can be any tenant, any user and any role, so one caller can play both approvers of a two-approver Gate 2, and the owner / `requester` / approver rules below rest on nothing but what the caller writes. That is acceptable on a developer's own machine and **must never face a network**: `python -m mde_context_api` refuses to bind a non-loopback address in this mode (exit code 2).

**Token table (production-shaped).** When the server has `API_TOKENS` (a JSON object, token to `{tenant_id, user_id, roles, ...}`) the presented token *is* the identity: the server looks it up (all entries compared as SHA-256 digests in constant time) and uses the configured tenant, user and roles. The `X-Tenant-Id` / `X-User-Id` / `X-Roles` headers are **ignored** (not required, not validated, not trusted), the dev token is not accepted, and an unknown token is the same `401` as every other credential failure. A token entry may also bind which sessions its holder may open (see `POST /v1/sessions`). The configuration is in [../packages/context-api.md](../packages/context-api.md).

A missing or malformed credential is a `401` with one fixed body, whichever part was wrong. SSO is out of scope; this is the seam it replaces.

**Who may do what.** The user who created a session is its **owner** and, only the owner, is the `requester`: a `requester` entry in `X-Roles` is ignored. A different user who holds a role that one of the session's gates requires (from the policy) may read the session and decide *their* gate. Everyone else, in the same tenant or another, gets `404`: the answer for a session they may not see is word for word the answer for an id that never existed.

**Tenant isolation.** Another tenant's session ids are never found: reads, the stream and event posts all return `404`.

## Routes

| Route | Purpose |
| --- | --- |
| `POST /v1/sessions` | create a session; returns the session view and the greeting events |
| `GET /v1/sessions/{id}` | where the session stands: state, plan phase, pending card or open gate, slot summary, plan job |
| `POST /v1/sessions/{id}/events` | send one inbound event; returns the immediate outbound events |
| `GET /v1/sessions/{id}/stream` | outbound events as server-sent events, with `Last-Event-ID` replay |
| `GET /v1/sessions/{id}/brief` | the current (or `?version=N`) Brief |
| `GET /v1/sessions/{id}/plan` | the Plan view of the current (or `?version=N`) spec |
| `GET /v1/sessions/{id}/spec` | `APP_SPEC.md` of the current (or `?version=N`) spec, as `text/markdown` |
| `GET /v1/sessions/{id}/versions` | every Brief and spec version with hash and status |
| `GET /healthz` | liveness: the process is up (no authentication, touches nothing) |
| `GET /readyz` | readiness: the database answers and the budget ledger is sound (no authentication); `503` otherwise |
| `GET /v1/budget` | the gateway's LLM budget totals (development; no per-scope breakdown) |

`GET /docs`, `GET /redoc` and `GET /openapi.json` serve the interactive documentation **only when `API_DOCS=1`** (or `python -m mde_context_api --docs`): they need no credentials, so they are off by default and answer `404`. The schema itself is [../api/openapi.json](../api/openapi.json), exported offline.

### `POST /v1/sessions`

```json
{ "mode": "enterprise", "policy_id": "regulated-bank", "org_profile": { "brand": { "name": "Acme" } } }
```

* `mode`: `guided` (default) or `enterprise`.
* `policy_id` (optional): overrides the mode's default. The default for `guided` is `guided-default`; for `enterprise` it is `DEFAULT_ENTERPRISE_POLICY` (`enterprise-it-services-firm`). The policy must exist and be a policy of the same mode, otherwise `422`.
* **Bound tenants.** Without a binding the caller chooses `mode` and `policy_id`, as above. If the deployment binds the caller's tenant (`TENANT_POLICIES`) or its token (an `API_TOKENS` entry) to a set of policies, the request must lie inside it: a mode or policy outside the set is `403` (`forbidden`, plain message naming what is allowed), and a `mode` or `policy_id` the request leaves out takes the binding's default (so a tenant bound to Enterprise policies gets an Enterprise session from `{}`). When both a tenant and a token binding apply, both must hold. This is how an organisation keeps its users from opening a Guided session to skip its banned libraries, regions, multi-approver gates and research limits.
* **Rate limit.** Session creation is limited per caller and per tenant (default 10 per minute and 100 per hour each): over the limit is `429` (`too_many_requests`) with a `Retry-After` header in seconds. A refused request is not counted.
* `org_profile` (optional, Enterprise only): the organisation's standing answers, a JSON object of at most `max_org_profile_bytes` (64 KB). It is stored for the tenant **before** the session starts (replacing the old one) and is the starting context of this session: Discover turns the documented keys into `assumed` slots with source `org_profile` and says so in one `notice` (`code: "org_profile_loaded"`, right after the greeting). Without `org_profile`, an Enterprise session starts from the tenant's stored profile, if any; a Guided session and another tenant's session never see it. Unknown keys and values are dropped and values that look like secrets or instructions are never kept. The keys are listed in [../packages/discovery.md](../packages/discovery.md) ("Org Profile").

`201`:

```json
{
  "session": { "session_id": "ses_01...", "app_id": "app_01...", "mode": "guided", "policy_id": "guided-default",
               "state": "DISCOVERING", "plan_phase": null, "pending_card": null, "gate": null, "slots": {},
               "last_seq": 1, "active_job": null, "...": "..." },
  "events": [ { "type": "assistant_message", "seq": 1, "text": "Hi! ...", "chips": ["An online shop", "..."], "canned": true } ],
  "org_profile_saved": false
}
```

### `GET /v1/sessions/{id}`

Returns the `SessionSnapshot`: `state` (`DISCOVERING`, `BRIEF_REVIEW`, `PLANNING`, `PLAN_REVIEW`, `HANDED_OFF`, `PAUSED`, `STOPPED`, `ARCHIVED`), `plan_phase` (`research` or `build` while a run is active), `origin`, `pending_card` (the card to show), `gate` (the open gate with its page, buttons and approval status), the Brief / spec / hand-off version numbers, `paused_for` (`"budget"` when the session is PAUSED because the budget cap was reached, see the Events section; null for a pause the user asked for), `slots` (value, state, source; `state` is `disputed` while two stakeholders disagree), `queued_messages`, `last_seq` (the stream cursor), `allowed_transitions` and `active_job` (`{id, state, phase, cancel_requested}`). Use it to resume: it says where the user left off.

### `POST /v1/sessions/{id}/events`

The body is one **inbound event** (a JSON object with a `type`):

| `type` | Fields | Notes |
| --- | --- | --- |
| `user_message` | `event_id`, `text` | free text; costs 1 LLM call (a starter chip is typed text too) |
| `option_selected` | `event_id`, `card_instance_id`, `option_ids`, `other_text`, `you_decide`, `skipped` | a tap on a card; costs 0 LLM calls |
| `upload_added` | `event_id`, `upload_id`, `filename`, `media_type`, `size_bytes`, `text`, `data_ref` | see "Uploads" |
| `gate_decision` | `event_id`, `gate` (`brief` or `plan`), `decision` (`approve`, `change`, `reject`), `version`, `hash`, `comment`, `role`, `approver_id` | bound to the `version` and `hash` the user saw |
| `ui_action` | `event_id`, `action`, `target`, `value` | `stop`, `undo`, `restart`, `pause`, `resume`, `finish_with_defaults`, `toggle_assumption`, `toggle_research_item`, `toggle_feature`, `edit_row`, `archive`, `idle` |

`event_id` is chosen by the client (1 to 128 characters: letters, digits and `_ . : -`; use a ULID such as `evt_01...`).

Response, `200` (processed) or `202` (processed **and** a plan job was enqueued, for example Gate 1 approved):

```json
{
  "session_id": "ses_01...",
  "events": [ { "type": "state_changed", "from_state": "BRIEF_REVIEW", "to_state": "PLANNING", "plan_phase": "research", "seq": 14 },
              { "type": "assistant_message", "text": "Great. I'm researching similar apps ...", "seq": 15 } ],
  "state": "PLANNING",
  "plan_phase": "research",
  "job": { "id": "job_01...", "state": "queued", "phase": null, "cancel_requested": false },
  "last_seq": 15
}
```

Discover turns answer **synchronously**: their events are in `events`. Planning (research, then the build) runs in the background; its progress arrives on the stream.

**Rules the API keeps for you** (spec 2.2): an approval is bound to `version` and `hash` (a stale pair is not counted and the current gate is shown again with a diff: a `notice` with code `stale_gate`, status `200`); a typed "yes" approves only when a gate is open and the engine is confident (the approval records `via: "typed"`); silence never approves (`idle` at a gate does nothing); the `requester` is the session owner; where a gate needs several roles each is approved by a different person; `spec_ready` is emitted only after Gate 2 approval of the current hash by every required role and a successful commit.

**The budget pause.** When the budget cap is reached the session does **not** start (or continue) planning: it goes to `PAUSED` (`paused_from` says where; `paused_for` is `"budget"`), one `notice` with code `budget_cap_reached` (level `warn`) says so in plain words, and nothing is built. The check runs before the plan job is enqueued (Gate 1, a change at Gate 2 or after hand-off, the answer to a builder question) and before each phase of a run in flight, so a run that finds the budget gone stops cleanly between research and build. Guided says it briefly; Enterprise adds the roles that can lift it. **Resume** with `ui_action resume`: the owner may, and so may an approver (a user holding a required role of either gate) while the session is held for the budget; it is refused with the same notice while the budget is still out, and otherwise starts what was held. A change request that arrived while the budget was out is not applied: the user asks again after resuming.

**Two stakeholders disagree (Enterprise).** When a different person gives a different answer to a slot someone else already answered (an approver's comment on a "change" or "reject" decision, or the requester overruling an approver), the notice `slot_conflict_resolved` says which value was used and why (the policy's `role_precedence`; both answers and roles are named and both stay on record); when precedence cannot decide, `slot_disputed` (level `warn`) says the earlier value stays, the slot's `state` becomes `disputed` and the requester must choose (change that row, or say which to use: `slot_dispute_settled`). While a slot is disputed no gate completes: approving shows `dispute_blocks_approval` and the gate again.

**Replying to a question the builder asked.** While a run waits for an answer (`state` DISCOVERING with a `plan_phase`), a tap or an answer resumes it even when it leaves the value as it was. Chat that does not answer keeps the question and adds a `notice` `answer_needed`; after a few such replies an assistant message offers the chip "Use the default" (typing it takes the recommended option).

`idle` is how a client reports that the user has not answered a card for a while: the first time the session nudges once and shows the card again; the next `idle` takes the recommended option (marked `assumed`, with its reason) unless the slot must be confirmed by a person.

### `GET /v1/sessions/{id}/stream`

`text/event-stream`. Every outbound event is one frame:

```
retry: 3000

id: 17
event: assistant_message
data: {"type":"assistant_message","event_id":"evt_01...","session_id":"ses_01...","seq":17,"text":"...","chips":[],"canned":true,"language":"en"}

: keepalive

```

* `id` is the event's per-session `seq` (1, 2, 3, ... with no gaps); `event` is its `type`; `data` is the whole event as one line of JSON.
* **Replay.** Send `Last-Event-ID: <seq>` (what `EventSource` does on reconnect) or `?after=<seq>`: you get every event after that seq, then the live tail. The header wins over the query. A `Last-Event-ID` is usable when it is ASCII digits only and at most 12 of them (larger numbers are clamped to the largest seq a session can hold); anything else, including a very long value, is ignored and the stream starts from the beginning: never an error. `after` must be between 0 and 2147483647 (`422` otherwise).
* **Live tail.** The server polls the log with one short transaction about every 0.5 s (`SSE_POLL_SECONDS`) while events flow; an idle stream backs off (each empty poll waits half as long again, up to `SSE_MAX_POLL_SECONDS`, 2 s) and returns to 0.5 s as soon as an event is sent. **No database connection is held while a stream is open or idle.** A comment line `: keepalive` goes out every 15 s of silence.
* **Ending.** A stream ends after `SSE_MAX_SECONDS` (900 s); reconnect with `Last-Event-ID`. Query parameters for bounded reads: `follow=false` (send what is stored, then close), `limit=N` (close after N events), `timeout=S` (close after S seconds).
* **Cap.** A caller may hold at most 5 live streams at once (`API_MAX_STREAMS_PER_PRINCIPAL`); the next is `429` (`too_many_streams`, `Retry-After: 5`, a plain message). The slot comes back when the stream ends for any reason, including the client hanging up. A bounded read (`follow=false`) holds no slot.
* If the session becomes unreadable mid-stream the server sends one `event: stream_error` frame (`{"code","message"}`) and closes.
* A session that does not exist or is not yours is a plain `404` JSON error *before* any streaming starts.

### Documents

* `GET /brief` returns the `Brief` (rows, assumptions, `version`, `hash`, `status`). `404` until the interview has produced one.
* `GET /plan` returns the `PlanView` (screen map, look preview, data in plain words, what research added with toggles, real versus mocked, roles, the diff since Gate 1, backlog, and for Enterprise the approver status, comments and full-file view).
* `GET /spec` returns the file **byte for byte** with `Content-Type: text/markdown; charset=utf-8` and headers `X-Spec-Version`, `X-Spec-Hash` (the spec hash: sha256 of the body with its front-matter `hash:` value blanked, equal to the `hash:` line inside the file; `mde_spec_loader.hashing.verify_hash(body)` checks it), `X-Spec-Status` (`draft`, `approved`, `handed_off`, `superseded`), `X-Spec-Git-Sha` (once committed) and `ETag`. It serves drafts too; only a version with status `handed_off` is in the project repository.
* `GET /versions` returns `{"versions": [{"kind": "brief" | "spec", "version", "hash", "status", "git_sha", "created_at", "approved_at"}]}`.

For these three `?version=N` selects a version; an unknown version is `404`.

### Ops

* `GET /healthz`: `{"status": "ok", "version": "0.1.0"}`.
* `GET /readyz`: `200 {"status": "ready", "checks": {"database": "ok", "ledger": "ok"}}`, or `503 {"status": "not_ready", "checks": {"database": "unreachable", "ledger": "failed"}}` (each check answers on its own; the reason is never shown). `ledger` is the budget ledger's integrity check (`integrity_problem()`: a deleted or truncated ledger would silently reset the USD 6.00 budget); it is present when the gateway runs in process (an HTTP gateway keeps its own ledger and its own `/readyz`). This route has no authentication, so it never shows the spend, a cap, the tripwire or any number: those are `GET /v1/budget`, which needs the bearer token.
* `GET /v1/budget`: totals only (`cap_micros`, `effective_cap_micros`, `spent_micros`, `reserved_micros`, `live_calls`), for development. All amounts are integer micro-dollars; mock mode spends nothing. There is **no per-scope breakdown and no tripwire**: the gateway's own ledger lists every tenant's `session:<id>` spend, so that view stays on the service-to-service gateway API (`llm-gateway-api`, whose `/v1/budget` is unchanged) and never reaches a caller of this API.

## Idempotency

Every inbound event has a client-chosen `event_id`. A repeated id is **not processed again**: the response is the original one, with the same event ids and the same `seq`s, and nothing new is appended to the log (a repeat sent while the first is still running answers at once from what is stored, or with a `notice` `message_in_progress`). Retry freely after a timeout or a dropped connection: resend the same event with the same id. Ids are scoped to the session (two sessions may use the same id). Events of one session are processed one at a time.

## Errors

Every non-2xx response has the same body (`ErrorBody`):

```json
{ "code": "hash_mismatch", "message": "That version is out of date. Open the latest page and decide again." }
```

Clients branch on `code`; `message` is for people and may change. `request_id` is added to every 5xx body (and the `X-Request-Id` response header is on every response, a well-formed request header of that name is kept); `details` lists `{field, problem}` pairs for request validation (never the offending value). An unexpected failure is a `500 internal_error` with the request id and **no stack trace**.

| Status | `code` | When |
| --- | --- | --- |
| 401 | `unauthorized` | missing or invalid token or identity headers (always the same body) |
| 403 | `forbidden` | you may read this session (you hold one of its approver roles) but may not do that; or the mode or policy of a new session is outside what your tenant or token may start |
| 404 | `not_found` | no such session, a session of another tenant, or a session you hold no role in; no document yet; no such version; no such route |
| 405 | `method_not_allowed` | wrong method |
| 409 | `conflict` | the session changed under the request (resend), a run is still stopping |
| 409 | `hash_mismatch` | the version or hash is not the current one (raised for direct callers; through the API a stale gate decision is a friendly notice instead) |
| 409 | `gate_error` | you may not decide that way: wrong role, one person for two roles, `approver_id` is not you, the requester approving for another role |
| 409 | `invalid_transition` | not possible in the current state |
| 409 | `cancelled` | the request was cancelled |
| 413 | `payload_too_large` | the body (`API_MAX_BODY_BYTES`, default 1 MB), an upload's text (512 KB) or an image's declared size (10 MB) is over the limit |
| 415 | `unsupported_media_type` | an upload that is not text, markdown or an image (PDFs: see below) |
| 422 | `validation_failed` | the request is not valid: a field, a policy that does not exist or has the wrong mode, an unknown event type, a bad `event_id` |
| 429 | `budget_exceeded` | the LLM budget is used up |
| 429 | `too_many_requests` | too many sessions created by you or your tenant; `Retry-After` says when to try again |
| 429 | `too_many_streams` | you already hold the maximum number of live event streams; `Retry-After: 5` |
| 500 | `config_error` | the service is not set up correctly |
| 500 | `internal_error` | anything unexpected (quote `request_id`) |
| 502 | `gateway_error` | the AI service could not answer |
| 503 | `handoff_failed` | the approved spec could not be saved (nothing was recorded; retry; `Retry-After: 2`) |

## Uploads

An `upload_added` event carries what the client has already extracted; the API never fetches a URL a user supplies and parses no files. **Text, Markdown and images only.**

* `media_type` `text/plain` or `text/markdown` (parameters such as `; charset=utf-8` are ignored): put the content in `text` (untrusted: it is data, never instructions; at most 512 KB).
* `media_type` `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/heic`, `image/heif` or `image/bmp`: send **no `text`**, only an opaque `data_ref` (letters, digits and `_ . / : @ + = -`, at most 512 characters) naming where the client stored the file. **Image bytes never enter an event**: a `data:` URI, base64 or any inline content is refused (`422`). `size_bytes` may not exceed 10 MB.
* **PDFs and every other type** are refused with `415` and a friendly message: *"PDFs are not supported yet. Please paste the text you want me to use into the chat instead."* (other types: *"I can read plain text, Markdown and images, but not that kind of file. Please paste the text into the chat, or add an image."*). A PDF is recognised by `application/pdf`, or by a `.pdf` name sent as `application/octet-stream`.
* `filename` is at most 255 characters with no control characters.
* The id-like fields have a shape, not just a length: `card_instance_id`, each of `option_ids` (at most 100), `upload_id`, `target` and `selected_screen_id` are 1 to 200 characters of letters, digits and `_ . : -`; `event_id` is at most 128 starting with a letter or digit; `media_type` is `type/subtype` with at most four simple parameters (`text/plain; charset=utf-8`), at most 200 characters (or empty, which is an unknown type and is answered `415` in words); `data_ref` is at most 512 characters of letters, digits and `_ . / : @ + = -` (the length is the contract's, the character set and the "never the file itself" message the API's); a gate decision's `hash` is exactly the 64 lowercase hex digits of the hash it was shown, `version` is an integer from 0 to 2147483647, `role` and `approver_id` have the shape of a role name and a user id. Anything else is `422` (the error lists the field, never the value).

## Secrets

Everything free-text in an inbound event (the message, "something else" text, an upload's text **and file name**, a gate comment) is screened **before** anything is recorded: secret-looking spans (API keys, tokens, passwords, connection strings, private keys) are replaced by `[REDACTED]` (hidden characters are dropped and the text is folded first, so a key split with zero-width characters or typed in full-width letters is still found). The stored event, the approval comment shown to other approvers, and what the engines see are the screened versions. The reply carries a `notice` with code `secret_redacted`. The id-like fields above are screened the same way, because a key fits an id's character set: a `card_instance_id`, option id, upload id or media type, `data_ref`, role, approver or ui `target` that holds a secret is stored as `[REDACTED]` (it then matches nothing, as a made-up id would), and an `event_id` that holds one is replaced by a stable one-way stand-in (`evt_redacted_...`) so a resend is still a resend. A `hash` is hex by contract and is never scanned.

## Event catalog

Outbound events all have `event_id`, `session_id`, `seq`, `at` and `in_reply_to` (the inbound event id, when there is one); the examples below show the type-specific fields.

| `type` | When | Type-specific fields |
| --- | --- | --- |
| `assistant_message` | a reply | `text`, `language`, `chips` (suggested short replies), `canned` (no LLM used) |
| `card` | a question | `card` (a `CardInstance`: `instance_id`, `card.id`, `card.slot`, `card.type`, `card.text`, `card.options[]`, `card.skip`, `progress`) |
| `progress` | while planning | `phase` (`research` or `build`), `step`, `label`, `done`, `total` |
| `gate` | Gate 1 or Gate 2 opens or re-opens | `gate`, `version`, `hash`, `page` (`kind: brief` with a `Brief`, or `kind: plan` with a `PlanView`), `buttons`, `required_roles`, `approved_roles`, `diff_lines`, `single_button` |
| `plan_ready` | the spec is built | `version`, `hash` |
| `spec_ready` | Gate 2 approved and committed | `app_id`, `version`, `hash`, `git_sha`, `path`, `repo_dir` |
| `state_changed` | the session moved | `from_state`, `to_state`, `plan_phase` |
| `notice` | a friendly, non-fatal message | `code` (stable), `text`, `level` (`info` or `warn`) |

Examples (abridged):

```json
{"type":"card","seq":3,"card":{"instance_id":"crd_01...","card":{"id":"Q-type-01","slot":"idea.archetype","type":"choice3",
  "text":"What are you building?","options":[{"id":"A","label":"An online shop","recommended":true}],"skip":{"enabled":true}},
  "progress":{"asked":1,"cap":10}}}

{"type":"gate","seq":12,"gate":"brief","version":1,"hash":"3f9a...","buttons":["Looks right","Change something"],
 "required_roles":["requester"],"approved_roles":[],"diff_lines":[],"single_button":false,
 "page":{"kind":"brief","brief":{"version":1,"hash":"3f9a...","rows":[{"key":"building","text":"..."}]}}}

{"type":"progress","seq":16,"phase":"research","step":"search","label":"Looking at similar apps","done":2,"total":4}

{"type":"plan_ready","seq":24,"version":1,"hash":"a84d..."}

{"type":"spec_ready","seq":31,"app_id":"app_01...","version":1,"hash":"a84d...","git_sha":"6340...","path":"APP_SPEC.md",
 "repo_dir":"data/projects/tenant-a/app_01..."}

{"type":"notice","seq":9,"code":"stale_gate","level":"info","text":"That page was out of date, so I did not count your answer. Here is the latest version."}
```

Notice codes the flow itself uses (stable; the text is not): `stale_gate`, `gate_not_open`, `not_an_approval`, `submitted_for_approval`, `waiting_for_approvers`, `message_queued`, `message_in_progress`, `run_still_stopping`, `session_paused`, `session_archived`, `nothing_to_stop`, `cannot_pause_while_planning`, `wait_for_plan`, `research_unavailable`, `build_failed`, `no_change_needed`, `change_not_understood`, `frontend_notify_failed`, `action_not_available`, `budget_reached`, `budget_cap_reached`, `answer_needed`, `content_flagged`, `slot_conflict_resolved`, `slot_disputed`, `slot_dispute_settled`, `dispute_blocks_approval`, `secret_redacted`; engines add their own (for example `unsafe_declined`, `stale_card`).

## The run and the order of events

1. Create a session: `assistant_message` (the greeting).
2. Talk: `card`, `assistant_message`; a tap costs no LLM call.
3. When the interview is complete: `state_changed` (to `BRIEF_REVIEW`) and `gate` (the Brief).
4. `gate_decision` approve: `state_changed` (to `PLANNING`), `assistant_message`; the response is `202`.
5. In the background: `progress` events, `plan_ready`, `state_changed` (to `PLAN_REVIEW`), `gate` (the Plan).
6. `gate_decision` approve: `state_changed` (to `HANDED_OFF`), `spec_ready`.
7. Afterwards the same session takes changes (typed, tapped or by upload): a **light** change is applied directly, a **heavy** one re-opens Gate 2. A change is light only when the requester alone is Gate 2 and nothing about it was inferred or exceeds the policy's limits; where anyone else must approve the plan, every change returns to Gate 2.

`ui_action stop` during `PLANNING` cancels the run and returns to where it started (the gate is shown again); messages typed during `PLANNING` are queued and applied when the plan is ready.
