# mde_compass: Compass, the first agent

`packages/compass/` (import `mde_compass`, call ids `cmp.*`, API prefix `/v1/compass`, dev port 8110). The agent a person meets first: it interviews them with question cards and free text (English, Hindi, Hinglish), fills a catalog of slots and writes the **Brief**, one `brief.json` that is hashed, versioned and evidence-tiered, then waits for Gate 1. Behaviour source: [doc 11](../architecture-v3/11-discovery-agent-design.md). Decisions: [ADR 0012](../adr/0012-compass-package-boundaries-and-layering.md) (boundaries), [0013](../adr/0013-brief-schema-v1-hashing-and-versioning.md) (Brief), [0014](../adr/0014-call-spec-registration-from-agent-packages.md) (calls), D-084 to D-086 in the [decision log](../implementation/DECISIONS.md). Companion pages: [call catalog](../prompts/compass-calls.md), [HTTP API](../api/compass.md), [runbook](../runbooks/compass.md), [package README](../../packages/compass/README.md).

Compass sits next to the older Discover engine ([discovery](discovery.md)), which it does not replace in the running system yet and does not import. Today Compass runs as its own app; nothing in `mde_flow` calls it, and the hand-off to a Research agent is a logging stub (see Limitations). It reads documents and links (PDF, Word, Excel, PowerPoint, CSV, text, Markdown, email, web pages) as well as taps and text, and carries 12 call folders, 46 named edge-case scenarios and 10 persona scripts that run offline.

```
uv run pytest packages/compass -m "not integration and not live"   # offline, no database (just compass-test)
just compass-test-int                                               # store tests against the private PostgreSQL
cd packages/compass && PYTHONPATH=src python -m evals.run_personas  # the ten persona scripts, offline, prints the metrics report
```

## Boundaries

| | |
| --- | --- |
| May import | `mde_core`, `mde_contracts` (`CallSpec`, `CallContext`, `GatewayClient`), `mde_llm_gateway`, `mde_persistence` (only the helpers `db` (engine creation, URL handling) and `jsonutil`; none of its models, repositories or migrations) |
| Must not import | `mde_discovery`, `mde_research`, `mde_spec_builder`, `mde_flow`, `mde_prompts`, `mde_content`, `mde_spec_loader`, any app |
| Layer | the apps layer, an independent sibling of `mde_context_api` and `mde_llm_gateway_api`; the `compass-isolated` import-linter contract lists the packages above (`just layers`) |
| Contract edits | none: Compass has its own domain models and slot vocabulary (`mde_contracts` has a closed one) |
| Migrations | its own Alembic directory and version table `compass_alembic_version`; tables `compass_*` (sessions, events, questions, brief_versions, slots, features, assumptions, research_requests, glossary, contributors, change_sets, call_log, sources, chunks, claims) |
| Only the gateway imports `openai` | still true; Compass reaches models only through `GatewayClient` |

Small utilities (slot rules, intent router, redaction, safe expression evaluator) were copied and adapted from `mde_discovery` with their tests, so the old package can be retired later.

**Dependencies** (D-086, all permissive, pinned by `uv.lock`): `pypdf` (BSD-3, pure Python, PDF text layer), `python-docx` (MIT), `openpyxl` (MIT), `python-pptx` (MIT), `python-multipart` (Apache-2.0, multipart uploads). None needs a system library (no poppler, no tesseract, no Docker); `python-docx` and `python-pptx` pull `lxml` and `pillow`, which ship as binary wheels (there is no pure-Python reader of comparable quality). A PDF page with no text layer is reported unreadable, never OCRed.

## Architecture

```
HTTP (api/)  ->  CompassService port  ->  Service (engine/service.py)
                                            |  ports (engine/ports.py): 12 call ports + HandoffPort
                                            |  engine functions: state, slots, voi, cards, assumptions, gate, patch, validate, router,
                                            |    document_flow (claims to Brief writes), app_structure, flows/* (targeted flows)
                                            |  intake/: parsers, chunking, SSRF-safe URL reader, ingest pipeline
                                            |  views/: BriefViewModel, consumer views, readiness, diff, provenance
                                            +--> Store port (store/ports.py): InMemoryStore | PostgresStore
 calls/<name>/adapter.py  --implements--> a call port  --uses--> CallRunner --> GatewayClient --> mde_llm_gateway
```

