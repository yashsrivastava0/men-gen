# 07. Quality, evaluation and model policy

How we know a stage is done, how we prove a spec is sufficient for a weak model, how we score everything, and how models are chosen and changed. (AD-17, AD-18, AD-19)

## 1. Quality dimensions
| Dimension | Meaning | How measured |
|---|---|---|
| Sufficiency | A weak model can implement each slice without asking | Sufficiency probe, canary implementability |
| Fidelity | Explicit requirements are captured and nothing is invented | Requirement recall / precision against seeded ground truth; Locked-without-evidence = 0 |
| Consistency | No contradictions across apps, screens, rules, contract | Graph validators, cross-vendor critic |
| Traceability | Every node has provenance; requirements link to screens, tests, files | Provenance coverage, RTM completeness |
| Design quality | Output is not generic; tokens are respected; accessibility defaults hold | Direction diversity axes, token adherence, contrast, a11y lint, human rubric |
| Usability of views | Users understand gates and approve confidently | Edit rate, time at gate, abandonment, comprehension checks |
| Operability | Resumable, cheap, fast, observable | Latency, cost, degraded rate, replay success |

## 2. Graph validators (code, run on every commit and on every change dry run)
| ID | Check |
|---|---|
| V-01 | Every artifact validates against its versioned schema |
| V-02 | IDs unique and stable; tombstones and aliases resolve |
| V-03 | Every edge points to an existing node |
| V-04 | Every screen is reachable from an entry point; no dead ends except terminal screens |
| V-05 | Every flow starts and ends on real screens; every step maps to an action |
| V-06 | Every data binding names a real entity field or operation |
| V-07 | Every action resolves to a navigation target, mutation or modal that exists |
| V-08 | Every screen and operation has a role rule (default deny); no unused or undefined roles |
| V-09 | Lifecycles: every status reachable, terminal states defined, transitions role-gated |
| V-10 | Contract lint: valid OpenAPI, examples validate against schemas, error model consistent, enums come from lifecycles |
| V-11 | Vocabulary consistent across apps (entity, role, status names) |
| V-12 | Tokens: DTCG-valid, references resolve, scales monotonic, contrast met in light and dark, no raw values in screens |
| V-13 | Mandatory states present for each screen type (SC-05) |
| V-14 | Every copy key exists for every locale; no hardcoded strings |
| V-15 | Fixtures satisfy schemas and counts from the mock plan; edge classes present; synthetic markers present |
| V-16 | Every feature has a capability tier; tier B items flagged; tier C only as needs-engineering |
| V-17 | Tier rules: Locked has evidence; research and assumptions not Locked; deviations recorded |
| V-18 | Budgets: slice tokens, screen counts vs profile, total size |
| V-19 | Scope Ledger consistency: each screen satisfies at least one `in` feature; no `in` feature without screens; `later` / `out` have no screens |
| V-20 | No secrets in spec or fixtures |
| V-21 | Determinism: same graph renders the same file hash |
| V-22 | Interactive elements have accessibility labels and adequate targets |
| V-23 | Only allowlisted icons and packages are referenced |

## 3. Sufficiency probe and canary (the central quality device)
**Idea.** The spec is tested against the same class of model that will consume it. If a weak model has to ask, the spec is insufficient.

**Procedure (VRFY-2) per slice** (screen, flow, operation group, entity group):
1. Build the canary input: Tier 0 + the slice + referenced interface names (tokens, kit signatures). No chat history, no research.
2. Prompt the canary (the weakest intended downstream class at low effort, for example Haiku 5.5 low or GPT-6 Luna low): "You must implement this. Return an outline (components, bindings, states handled, actions) and a list of questions. Do not guess. Mark questions that block implementation."
3. Evaluate programmatically: blocking questions; outline completeness against the slice (all sections, states, actions, bindings present); contradictions flagged.
4. Each blocking question becomes a **gap**: first resolved by a pattern default (marked Default / assumed, listed at G2); only must-confirm items are asked of the user (VF-01).
5. Re-probe after resolution, at most two rounds.
**Pass:** zero blocking questions and 100% outline coverage.
**Handoff probes (AD-27).** The same device is applied at every handoff, not only to the final spec: the Brief is tested by weak Research, Blueprint and Design planners (doc 11 §9); the Blueprint by a Design and Screens planner; and so on. A stage's gate opens only when its consumer's canary can start work without asking.
**Extras.** *Contradiction probe* (canary lists conflicts between a slice and the rules it references); *flow probe* (canary plans each golden journey step by step); Large profile uses two canaries (one per vendor) to avoid fitting one model's quirks; optional **canary build**: once the front-end engine exists, two or three representative screens are actually built (type-check and render) before G2.

