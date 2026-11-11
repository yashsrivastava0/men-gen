# Research agent HTTP API (`mde_research_agent`)

The Research agent (agent 2 of the Context Engine) runs in the foreground after Gate 1. This page is the contract the test front end (and later the full front end) codes against. Design: `docs/architecture-v3/13-research-agent-design.md`. Package page: `agents/research/README.md`.

Base URL (dev): `http://127.0.0.1:8120`. Prefix: `/v1/research`. All bodies are JSON, UTF-8. The server allows cross-origin calls from the test console (explicit origins, `Authorization`, `Content-Type`, `X-Tenant-Id`, `X-User-Id`, `Last-Event-ID`).

## Auth (dev)

Same dev headers as Compass: `Authorization: Bearer dev-token` (or `RESEARCH_DEV_TOKEN`), `X-Tenant-Id`, `X-User-Id`. The console reuses the values from its Settings dialog. A wrong or missing token is one identical `401 {code: "unauthorized"}`.

## Errors

Always `{"code": "<machine_code>", "message": "<plain words>"}`. Codes used by the UI: `unauthorized` (401), `not_found` (404), `run_active` (409, one run per program at a time; `message` names the run), `bad_request` (422), and the export refusals (422): `not_an_export`, `not_format_2`, `not_approved`, `hash_mismatch`, `missing_research_view`. Show `message` to the user; never a stack trace.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /v1/research/config` | `{mode: "mock" \| "live", search_provider: "tavily" \| "serper" \| "fake", keys: {tavily: bool, serper: bool}, model: string, checker_vendor: "same" \| "different" \| "none"}`. Booleans only, never a key. The UI shows a banner when `mode` is `mock`. |
| `POST /v1/research/runs` | Start a run. Body `{program_id?: string, brief_export: <the Compass format-2 export object>}`. `201 {run_id, program_id, status, events_url}`. 409 `run_active` if the program already has a run in progress. 422 with a refusal code if the export is not an approved format-2 export (the three hashes must agree). The export can be up to 8 MB. |
| `GET /v1/research/runs/{run_id}` | The run **snapshot** (below). A reloaded page draws from it, then tails events from `last_seq`. |
| `GET /v1/research/runs/{run_id}/events` | Server-sent events, resumable: send `Last-Event-ID: <seq>` or `?after=<seq>`. Replays stored events, then tails until a terminal event (`run.finished`, `run.skipped`, `run.failed`), then the stream ends. Use `fetch` and a stream reader, not `EventSource` (it cannot send auth headers). |
| `POST /v1/research/runs/{run_id}/skip` | Stop researching. `202 {status}`. The run commits `skipped` with the findings verified so far. Always available while running. |
| `POST /v1/research/runs/{run_id}/targets/{target_id}/cancel` | Stop spending on one topic. `202`. The topic becomes `cancelled` and appears as a gap with reason `user_cancelled`. |
| `GET /v1/research/runs/{run_id}/result` | The committed `research.json` of that run (404 until committed). |
| `GET /v1/research/programs/{program_id}/latest` | The newest committed `research.json` (immutable, hashed; this is what "Download research.json" saves). 404 if none. |
| `GET /v1/research/programs/{program_id}/review` | The findings page data (below): the same content with per-item links, dates and the owner's rejections applied. |
| `POST /v1/research/runs/{run_id}/items/{item_id}/reject` | Body `{reason?: string}`. `200 {item_id, rejected: true}`. Rejections are an overlay: the committed file never changes; `review` shows `rejected: true` and the item is withdrawn from downstream views. |
| `GET /healthz` | `{ok: true}`, no auth. |

## Event stream (`research.event.v1`)

SSE frame: `id: <seq>`, `event: <type>`, `data: <Event JSON>`, blank line. A `retry: 3000` frame first and `: keepalive` comments every 15 s. Events are stored; replay after a reload loses nothing. `seq` starts at 1 and is contiguous per run: if a client sees a gap it should refetch the snapshot.

```json
{"id": "evt_01...", "seq": 42, "ts": "2026-11-11T12:30:05Z", "run_id": "rrn_01...", "type": "query.issued",
 "target_id": "T-03", "round": 1, "phase": "search",
 "label": "Searching: yoga studio app features Bengaluru",
 "detail": {"query": "yoga studio app features pricing Bengaluru", "variant": "descriptive", "lang": "en", "country": "in", "provider": "tavily"},
 "counters": {"searches_used": 7, "searches_planned": 28, "searches_ceiling": 40, "pages_read": 9, "facts_verified": 5,
              "targets_done": 2, "targets_total": 8, "elapsed_s": 41, "eta_s": 70},
 "schema": "research.event.v1"}