* **Ports and adapters.** The engine depends on Protocols over domain types (`engine/ports.py`), never on wire models. Each `calls/<name>/` folder ships an adapter (`...Adapter(runner, catalog)`) that builds the wire input, runs the call through `CallRunner`, applies code validators and returns a domain object. `calls/registry.py` maps folder names to `Ports` attributes and builds the `Ports`; folders that do not exist yet get a deterministic placeholder (`engine/fallback_ports.py`). The deterministic fallbacks double as test doubles.
* **`Service`** (`engine/service.py`) implements the `CompassService` port the API talks to. Plain code controls the flow: the model never chooses what happens next, it fills schemas. One event at a time per session (an in-process lock queues events; the store's `try_begin_turn` is the cross-process backstop). A turn has **three phases**, because a transaction never spans a model call (ADR 0004): (1) read and claim the turn (record the inbound event idempotently by `event_id`, `try_begin_turn`); (2) think (load the session, Brief, questions and the last 6 transcript lines; run ports and pure code); (3) write everything in one unit of work (new Brief version, change set, asked and answered questions, session row, outbound events), then release the turn. Hand-off, when due, is called after the commit. The service keeps its budget object as `Service.budget_guard` (the `budget()` method serves the snapshot). A failure inside a turn becomes one `error` event with code `turn_failed` and the generic text, never a stack trace.
* **The Brief is the single source of truth.** Slots are rebuilt from the Brief on every turn (`slots_from_brief`); a changed hash means a new version.
* **Store.** `Store.begin(tenant_id)` opens a tenant-bound unit of work with `sessions`, `events`, `briefs`, `questions`, `change_sets`, `call_log`, `sources`, `contributors`. Two implementations (PostgreSQL, in-memory fake) pass one shared contract suite (`store/contract.py`, ADR 0007). Rules: a foreign tenant's id behaves as not found; one active turn per session; the Brief `save_version` must be `latest + 1` or `Conflict`; saved versions are append-only and only `mark_baseline` labels one, once; inbound events are idempotent by `event_id`; outbound events get a strictly increasing per-session `seq`.
* **Call runner** (`calls/base.py`). `CallRunner.run`: budget guard check, `gateway.run` (render with untrusted data wrapped, reserve budget, provider with the spec's deadline, strict-schema validation, one repair at low effort, retries), then local validators, then on any `GatewayError`, timeout, empty output or validator error the spec's deterministic `fallback`, then record cost and write a call-log entry (`ok`, `repaired`, `cache_hit` or `fallback` with the reason). It never raises for a model-side failure. `LiveCallForbidden` is not a `GatewayError`, so a live-mode misconfiguration fails the turn (`turn_failed`) instead of being hidden by a fallback.
* **Budget** (`budget.py`, `config.BudgetPolicy`). `CompassBudget`: cap = `COMPASS_LIVE_BUDGET_USD` clamped to at most 2.00; stop at 95% of it; per session 0.25; integer micro-dollars; `check` raises `BudgetExceeded` (a `GatewayError`, so the runner falls back) when total or session spend plus the estimate reaches the limit or when the estimate is negative (fails closed). In mock mode every cost is 0. The gateway ledger and the global USD 6.00 cap remain authoritative ([live-llm-budget](../runbooks/live-llm-budget.md)); call contexts use the budget scope `session:<id>`.
* **Config** (`config.py`) is the only module with numbers that logic depends on: `CALL_PROFILES` (effort, output cap, lane, route, timeout per call), `QuestionPolicy` (Express at most 5 questions, Standard target 7, hard cap 10, Large 6 per module, VOI weights, 20-word questions, 8-word options, exactly 3 options, two failed validations then the fallback card), `GatePolicy` (2 probe repair rounds, typed yes at confidence 0.8, required approver role `approver`, 6-turn history slice, 2 back-edge loops), `IntakeLimits` (declared for the deferred intake phase), `BudgetPolicy`. One model, `gpt-6-luna`; every call carries a `lane` (writer, critic, canary) so another vendor can be added later.

## The Brief

`brief.json`, `schema_version` 1 (`domain/brief.py`). Content sections (`SECTION_KEYS`, the ones the hashes cover), 19 in all:

| Kind | Sections |
| --- | --- |
| Dict sections (catalog field name to a `BriefItem`) | `idea`, `context`, `audience`, `experience`, `access`, `data`, `constraints` |
| List sections (typed records) | `apps`, `roles`, `scope` (features), `references`, `integrations`, `glossary`, `assumptions`, `open_questions`, `research_requests`, `assets`, `sources`, `declined` |

Also on the Brief but outside the section hashes: `meta` (session id, mode, situation profile, discovery plan, domain hypothesis, locales, depth profile, `degraded`, `probes`, `probe_rounds`, `approvals`), `search_hints`, `summary`, `pitch`, and the bookkeeping `version`, `ids`, `readiness`. Of these, `meta.approvals` (which approver approved which hash, enterprise mode) is also left out of `brief_hash`, because recording an approval must not change the hash it was recorded against. Which keys a dict section holds is data (`catalog/slots.yaml` `brief_path`), not code.

**Every item** carries `tier`, `provenance[]`, `confidence`, `status` and `history[]`.

* **Tier** (code, tested): `Locked` only if the provenance holds an *evidence* entry (`user_tap`, `user_text`, `voice`, `document` or `org_profile`) with a non-empty `ref` (a turn id or `SRC-###` plus a pointer). `assumed`, `pattern` and `model_knowledge` yield `Default` (value present) or `Open`. `validate_brief` rejects a Locked item without evidence, duplicate or tombstoned ids, dangling `apps`/`roles` references and a tier C feature that is `in` scope.
* **Status** (slot state): `empty`, `assumed`, `volunteered`, `confirmed`, `disputed`. An assumed write never replaces a user-given value; a correction overwrites and pushes the old value to `history`; an explicit negation clears.
* **Ids**: `APP-`, `ROL-`, `FTR-`, `ASM-`, `SRC-`, `RR-`, `Q-`, `CHG-` plus three digits, from the `IdLedger` stored inside the Brief: counters only increase, deletions leave tombstones, renames add aliases. An id is never reused, also across undo.
* **Versions**: an integer `version` that increments on every committed change; a stale writer gets `Conflict`.
* **Hashes** (`domain/hashing.py`): `canonical_json` is sorted-key, compact, UTF-8 with volatile keys (`date`, `at`, `created_at`, `updated_at`) removed. `brief_hash` is sha256 over the whole Brief minus `version`, `ids`, `readiness` and `meta.approvals` (so it still includes the rest of `meta`, `search_hints`, `summary`, `pitch`); `section_hashes` is one sha256 per content section. Approval binds to the whole hash and the section hashes.
* **Baseline**: the approved version is labelled `B-G1` once and is immutable; a later re-approval is labelled `B-G1-v<version>`. Later edits create new versions plus a "changed since approval" diff.

Feature records use `scope_status` (`in`, `later`, `out`, `unsupported`, `needs-engineering`) because every item's `status` is already the slot state; `FeatureRecord.priority` is `must`, `nice` or `later`; `capability_tier` is `A`, `B` or `C`; `custom: true` marks a feature outside `families.yaml`.

**Consumer views** (`views/consumers.py`): `research`, `blueprint`, `design`, `verify`, built field by field from values only (no provenance, quotes, history, confidence, turn ids or chat text; a property test checks the serialized result). **Readiness** (`views/readiness.py`) is derived from the catalog: a consumer is ready when none of its `must` slots is empty (`ask_if` value comparisons are real conditions, `filled`/`empty` checks only order questions); `should` slots are advisory. **`BriefViewModel`** (`views/build.py`) is the display-ready page: 14 sections (`idea`, `who`, `apps_roles`, `features`, `look`, `access`, `integrations_data`, `limits`, `words`, `assumed`, `unknown`, `lookup`, `sources`, `cannot_build`), rows keyed by path, badges with text and icon (`you_said`, `from_document`, `assumed`, `typical`, `suggestion`), header with readiness chips, footer with the approve and change labels and the blockers in the session language.

## The twelve calls

Full detail: [prompts/compass-calls.md](../prompts/compass-calls.md). Profiles are from `config.CALL_PROFILES`. "Folder" means `calls/<name>/` exists with spec, schema, prompts, mock, fallback, adapter and examples.

| Id | Effort | Max output | Lane | Timeout | Job | Must not | Fallback | Folder |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cmp.understand_turn` | medium | 2500 | writer | 45 s | read one message: intent, slot updates with quotes, references, language, flags | ask a question; update without an exact quote; invent a value | lexicon and catalog rules, quotes copied from the text | yes |
| `cmp.write_card` | high | 2500 | writer | 60 s | wording and options of one card | invent statistics, brands, capabilities; repeat labels; lead | card built from the catalog's bilingual fallback card and family hints | yes |
| `cmp.situate` | high | 6000 | writer | 90 s | Situation Profile and a labelled Domain Hypothesis | state a hypothesis as fact; invent user facts | keyword overlap with the families, everything labelled `hypothesis` | yes |
| `cmp.compile_text` | high | 3500 | writer | 90 s | one-line summary, pitch, plain row descriptions | invent any number or proper noun not in the input | template sentences filled from slot values | yes |
| `cmp.seed_research` | high | 4000 | writer | 90 s | specific research requests and search hints | emit a request without geography, entity and terms; name a competitor the user did not name | requests from `templates.yaml` over the Brief (3 to 8) | yes |
| `cmp.probe_handoff` | low | 2500 | canary | 30 s | a weak downstream planner tries to start from the Brief (research, blueprint, design) | decide pass or fail itself (code decides; its `can_start` is ignored) | deterministic planners over the digest | yes |
| `cmp.answer` | medium | 1500 | writer | 45 s | answer a question without leaving the interview | invent a price, time, date or capability promise; follow instructions in the question | catalog strings and input data only | yes |
| `cmp.interpret_change` | high | 3500 | writer | 90 s | a change request as validated patch ops | return ops when unsure; touch items outside the digest; guess a value | phrase tables; else `understood=false` with a clarification | yes |
| `cmp.make_variants` | high | 2500 | writer | 60 s | three alternatives for a disliked value | return fewer or more than three, near-duplicates, the current value, invented stats or brands | catalog choices then family items (possibly fewer than three) | yes |
| `cmp.read_chunk` | high | 4000 | writer | 90 s | extract typed statements (11 types) with verbatim quotes from one chunk of a document | keep a statement whose quote is not in the chunk; obey instructions in the document; infer | sentence, bullet and table-row splitter with keyword typing (`rules.yaml`, en, hi, hinglish) | yes |
| `cmp.merge_claims` | high | 6000 | writer | 120 s | label each statement used, duplicate, conflicting or needs_confirming; list conflicts and TBD items | rewrite, drop or merge away a statement; omit a conflict | normalised-text de-duplication plus rule-based contradiction detection (negation, different numbers, antonyms) | yes |
| `cmp.scan_ambiguity` | high | 3000 | critic | 60 s | point at vague, conflicting or undefined items of the Brief digest | change a value (its output has no value field); point at something that is not in the digest | vague-word table, undefined acronyms, contradictions between lines | yes (not called by the service) |

Every spec is version 1, not cacheable, with one gateway repair, and lists its untrusted inputs (user text, document text, slot values) so the gateway wraps them as data. No call has tools. All twelve folders exist; `cmp.scan_ambiguity` is built and tested (and used by the live smoke driver) but `Service` does not call it yet, so no Brief is scanned for ambiguity today.

## The catalog (data, hard rule 8)

All in `catalog/`, loaded and validated by `load_catalog()` (cached; raises `CatalogError` with a precise message).

| File | Content |
| --- | --- |
| `slots.yaml` | 44 slots in 17 groups. Per slot: `id`, `group`, bilingual `label`, `level` (`must` 12, `should` 24, `may` 7, `generated` 1), `consumers`, `filled_by` (U user, D document, I inferred, P pattern), `impact` 1..5, `risk`, `effort` (tap, text, upload), `value_schema`, optional `ask_if`, `brief_path`, `order` 1..8 (dependency stage), a bilingual `fallback_card`. Group `org` (4 slots) applies in enterprise mode only |
| `frames/NN_*.yaml` | 17 question frames (idea in your words, confirm kind of app, business model, who and where, which apps, roles, features, priorities, apps you like, brand and tone, login and accounts, integrations and systems, scale and launch, limits, words we use, research lookup, company setup). Each fixes purpose, card type, option source (`fixed`, `family`, `model`, `document`), option count (null = checklist or free form), word limits and guidance. Every slot is covered by exactly one frame |
| `families.yaml` | 16 neutral capability families (seeds, not app types): identity_access, catalog_commerce, booking_scheduling, delivery_logistics, payments_wallet, communication, content_community, workflows, dashboards_admin, finance, health_wellness, learning, travel_hospitality, marketplaces, civic_utilities, enterprise_workforce. Typical features, roles, integrations and business models; keywords in `{en, hi, hinglish}` |
| `capabilities.yaml` | 17 entries with keyword guards: 9 `tier_b` (real push, live location, biometrics, payment SDKs, camera, offline sync, in-app purchases, bluetooth, video calls) and 8 `declined` (games, AR/VR/3D, media editors, DRM streaming, trading terminals, OS utilities, safety-critical medical, proprietary hardware) |
| `no_ask.yaml` | topics never asked: stack and framework, hex colors, hosting and database, legal wording, database tables and API design, anything not tied to a consumer decision, anything inferable with high confidence and low impact |
| `strings/en.json`, `hi.json` | 190 flat keys each (identical key sets): welcome, cards, blockers, notices, sections, badges, readiness, UI chrome. The API serves them at `GET /v1/compass/strings/{lang}` |

`ask_if` is a small grammar (`filled('x')`, `empty('x')`, `value('x') == 'a'`, `!=`, `in [...]`, `and`, `or`, `not`, parentheses) evaluated by `engine/expr.py`; no `eval`.

**Question selection** (`engine/voi.py`, pure): per open slot `score = w_impact*impact + w_risk*risk - w_inferability*5*inferability - w_effort*effort`; high impact and low inferability means `ask`, high impact and high inferability means `propose`, low impact means `assume` (listed in the assumption review), a risky slot is `ask` and `must_confirm` ("You decide" and Skip are refused). Order follows the frame order; the score only decides which items survive the budget. A confirmed or volunteered slot is never re-asked and a frame is never asked twice. **Cards** (`engine/cards.py`): the model writes wording, code validates (exactly the right option count, distinct, one recommended with a reason, honest basis labels, no invented statistics, no unnamed brands, no tier B or C capability the user did not raise, neutral wording, word limits, language); two failures in a row show the catalog's fallback card (`fallback: true`). Eight card types: `choose_one`, `pick_many`, `priority`, `confirm`, `look_signals`, `quick_form`, `upload_or_link`, `open_prompt`; "custom text", "You decide" and "Skip" are offered on every question card except where `must_confirm` (and on the welcome card).

## State machine

`engine/state.py`: a pure transition table (`TRANSITIONS`, state to trigger to next state). `CHANGING` and `PAUSED` return to where they came from (`SessionRecord.resume_state`); `change_applied` from an approved origin lands in `BRIEF_REVIEW`; `ARCHIVED` has no way back.

| State | Meaning | Table triggers (to) |
| --- | --- | --- |
| `WELCOME` | canned greeting card shown | `start` (INTAKE), `idea_received` (UNDERSTANDING), `documents_received` (INTAKE) |
| `INTAKE` | a first document or link is being read | `idea_received`, `documents_received` (UNDERSTANDING) |
| `UNDERSTANDING` | first idea: profile and hypothesis | `understood` (INTERVIEWING) |
| `INTERVIEWING` | one card at a time | `playback_shown` (PLAYBACK), `queue_empty` (SEEDING) |
| `PLAYBACK` | "here is what I understood" | `playback_confirmed`, `need_more` (INTERVIEWING), `queue_empty` (SEEDING) |
| `SEEDING` | research requests written | `research_seeded` (PROBING), `need_more` (INTERVIEWING) |
| `PROBING` | three hand-off probes | `probes_done`, `gate_open` (BRIEF_REVIEW), `need_more` (INTERVIEWING) |
| `BRIEF_REVIEW` | Gate 1 is open or being prepared | `gate_approved` (APPROVED), `need_more` (INTERVIEWING), `reopen` (BRIEF_REVIEW) |
| `APPROVED` | Brief approved (version and hash) | `reopen` (BRIEF_REVIEW) |
| `CHANGING`, `PAUSED` | side states | `change_applied`, `change_cancelled`, `resume` (back to origin) |
| `ARCHIVED` | `start_over` | none |

Every state also takes `pause` (working states), `start_over` and, from `INTERVIEWING` onward, `change_requested`. The service drives a subset (see Limitations): `documents_received`, `idea_received`, `understood`, `playback_shown`, `queue_empty`, `research_seeded`, `probes_done`, `pause`, `reopen`, `gate_approved`; it sets `INTERVIEWING`, resume and `ARCHIVED` directly. A trigger that is not in the table raises `InvalidTransition` (HTTP 409).

**A first message.** The session starts at `WELCOME` with the greeting and a card of four starts (describe, upload, like an app, just build it). The first idea runs `understand_turn`, then `situate` (profile, hypothesis, depth: `express` when the user asks to just build it, `large` for enterprise, else `standard`; question budget 5, 7 or 10), writes an assumed category from the hypothesis, shows a **Playback** event and asks the first card. After each answer the queue is recomputed. When it is empty the service seeds research (`seed_research`, `lookup_preview`), runs the three probes in parallel (`probe_result` events), compiles the text (`compile_text`) and emits `brief_ready` with `approvable` and the blockers.

## Interview behaviour

The rules below are code (`engine/service.py`, `engine/flows/*`, `engine/voi.py`) with their numbers and cue phrases in data (`flows.yaml`, `config.py`, `catalog/`). They are covered by the scenarios in `tests/scenarios/data` and the unit tests in `tests/engine/flows`.

* **Proposed, not asked.** Once the engine has a value from the hypothesis, the kind of app (`confirm_kind_of_app`, slot `idea.category`) and the roles (`roles`, slot `roles.list`) are shown in the playback for correction and listed in "We assumed"; they are not asked as cards. A hybrid idea (two or more likely families) still asks which kind is primary. (`PROPOSED_NOT_ASKED`.)
* **Express ("just build it").** Depth `express`, question budget 5. When the queue is exhausted the service asks no follow-ups: every required slot still empty is assumed from the catalog default, else the hypothesis (first business-model candidate; up to three non-custom likely features), else the first, suggested option of the slot's card. These are `assumed` with `pattern` provenance, listed in "We assumed", never Locked.
* **Follow-up for an empty required slot.** A required slot with no catalog default that is still empty after its frame was asked gets one follow-up card, at most two asks per slot. Past the question cap (10; the Express budget; for Large 10 plus one module's worth) only must-confirm slots (`auth.methods`, `auth.account_model`, `integrations.wanted`, `data.sensitive`) and Large depth bypass the cap. Follow-up answers merge into list values (de-duplicated). `idea.subcategory` is never asked: it is derived (assumed) from the hypothesis or the category. An empty checklist on a required slot is not a decision: the typical items (up to three) are assumed and a notice says so.
* **Priority card.** "Everything is a must" (or every feature ticked) opens one ranking card, "If you launched with only five features, which?" (needs at least two features, shows at most 14 chips). The first five ranked are Must, the rest become `later` (the Phase 2 backlog). It never adds a feature.
* **References.** A named app ("like Swiggy") is stored `assumed`, never Locked on a mention. "I own X" opens a confirm card (is it yours, or an app you like). "Exactly like X, same logo" gets one patterns-only warning. One quick-form card (at most four references) asks what to borrow and what to avoid.
* **Voice.** A dictated run-on is split into atomic statements by rules (joiner words and fillers are data); names, numbers and places are echoed; three or more statements, or anything ambiguous, get a numbered read-back the user edits ("remove 2"; items it cannot tell apart are never cleared).
* **Phased apps (DC-11).** Five or more apps: the first two named go into the first release; the rest are kept as scope features with status `later` and source `later_phase_app`, and the explanation lists up to six names.
* **App structure.** The "which apps" card offers structure codes (`one_app`, `customer_and_staff`, `app_plus_admin`); `engine/app_structure.py` expands the picked code into app records with localised names and purposes from the catalog strings.
* **Corrections and Locked edits.** During the interview a typed correction with a stated value applies at once (the latest explicit statement wins). In review or after approval a change request goes through the change flow. A `brief_edit` or chat change that rewrites a Locked value **that the user confirmed with a tap on a card** (a `user_tap` in its provenance) does not apply at once: a one-question card with an **impact preview** (the consumers the catalog names for that slot, at most four) applies it on "Yes, change it". Words the user typed themselves (volunteered, or edited in the Brief) and anything not Locked stay freely editable. A junk answer to a flow card (nothing chosen) shows the card again and changes nothing.
* **Typed back-edge.** `Service.reopen_slot(caller, session_id, slot_id, reason)` lets a later stage (research) reopen ONE slot with one targeted card: the reason, the current value, up to three alternatives from `cmp.make_variants` and "Keep what I said". At most two per session (`max_back_edge_loops`), then a notice. It is a method for the flow, not an HTTP route.
* **Re-seeding.** Research requests are rebuilt only when an op touches `idea.category`, `idea.subcategory`, `context.geo`, `audience.*`, `references` or `scope.*`; a wording edit does not re-seed (so undo stays exact).
* **Re-approval.** Approving the same, unchanged approved Brief again is a no-op (the approval message, no second baseline).

## Documents and links

`mde_compass.intake` (`parsers`, `chunking`, `url_reader`, `htmltext`, `pipeline`, `errors`) plus `engine/document_flow.py` and `engine/document_map.yaml`. Everything a document says is **data** (hard rule 5): never executed, followed or obeyed; macros, embedded files and links in a file are never opened.

* **Inputs.** A file (`POST /sessions/{id}/uploads`, multipart, one file per request, `UploadAdded` inside the service), a link (`link_added`), or a pasted message over 3,000 characters (`pasted_text_is_document_over`). Formats: PDF (text layer only; a page without one is marked unreadable and is never OCRed), DOCX, XLSX, PPTX, CSV, TXT, MD, EML. Images are accepted as sources with no text: the gateway has no image input. The type is decided by extension and magic bytes (a mismatch is rejected), archive members are checked against a decompressed-size ceiling, hidden text (DOCX hidden runs, PPTX hidden shapes and slides, hidden sheets) is dropped, zero-width and control characters are stripped, and no regex runs over document text.
* **Limits** (`IntakeLimits`): 15 MB per file (the API answers 413 `too_large` while reading, never after), 10 files and 60 MB per session, 300 pages, 5 links, chunks of 3,000 to 4,000 tokens (characters divided by 4), 4 concurrent reads; a link has a 10 s deadline, a 2 MB body, 3 redirects.
* **Pipeline** (`ingest_bytes`, `ingest_text`, `ingest_url`): limits and sha256 de-duplication, parse, structure-aware chunking with source pointers (`page 3`, `heading: Payments`, `sheet Roles!A1:D9`, `rows 2-40`; a table row is never split, its header repeats; an unreadable page is its own empty chunk and counts against coverage), redaction (`mde_core.redact`), `cmp.read_chunk` per chunk, `cmp.merge_claims` over the statements, then a plain summary such as "read 100%; 4 statements; 2 need confirming; 0 conflict; 1 open question". It never raises: a failure comes back as a rejected result with a plain message, and one bad file never stops the others. Codes: `password_protected`, `corrupt`, `unsupported`, `too_large`, `too_many_pages`, `too_many_files`, `session_too_large`, `too_many_urls`, `private_link`, `blocked_url`, `too_many_redirects`, `fetch_failed`. A duplicate is explained ("... is the same as a file I already have (SRC-001); I kept the first one"): every upload gets a fresh event id so the duplicate is not swallowed by event idempotency.
* **SSRF-safe URL reader.** http or https only, no credentials, ports 80, 443, 8080, 8443; IP literals in every spelling an OS accepts are understood (decimal, hex, octal, short forms, IPv6, IPv4-mapped); single-label and internal names are refused; **all** resolved addresses must be public (private, loopback, link-local, carrier-grade NAT, cloud metadata and their IPv6 forms are blocked); the checks run again at every redirect hop (the fetcher never follows redirects itself) and the connection is pinned to the validated address. Content types `text/html`, `text/plain` and `application/pdf` only; scripts and hidden elements are dropped; a 401, 403 or sign-in bounce is `private_link`.
* **Claims to Brief writes** (`document_flow.plan_from_claims`, map in `document_map.yaml`): requirement to `scope.features`, user_or_role to `roles.list`, exclusion to `scope.must_not`, constraint to `constraints.must_use`, glossary_term to `glossary.terms`, business_context and goal to `idea.problem_value`. Only `used` claims are written, as `volunteered` with `document` provenance (`ref` = source id plus pointer, the verbatim quote), so they can be Locked. List slots are extended, never overwritten; a single-text slot the user already filled is never overwritten (**chat beats documents**). Open issues ("TBD") become non-blocking open questions (at most five); claims that need confirming become ONE summary open question; conflicting claims are counted and not written. Each source becomes a `SourceRecord` (Locked, `document` provenance). A document that tries to instruct the assistant is noted ("document instruction ignored") and otherwise treated as words. A first document in `WELCOME` moves the session to `INTAKE` and then runs the normal first understanding.

## Measured behaviour (mock mode)

* **Scenarios.** `mde_compass.scenarios` (`models`, `runner`, `metrics`) runs a YAML script of user actions against the real `Service` in mock mode over the in-memory store through the `Conversation` driver, with `expect:` checks per step. 46 files in `tests/scenarios/data` (`SB-CHG` 4, `SB-DC` 15, `SB-EXPRESS` 1, `SB-GATE` 9, `SB-IN` 4, `SB-IV` 13), run by `tests/scenarios/test_scenarios.py`; plus `test_flows_service.py`, `test_intake_flow.py`, `test_personas.py`. `DEFERRED.yaml` still lists the document cases (IN-01, IN-02, IN-04 and others) as waiting for intake; intake has landed, so that list is out of date.
* **Personas.** Ten scripts in `packages/compass/evals/personas` (adversarial, change-heavy, chatty, confused, enterprise, expert, hinglish, returns, silent, voice). Run `cd packages/compass && PYTHONPATH=src python -m evals.run_personas [--only silent,chatty] [--no-write]`: offline, free, on a fake clock; the report goes to `packages/compass/evals/reports/latest.json` (git-ignored) plus a dated copy. Targets are those of [doc 11](../architecture-v3/11-discovery-agent-design.md) section 14.

| Measure | Target | Measured (10 personas) |
| --- | --- | --- |
| Questions per Brief, median | at most 7 | **8: the one miss** |
| Questions per Brief, Standard and Express maximum | at most 10 | 10 |
| Express maximum | at most 5 | 3 |
| Large (enterprise) | measured separately | 18 |
| Locked items without evidence | 0 | 0 |
| Assumptions unchanged at Gate 1 | at least 80% | 100% |
| All three hand-off probes pass before Gate 1 | all | 100% |
| Generated cards valid or using the fallback | 100% | 100% (2 of 86 cards used the fallback) |
| Hidden instructions ignored | 100% | 100% (3 tests) |
| Turns with errors | 0 | 0 |
| Reached review, approved | | 100%, 100% |

8 of 9 targets are met. The open miss is the median: required, risk-high, must-confirm slots (`auth.methods`, `auth.account_model`) cannot be assumed or skipped, and the interview has to ask about them. The lever is a product decision in the catalog (for example a safe default for `auth.account_model`, or letting it be proposed in the playback and confirmed at Gate 1); it is not applied here. These numbers are from mock mode (deterministic fallbacks standing in for the model), so they measure the engine and the catalog, not the model's wording; the live smoke below is the first step toward measuring that.

* **Live smoke (batch A).** `mde_compass.live_smoke` runs one real session plus one direct call of every call type and reports outcome, tokens, cost and latency per call, which calls fell back, spend and the cap; vision and streaming are reported as unsupported. `tests/live/test_live_smoke.py` (marker `live`, allowance USD 0.10 through `COMPASS_LIVE_BUDGET_USD`) writes `packages/compass/evals/reports/live-smoke.json`; `tests/test_smoke_driver_offline.py` runs the same driver in mock mode so it keeps working. The command is in the [runbook](../runbooks/compass.md#5-live-calls-lead-or-the-user-only). It is the lead's or the user's to run; this page has no live measurement.

## Gate 1

* A `gate_decision{approve}` (HTTP `POST /gate`, or a typed yes) is accepted only when there is **no blocker and the supplied version and hash match the current ones**; an approval without version and hash is refused. If `section_hashes` are supplied they must also match.
* Blockers (each a catalog string, shown in the user's language): a `must` slot is still empty; a high-impact assumption (catalog impact at least 4 or risk high) has no explicit decision, **silence never approves**; a hand-off probe has not passed (unless the Brief is flagged `degraded` after 2 repair rounds); a blocking open question; version or hash mismatch; in enterprise mode a required approver role (`approver`) has not approved this hash (the approval is kept in `meta.approvals`, which `brief_hash` ignores, so approving does not move the hash; contributors `approver` and `commenter` are created with an enterprise session).
* A typed yes (`classify_yes`) counts only in `BRIEF_REVIEW`, when the gate is open, the message is exactly an approval phrase (English, Hindi or Hinglish, fillers allowed, at most 8 words, no hedge); "ok but change the colour" is a change request; anything else is unclear.
* Approving the same unchanged Brief again is a no-op. On approval: state `APPROVED`, version labelled `B-G1` (first time), `brief_approved` event, consumer views built (`research`, `blueprint`, `design`, `verify`) and passed to the `HandoffPort` after the commit. `GET /handoff` serves the same views from the approved version and answers 409 before approval.
* An edit of an approved Brief (any change source) voids only the sections whose hash moved, the session returns to `BRIEF_REVIEW` with a "changed since approval" notice, and approval needs a fresh decision.

## Changes and undo

Every content change after the first draft is a `ChangeSet` of `PatchOp`s (`set`, `add`, `remove`, `clear`), addressed by one path grammar: `<section>.<key>` for dict sections (`idea.summary`), `<section>.<ID>[.<field>]` for list sections (`scope.FTR-004.priority`). `engine/patch.py` applies ops to a copy, validates, assigns `version + 1`, records provenance (a user edit becomes `confirmed` with the actor's evidence; a non-user actor can never overwrite a user-given value), computes the **voided sections** (sections whose hash moved) and builds `inverse_ops`. Sources: `ui_edit` (`brief_edit`), `feedback` (`brief_feedback` with a reason chip), `chat` (typed change request, via `cmp.interpret_change`), `assumption` (`assumption_decision`), `lookup` (`lookup_edit`), `undo`, `engine`. A change to `idea`, `context`, `audience`, `scope` or `references` re-seeds research; the probes whose sections moved are re-run. "I don't want this assumption" or a "wrong tone" chip opens a three-variant card (`cmp.make_variants`); a request that is ambiguous returns an either/or card instead of ops. Touching a Locked item is allowed and flagged. **Undo** (`command{undo}`) applies the inverse ops of the latest change set as a new version (`undoes` points at the reverted `CHG-` id); history is never rewritten. `GET .../brief/diff?from=&to=` returns the entries between any two versions.

## API and events

Endpoints: [api/compass.md](../api/compass.md). In short: `POST/GET /v1/compass/sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/events`, `GET /sessions/{id}/stream` (SSE), `GET /sessions/{id}/brief`, `/brief/view`, `/brief/versions`, `/brief/diff`, `/history`, `/provenance/{item_id}`, `POST /sessions/{id}/gate`, `GET /sessions/{id}/handoff`, `/calls`, `/budget`, `/strings/{lang}`, `POST /sessions/{id}/uploads` (multipart, one file per request, answers an `EventAck`), plus `/healthz` and `/readyz`.

**Inbound events** (discriminated by `type`, each with a client-chosen idempotent `event_id`, 1 to 64 characters of `A-Za-z0-9_.:-`): `user_message` (text up to 20,000 characters, `input_mode` text or voice, optional `lang_hint`), `card_answer` (`card_id`, `value` = an option id, a list of ids, ordered ids or a form object, optional `custom_text`), `card_skip`, `card_you_decide`, `card_back`, `upload_added`, `link_added`, `command` (`undo`, `back`, `pause`, `resume`, `start_over`, `finish_with_defaults`, `show_what_you_know`), `brief_edit` (`item_id` path, `value`), `brief_feedback` (`target`, `reason`, `text`), `assumption_decision` (`id`, `accept` or `change`, `value`), `lookup_edit` (`add` or `remove`), `contributor_switch`, `gate_decision` (`approve` or `request_changes`, `version`, `brief_hash`, `section_hashes`). A repeated `event_id` is acknowledged and ignored (`duplicate: true`).

**Outbound events**: `assistant_message` (text, language, "I picked up" chips), `card`, `playback` (editable rows plus what is unclear), `notice` (code, text, chips, severity), `progress` (stage, n, of about), `brief_patch` (change set id, versions, summary, ops, voided sections, undoable), `brief_ready` (version, hash, section hashes, `approvable`, blockers), `brief_approved` (version, hash, baseline), `lookup_preview` (research requests), `probe_result` (kind, passed, gaps, round), `budget_warning`, `error` (code, message), `assistant_delta` (reserved). `budget_warning` and `assistant_delta` are defined but never emitted today.

**SSE replay.** Every outbound event is stored with a per-session `seq`; the stream frame `id` is that `seq`. A client that reconnects with `Last-Event-ID: <n>` (or `?after=<n>`) receives everything after `n`, then the live tail; `follow=false` replays and closes. Idle streams back off between polls, send `: keepalive` every 15 s, close after 900 s and are capped at 8 per caller.

## Static test UI

`static_ui/` (`index.html`, `app.js`, `styles.css`, no build step), served at `/ui/` by the same app under a strict CSP (`script-src 'self'`). It sends the dev auth headers from a Settings dialog (kept in `localStorage`, defaults `dev-token`, `demo`, `demo-user`), reads the stream with `fetch` (EventSource cannot set headers), renders cards and playback, a live Brief pane (sections, badges, edit in place, history drawer, provenance), a lookup tab, a sources tab, real file uploads (a card, the Sources tab and the welcome button), a language switch (English, Hindi), a theme switch, "Updated" tags on rows that changed, optional browser speech input, and a Developer drawer with the spend meter and call log. It renders `BriefViewModel` and events only; it has no engine logic.

## Safety

`safety/screen.py` runs on every free-text field before storage (`Service._sanitize`) and before any prompt: cut to a maximum length, strip hidden characters, redact secrets (`mde_core.redact`), mask card-like numbers (13 to 19 digits, Luhn valid, last four kept), then detect on the redacted text: injection phrases (noted, never obeyed), unsafe requests (illegal, harmful, abuse) and declined categories (the 8 `declined` capabilities, with a polite alternative; a companion app for a declined thing is allowed, and benign phrases such as "game plan" cancel the match). Phrase tables are data (`safety/lexicon.yaml`). A declined request is recorded in `declined[]` and shown, never built. Web, upload and document text is data: no call has tools (hard rule 5). Money is integer micro-dollars; IDs are prefixed ULIDs.

API protections: dev bearer token compared in constant time, identical 401 for every auth failure, tenant isolation by `tenant_id` (a foreign id is a 404), request body limit 256 KB (`COMPASS_MAX_BODY_BYTES`), `X-Request-Id`, security headers (CSP, nosniff, no-referrer) on every response, a refusal to bind a non-loopback host with dev auth.

## Known limitations

* **No vision and no streaming through the gateway** (`ProviderRequest` is text only). Images are accepted as sources with no text, a PDF page without a text layer is reported unreadable (never OCRed), and `assistant_delta` is never emitted.
* **No live evidence.** The live smoke driver exists and has an offline twin test, but this page has no live measurement: call timing, token use, cost, the effect of the real model on card wording and the measured numbers above are all from mock mode. Not built or not run: live batches B to D, cassettes for Compass, prefetch (`prefetch_enabled` is off), a `ChainedRegistry` gateway hook.
* **`cmp.scan_ambiguity` is not called by the service.** The call, its port and its tests exist; no turn runs it.
* **Hand-off is a stub.** `compose.build_service` wires `LoggingHandoff` (logs and keeps the delivered views in memory); nothing consumes the Brief yet. `reopen_slot` is likewise a method waiting for a caller.
* **The service drives a subset of the state table.** `CHANGING` and several triggers (`start`, `playback_confirmed`, `need_more`, `gate_open`, `change_requested`, `change_applied`, `change_cancelled`) exist in `engine/state.py` but `Service` applies changes and edits inside the turn without entering `CHANGING`.
* **`brief/view?lang=`** is validated but not applied: the real `Service.brief_view` has no language parameter, so the view uses the session locale.
* **`SessionSummary.question_n` and `question_of_about`** are always 0; the progress counter is on the card (`progress`) and the `progress` event.
* **`footer.approvable` in the view model** is true outside `in_review`; use `header.status` or the `brief_ready` event.
* **Never emitted:** `budget_warning` (defined and handled by the UI, no engine code raises it) and `CompassBudget.reconcile` (reconciling the Compass counter with the gateway ledger) is never called.
* **Mock readers are heuristic.** The deterministic fallback of `cmp.read_chunk` types sentences by keyword; for example a refund rule can land under roles. The model path adds quote and injection checks on top, but only the live smoke can show how good the real reader is.
* **Windows is unverified.** psycopg's async mode needs a selector event loop; the Context API's launcher sets the policy before any loop exists, `python -m mde_compass` does not (the store engine sets it on creation, which happens inside uvicorn's running loop). Compass was built and run on macOS; PostgreSQL mode is exercised by the integration tests, the in-memory mode by everything else.
* **Fixed since the first version of these pages:** the budget endpoint (the service attribute is now `budget_guard`), the enterprise approval hash (`meta.approvals` is excluded from `brief_hash`).
