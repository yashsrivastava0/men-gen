# Spec agent: handoff for a session on another machine

Written 2026-11-14 on branch `feat/spec-agent`, on top of commit `84d6ebe`. Everything here was checked that day against the repository and the nine saved live reports; where something was not checked, the page says so. A fresh session has no memory of the build. Start with `git log --oneline -5` and `git status`. If the tip has moved, the state may have moved too.

## 1. What this is

Spec is the third engine of the Mobigen Context Engine. It takes an approved Compass Brief and a Research run and writes one static folder of files (the Spec Pack). The pack tells the front-end engine what the app is and does, never how it looks. The parts:

| Part | Where | Job |
| --- | --- | --- |
| `spec-core` | `packages/spec-core/` | the deterministic core: graph model, validators, derivation from Compass and Research, packager, synthetic inputs |
| `compass-intake` | `packages/compass-intake/` | reads Compass exports and Research outputs (a copy of Research's reader, D-175) |
| Spec agent | `agents/spec/` (package `mde_spec_agent`) | the run, the three model lanes, the money wrapper, the HTTP API, the Bench backend |
| Verify | `agents/verify/` | the critic for deep apps only; loaded through the entry-point group `mde.spec.verify` (D-176) |
| Spec Bench | `apps/spec-bench/` | static front end, served by the Spec app on port 8130 |
| Proof tools | `evals/spec/` | the measurements for the proof gates (`proof-tools.md`) |
| Edge suite | `tests/edge/` | 200 linked case ids |

It was built from the owner's "prompt 12 v4". That prompt is kept outside the repo, at `~/Desktop/chats/projects/mobile-design-engine/prompts/12-spec-agent-build-prompt.md` on the owner's Mac. Its design documents 15 to 19 are copied into `docs/architecture-v3/` (D-174). The branch was cut from `main` at `4f16ded`, with the safe-point tag `pre/spec-agent-20261114-0258`.

**Read before acting:**

1. **Not merged.** `feat/spec-agent` is not in `main`. Both `main` and `origin/main` are at `4f16ded`, and `main` has no `agents/spec`, `packages/spec-core` or `packages/compass-intake`. No DECISIONS entry records a merge, and D-174 says nothing merges into `main` without the owner's yes. **Branch from `feat/spec-agent`, not from `main`.**
2. **Nine live reports, not eight.** Commit `ebc558b` added the ninth, the second Thorough run, which ended `partial`. Booked spend is USD 1.68 against the USD 5.00 cap (section 4.3).
3. **The planned live allocations do not fit the money left.** Section 5 lists lines that add to USD 4.00. The cap has USD 3.32 left and the tripwire USD 2.82. Re-cost the plan and record the new allocation before the first live spend.
4. **Two counts in the build brief do not match the repo.** `tests/edge` collects 277 test items, and its source has 66 `xfail` markers (grep count). The brief and commit `c6c946b` say 70. The full-suite figure of 11,512 passed is not in the repo and was not re-run for this page.

## 2. Read first, in this order

1. `CLAUDE.md` (root rules and commands) and `agents/spec/CLAUDE.md` (the package's job and test command).
2. `docs/implementation/CURRENT_STATE.md`, section 2 (the Spec focus). It is partly stale: it says eight runs, USD 1.26 and next decision D-189. Section 9 below lists the stale lines.
3. `docs/spec-agent/BUILD-PLAN.md`: phases, exit checks and the proof gates.
4. `docs/spec-agent/README.md`: the package index and the live results (also partly stale, see section 9).
5. `docs/spec-agent/proof-tools.md`: the proof harness in `evals/spec/`, its verdicts and how to call it.
6. `docs/implementation/DECISIONS.md`, lines 258 to 275 (D-174 to D-190). The next free number is **D-191**.
7. When needed: the design, `docs/architecture-v3/19-spec-big-apps-bench-and-result-first.md` first (it wins), then 18, 17, 16 and 15; the HTTP contract `docs/api/spec-agent.md`; the package pages `docs/packages/spec-core.md`, `compass-intake.md`, `spec-agent.md` and `verify-agent.md`.

## 3. Hard rules that apply

- **Never** read, print, log, commit or copy `.env` or any key. `.env` is git-ignored and not tracked. To see presence only, run `uv run mde doctor`.
- **Live mode is one setting: `LLM_MODE=live`** (CLAUDE.md rule 13, D-140). Do not add another key, permission flag, readiness check or hook that blocks live mode. `tests/security/test_single_live_switch.py` scans every tracked text file for forbidden phrases that describe such a gate. Read its `FORBIDDEN` list before you write about live mode. Say "LLM_MODE=live is the only live setting."
- **Only `mde_llm_gateway` imports a model vendor's SDK** (rule 2). Engines name a route (`fast`, `standard`, `deep`), never a model or a vendor (rule 14, D-171). The model is set in configuration: `MODEL_ROUTES=claude-haiku-5-5` in the launch shell.
- **Every model call** goes through the gateway with a versioned call spec, a mock handler and a deterministic fallback (rule 4).
- **Money is integer micro-USD**, never float. The hard cap for Spec live work is USD 5.00 (`SPEC_LIVE_BUDGET_USD=5.00`), with the tripwire at 4.50 (`SPEC_TRIPWIRE_USD=4.50`). Both are set in the launch shell, never in `.env` (D-177, D-184).
- **Offline tests only**, except tests marked `live`. Live batches are run by the lead or `live-runner`, one at a time (CLAUDE.md rule 3).
- **No SQLite anywhere**, not even in tests.
- **Markdown only** in `README.md`, `CLAUDE.md`, `AGENTS.md` (any depth), `.claude/**` and `docs/**`. `just docs-check` enforces it.
- **Do not touch Compass or Research** (`agents/compass`, `apps/web`, `agents/research`, `apps/research-console`) from Spec work.
- **Ask nobody.** Decide, and log the decision in `docs/implementation/DECISIONS.md` with the next number.
- **Never** force-push, `reset --hard`, or delete or move a tag or branch without the owner's yes. A medium or major task starts with a checkpoint: commit what is pending, then tag `pre/<task>-<yyyymmdd-hhmm>` (CLAUDE.md, "Commit checkpoints").
- **Update `docs/` with every behaviour change.** Keep this folder current.

## 4. State

### 4.1 What is built

All build blocks are in git. The session that built them reported a green offline suite before its last three lane commits (section 4.2).

| Block | In git | Proof gate status |
| --- | --- | --- |
| W0 checkpoint, scaffold, layering | yes | none listed |
| S0 Foundation (graph, validators, config, profiles, vocabularies, harness) | yes, offline | PS-0 and PS-1: no saved report |
| S1 Deterministic core (libraries, derivation, planner, sharder, packager) | yes, offline | PS-2: no saved report |
| S6a Bench skeleton (auto-select, API, journey rail, pack explorer) | yes | the Bench has never been opened in a real browser |
| S2 Product lane (SPEC-PLAN, PROD-MODULE, PROD-INTEG, PROD-JOURNEYS) | yes | PS-3: in all live reports, not scored |
| S3 Design lane (DSGN, tokens by code, presets) | yes | none listed |
| S4 Screens lane (SCRN, MOCK-HERO, BEHAV, COPY-L, G2-SUMMARY) | yes | PS-4 not run; PS-5 run in live reports, not scored; screens lack the doc 15 section 5.7a type-specific fields |
| S5 Self-check and Verify for deep | yes | PS-6 and PS-7 not run; G1.5 "change" moves are recorded, not applied |
| S6b Bench second half (offline parts) | yes | PS-11 not run |
| Edge suite `tests/edge` | yes | the BUILD-PLAN item stays unticked until `test_fn_31` passes (4.2) |

The last three lane commits, newest first: `84d6ebe` (PROD-MODULE mends unknown cross-references), `c74cb68` (SCRN repairs vocabulary slips by code), `29db3db` (DSGN near misses are repaired by code). Those are the R-code commits that came after the last live Thorough run.

### 4.2 Tests

- **Full offline suite.** The session that built this reported 11,512 passed before the last three lane commits. A final run after those commits was still going when this page was written. That number is not in the repo and was not re-run here. Re-run before you trust it (section 7).
- **Edge suite.** `tests/edge` collects 277 test items. Commit `c6c946b` says 200 case ids are linked. The source has 66 `xfail` markers (grep count), each strict. The commit message says 70. Find them with `grep -rn 'xfail' tests/edge`. Each reason names a `BUG-` or `GAP-` id. Whether `test_fn_31` in `tests/edge/test_fn_more_cases.py` passes on the current tip was not checked. BUILD-PLAN says to tick the edge item when it does.

### 4.3 Live runs

The nine reports are in `docs/spec-agent/outputs/live-2026-11-14/`, one `run_<id>.report.json` each. Every number below is copied from those files. "Clean first try" is calls minus repaired. In every report each fallback is also counted as a repair.

| # | Run id | Profile | Status | USD | Calls | Clean first try | Repaired (fallbacks in it) | Validators ok | Banner |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `run_01M7BMPMABTRBPDM6JWP3KKXBZ` | compact (Quick) | approved | 0.0413 | 17 | 9 | 8 (6) | yes | weak_route, "108 of 18 nodes" |
| 2 | `run_01M7BN4NTQG4EWSTFHG79YYGRA` | compact | approved | 0.0436 | 17 | 8 | 9 (5) | no | weak_route, "4 of 18" |
| 3 | `run_01M7BNQ4CRDXTGWEAMV2QSF31F` | compact | approved | 0.0820 | 17 | 13 | 4 (3) | yes | none |
| 4 | `run_01M7C3D7S4JVKC41EEFNZ8J9ND` | compact | approved | 0.0790 | 17 | 11 | 6 (3) | yes | weak_route, "4 of 18" |
| 5 | `run_01M7CP8B4CX1W9CP22ZBWKTGB6` | compact | approved | 0.0834 | 17 | 14 | 3 (1) | yes | weak_route, "8 of 18" |
| 6 | `run_01M7CPYEQ62FEN12Q05YE8S6BA` | compact | approved | 0.0701 | 17 | 16 | 1 (0) | yes | none |
| 7 | `run_01M7CQ4F4MPG6ZGFRA9QWNVFCX` | standard | approved | 0.1450 | 25 | 22 | 3 (0) | no | none |
| 8 | `run_01M7CQCXT7XKPQKGVVT7JGYTQH` | deep, Verify (Thorough run 1) | approved | 0.5382 | 83 | 65 | 18 (10) | no | weak_route, "12 of 65" |
| 9 | `run_01M7CTNC2ZH4Q0B4D3Z82ECAC6` | deep, Verify (Thorough run 2) | partial: `rate_limit` | 0.2942 | 48 | 39 | 9 (1) | no | none |

- **Totals.** The nine reports add to USD 1.3768 and 258 calls.
- **Packs.** Run 8 wrote 222 files and 344,867 tokens, with 42 task cards. Its largest task read 27,868 of the 28,000 budget. Run 9 wrote 230 files, 356,760 tokens and 43 task cards, largest read 26,912. All nine reports show `over_budget: 0`.
- **Banners.** Four of the six Quick runs show `weak_route`, not just the one in the table. Run 1 reads "108 of 18 nodes", which is more repairs than nodes. Nothing explains that count yet.
- **Validators.** `ok` is false in run 2, the Standard run and both Thorough runs. The report's `by_validator` field is empty in all nine, so the failing check is not named. Five of six Quick runs report `ok` true.
- **Critic vendor.** Run 8 records `same`. The service passes the literal `"same"` where it builds Verify (`agents/spec/src/mde_spec_agent/service.py`, lines 799 and 1215). Run 9 records none, because it stopped first.
- **Thorough run 1 (row 8) timing.** Its id time is 15:19:42 (+05:30). That is before commit `838368c` (D-188, 15:40:51), so run 8 does not show that fix.
- **Thorough run 2 (row 9) stop.** Its id time is 16:16:44 (+05:30). That is after commit `f3bd091` (16:16:36) and before the D-190 fix `1939ce6` (18:12:08). D-190 records the cause as a Verify canary that timed out while the breaker was open. The report itself only says `partial_reason: rate_limit`. The D-190 fix has never run live.
- **Quick runs 1 to 5.** They were made while the code was changing. Compare them with care.

**Spend.** `uv run python scripts/spec_spend_check.py`, read on 2026-11-14:

- The spend record holds 1,682,477 micro-USD (USD 1.68), with 0 reserved.
- The period ledgers under `data/.budget/spec` add to 1,578,900 micro-USD (USD 1.58).
- The check reports the higher figure. So **USD 1.68 is booked**, leaving USD 3.32 under the cap and USD 2.82 under the tripwire.
- The record is about USD 0.31 above the sum of the nine reports, and about USD 0.10 above the ledgers. The repo does not explain either gap.

### 4.4 Where the proof gates stand

Nothing is Proven. A gate is never ticked in `BUILD-PLAN.md` until its own bar is met. The bars are in doc 16 and the `proof:` section of `packages/spec-core/src/mde_spec_core/data/spec-config.yaml`.

| Gate | Status |
| --- | --- |
| PS-0 offline build | no saved report |
| PS-1 intake on the nine exports | no saved report |
| PS-2 packager and budgets | no saved report |
| PS-3 derivation and phases (Product lane) | run in the live reports, not scored; the run on all nine exports is not done |
| PS-4 freedom | not run |
| PS-5 writing quality per step | six Quick runs and one Standard run, not scored; Standard has one run, not three |
| PS-6 consumer probe and developer test | not run |
| PS-7 planted-defect test | not run (Verify ran inside run 8; that is not this test) |
| PS-8 scale | not run |
| PS-9 edge suite and provider switch | offline suite built; the live provider-switch runs are not run |
| PS-10 content model | not run |
| PS-11 Spec Bench | not run |
| PS-12a synthetic 60-screen app | not run |
| PS-12b real Thorough export with Verify | run 8 approved (83 calls, 42 task cards); run 9 partial; not scored |
| PS-12c Uber Eats-class program | not run |

## 5. What is not done (the pending list, in order)

**(a) Re-run the live Thorough run on the fixed code,** 65 screens over 5 apps (D-180), with Verify. Re-run Standard once more. Run 8 predates the D-188 fix and run 9 stopped before the D-190 fix; neither shows the current code. Run 9's stop is the first thing this re-run tests.

**(b) Scored live proof gates, PS-3 to PS-12, with repeats.** Use `evals.spec.report.run_all` and `make_proof_runner` (`docs/spec-agent/proof-tools.md`). Report ranges, not single numbers. A live measurement gets `pass` or `fail` only when it is repeated at least twice; one live run gives `single_run` (`proof-tools.md`). The planned allocations from the build brief:

| Line | USD |
| --- | --- |
| price check | 0.05 |
| PS-3 | 0.35 |
| PS-4 | 0.20 |
| PS-5 | 0.50 |
| PS-6 | 0.30 |
| PS-7 | 0.25 |
| PS-12a synthetic 60-screen app, with Verify | 0.60 |
| PS-12b Thorough, with Verify | 0.60 |
| PS-8 extras | 0.15 |
| PS-9 live | 0.20 |
| PS-10 | 0.30 |
| Bench smoke | 0.05 |
| reserve | 0.45 |
| **total** | **4.00** |

The total does not fit. Money left is USD 3.32 to the cap or USD 2.82 to the tripwire. Without the reserve, the lines still add to 3.55. Cut lines before any live spend, in the brief's drop order: PS-12c first (not in the total), then PS-9 live (0.20), then PS-10 extras, then PS-5 repeats (keep two). The drop order alone may not be enough, so re-cost the lines. The one Thorough run (USD 0.54) is measured. The other lines are the plan's estimates. It is not stated whether the Thorough re-run in (a) is the PS-12b line. Settle that in the DECISIONS entry.

**(c) Write `docs/spec-agent/REPORT-<date>.md`,** in plain words. For each gate: numbers or ranges against the thresholds. Also: first-try and repair rates per call kind, R-code counts, banners, writer profile limits, budget booked against USD 5.00, every D-number, every place a doc is wrong, what the owner should read first, and the next step. The build brief asks for this document to be written by a Haiku-class agent.

**(d) Tick the boxes** in `BUILD-PLAN.md`, `PROGRESS.md` and `CURRENT_STATE.md`. Tag the phases `spec-agent/...-p<n>` (none exist yet: `git tag -l 'spec-agent*'` is empty). Push the branch and the tags with a plain `git push`.

**(e) Start the Bench and leave it running:** `uv run python scripts/spec_app.py start`, then open `http://127.0.0.1:8130/` in a real browser. This has not been done. No browser has opened the Bench, and `agents/spec/tests/e2e/browser_check.py` has no saved result.

## 6. Known gaps (say these plainly)

- Screens lack the doc 15 section 5.7a type-specific fields.
- G1.5 "change" moves are recorded but not applied.
- Verify runs only the final stage in the service, and it applies class A patches only, through a conservative applier (BUILD-PLAN).
- `critic_vendor` is a parameter of the Verify port, defaulting to `same`. The service passes the literal `"same"` where it builds Verify (section 4.3). No code in this build passes another value (checked by grep on 2026-11-14).
- The limiter is not halved on a 429. This is BUG-MD-3 (D-187, strict xfail).
- There is no change controller or change set. The build notes name GAP-FE-2, CH-3 and BG-4. Those IDs were not re-checked against the code for this page.
- The three older exports with no structure (`delhi-restaurant-delivery`, `pune-clinic-appointments`, `hospital-chain-enterprise`, in `docs/compass/research-inputs/`) end with 0 entities, operations and flows in the code baseline. The build notes say so; not re-checked here.
- **R-code counts in the reports are incomplete.** `rcode_counts` holds only the six core repair kinds. Lane repairs (D-189) go to the run's `RCodeLog` (`agents/spec/src/mde_spec_agent/run/context.py`, passed to the packager at `run/steps_code.py`) and are emitted as `repair.rcode` events. The saved reports cannot show them. Run 9 came after `f3bd091`, and all six core counts are zero there. Read the run's events and `first_errors` instead.
- Some Verify final-stage first errors are not recorded. The build notes say so; not re-checked here.
- The `weak_route` banner can count more repairs than nodes (run 1, "108 of 18").
- Validators fail with no named check (section 4.3).
- The Thorough Research input (`thorough-freight-fleet-india`) matches a partial Research run. `bench auto` says so. The three synthetic inputs have no Research run at all.
- The Bench has never been opened in a browser (section 5e).
- Every proof number so far comes from a single run.

## 7. How to run

Verified against the code on 2026-11-14. Run from the repository root.

```
# list the Compass + Research pairs the Bench would propose (read-only, no model)
uv run python -m mde_spec_agent bench auto

# one run, no model, no money
uv run python -m mde_spec_agent bench run --input quick-yoga-studio-bengaluru --offline

# a live run (spends money). Set these in the launch shell, never in .env.
# A key for the route's vendor must already be present; `uv run mde doctor` shows presence only.
export LLM_MODE=live MODEL_ROUTES=claude-haiku-5-5 SPEC_LIVE_BUDGET_USD=5.00 SPEC_TRIPWIRE_USD=4.50
uv run python -m mde_spec_agent bench run --input standard-produce-marketplace-nashik

# the numbers for one run folder (agents/spec/.runtime/runs/ is git-ignored)
uv run python scripts/spec_run_report.py agents/spec/.runtime/runs/<run_id> --out docs/spec-agent/outputs/<folder>

# the money check: spend record and period ledgers, read-only
uv run python scripts/spec_spend_check.py

# the Bench: API and front end on 127.0.0.1:8130
uv run python scripts/spec_app.py start        # also: status | logs | stop | restart
just spec-dev                                  # the same app in the foreground
just spec-fake                                 # TEST FAKE app on 8150, mock, no key
```

`--input` takes a file path, an id from `bench auto`, or `synth:<name>`:

| Input | Profile | What it is |
| --- | --- | --- |
| `quick-yoga-studio-bengaluru` | compact | real export, 13 screens, Research complete |
| `standard-produce-marketplace-nashik` | standard | real export, 28 screens, Research complete |
| `thorough-freight-fleet-india` | deep | real export, 132 Compass screens (65 derived), Research partial |
| `synth:big` | deep | synthetic single app, 60 screens, no Research run |
| `synth:program` | deep | synthetic program, 4 apps of 35 screens, no Research run |

Tests:

```
just spec-test                                              # agents/spec and agents/verify, offline
just spec-bench-test                                        # apps/spec-bench
uv run pytest tests/edge -m "not live and not integration"  # the edge-case suite
uv run pytest evals/spec -q                                 # proof tools, offline (about two minutes)
uv run pytest agents packages tests/edge apps/spec-bench evals -m "not integration and not live"   # full offline run (about 22 minutes)
just lint
just layers                                                 # runs uv run lint-imports
just docs-check
```

Notes for this machine: `just ci-fast` was red on 2026-11-11 (pyright errors in Compass and Research tests, CURRENT_STATE section 5). Check it before you trust it. `timeout` is not installed on this Mac. Pipes into `tail` hide the exit code of `pytest`, so redirect to a file and read `$?`. Use `sed -i ''` on macOS.

## 8. Lessons learned

- **Offline mocks never show a real model's habits.** Each live run found a new class of first-attempt failure. The examples below are copied from the `first_errors` in the reports.

| Failure shape | Example from a saved report | What fixed it |
| --- | --- | --- |
| an id the call never offered | `basis 'MOD-001' is not an id of this call` | BEHAV repairs an unknown basis id by code (`cc7761e`) |
| provenance that cites an unoffered id | `provenance 'compass:RUL-S003' cites 'RUL-S003', which is not an id of this call` | the Product lane repairs provenance by code (`3d67023`, D-189) |
| a new entity with no screen | `a new entity must be a join or log entity (it has no screen)` | code downgrades it (D-189) |
| a missing card | `SCR-028: exactly one card is needed, got 0` | SCRN repairs a missing card by code (`2f76b17`) |
| a G2 paragraph over the limit | `the paragraph is longer than 110 words` | G2-SUMMARY trims at a sentence boundary (`53e44d0`) |
| a plain-language guard | `guard 'method_chosen' must be signed_in, role_has_access, ...` | SCRN repairs plain-language guards by code (`71ebcdb`) |
| form words in prose | `uses form words (banner)` | the lint matches layout phrases only (D-185); "side by side" removed (D-189) |
| too many canary questions | `more than 12 blocking questions; keep the ones that truly block` | cut by code (D-190) |
| a reworded criterion or confirm item | `'RI-086 automated invoice processing ...' is not an item of the confirm list` | matched back by code (D-188) |

- **The fix pattern:** a mechanical repair in code (drop, or downgrade to assumed; never invent evidence), a prompt hint, then a test that reproduces the live shape (D-189).
- **Read `first_errors` in the run report before guessing.** `scripts/spec_run_report.py` prints them.
- **A 402 or a 429 that survives the gateway's retries stops the run** (D-187). **A Verify canary never loses the pack** (D-190).
- **Date every number by the commit it ran on.** Runs 1 to 9 each predate some fix.
- **Check the spend record before any live step.** The record and the ledgers disagree (section 4.3).
- **Disk was nearly full once.** If tests fail for no clear reason, clean the caches first. `agents/spec/.runtime/` is git-ignored and holds the run folders.

## 9. Process for the next session

- **Branch from `feat/spec-agent`** (not `main`, see section 1). Name the new branch for its work, for example `feat/spec-proof`. Commit in small steps. Use the footer lines `Task:`, `Phase:` and `Refs:`, and the `Co-Authored-By` trailer that names the model that wrote the commit.
- **Delegate lane fixes** to fresh subagents. Give each one the failure shape from `first_errors` and the test that reproduces it. The agent roster in `.claude/agents/` has no agent for `agents/spec`. `spec-engineer` and `builder-engineer` cover the older `spec-loader` and `spec-builder` packages, not this build.
- **Run live batches yourself** (or through `live-runner`), one at a time, with the caps in section 7 set in the launch shell.
- **Record every decision** in `docs/implementation/DECISIONS.md`, from D-191. Include the new money allocation (section 5b).
- **Never call a gate Proven** until its bar is met and its numbers are in the report.

## Appendix A: docs that are out of date

- `docs/implementation/CURRENT_STATE.md`, section 2: "eight" runs, "USD 1.26", next decision "D-189", and item 2 (uncommitted files and a failing edge test). The tree was clean on 2026-11-14, and the edge status is unknown.
- `docs/spec-agent/README.md`, "Live results": eight runs (now nine); "Quick and Standard show no banner" (four of six Quick runs show `weak_route`); "Quick reports validators.ok true" (five of six do); "the eight reports add up to USD 1.08" (nine add to USD 1.38).
- `docs/spec-agent/BUILD-PLAN.md`: "eight single live runs" in the status line; PS-12b says "one Thorough run" (there are two, one partial).
- The build brief's claim that the branch was "merged to main on 2026-11-14" is wrong for this repo (section 1).

## Appendix B: where things are

| Path | What |
| --- | --- |
| `agents/spec/src/mde_spec_agent/` | `service.py` (the run and API), `bench/` (auto-select), `run/` (run store, steps, events), `lanes/product`, `lanes/design`, `lanes/screens`, `calls/` (the guarded runner) |
| `agents/spec/tests/` | package tests; `e2e/browser_check.py` (Playwright, never run), `e2e/fake_app.py` |
| `agents/verify/` | the Verify agent |
| `packages/spec-core/src/mde_spec_core/` | `synth.py` (synthetic presets `tiny`, `small`, `medium`, `big`, `program`, `erp`), `rcode.py`, `data/spec-config.yaml` (the proof thresholds) |
| `packages/compass-intake/` | Compass and Research readers |
| `apps/spec-bench/` | `index.html`, `app.js`, `style.css`, `views/`, `tests/` |
| `evals/spec/` | proof tools; `fixtures/catalogues/` (React Native Paper and React Native Elements snapshots) |
| `tests/edge/` | the edge-case suite |
| `scripts/spec_app.py`, `scripts/spec_run_report.py`, `scripts/spec_spend_check.py` | the app, the run report, the money check |
| `docs/spec-agent/` | this folder: README, BUILD-PLAN, proof-tools, `outputs/live-2026-11-14/` (the nine reports) |
| `docs/architecture-v3/15` to `19` | the design (19 wins, then 18, 17, 16, 15) |
| `docs/api/spec-agent.md` | the HTTP contract |
| `docs/packages/spec-core.md`, `compass-intake.md`, `spec-agent.md`, `verify-agent.md` | package pages |
| `docs/implementation/DECISIONS.md`, lines 258 to 275 | D-174 to D-190 |
| `docs/compass/research-inputs/` | the nine Compass exports the Spec build reads (three real ones are used by `bench`) |

`packages/spec-builder/` and `packages/spec-loader/` belong to the earlier Context Engine (CURRENT_STATE section 1), not to this build.
