# Architecture v3.1: index, authority, and decision register

- **Status:** active, created 2026-11-09
- **Scope:** the Context Engine (engine 1 of 4) and the interfaces it owes to the Front-end, Backend and QA engines
- **Author:** the planning chat, acting as architect, system designer, developer-in-spirit and founder
- **Evidence labels used in these docs:** **[V]** verified in a primary or official source on 2026-11-09; **[S]** secondary (press, trackers, vendor blogs; directional); **[J]** architect judgment, to be confirmed by data

## 1. Authority and precedence
On 2026-11-09 the owner gave the planning chat sole authority over architecture decisions ("you are the sole authority over here"). This folder is the **current design**. Where documents disagree:

1. `architecture/` (this folder) wins on **what we should build and why**.
2. `01-specs/` final build spec rev 3 and the Claude Code implementation brief remain the reference for details not contradicted here (Python 3.13 / FastAPI / Pydantic v2 / PostgreSQL, gateway rules, budget rules, repo layout, banned frameworks).
3. `00-master/` Master 00-07: history and context; the corrections listed in Master 00 still apply to the sentences they name.
4. `02-design-history/` and `03-research/`: context only.
5. The **repo wins on facts about code that exists**. Repo facts in these docs are as reported on Nov 8 and not checked (see Step 0 in [09 roadmap and repo delta](09-roadmap-and-repo-delta.md)).

## 2. Owner directives that shape this design (2026-11-09)
| # | Directive (owner's words, lightly cleaned) | Where it lands |
|---|---|---|
| D1 | "Each agent creates one detailed output, one detailed file, consumed by one engine. Sequential flow, not too many engines, not too few." | AD-02; doc 03 |
| D2 | Even a low-grade model must be able to build a beautiful, correct, large app from our context: no polluted data, enough context, not hardcoded, not binding, not hallucinated. | AD-03, AD-04, AD-11, AD-17; docs 04, 07 |
| D3 | Many categories, many customers; **only the tech stack is fixed** (Expo, React Native). | AD-05, AD-15; doc 01 |
| D4 | Customer app and admin app are produced "in one go". | AD-22 |
| D5 | The user previews and interacts with the mock-data front-end, requests changes until happy; **front-end approval is the last user gate**; then the backend is generated, "full finalized". | AD-07, AD-09, AD-13; docs 04, 06 |
| D6 | Parallel, fast generation for small and big apps; the front-end engine may use a small model (Haiku-class) or a bigger one (Sonnet-class). | AD-11; doc 04 |
| D7 | The user may ask for changes a hundred times; the source of truth must change gracefully; out-of-scope feature requests need a defined path. | AD-10; doc 05 |
| D8 | No games, and no other categories that are hard to generate. | AD-06; doc 01 |
| D9 | Customers are high-paying: US, EU, India; IT-services firms (TCS/Infosys type) can be users. | doc 08 |
| D10 | "Do not much worry about privacy and security." | AD-24 (baseline only) |
| D11 | Haiku / GPT-6 Luna are sufficient for context work; performance over cost; high reasoning allowed. | AD-18 |
| D12 | Planning chat designs, guides and writes prompts; the other Claude builds. No git. Behave like a chat. | doc 09 |
| D13 | Decide on own reasoning and 2026 research; do not hallucinate; do not be bound by earlier data. | doc 10 |
| D14 | What the user sees must be beautiful and the full output must be shown. | AD-08; doc 06 |

## 3. How to read this folder
| Doc | Read it for |
|---|---|
| [Visual atlas](architecture-atlas.html) | The whole architecture as clickable diagrams (local file; open it in a browser) |
| [01 thesis and scope](01-thesis-and-scope.md) | What we are, who for, categories, capability tiers, exclusions, principles |
| [02 system architecture](02-system-architecture.md) | Layers, orchestrator, stores, gateway call catalog, reliability, baseline hygiene |
| [03 context engine](03-context-engine.md) | The six stage-engines, depth profiles, gates, artifact outlines, worked example |
| [04 spec package and hand-offs](04-spec-package-and-handoffs.md) | Spec graph, tiers, package files, Build Plan DAG, contract-first, G3 manifest, FE/BE/QA consumption |
| [05 change management and edge cases](05-change-management-and-edge-cases.md) | Change lanes, scope ledger, undo/variants, the full edge-case catalog |
| [06 preview and gate experience](06-preview-and-gate-experience.md) | What the user sees at each gate; Review Mode requirements for the preview |
| [07 quality, evals and models](07-quality-evals-and-models.md) | Sufficiency probe, validators, SpecBench-Mobile, model policy, per-stage acceptance gates |
| [08 market and investor case](08-market-and-investor-case.md) | Competitors, customers, pricing, defensibility, raise narrative, risks, investor Q&A |
| [09 roadmap and repo delta](09-roadmap-and-repo-delta.md) | Agent-by-agent order, what changes in the built repo, Step 0 audit, prompt template |
| [10 research log](10-research-log.md) | Every external claim with label, URL and date; unverified items; deferred compliance research |
| [11 discovery agent design](11-discovery-agent-design.md) | The first agent in detail: situation profile, slot catalog, question system, assumptions, research seeding, handoff probes, the Brief frame |

