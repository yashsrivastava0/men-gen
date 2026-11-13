# Research agent HTTP API (`mde_research_agent`)

The Research agent (agent 2 of the Context Engine) runs in the foreground after Gate 1. This page is the contract the test front end (and later the full front end) codes against. Design: `docs/architecture-v3/13-research-agent-design.md`. Package page: `agents/research/README.md`.

Base URL: `http://127.0.0.1:8120` (the standalone app: the API and the front end are served from this one origin). Prefixes: `/v1/research` (runs, events, results) and `/api` (the app: inputs, budget, preflight; see "The standalone app" below). All bodies are JSON, UTF-8. There is **no CORS**: a state-changing request whose `Origin` is not the app's own is refused with `403 bad_origin`, and every response carries a strict content-security policy.

## Auth (dev)

Same dev headers as Compass: `Authorization: Bearer dev-token` (or `RESEARCH_DEV_TOKEN`), `X-Tenant-Id`, `X-User-Id`. The console reuses the values from its Settings dialog. A wrong or missing token is one identical `401 {code: "unauthorized"}`.

## Errors

Always `{"code": "<machine_code>", "message": "<plain words>"}`. Codes used by the UI: `unauthorized` (401), `not_found` (404), `run_active` (409, one run per program at a time; `message` names the run), `not_ready` (409, with `missing: [names]`: the variables to set, never values), `budget_tripwire` and `budget_exhausted` (409, no new run starts), `bad_origin` (403), `too_large` (413), `bad_request` (422), and the export refusals (422): `not_an_export` (no Brief in the file) and `unusable_brief` (no idea, no facts and no research requests). A missing approval, a changed hash, an older format or a missing research view is not refused: the run goes ahead and `input_quality.input_binding` says what could not be checked. Show `message` to the user; never a stack trace.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /v1/research/config` | `{mode: "live", search_provider: "tavily" \| "serper" \| "none", keys: {tavily: bool, serper: bool}, model: string, checker_vendor: "same" \| "different" \| "none"}`. Booleans only, never a key. The model name comes from the settings. |
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


## The standalone app (`/api/*`)

One process serves the API and the static front end (`apps/research-console/`). The page gets its dev token from `GET /api/session` (same origin only). Live models and the live web only: there is no mock mode, no replay mode and no in-memory store in the app. The app always runs the gateway in live mode. If a search key is missing the app still starts; every start-run action returns `409 {code: "not_ready", missing: [<variable names>]}` and `GET /api/preflight` explains. Nothing falls back to canned content.

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | No auth. `{ok, ready, app, time}`. |
| `GET /api/session` | No auth, same origin. `{token, tenant_id, app, live}`. |
| `GET /api/inputs` | The approved Compass exports found in the inputs folder (`RESEARCH_INPUTS_DIR`, default `docs/compass/research-inputs`, read-only), `{folder, folder_exists, inputs: [...], skipped: [{name, reason, text, size}]}`. Rescanned on every call (cached by path, size and modified time), so a dropped-in file appears without a restart. Cards are sorted Tiny, Small, Medium, Big by the **computed** load. Each card: `id` (first 12 hex of the file's SHA-256), `name`, `size`, `modified`, `short_hash`, `also_named` (same content under other names), `changed_since_last_run`, `runs` (the last five), `active_run`, and `preview`. `skipped` reasons: `not_json`, `not_a_compass_export`, `unusable_brief`, `unreadable_export` (an unexpected reader error; the text names the error's class), `too_large`, `unreadable`, `unsafe_path`. |
| `GET /api/inputs/{id}` | One card with all its runs. The id is mapped to a file by the server; a path is never accepted. |
| `POST /api/inputs/upload` | The body is the export JSON (up to 25 MB). Checked like a listed file and stored under the app's data folder (never in the inputs folder). `201 {accepted: true, input}` or `201 {accepted: false, reason, text}`. |
| `POST /api/runs` | Body `{input_id}`. `201 {run_id, program_id, status, created, queue_position, events_url}`; `200` with `created: false` when that input already has an active run (the second start returns the active run). One run at a time by default (`RESEARCH_MAX_CONCURRENT_RUNS`): further starts get `status: "queued"` and a `queue_position`, and start by themselves. |
| `GET /api/runs` | `{runs: [{run_id, status, input_id, input_name, size_class, load_class, created_at, started_at, finished_at, reason, content_hash, queue_position, spend_micros}]}`, newest first. |
| `POST /api/runs/{id}/cancel-queued` | Cancel a run that has not started; it gets a terminal `run.failed` event (`code: "cancelled"`) and status `cancelled`. |
| `GET /api/budget` | `{enabled, period_id, total_micros, tripwire_micros, spent_micros, remaining_micros, state: "ok" \| "tripwire" \| "exhausted", write_failed, active_runs}`. |
| `GET /api/spend` | The whole spend record (`research.spend.v1`): periods (with the history of restarts) and one entry per live action (`kind` run, bakeoff, probe, ping or smoke; `by_step` in micro-USD; `running_total_micros`; `reconciled`). |
| `GET /api/credits` | Search credits, a second budget next to USD. **Credits are what a search provider counts, never money, and they are advice only: nothing here ever blocks a run.** `{enabled, providers: [{provider, label, unit: "credit", used, limit, remaining, level: "ok" \| "amber" \| "red" \| "unknown", source: "provider" \| "counted" \| "unverified_endpoint", as_of, read_at, plan, note, key_present, key_fingerprint, key_changed_at, can_check, baseline}], per_run: [{run_id, at, credits: {provider: n}, total}], estimate_hint: {size_class: {provider: {low, mid, high}}}, primary, write_failed, check: {checked_at, error, wait_s}}`. `?refresh=1` first asks Tavily for its own count (see below). `used`, `limit` and `remaining` are integers or null (Serper has no allowance figure: `remaining` only). `level`: amber at 80% of the allowance used or fewer credits left than three high-estimate Small runs, red at 95% used or fewer left than one; `unknown` with no count. `source`: `provider` = the provider's own figure (Tavily `GET /usage`) and nothing counted since; `counted` = the owner's baseline (or the provider's last reading) plus what this app has booked since; `unverified_endpoint` is reserved and not produced today. |
| `POST /api/credits/refresh` | Ask Tavily for its own count now (`GET https://api.tavily.com/usage`, bearer key, 10 s timeout) and store the reading. Throttled to one call a minute (longer after a `429`'s `Retry-After`). Same body as `GET /api/credits` plus `refresh: {done, throttled, wait_s?, error}`; `error` is one of `auth`, `rate_limited`, `quota`, `timeout`, `network`, `bad_response`, `http_error`, `not_available` (no key or no client). It never fails the request. Also done once when the app starts and after every run. Serper has no documented balance endpoint, so it is counted from each answer's `credits` against the baseline. |
| `POST /api/credits/baseline` | Body `{provider: "tavily" \| "serper", kind: "used" \| "remaining", amount, limit?}`: what the owner's account page shows ("Tavily 93 of 1,000 used": `used`, 93, limit 1000; "Serper 2,389 left": `remaining`, 2389). Whole numbers only (strict: no `93.5`, no `"93"`, no `true`), `422` otherwise; `409` with a plain sentence for an impossible pair (more than the limit). Counting restarts from that moment. Returns the `GET /api/credits` body. |
| `POST /api/budget/restart` | Body `{"confirm": "RESTART"}`. Closes the period (history kept), opens the next at 0.00 with its own gateway ledger file. `409` while a run is active or queued, or without the confirm word. |
| `GET /api/preflight` | `{ready, checks: [{id, label, ok, detail, fix}], model, checker_vendor, time}`. Switches, provider keys (booleans), search provider, inputs folder, data folder writable, store, budget, front-end files, model routes. One more line per provider whose key is set, `credits:<provider>` (`{ok: true, detail, note, level, ...}`): information only, `ok` is always true and it never changes `ready`; `note` is a plain sentence when the provider is running low. |
| `POST /api/preflight/ping` | About USD 0.01: one search and one tiny model call, booked in the spend record. `{search: {ok, ...}, model: {ok, ...}}`. |
| `GET /api/settings`, `POST /api/settings` | `{primary_provider: "auto" \| "tavily" \| "serper"}`, saved in the data folder; the search provider is built when the app starts, so it takes effect after the app is restarted (`uv run python scripts/research_app.py restart`; a run in progress is never affected). |

### Search credits (advisory, global)

`<data folder>/provider-credits.json` (`research.credits.v1`) is ONE file for the whole app: keyed by provider and a one-way fingerprint of the key (the first 12 hex characters of its SHA-256; the key is never stored or logged), not by run, tenant, project or user, and outside the budget periods, so `Restart budget` never resets it. Per provider it holds the owner's `baseline`, the credits counted since, Tavily's last own `last_reading`, and the credits of the last 50 runs. A different key starts a fresh account (the old figures are kept under `previous_keys`). The ledger counts what a provider BILLED (`SearchResult.credits`: Tavily `usage.credits` after `include_usage: true`, Serper's `credits`, else the price table), so a retry that is billed twice counts twice; a cache hit counts nothing. The credits of a run are recorded in the run row's `meta.credits` (`{provider: n}`), the run's spend-record entry (`meta.credits`), the evidence file (`credits`, shown as a line in the Markdown report header) and the ledger's per-run list. They are NOT part of `research.json` (the schema and the content hash are unchanged). Each size class's estimate carries `credits: {tavily|serper: {low, mid, high}}` (`high` = 1.2 x the high number of searches, because a search can be billed more than once) and `credits_measured_runs`.

### Money

USD 5.00 in total (`RESEARCH_LIVE_BUDGET_USD`), a tripwire at 4.50 (`RESEARCH_TRIPWIRE_USD`): at or above it the run in progress finishes and no new run starts. Hard stop at 5.00: every model and search reservation carries the period scope `research_period:<id>` capped at the total, so the gateway guard refuses a call that would pass it and the run commits `partial` with a `budget_stop` warning. There is no per-run cap and no call-count cap. Every period has its own ledger file (`data/.budget/research/ledger-<period>.jsonl`), so a restart never touches the shared ledger. The guard's cap must leave its 80% must-run tripwire above 5.00: the app raises its own cap to at least `total / 0.8 x 1.04` (USD 6.50), or uses `LLM_BUDGET_USD` if that is higher.

### Preview (on every input card; no model call, no web call, no cost)

`{idea, place, languages, counts: {features, roles, apps, integrations, references, facts: {kind: n}}, compass: {tier, degraded, format} (information only), input_binding: "verified" | "unverified_no_baseline" | "unverified_hash_mismatch", load: {score, class, components}, size_class: "tiny" | "small" | "medium" | "big", size_label, light, signals, trust: {on, why}, topics: {total, by_job: {job: {count, labels}}, not_run, cap}, not_run: [{id, label, job, priority, requests, reason, text, est_searches, est_usd_micros}] (planned and not run at this size, each with its rank and what running it would cost: `plan.yaml topic_cost`), not_run_cost: {topics, est_searches, est_usd_micros}, risk_topics: {total, by_class} (the topics of a risk class, which run outside the cap), declined, dropped: [{label, reason, text, job, requests}], warnings: [{code, text}] (an unverified seal is one), requests: {trusted, template, thin, mapped, not_run, dropped, unmapped, items: [{id, topic, status, reason, text, topics: [{id, label, job}]}]} (`status` is `mapped`, `not_run`, `dropped`, `unmapped` or `ignored`; a request is carried by at most one topic), place: {country, countries: [{name, code}], cities, uncertain}, estimate: {size_class, searches, pages, minutes, usd_micros: {low, mid, high}, credits: {tavily: {low, mid, high}, serper: {low, mid, high}}, credits_measured_runs, basis: "estimate" | "measured", note}}`. The estimate says "estimate, not measured" until the spend record holds a finished run of that size class; then `mid` is the median of the measured runs.


## Results, review, sources, report, spot-check (contract for the app's front end)

These read a **committed** run (404 until committed). Item shape follows `research.json` (`research.v4` now, `research.v5` after the engine changes; the front end must tolerate both: fields such as `evidence`, `store`, `about_region`, `rank`, `tokens_est`, `lane_quality`, `bn_coverage` and `validation` may be absent on v4). An owner rejection is an overlay (`rejected: true`); the committed file never changes.

| Method and path | Purpose |
| --- | --- |
| `GET /v1/research/runs/{id}/overview` | Everything except the item list: `{run_id, schema, status, content_hash, program_id, input_name, size_class, created_at, finished_at, profile, coverage, budget, input_quality, lane_quality, bn_coverage, validation, norms, suggestions, confirm, gaps, conflicts, defaults_proposed, slices, dropped, flagged, metrics, rejected_ids, spend_micros, fallbacks}`. |
| `GET /v1/research/runs/{id}/review?limit=50&offset=0&job=&lane=&evidence=&flag=&impact=&kind=&store=&q=` | Paged, filtered, ordered items: `{total, offset, limit, items: [...], facets: {job: {name: n}, lane, evidence, flag, impact, kind, store}}`. Each item is the `research.json` item plus `lane`, `job`, `rejected`, `best_quote: {text, source_id, domain, url, published}` (the quote from the evidence file) and `sources: [{id, domain, url, title, published, class}]`. `q` is a case-insensitive text match on label, claim and quote. Filters answer from memory in well under 100 ms. |
| `GET /v1/research/runs/{id}/sources?limit=100&offset=0&q=` | Every page the run used: `{total, items: [{id, url, domain, title, class, published, used_items}]}`. |
| `GET /v1/research/runs/{id}/report` | The readable Markdown report (`text/markdown`), rendered on demand, never stored. |
| `GET /v1/research/runs/{id}/metrics` | The run's metrics page (quote pass rate first-try and final, number-check rejections, fetch success by reason, searches contributing, slots filled per job, appendix share, lane quality, checker rates, merge rate, stage timings and cost by stage and call kind, fallbacks used). |
| `GET /v1/research/programs/{id}/latest?view=compact` | A deterministic compact projection (main-store items, sources in one table). |
| `GET /v1/research/programs/{id}/decisions` | The owner's rejections: `{decisions: [{run_id, item_id, kind, reason, at}]}`. |
| `POST /api/spot-checks` | Body `{run_ids: [..], seed?: int, per_run?: 20}`. Creates and stores a sample (seeded, stratified 8 high-impact, 6 service or integration, 3 typed-data, 3 ordinary per run; a short stratum is topped up from the next and the card says so). `201 {id, seed, run_ids, cards: [{key, run_id, item_id, stratum, topped_up, job, kind, claim, data, quote, source: {domain, url, published}, evidence, impact}], verdicts: {}}`. |
| `GET /api/spot-checks`, `GET /api/spot-checks/{id}` | List, or one check with its verdicts (resumable). |
| `PUT /api/spot-checks/{id}/verdicts/{card_key}` | Body `{verdict}`: `ok`, `not_relevant`, `claim_not_in_quote`, `wrong_or_outdated`, `wrong_place_or_company`, `invented_detail`, `skipped`. Saved at once. |
| `GET /api/spot-checks/{id}/summary` | `{counts, answered, total, supported_pct, ok_pct, invented_high_impact, invented_total, pass: {supported, ok, no_invented}, passed, worst: {job|kind: ...}}` against the thresholds (supported 95% or more, ok 90% or more, no invented detail on a high-impact item and fewer than 3 overall). |
| `GET /api/spot-checks/{id}/export?format=md\|json` | The result as a download. |
