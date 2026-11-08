# 02. System architecture

Scope: layers, orchestration, stores, the LLM gateway and its call catalog, services, reliability, and the baseline hygiene that replaces the deferred privacy/security work. Implementation language and framework decisions from the earlier brief stay in force unless noted (Python 3.13, FastAPI, Pydantic v2, PostgreSQL only, SSE, no agent frameworks, only the gateway imports provider SDKs).

## 1. Layers
```
        Users (requester / approver / commenter / viewer)
        Web app: chat + review surfaces + preview  (doc 06)
                          |  API + SSE event stream
 +------------------------ Program Orchestrator -------------------------+
 | Program > Apps > Sessions > Runs | gates | change sets | versions | audit |
 +-----------+------------------+----------------+------------------------+
 | Context   | Front-end        | Backend        | QA                     |
 | Engine    | Engine           | Engine         | Engine                 |
 | 6 stages  | build + preview  | server         | journey replay         |
 +-----------+------------------+----------------+------------------------+
 |                          Shared services                                |
 | LLM Gateway | Artifact store | Spec Graph store | Pack / Kit registry     |
 | Doc-parse | Search + fetch | Render (wireframes) | Preview + build host   |
 | Eval harness | Observability                                            |
 +-------------------------------------------------------------------------+
```
(AD-01.) An engine is a pipeline of schema-checked steps with code controlling the order. It is not an autonomous agent loop. The only bounded tool loops are in Research (read-only) and, later, inside Front-end/Backend coding workers.

## 2. Program Orchestrator
A durable workflow layer shared by all engines.

| Concept | Meaning |
|---|---|
| Program | One customer engagement: one or more apps sharing roles, design, domain and contract |
| App | Customer, admin/operations, partner... each with its own navigation and screens |
| Session | One conversation context inside a program (many contributors in Enterprise) |
| Run | One execution of a stage or engine against specific artifact versions |
| Artifact | An immutable, content-addressed, versioned file (sha256) with a schema id |
| Gate | A decision point bound to an artifact hash (and section hashes) |
| Change set | A group of patch operations with lane, impact, estimate, approvals, undo (doc 05) |
| Event | Append-only log entry: inbound (message, tap, upload, gate decision, change request) and outbound (message, card, progress, view-ready, gate-open) with per-session sequence numbers for SSE replay |

Rules carried over and kept: one active run per program (unique partial index plus advisory lock); cooperative cancellation between steps; inbound events are idempotent by event id; transactions never span an engine call; silence never approves.

Session states (persisted, driven by a pure transition table): DISCOVERING, BRIEF_REVIEW, PLANNING (research, blueprint, design, screens, verify; remembers where it started), PLAN_REVIEW, BUILDING (front-end), PREVIEW_REVIEW (G3), BACKEND_BUILDING, DELIVERED; side states PAUSED, STOPPED, ARCHIVED. Policy gates add CHECKPOINT states without changing the table's shape.

## 3. Stores
- **Artifact store:** object storage (local disk in dev) for immutable artifacts, metadata and hashes in PostgreSQL. Atomic commit: an artifact is visible only after validators pass.
- **Spec Graph store:** PostgreSQL tables for nodes and edges with version ranges (`valid_from`, `valid_to`), stable IDs, tombstones, tier/provenance/status columns, and a materialized JSON export (`spec.json`). Every commit runs the validators in doc 07 on the new graph before it becomes visible. Variants (branches) are named graph forks that share history.
- **Event log:** append-only; used for audit, replay and SSE.
- **Pack / Kit registry:** versioned data: Capability Modules, screen recipes, Stack Kit versions, Backend Kit, design presets, question templates, policy profiles. Official entries are reviewed; model-proposed entries stay candidates until reviewed.
- **Cache:** stage outputs keyed by `hash(input artifacts + call spec versions + model ids)`; identical input never recomputes (AD-26).

## 4. LLM Gateway
Every model call goes through the gateway (existing design retained and extended).