## 4. Decision register
Status: **Owner** = directed by the owner; **Decided** = architect decision; **Default [J]** = architect default to confirm with data or at the relevant phase.

| ID | Decision | Detail | Status |
|---|---|---|---|
| AD-01 | Layers: shared services under a Program Orchestrator running four engines (Context, Front-end, Backend, QA); each engine = typed inputs, typed outputs, events | 02 | Decided |
| AD-02 | Six stage-engines in sequence, one artifact each, typed back-edges (`needs_decision`, max 2 loops); parallelism only for research reading and for screen batches after shared decisions freeze; three depth profiles (Express, Standard, Large) | 03 | Owner + Decided |
| AD-03 | The Spec Graph is canonical; `APP_SPEC.md` is the single compiled file; slices, `spec.json`, tokens, contract, scenarios and build plan are derived | 04 | Decided |
| AD-04 | Every node carries tier (Locked/Default/Open), provenance, status, confidence and a stable ID that is never reused; research never creates Locked nodes | 04 | Decided |
| AD-05 | Capability Module Library ("compose, don't invent") and a Scope Ledger / Feature Catalog the user can toggle | 01, 05 | Decided |
| AD-06 | Honest capability tiers A/B/C; games, AR/VR/3D, media editors, DRM streaming, trading terminals, OS-level utilities, safety-critical and proprietary-hardware apps are declined with an alternative | 01 | Owner + Decided |
| AD-07 | Mandatory gates G1 Brief, G2 Spec, G3 Front-end preview (last user gate); policy gates G1.5, G2.5, G4 | 03, 06 | Owner + Decided |
| AD-08 | Review surfaces are summary-first with the full output one click away; provenance chips; version diff; wireframes rendered by code from the spec; downloads | 06 | Owner + Decided |
| AD-09 | Spec-linked preview ("Review Mode"): web link first, dev build for device, Expo Go only when the SDK matches; persona, state, scenario, seed and feature controls; click-to-inspect via `@spec` stamps | 06 | Decided |
| AD-10 | Change lanes L0-L5, change sets, Scope Change Protocol, undo, variants | 05 | Owner + Decided |
| AD-11 | Build Plan DAG: waves, task cards with file ownership, interface freeze, complexity and model-class hints, deterministic plumbing generated by code | 04 | Decided |
| AD-12 | Contract-first: Contract v0 from Blueprint, reconciled with actual front-end usage and frozen as v1 at G3 | 04 | Decided |
| AD-13 | G3 freezes an Approved Build Manifest (Behavior Book, Integration Activation Checklist, Contract v1, golden journeys, fixtures); backend and QA then run automatically and are accepted by contract tests and journey replay | 04, 06 | Owner + Decided |
| AD-14 | Tokens as W3C DTCG 2025.10 JSON plus an exported `DESIGN.md`; platform fidelity is a Default setting | 03, 04 | Decided |
| AD-15 | Stack Kit and Backend Kit are fixed defaults, re-verified by canary golden apps on every release; allowlisted packages and icons; backend default TypeScript + PostgreSQL | 01, 04 | Decided / Default [J] for backend |
| AD-16 | Spec Server: MCP tools + AGENTS.md entry + budgeted slices (Tier 0 ≤ 8k tokens, screen slice ≤ 6k) | 04 | Decided |
| AD-17 | Structured elicitation plus the sufficiency probe with a weak canary model; gaps filled with pattern defaults, user asked only for must-confirm items | 03, 07 | Decided |
| AD-18 | Models: provider-agnostic gateway (add Anthropic beside OpenAI); Haiku 5.5 and gpt-6-luna at high effort by default; writer and critic from different vendors; premium lane only on eval evidence; pinned versions; failover | 02, 07 | Owner + Decided |
| AD-19 | Eval-first: SpecBench-Mobile gates every prompt or model change | 07 | Decided |
| AD-20 | Context hygiene: no chat transcripts or raw research downstream; each stage receives only what it needs | 03, 04 | Decided |
| AD-21 | Document intake for PDF, DOCX, XLSX, PPT, email, CSV, images, audio and links; content is data and extraction calls have no tools | 03 | Decided |
| AD-22 | Program model: customer, admin (and optional partner) apps in one session; admin default is a responsive Expo app (phone, tablet, web) | 03 | Owner + Decided |
| AD-23 | Platform-risk rule: never ship a store app that downloads or executes generated code (Apple 2.5.2) | 02, 06 | Decided |
| AD-24 | Baseline hygiene only (secrets redaction, documents-as-data, no real PII in mocks, tenant isolation); regional compliance packs deferred | 02, 10 | Owner |
| AD-25 | SDLC documents (BRD/SRS, user stories, test plan, traceability matrix) are exports of the graph | 04 | Decided |
| AD-26 | Stage outputs cached by input hash (idempotent, resumable), per-session cost caps, vendor failover | 02 | Decided |
| AD-27 | **Handoff probes:** every stage's output is tested by a weak canary of the stage that consumes it (Brief by Research, Blueprint and Design canaries; spec by the builder canary) before its gate opens | 07, 11 | Decided |
| AD-28 | **Consumer-driven Brief:** every field has a named consumer and every consumer need has a field; consumer views are derived by code; Discovery seeds Research with specific `research_requests`; a readiness report per consumer is shown at G1 | 11 | Decided |
| AD-29 | **Question frames:** fixed purpose, shape and guardrails; generated wording and options with visible basis labels; always a custom input, "you decide" and skip; validated by code with a deterministic fallback; questions chosen by value of information, not by a fixed script | 11 | Decided |

