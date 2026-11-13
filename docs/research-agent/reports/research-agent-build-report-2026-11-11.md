# Research agent: build report (2026-11-11)

Build prompt 05 (branch `feat/research-agent`). This report says what the Research agent is, how it works, what was built against the plan, what was checked, what was not done, and what happens next. It is written after the offline build and before any live run: **no live batch has been run yet, so nothing here says how good the research is on the real web.**

## 1. Status in one table

| Area | State |
| --- | --- |
| Agent (plan, search, read, extract, verify, assemble, validate, commit) | Built and running end to end on offline fakes, all three real examples and three synthetic Briefs |
| HTTP API and resumable event stream | Built, documented, tested in process and over the PostgreSQL store |
| Test console screens (progress, findings, saved-export picker) | Built, checked in real Chrome against the real Research dev server |
| Offline tests | 1,136 research tests plus 46 PostgreSQL integration tests pass; the repo unit suite passes |
| Live bake-off (Tavily vs Serper) | **Not run** (needs `LLM_MODE=live`) |
| Live runs on the three examples and a synthetic Brief | **Not run** |
| Quality iteration on real output | **Not started** (depends on the live runs) |
| Live-spend ceiling | Raised from USD 6.00 to 8.00 by the owner's decision; cap in force stays 6.00 until `LLM_BUDGET_USD` is set |

## 2. What the Research agent is

The Context Engine has four agents. Compass interviews the user and writes the **Brief**, which the user approves at Gate 1. **Research** (this agent) then searches the web and hands Spec one structured playbook. Spec and the frontend engine are built elsewhere, later.

- **Input:** an approved Compass export (format 2). The Brief hash, the approved baseline hash and the research view's hash must agree, or the run is refused (RV-13).
- **Output:** `research.json` (`research.v4`), indexed by the Brief's own ids, plus `research_evidence.json` (pages, verbatim quotes, offsets). It carries items, **norms** ("seen in n of N comparable sources", levels core, common, advanced, emerging, limited evidence, with how recent the newest source is), suggestions, a list to confirm, gaps, conflicts, and one digest per Spec lane (blueprint, integrations, design, screens, mock, verify).
- **Why it exists:** so Spec can weigh what comparable apps usually do instead of copying one page, and so every claim can be traced to a source page.
- **Rules it keeps:** plain code controls the flow; every model call is a single structured, schema-validated call through the shared gateway (versioned spec, mock handler, deterministic fallback); no agent loops or frameworks; web content is untrusted data (no tools on any call that reads it, no URL, action or tool fields in any output schema); nothing is hard-coded to one company or app type.

## 3. How it works

### 3.1 The run, step by step

1. **Read and trust.** The reader checks the hash binding and turns the export into a trusted input. It flags problems instead of silently trusting them: a request list that looks like a template is rebuilt; failures are recomputed from the spend record; weak geography is marked `geo_uncertain`; the user's own brand is never searched alone; the structure is treated as a hint.
2. **Research load from content.** A score from what the Brief contains (features, rule, compliance, integration and non-functional facts, 3 per extra app, 5 for sensitive data, 2 per reference). Under 25 is **focused**, 25 to 60 **standard**, above 60 **deep**. The tier is never an input (RV-04 refuses a result if it changed anything). On the three examples: Quick 17 (focused), Standard 27 (standard), Thorough 83 (deep).
3. **Plan.** A deterministic planner builds ranked topics across eight jobs: features, structure, flows, data, services, design, screens, local. Each job's targets, questions and **slot schemas** (what a covered topic must contain, for example "at least 6 design conventions, 4 distinct aspects") are data files. One model call may propose extra topics; code validates and merges them.
4. **Per-topic workers, in parallel** (bounded lanes). For each topic: write queries (anchor + place + intent, with variants), check each query's anatomy in code (RV-14), search through `SearchPort`, fuse results (reciprocal rank fusion), triage deterministically, read pages through our own `FetchPort`, pick the best passages (BM25), extract typed items, and **verify every quote in code**.
5. **Quote verification.** A quote must be found in the page text after one normal form: Unicode NFKC, zero-width removal, quote and dash folding, whitespace folding, case fold in canonical decomposed form, ellipsis-stitched fragments allowed, with an offset map back to the original text. A claim whose quote is not found is dropped, never repaired by a model.
6. **Coverage audit and follow-ups.** After a round, code checks each topic's slots. A gap analyst model call may propose follow-up queries (at most 2 to 3 per topic, never a repeat). A topic ends when it is covered, found to have nothing (`not_found`), the round gain is under 10%, a ceiling is reached, the user skips, or the user stops that topic.
7. **Checker.** A separate model call reviews claims. Code checks stay primary. The checker runs on the `standard` route reserved for it, as the same model family as the writers (recorded as `checker_vendor: same`).
8. **Assemble once.** Code builds items, assigns ids after sorting, computes prevalence and recency (levels need at least 3 independent sources; source classes limit what SEO listicles can support), detects conflicts (including researched values that contradict a Locked Brief number; Locked wins), and builds the per-lane digests. One model call (the assembly call) orders the items in each lane and writes a short use note for each; it may not add facts (RV-05), and a deterministic fallback uses the notes in `use_notes.yaml`.
9. **Validate and commit.** Sixteen validators (RV-01..17, RV-11 reserved) run. Two can refuse a commit (RV-04, RV-13). The result is written, hashed and committed in one shielded step (result stored, then status, then the final event). A committed result is immutable.