## 4. SpecBench-Mobile
A private benchmark, with a public subset and methodology after pilots (credibility with buyers and investors).

**Composition (v1, ~120 cases).**
- 15 domains x 2 modes (idea-only, PRD-led): food delivery, grocery / quick commerce, fashion retail, B2B ordering, hotel and travel booking, clinic / telehealth shell, fitness, school-parent, online courses, field-service dispatch, logistics tracking, HR self-service, helpdesk / ITSM, wallet / banking-style, real estate listings (extend to 20 domains).
- Multi-app programs (customer + admin; customer + restaurant + rider + admin).
- Large (100+ screens) and Express (≤ 12) profiles.
- Adversarial: injected instructions in documents, contradictory documents, unsupported categories, "exactly like [brand]".
- Multilingual: Hindi, Hinglish, Arabic (RTL), mixed-script documents.
- Persona scripts: silent, chatty, confused, expert, stakeholder conflict, returns-after-days, dictating run-ons.
- Change suite: 100 scripted changes spanning every lane, including scope-boundary requests and rapid-fire batches.
- Edge-case scenarios: one per catalog ID (doc 05), named `SB-<ID>`.

**Ground truth.** Synthetic PRDs are generated from seeded requirement lists so recall and precision are measurable; a small set of realistic, sanitized PRDs is added. A private holdout set is never used for prompt tuning.

**Scoring.** Programmatic first: schema validity (first pass and after repair), reference integrity, requirement recall and precision, provenance coverage, Locked-without-evidence count, probe pass and blocking-question rate, canary implementability, token adherence, accessibility lint, determinism, budgets, cost, latency, degraded rate. **LLM judges** only for semantic matching and subjective rubrics; the judge is from a different vendor than the writer, calibrated on about 100 human-labeled pairs with reported agreement, and never the sole gate for an invariant. A monthly human rubric review (1-5 for sufficiency, clarity, design direction, plausibility) on 10 random outputs.

**Gating.** Any invariant violation is a hard fail. A drop of more than 2 points absolute on a key metric fails the change. Metrics ratchet upward as the system improves. The harness runs in replay mode (cassettes) on every change and nightly, and live in small budgeted batches (existing rule: explicit owner approval for live spend).

## 5. Initial targets [J], to be calibrated on data
| Metric | Target |
|---|---|
| Schema validity before repair / after repair | ≥ 97% / ≥ 99.5% |
| Reference integrity after validators | 100% |
| Requirement recall and precision (PRD-led) | ≥ 90% each |
| Locked nodes without evidence | 0 |
| Cards: median / max (Guided) | ≤ 7 / ≤ 10 |
| Blocking questions per slice after repair | ≤ 0.05 |
| Canary implementability (first attempt, sampled screens) | ≥ 85% |
| Critic recall of seeded defects | ≥ 90% |
| Determinism | 100% |
| Change requests after G3 caused by spec insufficiency | < 10% of all |
| Time after G1 (Express / Standard / Large) | ~3 / ~8 / ~15 min |