```

`label` is user-facing text built by the server from templates and the Brief's own words: **show it as is**. Events carry domains, titles and counts, never page text or quotes. `counters` is on every event; draw the progress bar and clocks from the latest one (`eta_s` may be `null`).

| Type | When | `detail` fields | What the UI shows |
| --- | --- | --- | --- |
| `run.started` | intake done | `load_class` (`focused`/`standard`/`deep`), `load_score`, `minutes`, `plan_source`, `degraded`, `depth_word` | "Researching your idea", the depth in plain words (`depth_word`) and the time box |
| `plan.ready` | plan done | `targets: [{id, label, job, lane, priority, status}]`, `declined: [string]` (topics "we already know"), `planned_searches`, `plan_source`, `country` | the topic list; declined topics as "We already know this" |
| `target.started` / `target.covered` / `target.not_found` / `target.cancelled` | per topic | `job`, `topic`, `slots_filled`, `slots_required`, `queries` (not_found) | topic row status and a per-topic cancel control while it runs |
| `query.issued` | each search | `query`, `variant` (`literal`/`descriptive`/`local`), `lang`, `country`, `provider`, `domain?` | feed line |
| `search.results` | search returned | `count`, `domains[]`, `query` | feed line with domains |
| `page.reading` / `page.read` / `page.skipped` | per page (throttled) | `domain`, `title`, `chars?`, `reason?` (`no_content`, `paywall`, `blocked`, ...) | feed line; skipped pages shown quietly |
| `fact.found` | extraction returned (unchecked) | `kind`, `label`, `topic` | item marked "checking" |
| `fact.verified` | code check passed | `kind`, `label`, `domain`, `level?` | the item moves to "checked" |
| `fact.rejected` | check failed | `reason`, `topic`, `label?` (the label of the unchecked item, when one was shown as "checking") | counted, not shown as a finding; the matching "checking" row is closed |
| `entity.discovered` | competitor, provider or authority found | `name`, `kind?` | "Found: NAME" chip |
| `round.completed` | end of a round | `round`, `slots_filled`, `slots_required`, `new_slots` | coverage bar |
| `gap.detected` | after the audit | `targets[]`, `count` | "Taking a second look at N topics" |
| `warning` | slow provider, timeout, rate limit, budget stop | `code` (`taking_longer`, `slow_provider`, `rate_limited`, `provider_down`, `no_results`, `no_keys`, `model_failed`, `budget_stop`), `message` | plain-language note, never an error dump; the first three read as "taking a bit longer" |
| `run.finished` / `run.skipped` | end | `status` (`complete`/`partial`/`skipped`), `facts`, `pages`, `searches`, `usd`, `elapsed_s` | counts; open "What we found" |
| `run.failed` | end | `status`, `reason`, `code` | plain-language failure; offer to retry |

## Run snapshot (`GET /runs/{run_id}`)

```json
{"run_id": "rrn_01...", "program_id": "...", "status": "running", "phase": "search", "round": 1,
 "started_at": "...", "finished_at": null, "load_class": "standard", "load_score": 27, "depth_word": "a standard-depth study",
 "plan_source": "mixed", "input_quality": {"requests": {"trusted": 7, "template": 0, "thin": 0}, "degraded": true, "geo_uncertain": false, "flags": []},
 "counters": {"searches_used": 7, "...": 0},
 "targets": [{"id": "T-01", "label": "Comparable produce apps", "job": "features", "lane": "blueprint", "priority": 95, "status": "running", "slots_filled": 1, "slots_required": 2}],
 "declined": ["a currency symbol"], "entities": ["NAME", "..."], "feed": [ /* the last 60 events */ ],
 "last_seq": 42, "result_status": null, "content_hash": null, "reason": null}