### 3.2 Limits and what a stop means

Each load class has a **soft target** and a **hard limit** in `ceilings.yaml`; the hard limit is never exceeded (RV-12) and a hard stop commits `partial` with a `budget_stop` warning.

| Class | Soft: searches / pages / seconds | Hard: searches / pages / seconds | Topics soft / hard |
| --- | --- | --- | --- |
| focused | 20 / 24 / 180 | 32 / 40 / 330 | 10 / 14 |
| standard | 40 / 60 / 360 | 64 / 96 / 600 | 16 / 22 |
| deep | 80 / 140 / 600 | 120 / 210 / 960 | 26 / 34 |

**Skip** commits `status: skipped` with the verified items so far. A topic the user stops becomes a gap with reason `user_cancelled`. Topics that did not fit become visible gaps (`not_run`), not silence.

### 3.3 Events and reload

Every action becomes a stored `research.event.v1` event with a per-run sequence number: plan ready, each query, each search result, each page read, each fact found, verified or rejected, entities discovered, rounds, warnings, the final event. The stream is SSE with `Last-Event-ID` resume; every detail field is on a per-type allowlist (RV-17); a pure reducer folds events into the snapshot a reloaded page reads. The run continues if the tab closes.

### 3.4 Determinism

An `as_of` date, ids assigned after sorting, stage decisions that never depend on completion order, a per-URL page lock, and volatile data (run id, timestamps, seconds) kept in a separate block excluded from the content hash. A chaos test with random delays gives one hash, and a recorded run replays offline to the same hash.

### 3.5 Money and safety

- **Model calls** go only through `mde_llm_gateway`; only that package imports `openai`. Spend is booked in the shared ledger under `rsch.` ids. Search cost is booked through the guard as `rsch.search.<provider>`.
- **Caps:** a `research_run` scope per run (`RESEARCH_RUN_CAP_USD`, 2.50), a Research total (`RESEARCH_LIVE_BUDGET_USD`, 4.00), the shared ledger, and the repo ceiling.
- **Fetching** is our own code: SSRF checks on every hop (including NAT64 and 6to4 forms), robots, size and time caps; paywalled or unreadable pages are skipped and never bypassed.
- **Secrets:** keys are `SecretStr`; the code only exposes `keys_present()` booleans; the scripts never read or print key values.
- **Live guard:** the live scripts refuse to start unless `LLM_MODE=live` is already in the environment; they never set it.

## 4. What was built (against the plan)

