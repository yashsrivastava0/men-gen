# BUILD PROMPT 01: COMPASS (the first agent), v1, 2026-11-09

> **Update (2026-11-09, after the build):** superseded in part by `prompts/00-global-rules.md`. Compass lives in `agents/compass/` (not `packages/compass/`); its test UI lives in the global front end `apps/web/` (not `static_ui/`); the global commit policy replaces the "never push / local `main` only" line. Prompt 02 performs the move. Kept below as the record of the first build.

Paste this into Claude Code, opened in the Mobile design engine repo. Before you run it, copy the project's `architecture/` folder into the repo as `docs/architecture-v3/` (the prompt names the sections it relies on). Prefer a strong model (Sonnet/Opus class) for this build; it is a large task.

---

## 0. How to work

You are Claude Code, implementing a new agent in this repo (Windows 11 PC, local git, no remote). I am the planning chat. I cannot see the repo and I do not run code. If this prompt disagrees with the repo about **facts of existing code**, the repo wins. About **what to build**, this prompt wins. Do not ask the owner questions unless you are truly blocked: decide, log the decision in `docs/implementation/DECISIONS.md` (next free D-number), and continue. Write ADRs (next free numbers) where this prompt says so.

Rules that stay in force from the existing repo (verify them in `CLAUDE.md`, `docs/implementation/DECISIONS.md`, `PROGRESS.md`):
- Python 3.13, async, uv workspace, FastAPI, Pydantic v2, SSE. **PostgreSQL only; no SQLite, not even in tests.** No agent frameworks (no LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen, Semantic Kernel, DSPy, instructor, OpenAI Agents SDK). **Only the gateway package imports `openai`.** Prompt templates use `.jinja`. No `.md` files outside `README.md`, `CLAUDE.md`, `AGENTS.md`, `.claude/**`, `docs/**`.
- Offline-first tests: every test runs in mock or replay mode; live runs only in tests marked `live`. Cassettes are committed after the secret scan. A missing cassette fails; it never goes live.
- Layering is enforced by import-linter. Update its contracts for the new package (section 3.3).
- Windows quirks: start servers only with `uv run python -m <module> --port <n>`; never touch Docker Desktop or the database named `mde`; the private Postgres is started with `uv run python scripts/dev_db.py up`; check free memory before any full test suite and run **one suite at a time, in the foreground**.
- Never print, log, copy or commit `.env`. Commit and push according to the global rules (`prompts/00-global-rules.md`): small commits on a task branch, `--no-ff` merge when green, push only if a remote exists.
- Do not edit or delete the old `mde_discovery` package or anything that depends on it. Compass is built **next to it**. (The old package will be retired in a later task.)

Read before you start: the repo `CLAUDE.md`, `PROGRESS.md`, `DECISIONS.md`, the ADRs, the old `mde_discovery` package (to find parts worth copying), `mde_llm_gateway` (call specs, providers, budget ledger, live gating, cassettes), `mde_persistence` (store ports, the fake-vs-real contract suite pattern, migrations). Also read `docs/architecture-v3/`: `00-index-and-decisions.md`, `01-thesis-and-scope.md`, `02-system-architecture.md` (§4 gateway), `05-change-management-and-edge-cases.md` (rows IN-*, IV-*, DC-*), `07-quality-evals-and-models.md` (§2 to §4), and especially `11-discovery-agent-design.md`. Doc 11 is the behavioral source of truth for this task; this prompt adds the engineering, the UI and the limits. If `docs/architecture-v3/` is missing, stop and report.

---

## 1. What Compass is

**Compass** is the agent a person meets first. When someone (a founder, a customer, an enterprise team, a company) arrives on the home page of the product, they talk to Compass. It is the most conversational agent in the platform and asks the most questions. It can talk to individuals and to organizations.

Its **only product is the Brief**: one structured file written for the agents below it (Research first, then Blueprint, Design, the final spec checker, change control). Everything else Compass does (chatting, reading documents, asking, proposing, storing) exists to produce a Brief that is correct, detailed, and useful to those agents.

Three rules shape every decision:
1. **Consumer-driven.** Every Brief field exists because a named downstream agent needs it; every downstream need has a field. Nothing is collected "just in case".
2. **Ask only what the user must decide.** Infer, propose and let the user correct; assume and show the assumption for approval. A correction is cheaper for a user than a blank question.
3. **Nothing is Locked without evidence.** Assumptions are visible, grouped, and approved by the user. Silence never approves.

The user sees the Brief **once**, as a beautiful, structured page, to approve it (Gate 1) or to change it by chatting. The chat history is stored (so changes can be located and explained) but is **never passed to downstream agents**.

Compass does **not**: search the web (it may read a page or file the user points to), design anything, choose technology (the stack is fixed: Expo, React Native, TypeScript), or build any downstream agent. No React front end in this task.

---

## 2. Name and boundaries
- Agent name: **Compass**. Package: `mde_compass`. Folder: `packages/compass/`. Call ids start with `cmp.`. API prefix `/v1/compass`. Dev port **8110**. Persona name in the UI: "Compass" (one constant, easy to change). Grep the repo first; if `compass` clashes with anything, choose the nearest unique name and log it.
- **Everything for this agent lives in that one folder** (code, prompts, schemas, structured call definitions, catalogs, store, API, static test UI, tests, evals). The only allowed exceptions, each to be named in your report: (a) one Alembic migration location if the repo cannot host a per-package branch, (b) a small additive hook in the gateway if call specs cannot otherwise be registered from outside, (c) import-linter config, (d) docs under `docs/` if the markdown rule forbids docs inside the package.
- Compass must **not import** `mde_discovery`, `mde_research`, `mde_spec_builder`, `mde_flow`, or any app. Copy-adapt what is worth keeping from `mde_discovery` (slot state machine, intent routing, redaction, language handling, card schema, scenario harness) **into** the Compass folder, with its tests, so the old package can be deleted later without touching Compass. Compass may depend on `mde_core`, `mde_contracts` (if useful), the public client API of `mde_llm_gateway`, and `mde_persistence` utilities.
- Later the Context API will mount Compass's router. Design `create_app()` and a `router` so that both a standalone test server and later mounting work.