## 5. What changed versus the earlier design
| Earlier (final spec rev 3, Nov 8) | Now | Why |
|---|---|---|
| One session = one app = one spec | One session = one **Program** (customer + admin) = one spec | Owner D4; shared roles, entities, design, contract |
| Five archetypes in Domain Packs | **Capability Modules** composed per app; packs optional | Owner D3; avoids hardcoding categories |
| Steps: Discover, Review Brief, Research, Build spec, Review Plan, Hand off | Six stage-engines: Discovery, Research, Blueprint, Design, Screens, Verify and package; then Front-end, Backend, QA | Owner D1; clearer one-file-per-engine chain |
| Gates: Brief, Plan | G1 Brief, G2 Spec, **G3 Front-end preview (last user gate)**; policy gates G1.5, G2.5, G4 | Owner D5 |
| Spec Builder as one block (B1-B9) | Split into Blueprint, Design, Screens, Verify | Smaller, testable engines; parallelism after freeze |
| PDF parsing out of scope | Document intake in scope | Enterprises arrive with PDFs and decks |
| Research planner capped at 10 queries | Depth-budgeted, no fixed ceiling | Known gap closed |
| Hand-off at `spec_ready` | Hand-off includes Build Plan, Contract v0, scenarios; backend follows G3 | Owner D5, D6 |
| Policy and compliance packs prominent | Baseline hygiene only | Owner D10 |
| Gateway: OpenAI only | OpenAI + Anthropic, failover | Haiku 5.5 usable; vendor decorrelation |

## 6. Open items and assumptions
| ID | Item | My default | Revisit |
|---|---|---|---|
| OI-1 | Backend default stack | TypeScript + PostgreSQL, contract-first so targets are swappable [J] | Backend design phase |
| OI-2 | Preview hosting and optional streamed device | Web preview hosted by us; evaluate a streamed-device vendor later | Front-end design phase |
| OI-3 | Coding-agent harness for Front-end and Backend engines | Orchestrate commodity coding agents through our Spec Server rather than build an agent loop [J] | Front-end design phase (bake-off) |
| OI-4 | Pricing numbers | Hypotheses in doc 08 | After design-partner pilots |
| OI-5 | Module Factory: who builds and reviews the first ~20 modules | Strong model + human review | Before Stage 3 build |
| OI-6 | Stack Kit canary CI | Required before first external user | Before Front-end build |
| OI-7 | Regional compliance packs | Deferred by owner; research kept in doc 10 | When a customer needs it |
| OI-8 | SSO, VPC, SOC 2 | Deferred (table stakes later, not now) | First enterprise deal |
| OI-9 | Mapping of the built repo to v3.1 | Step 0 read-only audit | Next |
| OI-10 | Express profile UI | Brief and Plan shown on one review page, approved with one tap, recorded as two hash-bound approvals | Stage 1 design |

## 7. Changelog
- **v3.0 (2026-11-09):** six-stage sequential design, freedom tiers, provenance, sufficiency probe.
- **v3.1 (2026-11-09):** preview-driven G3 and backend hand-off, parallel build plan, change lanes and scope ledger, capability modules and category scope, edge-case catalog, investor case, privacy and security reduced to baseline.