| Plan phase | Built | Evidence |
| --- | --- | --- |
| 0. Baseline and spikes | Safety tag, branch, design docs copied, fixtures extracted from the three exports, hook fixed to `python3`, package registered in root `pyproject`, `.importlinter`, `justfile`; provider parameters checked on official pages | tag `pre/research-20261111-1206`; `docs/research-agent/provider-facts.md` |
| 1. Pure cores, data, intake | `core/` (URLs, quotes, text, load, RRF, passages, sources, anatomy, prevalence, norm keys, applies, slots, hashing, countries, dates, novelty, safety, language), catalog loader and 874 lines of data YAML, reader, schemas | 154 core tests, anti-hard-coding scan |
| 2. Offline pipeline | Ports and fakes, 14 call specs, runner, worker, director, budget, in-memory store, event writer, assembler, RV-01..17, scenarios, generality and determinism tests | all three real fixtures and three synthetic Briefs run with zero violations |
| 3. Real adapters, Postgres, API | Tavily and Serper adapters, production fetcher, recording and replay, spend booking, PostgreSQL store with one shared contract suite, routes, SSE, supervisor, dev server with CORS, CLI | 739 adapter tests, 46 integration tests |
| 4. Front end | Research screens in the console, picker, Compass tab hand-over after Gate 1, CSP origin, tests including a fake server checked against the documented contract | checked in real Chrome (section 6) |
| 5. Live bake-off | Script, judge call, estimate (about USD 0.40), refusal without `LLM_MODE=live` | **not run** |
| 6. Live runs and quality iteration | Batch script for four runs with cassette recording, one-page summaries, measures against the acceptance targets, allowance checks | **not run** |
| 7. Docs and report | README, package page, API page, runbook, ADR 0018, index, PROGRESS, decisions D-125 to D-131, this report | docs check passes |

Size: 106 source files (17,014 lines), 8,603 lines of tests, 14 prompt templates, 2,097 lines of console code, 272 files changed in 26 commits.

### The eight jobs and what each gives Spec

| Job | Looks up | Feeds |
| --- | --- | --- |
| features | what comparable apps offer, how common each feature is | blueprint |
| structure | how apps of this kind are organised and navigated | blueprint |
| flows | usual journeys and their steps | blueprint |
| data | what each record usually holds | blueprint, mock |
| services | providers and integrations (sign-in, payments, messaging, maps) with official sources | integrations |
| design | look, feel and platform guidelines, script and font needs | design |
| screens | usual screens and what is on them | screens |
| local | rules, norms and competitors for the place | verify |

## 5. Tests and checks

| Check | Result |
| --- | --- |
| Research suite (offline) | 1,136 passed |
| PostgreSQL integration (`just research-test-int`) | 46 passed |
| Console tests | 199 passed in `apps/web`; 264 with the Compass API tests |
| Validators | RV-01..17 each have a pass and a fail test (RV-11 is reserved) |
| Scenarios | 29 of the 34 design scenarios are named in tests by id (RS-01 to 09, 11 to 17, 19 to 28, 30, 31, 34); see section 7 for the other five |
| Generality | every fixture runs; three synthetic Briefs (Dubai dental ar/en, Nairobi coffee loyalty sw/en, a tiny converter); a garbage-request Brief; a scan finds no example brand, city or company in source, data or prompts; tier-invariance test |
| Determinism | same hash twice, under random delays, and from a recorded replay |
| Property tests | Hypothesis on quotes, URLs, prevalence and others; one found a real quote-matching bug (see section 8), fixed and pinned |
| Layers | `just layers` passes: the agent imports only core, contracts, gateway and third-party libraries; no other agent, app or the older research package |
| Static checks | ruff clean, ruff format clean, pyright 0 errors in `agents/research`, docs check 0 problems, secret scan passes |

**Known red outside this work:** repo-wide `pyright` reports 479 errors that predate it (473 in `agents/compass`, 4 in `packages/core`, 2 in a Compass latency script), so `just ci-fast` still fails at the typecheck step. I did not touch them. Four stale tests that expected the old 0.50 session cap and two unformatted files were fixed.

## 6. What was seen in the browser