---

## 3. Folder layout (guide; adapt to repo conventions, keep one folder)

    packages/compass/
      README.md                      what it is, how to run (mock and live), how to test, how to open the test UI
      pyproject.toml
      src/mde_compass/
        __main__.py                  `python -m mde_compass --port 8110`
        config.py                    all tunables in one place (model id, effort per call, caps, thresholds, limits)
        domain/                      Pydantic models: Brief, Slot, Claim, Source, Card, Assumption, ResearchRequest, Event, ChangeSet, ...
        catalog/                     DATA files (yaml/json), no logic:
          slots.yaml                 the slot catalog (section 5.1)
          frames/                    question frames (section 6.3)
          families.yaml              ~16 capability families with typical features (neutral seeds; section 5.6)
          capabilities.yaml          tier B hints, tier C and declined categories with alternatives
          strings/                   fixed UI and fallback texts: en.json, hi.json (Hinglish handled by the model)
        calls/                       ONE SUBFOLDER PER STRUCTURED LLM CALL (section 7)
          read_chunk/ merge_claims/ situate/ understand_turn/ answer/ write_card/
          scan_ambiguity/ seed_research/ probe_handoff/ compile_text/ interpret_change/ make_variants/
            spec.py  schema.py  prompt.jinja  examples/*.json  mock.py  tests/
        engine/                      control flow in CODE: state machine, turn loop, question queue (value of information),
                                     slot rules, assumptions, research seeding, probes, changes, gate, handoff
        intake/                      file and URL reading (section 6.5)
        store/                       ports + Postgres implementation + in-memory fake + migrations + contract tests
        views/                       deterministic renderers: BriefViewModel, consumer views, readiness, diffs
        safety/                      redaction, injection tagging, SSRF guard, declined-category guard
        api/                         FastAPI router, request/response models, SSE
        static_ui/                   index.html, styles.css, app.js (section 8)
      tests/                         unit/, property/, scenarios/ (yaml), contract/, api/, ui_smoke/, evals/
      evals/                         persona scripts, synthetic PRD generator, ground truth, scoring

### 3.1 Import-linter and registration
Add a layering contract for `mde_compass` (allowed imports as in section 2; nothing may import Compass except apps). The gateway must never import Compass: **register** Compass's call specs, prompt renderers and mock handlers with the gateway through the existing registration mechanism, or add a minimal additive registry hook to the gateway (no behavior change for existing callers). Call specs defined in the Compass folder are the single definition; the gateway only receives them at startup.

### 3.2 Decisions and ADRs
Write: ADR "Compass package, boundaries and layering"; ADR "Brief schema v1, hashing and versioning"; ADR "Call-spec registration from agent packages". Log D-numbers for every dependency you add (each must be pure Python, permissively licensed, pinned with uv).

---

## 4. Model, reasoning, budget

- **Model:** `gpt-6-luna` only, through the existing gateway (OpenAI Responses). No other provider in this task. Do not add the Anthropic provider here. Give every call spec a `lane` field (`writer`, `critic`, `canary`) so a second vendor can be added later without redesign.
- **Verify in the first live smoke call** that the model id is available, strict JSON schema works, effort levels work, and whether image input works. If the model is unavailable, **stop and report**; never substitute silently.
- **Reasoning effort** per call is in section 7 (starting values). Reasoning tokens count against `max_output_tokens`, so set each call's budget generously and measure in the smoke test. Latency matters for chat: heavy calls run at high effort but are overlapped (section 6.3, prefetch) or run in the background with progress shown.
- **Owner's live-test cap: USD 2.00 in total for all live testing of Compass, hard maximum** ("cost is not the main factor; $1 is probably enough; the cap is $2"). Implement it in code, additive to the existing global cap and ledger: `COMPASS_LIVE_BUDGET_USD=2.00`, clamped (no value above 2.00 is accepted), effective stop at 95% (1.90), per-session cap 0.25, fails closed. Tag ledger spend with the agent name if the ledger supports tags; otherwise keep a Compass-only counter that is reconciled against the ledger. Show "spent / cap" in the test UI. Live calls still need `LLM_MODE=live`.
- **The owner pre-authorizes only these live batches, within the $2 cap** (details and allowances in section 9.4): A smoke, B conversations, C documents, D changes and probes. Print the batch plan and expected cost before each; report the actual spend after each; stop immediately if a batch crosses its allowance. Keep at least USD 0.50 in reserve.

---

## 5. Data contracts

### 5.1 Slot catalog (implement as data: `catalog/slots.yaml`)
Levels: **must** (cannot leave empty; may be assumed with approval), **should** (ask if budget allows, else assume), **may** (infer and list). Filled by: U user, D document, I model inference with a stated basis, P pattern default from `families.yaml`. Each slot also declares: `consumers` (who needs it), `impact` (how many downstream decisions and how costly a wrong guess), `risk` (hard to undo or sensitive), `effort` (tap, text, upload), `value_schema`, `default`, and `ask_if` (a precondition expression).

| Group | Slot | Level | Consumers | Filled by |
|---|---|---|---|---|
| Idea | `idea.summary` (one line + pitch) | must | all | U, D |
| | `idea.category`, `idea.subcategory` | must | Research, Blueprint | I, confirmed in playback |
| | `idea.business_model` | must where money flows | Research, Blueprint | U, P |
| | `idea.problem_value`, `idea.differentiator` | should | Research, Design | U, D, I |
| Context | `context.geo` (country, city, region) | must | Research, Blueprint, Design | U, D |
| | `context.scale_launch` (pilot size, timeline) | should | Blueprint, Front-end, QA | U, P |
| | `goals.primary`, `goals.success_measures` | should | Blueprint, QA | U, D, I |
| Audience | `audience.segments` (who, in plain words) | must | Research, Design, Screens | U, D |
| | `audience.traits` (age, tech comfort, languages, accessibility) | should | Design, Screens | U, I |
| | `audience.usage_context` | may | Design, Front-end | I, P |
| Apps and roles | `apps.list` (purpose, surface) | must | Blueprint, Design | I, U |
| | `roles.list` (user's words, goal, rough permissions) | must | Blueprint | I, U |
| Scope | `scope.features` (status, intent, priority, size, tier) | must | Blueprint, Research, Change control | D, I, U |
| | `scope.priority` (MVP boundary) | should | Blueprint | U |
| | `scope.must_not` | may | Verify, Change control | U, D |
| | `scope.references` ("like X": borrow, avoid) | should | Research, Design | U |
| Experience | `brand.assets`, `brand.name` | should | Design | U |
| | `look.tone_words`, `look.visual_references`, `look.density_mode` | should | Design | U |
| | `locales` (chat, app languages, right-to-left) | must | Design, Screens | U, I |
| | `devices.profile` | should | Design, Front-end | I, P |
| Access | `auth.methods`, `auth.account_model`, `onboarding.approvals` | must / should | Blueprint | U, P |
| Data | `integrations.wanted` (real later or mock) | should | Blueprint, Backend | U, P |
| | `data.existing_systems` (APIs, databases; uploads) | should | Blueprint, Backend | U, D |
| Limits | `constraints.*` (offline, performance, accessibility, must-use, must-not) | may | Blueprint, Front-end | I, P |
| Words | `glossary.terms` (term, synonyms, avoid) | should | Research, Blueprint, Screens | D, U, I |
| Unknowns | `unknowns.research_wanted` | generated | Research | I |
| Enterprise | org profile, approvers, design system, API catalog | should in enterprise mode | all | D, U |

Slot states: `empty`, `assumed`, `volunteered`, `confirmed`, `disputed`. Rules: a correction overwrites (history kept); an explicit negation clears; an `assumed` value never replaces a `confirmed` one; a slot the user did not touch is never reset. **No-ask list:** stack and framework, hex colors, hosting, legal wording, anything not tied to a consumer decision, anything inferable with high confidence and low impact.

### 5.2 The Brief (canonical `brief.json`, schema version 1)
Top-level keys: `meta` {program/session ids, mode (guided|enterprise), situation_profile, locales, schema_version}; `idea`; `context`; `audience`; `apps` [{id APP-###, name, purpose, surface, audience, roles[]}]; `roles` [{id ROL-###, name_in_user_words, goal, rough_permissions[]}]; `scope` [{id FTR-###, name, intent, apps[], family, status in|later|out|unsupported|needs-engineering, priority must|nice|later, source, size{screens,entities,integrations}, capability_tier A|B|C}]; `references`; `experience`; `access`; `integrations`; `data`; `constraints`; `glossary`; `assumptions` [{id ASM-###, claim, reason, basis, impact low|high, confidence, theme, status}]; `open_questions`; `research_requests` (5.4); `assets` [{id, kind, summary, pointer, consumers[]}]; `sources` [{id SRC-###, kind, name, sha256, coverage}]; `declined` [{what, reason, alternative}]; `readiness` {research, blueprint, design, verify: pass|gaps[]}.

**Every item** (not only top-level keys) carries `tier` (Locked | Default | Open), `provenance[]` ({type: user_tap|user_text|voice|document|assumed|pattern|model_knowledge|org_profile, ref: turn id or SRC id + pointer, quote, date}), `confidence` 0..1, `status`, and `history[]` (previous values with turn ids, for undo and "where did this come from").

**Tier rules (code, tested):** Locked only if provenance includes `user_tap`, `user_text`, `voice`, `document` or `org_profile` with a non-empty evidence reference. `assumed`, `pattern`, `model_knowledge` can only produce Default or Open. A Locked item without evidence is rejected by the validator.

### 5.3 IDs, versions, hashes
Stable IDs (`APP-`, `ROL-`, `FTR-`, `ASM-`, `SRC-`, `RR-`, `Q-`) are assigned once per session and **never reused**; deletions leave a tombstone; renames add an alias. The Brief has an integer `version` that increments on every committed change. `canonical_json()` (sorted keys, UTF-8, no volatile fields) feeds sha256 for the whole Brief and for each section. **Approval binds to the whole hash and the section hashes.** Any later change voids only the affected sections' approvals. The approved version becomes baseline `B-G1` and is immutable.

### 5.4 Research requests (what Research should look up)
Each request: `id RR-###`, `topic`, `question`, `decision_unlocked`, `consumer`, `geography`, `source_languages[]`, `terms[]` (user vocabulary plus synonyms), `references[]`, `prefer_sources[]`, `avoid[]` (from the user's exclusions), `depth quick|standard|deep`, `priority`, `shown_to_user_as`. A request without a geography, an entity or terms is invalid. Also `search_hints`: disambiguated names, synonyms, local competitors named by the user, languages to search in.

### 5.5 Question card (schema)
    {
      "id": "Q-014", "frame": "business_model", "slots": ["idea.business_model"],
      "type": "choose_one | pick_many | priority | confirm | look_signals | quick_form | upload_or_link | open_prompt",
      "question": "...", "why_we_ask": "...",
      "options": [{"id":"A","label":"...","basis":"your_document|typical|suggestion|your_words",
                   "recommended":true,"reason":"...","sets":{"idea.business_model":"..."}}],
      "custom_input": {"enabled": true, "placeholder": "...", "voice": true},
      "you_decide": {"enabled": true, "assumes": "A"},
      "skip": {"enabled": true},
      "progress": {"n": 3, "of_about": 7}, "language": "en"
    }

### 5.6 Seed files (data, not logic)
- `families.yaml`: about 16 capability families as neutral seeds: identity and access; catalog and commerce; booking and scheduling; delivery and logistics; payments and wallet; communication; content and community; workflows (forms, approvals, tasks); dashboards and admin; finance; health and wellness; learning; travel and hospitality; marketplaces; civic and utilities; enterprise workforce. Each lists at least six typical features, the roles that usually appear, and typical integrations. These are **seeds to ground proposals**, not archetypes: the model may propose items outside them, flagged `custom` and confirmed by the user. (A real Module Library arrives later; keep the file format simple to swap.)
- `capabilities.yaml`: tier B features (real push, live location, biometrics, payment SDKs, camera, offline sync, in-app purchases, Bluetooth, video calls) and **declined categories** with alternatives: games of any kind (companion app instead); AR/VR/3D; photo or video editors; DRM streaming players; trading terminals; OS-level utilities; medical-device or safety-critical software; proprietary-hardware apps. Use both a model flag and a multilingual keyword guard; decline early, politely, with the alternative.

### 5.7 Events and API (stable; React will reuse it later)
Inbound events (idempotent by `event_id`): `user_message{text,input_mode text|voice,lang_hint}`, `card_answer{card_id, value}`, `card_skip`, `card_you_decide`, `card_back`, `upload_added{source_id}`, `link_added{url}`, `command{name: undo|back|pause|resume|start_over|finish_with_defaults|show_what_you_know}`, `brief_edit{item_id,value}`, `brief_feedback{target, reason, text}`, `assumption_decision{id, accept|change}`, `lookup_edit{add|remove}`, `contributor_switch{contributor_id}` (testing, enterprise), `gate_decision{decision, version, section_hashes}`.
Outbound: `assistant_message`, `assistant_delta` (only if the gateway can stream; otherwise full messages), `card`, `playback`, `notice`, `progress`, `brief_patch`, `brief_ready`, `lookup_preview`, `probe_result`, `budget_warning`, `error`. Every outbound event is stored with a per-session sequence number for SSE replay (`Last-Event-ID`).

Endpoints (prefix `/v1/compass`; dev auth headers as in the repo):
- `POST /sessions` (mode, locale, user_label); `GET /sessions` (home page: your projects); `GET /sessions/{id}` (status, progress, version, spend)
- `POST /sessions/{id}/events`; `GET /sessions/{id}/stream` (SSE)
- `POST /sessions/{id}/uploads` (multipart; limits in 6.5)
- `GET /sessions/{id}/brief` (canonical, `?version=`); `GET .../brief/view` (display view model, 6.9); `GET .../brief/versions`; `GET .../brief/diff?from=&to=`
- `GET /sessions/{id}/history` (paged events; `?slot=` or `?q=` returns the relevant slice); `GET .../provenance/{item_id}` (quotes, sources, value history)
- `POST /sessions/{id}/gate`; `GET /sessions/{id}/handoff` (after approval: consumer views, 6.8)
- `GET /budget` (dev only), `GET /healthz`, `GET /readyz`, static UI at `/ui/`

### 5.8 Store (PostgreSQL; JSONB for flexible payloads; tables prefixed `compass_`)
`sessions`, `events` (append-only), `sources` (+ parsed chunks), `claims`, `slots` (+ history), `features` (scope ledger), `assumptions`, `questions` (ledger: card, answer, why asked, scores), `research_requests`, `brief_versions` (full JSON, hash, section hashes, baseline flag), `glossary`, `contributors`, `change_sets`, `call_log` (call id, version, tokens, cost, latency, outcome). Repository **ports** with a Postgres implementation and an in-memory fake, both validated by one shared contract suite (repo ADR on fakes). Constraints: one active turn per session (advisory lock plus unique partial index), optimistic `version` on the Brief, idempotent events, transactions never span a model call.

---

## 6. Behavior

### 6.1 Situation Profile (adapt to most situations)
In the first turn, infer (never interrogate) and store: input richness (nothing / one sentence / rich / documents), clarity (vague / knows features / knows everything), expertise (non-technical / product / technical), size and stakes (tiny / startup / enterprise), reference-driven (a "like X" present), collaboration (one / several), language and mode (languages, voice or typing), urgency ("just build it" or thorough), register (casual or professional). These choose the **Discovery Plan**: which conversation modules run, depth profile (Express: at most 5 questions; Standard: about 7, never more than 10; Large/enterprise: per module, documents first), the voice and vocabulary (no jargon for non-technical users). The profile can be revised as the conversation reveals more.
**Minimum Viable Brief ("just build it"):** still ask what it is, for whom, where (country, city, languages) and which apps; assume and list everything else.

### 6.2 Pipeline (twelve steps; code decides, models help with language and inference)
0 Welcome (canned, localized: four starts: describe your idea, upload your documents, start from an app you like, just build it; voice button; language choice). 1 Intake (code). 2 Read each chunk in parallel (`cmp.read_chunk`): typed statements with source pointers. 3 Merge (`cmp.merge_claims`): dedupe, contradictions, coverage. 4 Situate (`cmp.situate`): Situation Profile and Domain Hypothesis (category and sub-category, business-model candidates, likely roles and features grouped by family, hard parts, glossary candidates); everything labelled a hypothesis. 5 Playback card: "Here is what I understood", editable in place. 6 Gap analysis (code): compare slots with consumer needs, score gaps, build the queue. 7 Interview loop. 8 Propose-then-correct checklists (roles, features, integrations, vocabulary). 9 Seed research (`cmp.seed_research`) and show **What we will look up for you** as editable chips. 10 Handoff probes (`cmp.probe_handoff`). 11 Assumption sweep (code). 12 Compile (`cmp.compile_text` plus code) and open Gate 1. Later stages may reopen one slot with a single targeted question; that is a typed back-edge (a stub hook now), never a new interview.

### 6.3 The question system (fixed purpose and shape, generated content)
Hard-coded questions per category bind users and ask irrelevant things; free-form AI questions invent options. Therefore:
- **Fixed (data):** the slot catalog; question **frames** (purpose, slot(s), card type, how options are sourced, validation hints); card shape; safety rules; a plain **deterministic fallback card per slot** (English and Hindi, neutral wording).
- **Generated (`cmp.write_card`):** the question wording in the user's own words and language, the options, the one-line "why we ask", the recommended option and its reason.
- **Basis labels** on every option: `your_document`, `typical` (only if backed by `families.yaml`), `suggestion`, `your_words`. No invented statistics. Never say "most people choose X" without a source.
- **Validation (code, before display):** exactly three distinct concrete options (or a checklist); each option maps to a valid slot value; no tier C capability; nothing outside `families.yaml` unless flagged `custom`; no brand names unless the user used them; neutral, non-leading wording; question at most about 20 words, options at most 8; language matches the user; exactly one recommended option with a reason; custom input, "You decide", and "Skip for now" always present; one idea per card. Two validation failures in a row: use the fallback card.
- **Frames (about 14):** idea in your words; confirm the kind of app; business model; who uses it and where; which apps; roles; features (checklist, conservatively pre-ticked: only strongly typical items ticked, extras offered unticked); priorities (forced ranking when "everything is a must"); apps you like and what to borrow or avoid; brand and tone (mood words, not full designs; the Design stage makes real directions); login and accounts; integrations and existing systems; scale and launch; limits (offline, devices, accessibility).
  The same frame must work in any domain. Example, "How will it make money?": food marketplace options "a small commission on each order / delivery fees paid by customers / a monthly plan for restaurants"; clinic booking options "patients pay per appointment / clinics pay a monthly fee to be listed / free for patients, clinics pay per booking"; both with "Something else", "You decide", "Skip".
- **Choosing the next question (value of information, pure function with config weights and unit tests):** score **impact** (number of dependent consumers and cost of a wrong guess, from the catalog), **inferability** (the model proposes a value and a confidence), **risk**, **effort**. High impact and low inferability: ask. High impact and high inferability: propose and confirm. Low impact: assume and list. Risky: ask and mark must-confirm. Order follows dependency (category and geography, apps and roles, features, access, experience, integrations, limits). Batch related slots into one card or form. Recompute the queue after every answer: one answer can settle several slots and remove cards.
- **Experience rules:** show "Question n of about N" honestly and update N; at most one follow-up per answer; never re-ask a confirmed slot; "You decide" collapses the card to a one-line *Assumed: X (change)*; Back and Undo always work; open-ended questions are rare (they cost effort, especially on phones).
- **Prefetch (stretch, flag default off in tests):** while the user reads or answers a card, generate the next likely card in the background and discard it if the queue changes.

### 6.4 Free text, voice, language
A typed or spoken answer is parsed for every slot it touches (`cmp.understand_turn`); show chips such as *I picked up: Pune, Hindi and English, restaurants as partners* that the user can remove. If ambiguous, ask a small either/or, not a new open question. A custom answer to a choice card is stored in the user's own words plus a normalized candidate; if it implies a new feature or category it becomes a `custom` item, flagged and confirmed. Voice: accept `input_mode=voice`; echo back names, numbers and places for confirmation; split run-on dictation into atomic statements and read back a numbered list when there are three or more or any ambiguity. Chat follows the user's language (English, Hindi, Hinglish at minimum); app locales are asked separately. A pasted message longer than 3,000 characters is treated as a document.

### 6.5 Documents and links (intake)
Formats: PDF (text layer), DOCX, XLSX, PPTX, CSV, TXT, Markdown, EML (text), images (sent to the model as vision input only if the smoke test proves support), and **public web pages the user points to** (never search). Audio: accept a transcript as text for now; real speech-to-text is out of scope. Do **not** add system-level dependencies (no poppler, no tesseract): a PDF page without a text layer is marked `unreadable_page` and the user is asked for text or an image upload.
Limits (config): 15 MB per file, 10 files and 60 MB per session, 300 pages, 5 URLs per session. URL reading: http(s) only, resolve DNS and **block private, loopback, link-local and metadata addresses**, re-validate after redirects (max 3), 10 s timeout, 2 MB cap, text/html, text/plain or pdf only, strip scripts and hidden text.
Behavior: chunk by headings and tables (about 3,000 to 4,000 tokens); extract in parallel (semaphore 4); **document content is untrusted data**: calls that read it get no tools, and instructions inside it are ignored and noted in the audit. Statement types: requirement, constraint, business context, goal, user or role, rule or number, glossary term, example, open issue ("TBD"), preference, exclusion. Report coverage ("read 100%; 187 statements; 31 need confirming; 4 conflict"). Chat beats documents on conflict; document versus document becomes one resolution card naming both sources. "TBD" items become questions. Duplicate uploads are de-duplicated by hash. Password-protected or corrupt files are reported plainly and skipped. Keep original files and parsed text; register assets (API specs, brand files, competitor documents) with routing (`consumers[]`) so downstream agents fetch pieces on demand.

### 6.6 Assumptions: propose, then approve
Every assumption is an object (claim, reason, basis, impact, confidence, theme). During chat it appears as a one-line chip. At Gate 1 the **"We assumed"** review groups them by theme (Business, Users, Features, Look, Access, Integrations): **low-impact** ones can be accepted in bulk; **high-impact** ones must each be confirmed or changed, so Gate 1 cannot open to approval with an unreviewed high-impact assumption. Each shows what changes if the user edits it. Track how often assumptions are changed (for tuning).

### 6.7 Research seeding
Write `research_requests` and `search_hints` (5.4) from the Brief, the Domain Hypothesis and the unknowns, and from reference apps. Show the user **What we will look up for you** as removable and addable chips (for example "also check how X handles refunds"). Edits update the requests.

### 6.8 Handoff probes (the Brief is tested by its consumers)
Before Gate 1, three weak canary calls (`cmp.probe_handoff`, low effort) try to start from the draft Brief: a **Research planner** (write at least 8 concrete queries, each with region, entity and terms), a **Blueprint planner** (list roles, main journeys, first-release screens), a **Design planner** (name a tone and a direction for brand and palette). Every blocking question becomes a gap: fixed by an assumption (shown for approval) or one card. Gate 1 opens for approval only when all three pass (max 2 repair rounds; then flag `degraded` and show it). After approval, `GET /handoff` returns **consumer views** built by code: a Research view (idea, category, business model, geography, languages, audience, references, feature intents, glossary, research requests, search hints, declined), a Blueprint view, a Design view, and a Verify view (full scope ledger and must-not list). Views contain **no chat transcript**; tests assert it.

### 6.9 The Brief page the user sees (beautiful and structured)
The API returns a **BriefViewModel** (built by deterministic code, display-ready, so any front end renders it without logic):
- `header`: project title, one-line idea, version, status (draft, in review, approved), readiness chips (*Ready for Research, Blueprint, Design* or the gaps), review progress (reviewed n of m sections).
- `sections` (fixed frame, dynamic content): Your idea · Who it is for · Apps and roles · Features (grouped by app and family, each with in / later / out and priority) · Look and tone · Access · Integrations and data · Limits · Words we will use · We assumed · Still unknown · What we will look up · Sources read · What we cannot build (with alternatives).
- Each `row`: id, label, formatted value (text, list or chips), badges for provenance (*you said*, *from your document*, *assumed*, *typical for apps like this*, *our suggestion*) as text plus icon (never color alone), `editable`, `history_available`, `needs_review`.
- `footer`: primary action "Looks right", secondary "Change something", and the list of blockers if approval is not yet possible.
Language of labels follows the session language (English and Hindi strings in `catalog/strings`). The same data feeds the static UI now and React later.

### 6.10 Changes, feedback and chat history
The user can change the Brief at any time (before and after approval) in five ways: inline edit of a row; "I'd change this" on a section or row (a short reason: *not what I meant, too much, something missing, wrong tone, I don't want this assumption* plus free text); chatting ("I didn't like the features list"; "make the app Hindi first"); toggling features or assumptions; editing the lookup chips.
Flow for chat or feedback changes: (1) detect `change_request` intent; (2) resolve the **target** (a section, row, slot or feature) by code from ids and glossary, and if ambiguous ask a small either/or card; (3) build a **history slice** (the targeted items' value history with their quotes and turn ids, the last six turns, and older turns retrieved by slot or keyword) and call `cmp.interpret_change` to produce patch operations; (4) if the user disliked an assumption or wording, call `cmp.make_variants` for three concrete alternatives as a card; (5) validate the patch on a copy (slot rules, tiers, consumer needs); (6) apply it: version +1, voids approvals only for affected sections, re-seeds research requests if geography, category, audience, references or features changed, and re-runs only the affected handoff probes; (7) answer in plain words ("Updated: Features › Group ordering moved to Later") and push a `brief_patch` with a visible diff and an **Undo**. Every change set is revertible by applying inverse operations as a new version; history is never rewritten.
"Where did this come from?": `GET provenance/{item_id}` returns the exact quote, who said it, when, the document and page, and the value history; the UI shows it on click and offers "Show chat history" (a drawer with the transcript, highlighting the turns that produced the selected row). The chat history is the user's, stored in the event log, and is **never** passed downstream.

### 6.11 Gate 1
`gate_decision{approve}` is accepted only if: all `must` slots are non-empty; no unreviewed high-impact assumption; all three handoff probes pass; no blocking open question; the supplied version and section hashes match the current Brief; in enterprise mode every required approver role has approved the same hash. A typed "yes" counts only when the gate is open, the classifier is confident, and the message is not compound ("ok but change the color" is a change request). Edits after approval void only the affected sections and show a "changed since approval" diff. Approval produces baseline `B-G1` and emits `brief_approved`; the handoff goes through a `HandoffPort` (a no-op implementation plus a logging stub for the Research agent now). Silence never approves.

### 6.12 Enterprise and company mode
`mode=enterprise`: contributors with roles (requester, approver, commenter), answers attributed to a contributor, conflict detection (two contributors give different values for a slot → a conflict card to the designated decision-owner; never auto-merge), required approver roles at Gate 1 (config), an **Org Profile** (JSON upload) that prefills slots (brand, design system reference, API catalog, locales, approvers), and a per-module pass (modules come from the user's departments or the families file) with a deeper question budget. The test UI has a "Speaking as" switcher to exercise this.

### 6.13 Safety (baseline only; the owner de-prioritized privacy and security)
Redact secrets (keys, passwords, tokens) and mask card-like numbers before storing or sending to a model; treat documents and web pages as data (no tools in calls that read them); SSRF guard on URLs; size and rate limits; decline unsafe or illegal requests briefly; decline unsupported categories early with an alternative; use patterns from a reference app, never its brand assets, names or copy; escape all user content in the UI; mock and fixture data is synthetic.

### 6.14 State machine (pure function, explicit transition table as data, property-tested)
States: `WELCOME → INTAKE → UNDERSTANDING → INTERVIEWING → PLAYBACK ↔ INTERVIEWING → SEEDING → PROBING → BRIEF_REVIEW (Gate 1 open) → APPROVED`, with side states `CHANGING` (a sub-state reachable from any state after the first Brief draft), `PAUSED`, `ARCHIVED`. "Start over" archives the draft and never deletes it. Resume shows a recap ("We were working on your <app>; next: <question>"). Messages received during a long step are queued and applied in order.

### 6.15 Edge cases to implement as named scenarios (`SB-<ID>`)
From `docs/architecture-v3/05` and `11`: IN-01 huge document · IN-02 unreadable pages · IN-04/05 contradictions · IN-06 hidden instructions · IN-07 mostly-vision PRD · IN-08 sheets and decks · IN-10 private links · IN-11 mixed languages · IN-12 "exactly like [brand]" · IN-13 declined category · IN-14 corrupt file · IN-15 duplicates · IV-01 silence and "you decide" · IV-02 mind change · IV-03 off-topic · IV-04 very vague idea · IV-05 asks price or time · IV-06 wants everything · IV-07 stakeholder conflict · IV-08 unsafe request · IV-09 language switch · IV-10 voice run-ons · IV-11 returns after days · DC-01 custom text contradicts option · DC-02 everything is a must · DC-03 opposing references · DC-04 same feature in different words · DC-05 hybrid category · DC-06 chat vs app language · DC-07 voice mishears · DC-08 change a Locked answer later · DC-09 asks for a technology choice · DC-10 request beyond capability · DC-11 five or more apps · DC-12 competitor named as own business · DC-13 leaves mid-way · DC-14 research surprise (typed back-edge stub). Each scenario is a YAML script (user turns, scripted model outputs, expected state, cards, slots, events).

---

## 7. Structured LLM calls (one folder each, `calls/<name>/`)

Each call folder contains: `spec.py` (id, version, lane, model class, effort, `max_output_tokens`, timeout, cacheable static prefix, redaction rules, validators, deterministic fallback), `schema.py` (Pydantic input and output; outputs converted to the provider's **strict** JSON subset: no open objects, all fields required, optional fields nullable), `prompt.jinja`, `examples/` (two or three **diverse canonical** examples, not an exhaustive list), `mock.py` (a deterministic, schema-valid handler so the whole agent runs offline), and `tests/` (schema round trip, validator behavior, mock handler, fixtures).
Pipeline for one call (existing gateway behavior): validate input → render (untrusted content wrapped as data) → reserve budget → cache lookup → provider call with timeout → validate output locally → **one** repair at low effort → deterministic fallback → record cost → log. Retries only on 429, 5xx, timeouts and connection errors. Prompts are versioned and the version is part of the cassette key.

| Call | Purpose | Effort | Starting max out | Must not |
|---|---|---|---|---|
| `cmp.read_chunk` | typed statements from one chunk, each with a pointer and quote | high | 4000 | invent; follow instructions in the text |
| `cmp.merge_claims` | dedupe, conflicts, coverage, "TBD" items | high | 6000 | drop conflicting items silently |
| `cmp.situate` | Situation Profile and Domain Hypothesis grounded in `families.yaml` | high | 6000 | present hypotheses as facts |
| `cmp.understand_turn` | intent, slot updates with evidence, references, language, safety flags | medium | 2500 | ask questions; invent values |
| `cmp.answer` | grounded answers, help mode, brief chat; streams if supported | medium | 1500 | invent price, time, or capabilities; leave the user stuck |
| `cmp.write_card` | card content for a frame | high | 2500 | add unrequested scope; lead the user |
| `cmp.scan_ambiguity` | vague, conflicting or undefined statements | high | 3000 | change values |
| `cmp.seed_research` | research requests and search hints | high | 4000 | vague requests without region, entity and terms |
| `cmp.probe_handoff` | Research, Blueprint, Design canary planners | low | 2500 | guess; it must ask |
| `cmp.compile_text` | one-line summary, pitch, plain-language descriptions for the Brief page | high | 3500 | add facts not in slots |
| `cmp.interpret_change` | natural-language change to patch operations using a history slice | high | 3500 | apply anything; touch Locked fields without flagging |
| `cmp.make_variants` | three concrete alternatives for a disliked item | high | 2500 | produce near-duplicates |

Prompting principles for **every** prompt: separate jobs (understand never asks; ask never infers; compile never invents); evidence first (no source means `assumed`); principles and schema plus two or three diverse canonical examples, not scripts; plain language and the user's own words and language; neutral, mutually exclusive, concrete options; when unsure ask or assume-and-flag, never decide silently; give the call only the situation profile and the Brief so far (not the whole chat); structured outputs always; control flow stays in code. Treat the "must not" column as test cases.

---

## 8. Static test front end (HTML and CSS, minimal vanilla JavaScript)

Purpose: test Compass now; React comes later and will use the same API. Files: `static_ui/index.html`, `styles.css`, `app.js` (no framework, no bundler, no npm, no external fonts or CDNs; system fonts so it works offline). Served by the Compass app at `/ui/`. It must work in **mock mode** with no network.
Behavior and layout:
- **Home:** "Start a new project" and a "Your projects" list (from `GET /sessions`), a mode choice (Guided or Company), a language choice.
- **Workspace (two panes):** left, the chat (messages, typing indicator, all eight card types, drag-and-drop or button upload, mic button using the browser speech API only if present, quick actions Back, Undo, Skip, You decide); right, tabs: **Brief** (live view-model rendering, rows appearing and turning from assumed to confirmed), **What we will look up**, **Sources**, **History**.
- **Brief page quality bar:** calm neutral palette with one accent, light and dark themes through CSS variables, 8 px spacing grid, a clear type hierarchy, section cards with consistent structure, provenance badges with text and icon, readiness chips, a sticky approval bar ("Looks right", "Change something") that lists blockers when disabled, changed rows highlighted with an *Updated* tag and an Undo toast, responsive down to phone width, keyboard accessible, visible focus states.
- **Change UX:** "I'd change this" on every section and row opens a small panel (reason chips plus free text) and puts a "Changing: Features › Group ordering" chip into the chat; the history drawer shows the transcript with the turns behind the selected row highlighted.
- **Developer drawer** (toggle): raw event stream, state and plan, slot table, the last calls with tokens, cost and latency, mock or live indicator, and a prominent **spent / cap** meter (cap USD 2.00).
- **Safety:** render user content with `textContent` only (no unsafe HTML); send a restrictive CSP header (same-origin only); no inline scripts.

---

## 9. Tests, evaluations and live batches

### 9.1 Offline (must all pass; run one suite at a time)
Unit (pure functions: value-of-information scoring, tier rules, card validators, hashing, state machine, patch application, view-model building, consumer views); **property tests** for invariants (no approval without a matching hash; approved versions are immutable; IDs never reused; Locked requires evidence; research and assumptions never produce Locked; silence never approves; unvalidated model output is never persisted; events are idempotent; consumer views never contain transcript text); the store **contract suite** run against the fake and real Postgres; API tests (httpx) including SSE replay; scenario scripts for every ID in 6.15; ten **persona scripts**: silent, chatty, confused, expert, Hinglish, enterprise stakeholder conflict, voice run-ons, adversarial (injection and unsafe), returns after days, change-heavy; UI smoke (static files served, referenced assets exist, CSP header present, view-model contract matches what `app.js` reads).

### 9.2 Evaluation harness (`evals/`)
A synthetic PRD generator (seeded requirement lists, rendered to DOCX, PDF text, Markdown, XLSX), a ground-truth matcher, and scoring that computes: requirement recall and precision, Locked-without-evidence count, questions per Brief, share of questions that changed a downstream decision, assumptions unchanged at Gate 1, handoff probe pass rate, card validation pass rate. Runs in mock/replay mode on every change; a few cases run live in batch D.

### 9.3 Definition of the numbers (initial targets, to be measured and reported honestly)
Recall and precision at least 90% on PRD-led cases; Locked without evidence 0; questions median at most 7 and never above 10 (Express at most 5); at least 85% of asked questions change a downstream decision; at least 80% of assumptions unchanged at Gate 1; all three handoff probes pass before Gate 1; 100% of generated cards pass validation or use the fallback; hidden-instruction tests 100% ignored; time to Brief about 3 minutes (Express) and 10 minutes (Standard) of interactive time.

### 9.4 Live batches (total cap USD 2.00; reserve at least 0.50; each needs `LLM_MODE=live` and a printed plan first)
- **A. Smoke (allowance 0.10):** one call of each type with real `gpt-6-luna`; confirm model id, strict schema acceptance, effort, token and cost reporting, vision support (yes or no).
- **B. Conversations (allowance 0.60):** four full sessions to a Brief: a vague non-technical founder; a clear small business; a Hinglish user; a company with an Org Profile and two contributors.
- **C. Documents (allowance 0.50):** a clean PRD, a messy PRD with contradictions and a hidden instruction, one image if vision works.
- **D. Changes and probes (allowance 0.30):** at least six change requests (including a dislike of an assumption and an out-of-scope feature), research seeding quality, handoff probes.
Record cassettes (secret-scan them), then replay offline. Report spend after each batch; stop if an allowance is crossed.

---

## 10. Phases (commit per phase when green; stop and report on a blocking conflict)
0. **Audit and plan** (read-only): reuse map of `mde_discovery` and gateway/persistence fit; list conflicts with this prompt; print a short plan; continue unless blocked.
1. **Skeleton and data:** package, config, domain models, catalogs, store ports + fake + Postgres + contract suite + migrations, state machine, safety, ADRs.
2. **Call framework and mocks:** all twelve call folders with specs, schemas, prompts, examples, mocks, tests; registration with the gateway; the `$2` Compass cap.
3. **Intake:** parsers, URL reader with SSRF guard, chunking, coverage, limits.
4. **Conversation engine:** situation, playback, value-of-information queue, frames, card generation and validation, free-text absorption, assumptions, research seeding, handoff probes.
5. **Brief, changes and gate:** compile, view model, consumer views, readiness, change flow with history slice and undo, versions and diffs, Gate 1, enterprise mode basics.
6. **API and static UI.**
7. **Offline tests and evals** to the bar in section 9.
8. **Live batches A to D** within the cap.
9. **Report.**

---

## 11. Definition of done
All offline tests green (counts reported); layering and format checks clean; every call has a folder with spec, schema, prompt, examples, mock and tests; all scenario and persona scripts pass; the test UI runs in mock mode and in live mode and shows the spent/cap meter; Brief page meets the quality bar; the change flow works from the UI (dislike a section, chat the change, see the diff, undo); history and provenance work; live spend never exceeded USD 1.90 (cap 2.00) and is reported per batch; no secrets in the repo; ADRs and decisions logged; `packages/compass/README.md` explains how to run everything; the old `mde_discovery` untouched and still green.

## 12. Report back (concise, honest)
1. What changed (files and folders at a high level) and anything outside `packages/compass/` (the allowed exceptions used).
2. Tests run with counts and the memory precautions taken; anything not run and why.
3. The metrics from 9.3 measured offline and live, against the targets; what missed and why.
4. Live spend per batch and total; model id and vision support confirmed or not.
5. Decisions and ADRs logged; dependencies added.
6. Deviations from this prompt with reasons.
7. Open questions that only the owner can answer, and a recommendation for retiring `mde_discovery`.
8. How to start it: the exact command, the URL of the test UI, and how to switch between mock and live.

## 13. Do not
Do not edit or delete `mde_discovery`; do not add providers or web search; do not add system-level dependencies; do not build React; do not exceed the cap; do not push; do not print secrets; do not invent facts in the Brief; do not let any model output into storage unvalidated.