**Call spec fields:** id, version, purpose, input schema, output schema (Pydantic converted to the provider's strict JSON subset), prompt templates (Jinja) with a cacheable static prefix, `effort`, model class (`small`, `mid`, `large`), vendor lane (`writer`, `critic`, `canary`), `max_output_tokens`, timeout, retry policy, validators, deterministic fallback, redaction rules, cost estimate.

**Pipeline for one call:** validate input -> render (untrusted content wrapped as data, no tools in calls that read it) -> reserve budget -> cache lookup -> provider call with timeout -> local output validation -> one repair call at low effort -> deterministic fallback -> commit cost -> log and trace.

**Providers:** OpenAI Responses (existing), **Anthropic Messages (new)**, Mock, Scripted, Replay, Record. Per-tenant routing and failover: if one vendor is down or rate-limited, the same call spec runs on the other (AD-18, AD-26). Bring-your-own cloud endpoints (Bedrock, Google Cloud, Foundry for Claude [V]) are a later routing option, not a rebuild.

**Model classes today:** `small` = Claude Haiku 5.5 or GPT-6 Luna (both about $0.10 / $0.50 per million tokens, ~1M context; Haiku 5.5 verified [V], Luna from aggregators [S]); `mid` = Sonnet 5.5 ($2 / $10) or GPT-6 Sol; `large` = Opus 5.5 ($4 / $20) or equivalent. Haiku 5.5's tokenizer produces roughly 30% more tokens than Haiku 4.5 for the same text [V], so token budgets are measured per tokenizer.

### 4.1 Call catalog for the six stage-engines
Effort values are starting points. "small->mid" means start small and promote only on eval evidence.

| ID | Purpose | Class | Effort | Lane |
|---|---|---|---|---|
| DISC-1 | Extract claims from one document chunk, with source pointers | small | high | writer |
| DISC-2 | Merge chunks, dedupe, detect contradictions, report coverage | small | high | writer |
| DISC-3 | Classify: category, apps in scope, candidate modules, size estimate, capability flags | small | high | writer |
| DISC-4 | Understand a typed turn: intent, slot updates with evidence, language | small | medium-high | writer |
| DISC-5 | Respond: grounded answers, help mode, brief chat (never invents price or time) | small | medium | writer |
| DISC-6 | Write a card's domain-specific options inside the card schema | small | high | writer |
| DISC-7 | Ambiguity scan over slots and claims | small | high | critic |
| DISC-8 | Compile the Brief and its one-sentence summary | small | high | writer |
| DISC-9 | Write `research_requests` and search hints from the Brief and its unknowns | small | high | writer |
| DISC-10 | Handoff probes: weak Research, Blueprint and Design planners try to start from the draft Brief | small | low | canary |
| RSCH-1 | Plan queries from the Brief, within depth and budget | small | high | writer |
| RSCH-2 | Extract facts from one page (no tools) | small | low-medium | writer |
| RSCH-3 | Synthesize findings grouped by consumer | small | high | writer |
| BLUE-1 | Product: PRD summary, personas, roles, modules, MVP vs later | small->mid | high | writer |
| BLUE-2 | Structure: journeys, cross-app scenarios, navigation, screen inventory | small->mid | high | writer |
| BLUE-3 | Domain: entities, lifecycles, rules | small->mid | high | writer |
| BLUE-4 | Contract: operations, events, error model | small | high | writer |
| BLUE-5 | Mock-data plan, integrations, NFRs | small | medium | writer |
| DSGN-1 | Direction: tone and three candidate looks from brand assets/site | small (vision) | high | writer |
| DSGN-2 | Tokens, scales, component variants, composition rules | small | high | writer |
| DSGN-3 | Voice and copy guide, imagery and icon policy | small | medium | writer |
| SCRN-1 | Screen batch writer (3-4 screens per call) | small | high | writer |
| SCRN-2 | Global behaviors and copy store | small | medium | writer |
| VRFY-1 | Critic: spec vs Brief, contradictions, missing flows | small->mid | high | critic |
| VRFY-2 | Sufficiency probe on one slice (deliberately weak) | small | low | canary |
| VRFY-3 | Repair from validator and critic findings | small | high | writer |
| VRFY-4 | Plain-language plan summary for G2 | small | medium | writer |
| CHNG-1 | Change interpreter: natural language -> patch operations | small | high | writer |
| CHNG-2 | Impact narrator: plain-language diff | small | medium | writer |
| CHNG-3 | Variant generator: three concrete options for ambiguous asks | small | high | writer |
| VIS-1 | Visual critic (front-end side): screenshot vs design intent | small (vision) | medium | critic |

Web search is a gateway tool (S-style call) with citations; its content is billed as input tokens (about $0.011 per search measured in the earlier L0 smoke test).

## 5. Services
| Service | Responsibility | Notes |
|---|---|---|
| Doc-parse | PDF, DOCX, XLSX, PPT, email, CSV, images, audio to structured text plus page images | Layout-aware text first; render pages to images for scans and diagrams; transcription for audio; caps on size and pages with a visible coverage meter |
| Search + fetch | Web search through the gateway; fetch of public URLs | SSRF-safe: public hosts only, size caps, no redirects to private ranges |
| Render | Wireframes and app map from the spec | Deterministic; same graph gives same output |
| Preview + build host | Builds the web preview and dev-build links, hosts them behind expiring links | Front-end engine; requirements in doc 06 |
| Pack / Kit registry | Modules, recipes, Stack Kit, presets | See section 3 |
| Eval harness | Runs SpecBench-Mobile, replays cassettes, scores | Doc 07 |
| Observability | Traces per session, stage and call; cost and latency; quality KPIs | Section 7 |

## 6. Reliability and operations
- **Idempotent, resumable stages.** Each stage checkpoints its partial outputs; a restart resumes after the last committed step. No half-written artifacts (atomic commit).
- **Fallback ladder** for every call: retry (transport errors only) -> repair -> deterministic default from the Module Library -> mark the node `degraded` and surface it at the next gate.
- **Failover.** Second-vendor routing for outages and rate limits; per-route circuit breakers.
- **Cost control.** Per-session and per-run caps (policy-set), rate limits, upload size caps, early input validation. The earlier USD 6 development cap and ledger rules remain for development only.
- **Version pinning.** Models are pinned by id; Haiku 5.5's retirement is "not sooner than Oct 7, 2027" [V]; a model switch needs an eval pass and a rollback path.
- **Determinism.** Stage outputs cached by input hash; sampling seeds used where providers support them; re-runs happen only when inputs change.
- **Concurrency.** Optimistic versioning on the graph; ops are commutative where possible; conflicts produce a conflict card, never silent overwrite (doc 05).
- **Parallel work principles.** Reading in parallel is safe. Writing in parallel is safe only against frozen shared decisions and non-overlapping file ownership (Cognition/Anthropic debate [S]; Anthropic compiler run [V]).

## 7. Observability
Trace id per session; spans per stage and call with model, effort, tokens, cost, latency, validation outcome, repair/fallback flags. Dashboards: time to approved spec, questions per Brief, edit rate at each gate, probe pass rate, repair rate, `degraded` rate, change-set lane mix, first-pass front-end compile/render rate, change requests after G3, cost per approved spec. Traces are replayable from cassettes for debugging and evals.

## 8. Baseline hygiene (AD-24; owner: not much worry about privacy and security)
Kept because they are cheap and protect quality, not as a compliance program:
1. Secrets (keys, passwords, tokens) are redacted before text reaches a model or storage.
2. Documents and web pages are **data, never instructions**; calls that read them have no tools; instructions found inside are ignored and noted in the audit log.
3. Mock data is synthetic; no real personal data in fixtures or seeds.
4. Tenant isolation in storage and queries; basic roles: requester, approver, commenter, viewer, admin.
5. Preview links are unguessable and expire.
6. Platform rule (AD-23): the product never ships a store app that downloads or executes generated code. Previews run in the browser, in Expo Go when the SDK matches, or in dev builds; deliverables are ordinary Expo projects built through normal pipelines. Apple enforced Guideline 2.5.2 against vibe-coding apps in 2026 [S].

**Deferred (owner decision, research retained in doc 10):** regional compliance packs (EU accessibility/cyber/AI Act, US COPPA/ADA/state privacy, India DPDP), SSO/SCIM, SOC 2, VPC/private deployment, data-residency controls, audit exports beyond the event log. Accessibility to WCAG 2.2 AA stays as a **design-quality default**, not a legal program.

## 9. Deployment topology
SaaS, multi-tenant first. The gateway and Spec Server keep BYO model endpoints and private deployment possible later without redesign (OI-8). The built system's development setup (private PostgreSQL on 127.0.0.1:54329, Windows dev machine, `uv`) is unchanged.

## 10. Interfaces between engines (summary)
| From -> To | Artifact | Defined in |
|---|---|---|
| Context -> Front-end | Spec Package (slices, tokens, contract v0, scenarios, Build Plan) via Spec Server | doc 04 |
| Front-end -> user | Spec-linked preview and build manifest | doc 06 |
| User -> Context | Change requests -> change sets | doc 05 |
| Front-end -> Backend | Approved Build Manifest (Behavior Book, Contract v1, fixtures, journeys) | doc 04 |
| Backend -> QA | Deployed backend plus manifest | doc 04 |
| QA -> user | Release report; optional G4 | doc 06 |