The Compass console (mock mode, port 8110) and the **real** Research dev server (port 8120) were driven in Chrome. A full interview, approval at Gate 1, the automatic start of the run, the findings, a reject through the real API, the `research.json` download and a reload all worked: the POST returned 201, the downloaded `research.json` had 28 items and 23 norms with status `complete`, and a reload re-attached with no second POST. Earlier, the front-end worker also checked the failed, partial, skipped, hash-refused, network-error, 409 and dropped-stream cases against its fake server, in light and dark and at phone width.

Found in this pass and fixed: two topics both labelled "Platform design guidelines" (now "Platform guidelines: <platform>"), and gap reasons the server emits that the console did not word. One behaviour to know: reopening a project whose run has finished does not jump to the Research tab; the tab is there and shows "What we found".

All content in these browser runs is mock content ("MOCK MODE" banner), so it shows the plumbing, not the research quality.

## 7. Not done, and honest limits

1. **No live result yet.** Search quality, Indian-locale quality, the provider choice, cost per run, how many required slots fill and how many norms reach 3 sources on the real web are all unknown. The numbers from offline runs (for example 31 items, 29 searches, USD 0) come from the fake world.
2. **Estimates, not measurements.** Dry-run cost estimates: Quick 0.28, Standard 0.57, Thorough 1.15, synthetic 0.57, about USD 2.6 for the four, plus about 0.40 for the bake-off. At the 8.00 ceiling the must-run-only tripwire is 6.40, so about USD 4.03 is usable above the 2.37 already spent; that leaves little room to re-run.
3. **Scenarios without an id-named test:** RS-10 (delta re-run after a Brief change) was **not built** (stretch goal). RS-18 (reload resumes), RS-29 (cancel a topic) and RS-33 (hash mismatch) are covered by API, intake and console tests that do not carry the id. RS-32 is only partly covered: quotes in the source language verify (RS-23), but items do **not** record a `source_language` field.
4. **Deterministic triage only.** The design allowed an optional model triage call; I did not build it.
5. **English only.** Labels are English. Intent words for other languages are flagged `reviewed: false` until a native speaker reviews them.
6. **Same-vendor checker.** Writers and checker are the same model family, flagged in the output. Code checks stay primary.
7. **Console code has no automated DOM test.** The helpers and the contract are tested (Node tests, a fake server against the documented API, real producer into real consumer); the DOM code in `research.js` was verified by hand in Chrome only.
8. **API gaps the console works around** (found by the console worker): `plan.ready` does not carry `slots_required`, so the live coverage bar estimates unreported topics; the snapshot keeps the last 60 events, so a reload loses older "checked" lines; `fact.found` has no stable id (only `fact.rejected` now carries the label). All are small additive changes.
9. **A Research address other than the default** needs `MDE_RESEARCH_ORIGIN` on the Compass server, or the browser blocks the call.
10. **Two research packages exist** until the older `mde_research` is retired in a separate task.
11. **Serper has no public API reference.** Its request and response facts are third-party only (flagged `[T]`); the live bake-off is what confirms them.

## 8. Decisions and deviations from the plan