## 6. Model and gateway policy (AD-18)
- **Defaults.** `small` class = Claude Haiku 5.5 and GPT-6 Luna at high effort for stage work, low for probe and canary, medium for narration. Haiku 5.5 is documented as built for classification, extraction and subagent work, with adaptive thinking controlled by `effort` [V]; Luna is the low-cost tier of the GPT-6 family [S]. The owner judged these sufficient for context work; performance matters more than cost.
- **Cross-vendor.** Writer from one vendor, critic from the other; probe uses the weakest class; tenant policy may pin vendors.
- **Premium lane (mid / large: Sonnet 5.5, Opus 5.5, GPT-6 Sol).** A configuration change, used only where evals show a gap: for example BLUE-3 recall below target, VRFY-1 seeded-defect recall below target, or a repair rate above 30% for a node type. A single premium retry is allowed per failing node.
- **Prompt caching.** Static prefixes (instructions, schemas, Stack Kit rules, module excerpts) are cached; Haiku 5.5's minimum cacheable prompt is 512 tokens and cache reads cost 10% of base input [V].
- **Batch API** (50% discount [V]) only for evals, Module Factory runs and pack building; never for interactive stages.
- **Pinning and change.** Models pinned by id (`claude-haiku-5-5`, `gpt-6-luna`); Haiku 5.5 retirement "not sooner than October 7, 2027" [V]. A model change requires the **bake-off protocol**: full SpecBench run, non-inferior on every gate metric, equal or better cost and latency, one-week shadow on a slice of sessions, rollback path.
- **Failover.** Per-route circuit breakers; same call spec on the other vendor; recorded in the trace.
- **Token budgets** are measured per tokenizer (Haiku 5.5's counts about 30% more tokens than Haiku 4.5 for the same text [V]).
- **Cost envelope (estimate [J]).** A 100-screen, two-app program: roughly 3-4M input and 0.4M output tokens across the Context Engine including probes and critic, which is about $0.5-2 at $0.10 / $0.50 per million before retries and premium calls. Default context budget per program $10 (policy). The expensive parts of the platform are downstream generation and infrastructure, which the spec exists to reduce.

## 7. Per-stage acceptance gates ("agent by agent")
Each step: owner approves the design -> I write one builder prompt -> builder implements -> acceptance run -> report -> state update -> next. A stage is "done" only when its gate passes.
| Stage | Deliverables | Tests | Gate (initial [J]) |
|---|---|---|---|
| 1 Discovery | `brief.json` schema; document-intake service; consumer-driven slot catalog; Situation Profile and Domain Hypothesis; question frames, validation and value-of-information queue; assumption register; research seeding; handoff probes; Brief View and readiness data (full list in [doc 11](11-discovery-agent-design.md) §14) | schema; invariants (property); IN-*, IV-*, DC-* scenarios; Stage-1 SpecBench cases | recall and precision ≥ 90% on PRD-led; Locked-without-evidence 0; questions median ≤ 7, max 10; ≥ 85% of questions change a downstream decision; ≥ 80% of assumptions unchanged at G1; Research, Blueprint and Design handoff probes pass; injection tests 100% ignored; every persona script ends in a valid Brief |
| 2 Research | `research.json` schema; job planner; search / extract / synthesize; cross-checks | citations; RS-* scenarios; 10-domain usefulness review | 100% of high-impact items sourced and dated; size cap holds; injection pass; fail-soft verified; ≥ 70% of items used or consciously rejected downstream |
| 3 Blueprint | `blueprint.json` schema; Module Library v1 (first ~20 modules); 3a/3b/3c calls; Contract v0 emitter | V-01 to V-11, V-16, V-19; BP-* scenarios | validators 100%; permission matrix and lifecycles complete; ≥ 80% of screens from library recipes in covered categories; human rubric ≥ 4/5 on 10 domains |
| 4 Design | `design.json`, DTCG tokens, `DESIGN.md`; direction generator; composition rules | V-12; DS-* scenarios; diversity check | contrast 100%; no raw values; three directions differ on the defined axes; human visual review of style tiles |
| 5 Screens | `screens.json`; screen-batch writer; global behaviors; copy store; complexity scoring | V-06, V-07, V-13, V-14, V-15, V-18, V-22, V-23; SC-* scenarios | mandatory states 100%; bindings resolve 100%; slice budgets 100%; acceptance criteria on every flow-critical screen |
| 6 Verify and package | validators, critic, probe, repair, packager, Build Plan, journeys, renderer, views | full V-*; VF-*, GT-* scenarios; determinism | blocking questions ≤ 0.05; canary implementability ≥ 85%; critic recall ≥ 90%; determinism 100%; G2 view complete |
| Cross-cutting: Change controller | CHNG-1/2/3, lanes, change sets, undo, variants | CH-* scenarios; lane classification set | classification accuracy ≥ 95%; L1 spec patch ≤ 10 s p90; undo exact |
| Cross-cutting: Gateway | Anthropic provider, failover, cassettes | contract tests for parity with OpenAI path | identical behavior on schema, repair, fallback, budget |
| Cross-cutting: Spec Server | MCP tools, AGENTS.md, slices | MCP conformance; budget tests | slices within budgets 100%; stamps resolve |
| Cross-cutting: Review surfaces | Brief, Structure, Plan views; wireframes; diff | usability checks | first view < 3 s; comprehension check passes |

## 8. Test strategy for the builder (summary)
Unit tests for pure functions (lane classification, validators, hash binding, slicers); property tests for invariants I-1 to I-12; scenario scripts for every edge-case ID; chaos tests for the OP-* rows (outages, partial failure, restarts); replay-based eval runs in CI; live batches only with explicit budget and approval; mutation checks on the most critical validators. Existing repo rules (offline-first tests, cassettes, no SQLite, banned frameworks) stay.