```

`status`: `queued`, `running`, `committing`, `complete`, `partial`, `failed`, `skipped`, `interrupted`. Target `status`: `queued`, `running`, `covered`, `not_found`, `cancelled`, `skipped`, `partial`. A `partial` run finished with some topics unfinished; say why using `reason` and the gaps.

## Findings (`GET /programs/{program_id}/review`)

```json
{"run_id": "...", "status": "complete", "content_hash": "...", "profile": {"load_score": 27, "load_class": "standard", "rounds": 2},
 "coverage": {"targets": 12, "covered": 9, "not_found": 2, "slots_required": 30, "slots_filled": 24},
 "budget": {"searches_used": 31, "pages_read": 58, "seconds": 241, "usd": "0.31", "usd_micros": 310000, "failures": 1},
 "input_quality": {"...": "..."},
 "items": [ReviewItem], "norms": [Norm], "suggestions": {"features": [Suggestion], "screens": [], "fields": [], "states": [], "validations": []},
 "confirm": [{"item_id": "RI-031", "why": "..."}], "gaps": [{"target_id": "T-07", "reason": "not_found", "label": "...", "queries": ["..."]}],
 "conflicts": [{"topic": "...", "item_ids": ["RI-004", "RI-009"], "note": "..."}],
 "slices": {"blueprint": {"item_ids": ["RI-001"], "digest": "text"}, "integrations": {}, "design": {}, "screens": {}, "mock": {}, "verify": {}}}
```

`ReviewItem` (= the `research.v4` item plus convenience fields):

```json
{"id": "RI-004", "job": "services", "spec_need": "Contract and integrations", "kind": "service_option", "lane": "integrations",
 "label": "Razorpay payments", "claim": "<= 40 words, plain text", "use_note": "<= 15 words", "slot": "providers",
 "impact": "high", "confidence": "high", "tier": "default", "flags": ["legal-adjacent", "single-source", "stale"],
 "applies_to": [{"kind": "INT", "id": "payments", "relation": "expects"}],
 "sources": [{"id": "S-001", "domain": "docs.example.com", "source_class": "official", "url": "https://...", "retrieved_on": "2026-11-11", "published_on": "2026-05-02"}],
 "independent_domains": 2, "prevalence": {"seen_in": 4, "of": 5, "level": "core"},
 "recency": {"newest_source": "2026-05-02", "age_months": 6, "trend": "rising"},
 "request_ids": ["RR-002"], "target_id": "T-03", "data": {"...": "fields by kind"}, "rejected": false}
```

`Norm`: `{area: "feature"|"screen"|"flow"|"entity"|"integration"|"design", key, label, prevalence: {seen_in, of, level}, recency: {newest_source, age_months, trend}, item_ids: [], matches_brief: "FTR-001" | null}`. Levels: `core`, `common`, `advanced`, `emerging`, `limited_evidence`. Show prevalence as "seen in n of N" with the level, and the newest source date.

`Suggestion` (always `undecided`, never scope): `{label, item_ids, prevalence?, state: "undecided"}`. The UI shows suggestions as toggles **off by default**; toggles are the owner's choices collected at Gate 2 (this test UI keeps them in the page only).

Flags (show as small chips): `single-source`, `conflict`, `stale`, `needs-confirm`, `legal-adjacent`, `entity_from_model`, `assumption_derived`, `geo_uncertain`, `limited_evidence`, `withdrawn`, `unchecked`, `checker_same_vendor`. `data` is shown in the raw-JSON view only (its fields depend on `kind`).

The six lanes are `blueprint`, `integrations`, `design`, `screens`, `mock`, `verify`; jobs are `features`, `structure`, `flows`, `data`, `services`, `design`, `screens`, `local`.

## `research.json` (`research.v4`)

The committed file is described by `agents/research/contracts/research.v4.schema.json` and the events by `research.event.v1.schema.json` (generated from the package models; a test keeps them in sync). Quotes never appear in it.

## Details the console relies on

* **Gap reasons** (`gaps[].reason`): `not_found`, `budget_stop`, `provider_down`, `not_run`, `not_worthwhile`, `conflict_unresolved`, `user_cancelled`, `ambiguous_reference`, `declined`.
* **409 `run_active`** carries a structured `run_id` next to the message (`{code, message, run_id}`); the console re-attaches to that run.
* **Skip and per-topic cancel on a run that is not active**: `POST .../skip` on a finished run returns the run unchanged (no error); `POST .../targets/{id}/cancel` answers `{cancelled: false}`. The console just re-reads the snapshot.
* **Preflight.** `Last-Event-ID` makes the event stream a non-simple request, so `OPTIONS` on `/events` is answered (the server allows the headers listed at the top).
* **`fact.rejected.label`** is present when the rejected item had been announced by `fact.found`, so a client can close its "checking" row.