| Id | Decision |
| --- | --- |
| D-125 | Live-spend ceiling 6.00 to 8.00 (owner). Only `HARD_CAP_USD` and its derived effective ceiling (7.60) moved; the cap in force stays 6.00 until the owner sets `LLM_BUDGET_USD` |
| D-126 | Package name `mde_research_agent` (owner's choice) because `packages/research` already owns `mde_research`; the older package is untouched |
| D-127 | Checker on Luna on its own route: the `standard` route is reserved for it (a new route would be a contract change) |
| D-128 | Markdown reports live under `docs/research-agent/` (no `.md` allowed elsewhere); JSON artefacts of live runs go to `agents/research/examples/` |
| D-129 | Compass CSP `connect-src` names the Research origin via `MDE_RESEARCH_ORIGIN`; everything else in the policy is byte-identical |
| D-130 | Where the design documents disagreed: shared cache; soft and hard ceilings; Skip commits `skipped`; rejections are an overlay; digests capped separately; flat extraction atoms |
| D-131 | The console registry gained `extras` so the Compass screen finds the Research module at boot |

Other deviations: the hook change (`python3`) went beyond `agents/research/`, as the prompt asked; the four stale session-cap tests and two formatting fixes in older files are outside the agent but were needed for the gates; the cap decision touched CLAUDE.md rule 3, the gateway page and runbooks.

Defect found by the property test and fixed: a letter whose case fold decomposes (Greek iota with dialytika and tonos) failed to match its own upper-case form; the folded text is now put in one canonical decomposed form on both sides.

## 9. Provider facts checked (no live call, no key used)

Opened 2026-11-11 from public pages; details in `docs/research-agent/provider-facts.md`.

- **Tavily [official]:** search reference and OpenAPI <https://docs.tavily.com/documentation/api-reference/endpoint/search>, credits <https://docs.tavily.com/documentation/api-credits>, rate limits <https://docs.tavily.com/documentation/rate-limits>, best practices <https://docs.tavily.com/documentation/best-practices/best-practices-search>. Confirmed: `country` is a lowercase name from a fixed list and only boosts; `language` boosts and `filter_by_language` strictly filters and needs `language`; `include_domains_mode` `restrict` or `prefer` and needs `include_domains`; `exact_match`; `start_date`; `include_published_date` (beta).
- **Serper:** only the home page and third-party pricing reports could be read; no public API reference exists. Treated as a lead, not a fact.

## 10. Recommended changes to design doc 13

1. Add `slots_required` (or a total) to `plan.ready`; add a stable `fact_id` to the `fact.*` events; add a `facts` list to the snapshot so a reload keeps older "checked" lines.
2. Record `source_language` on items (RS-32), and say whether RS-10 (delta re-run) is in or out of the first release.
3. Reserve the `standard` route for the checker until a different checker is priced.
4. Say that the tier is never an input and that the hard ceiling is never exceeded (both enforced by validators now).
5. Enumerate warning codes and gap reasons (now in `docs/api/research-agent.md`).
6. Note that Serper has no official API page, so any parameter claims about it need the live bake-off.

## 11. What happens next

The owner runs the live batches (the agent never sets `LLM_MODE=live`). Commands, prerequisites and troubleshooting are in `docs/runbooks/research-agent.md`:

```
! LLM_MODE=live LLM_BUDGET_USD=8.00 uv run python -m mde_research_agent.live_bakeoff --write-default
! LLM_MODE=live LLM_BUDGET_USD=8.00 uv run python -m mde_research_agent.live_runs --run quick --record
! LLM_MODE=live LLM_BUDGET_USD=8.00 uv run python -m mde_research_agent.live_runs --run all --record
```

After that: read each `research.json` as the Spec lanes would, check the acceptance measures (quote check 100%, required slots at least 70%, at least 60% of searches contribute, at least 70% of norms with 3 or more sources, at least 70% of dated design, feature and service items under 24 months, high-impact items corroborated or flagged), fix planner, slot schemas, query strategy, prompts and assembly where the output is thin, re-run inside the budget, and write the final report with the live tables. Near the allowance the script finishes the run in progress and starts no new one.

## 12. Git

- Branch `feat/research-agent` (local only, nothing pushed, nothing merged into `main`).
- Tags: `pre/research-20261111-1206` (safety point before the work), `research/offline-complete-20261111` (this checkpoint).
- 26 commits since the safety tag: `git log --oneline pre/research-20261111-1206..HEAD`.
- Undo the last commit: `git revert 39b7e0c`.
- Undo the whole task without rewriting history: `git revert --no-commit pre/research-20261111-1206..HEAD` then commit.
- Return to the safety point without losing history: `git switch -c recover/research pre/research-20261111-1206`.
- Working tree is clean.

## 13. Where things are

| What | Where |
| --- | --- |
| Agent code, data, tests | `agents/research/` (README there) |
| Console screens | `apps/web/console/agents/research/` |
| HTTP and event contract | `docs/api/research-agent.md` |
| Package page, runbook, ADR | `docs/packages/research-agent.md`, `docs/runbooks/research-agent.md`, `docs/adr/0018-research-agent-architecture.md` |
| Provider facts | `docs/research-agent/provider-facts.md` |
| Decisions | `docs/implementation/DECISIONS.md` (D-125 to D-131) |
| Progress | `docs/implementation/PROGRESS.md` |
