# Eval plan and test map

Owner: test-engineer. Normative sources: [spec section 9 (situation catalog) and 12 (evals)](../product/context-engine-spec.md), [brief 9](../implementation/BRIEF.md). Live spending rules: [runbook](../runbooks/live-llm-budget.md). First report: [mock-baseline](reports/mock-baseline.md).

This page says what is tested where, how to run it, what each metric means, and what the suite found in the product so far.

## 1. The map

| Level | Where | What it proves | Mark |
| --- | --- | --- | --- |
| Scenarios | `tests/scenarios/SIT-nnn-*.yaml` | one file per row of the 40-row situation catalog, played through the real Discover, Research and Spec Builder behind the real gateway (mock mode) and the real flow | `e2e` (in memory); `integration` (a handful also on PostgreSQL) |
| End to end | `tests/e2e/` | "hi" to `spec_ready` for five archetypes (Guided) and two policies (Enterprise), valid spec, loader slices in budget, hand-off commit, one `spec_ready`, no network | `e2e` |
| Security | `tests/security/` | injection suite, pasted secrets, log redaction, tenant isolation, spec secrets, repo hygiene (existing) | `e2e` for the heavy ones; `integration` for the PostgreSQL twins |
| Integration | `tests/integration/` | the fake store against the persistence contract (existing); whole journeys on PostgreSQL | `integration` |
| Evals | `evals/` | datasets, graders, runners, the sufficiency probe | run by `python -m evals.runners.mock_batch` (offline) |
| Live | `tests/live/`, `evals/runners/live_batch.py` | the real model (lead only) | `live` |

Everything here is offline unless it says live. The suite never touches the network: `socket_guard` (e2e) and `guard` (security) record and block any non-loopback connection, and every test must end with zero attempts.

## 2. How to run

```
uv run pytest tests/scenarios tests/e2e tests/security -q                  # offline, about 5 minutes in total (3m20s + 1m15s measured)
uv run pytest tests/scenarios tests/e2e -q                                  # the two main suites: 162 passed, 12 xfailed, 3m18s measured with other jobs running
uv run pytest tests/scenarios -k SIT-028 -q                                 # one row (variants: SIT-028-...[stop_...])
uv run python scripts/dev_db.py up                                          # then: PostgreSQL variants
uv run pytest tests/scenarios tests/security tests/integration -m integration -q
uv run python -m evals.runners.mock_batch --out mock-latest                 # every dataset, offline, writes a report
uv run python -m evals.runners.make_goldens --check                         # are the golden specs current?
uv run python -m evals.runners.make_situations --check                      # is the situations dataset the spec table?
```

A failing scenario prints a readable transcript (the user's step, every event it produced, the state and the LLM calls of that step, and each failed expectation). Secrets in a transcript are redacted.

## 3. The harness (`evals/harness`)

| Module | Job |
| --- | --- |
| `stack.py` | `build_stack()`: the whole product in one process. The real `Gateway` over `OverlayProvider` (scripted outputs by call id, the call spec's own mock handler for everything else), the real engines, the real `FlowService` over the in-memory store or `PostgresStore`, a git hand-off repo in a temp directory, a fake clock and sequential ids. Holds (`start_plan`) pause an engine mid-run so a test can type while research or the build is genuinely in progress. `network_guard()` blocks outbound sockets. |
| `driver.py` | `Driver` protocol, `FlowDriver` (over `FlowService`), `HttpDriver` (a `FlowDriver` subclass over `create_app` and an ASGI transport: the same journeys run over HTTP, with Postgres in the integration variants). |
| `journeys.py` | scripted conversations from "hi" to `spec_ready` (five archetypes, two policy profiles, Hindi and Hinglish); Enterprise approvals by every required role on one hash. |
| `checks.py` | one definition of a valid spec: nine validators, hash, parse and re-render, consumer simulator with slice budgets, the hand-off repo. |
| `observe.py` | "does this literal appear anywhere": events, records, prompts sent to the provider, call records, logs, every row of the in-memory store or PostgreSQL. |
| `persona.py`, `briefs.py`, `catalog.py` | datasets. |
| `shims.py` | BUG-1 shim (dead since BUG-1 was fixed; remove it in Phase 5). |

Scenario file format (extended Appendix B) is documented at the top of `tests/scenarios/runner.py`: `provider_script` entries (`{intent: ...}` outputs, `{error: timeout}`, `{raw: "..."}` malformed output, `{mock: true}`), `variants`, inline `policies`, tiny budget caps (`gateway: {session_cap_usd: ...}`, the real budget guard refuses), step actions (`user`, `tap`, `upload`, `decide`, `ui`, `wait_for_plan`, `start_plan`/`finish_plan`, `advance_clock`, `new_session`) and `expect` keys. A scripted entry that is never used fails the scenario (a typo must not silently fall back to the mock). `known_issue:` runs a variant as `xfail(strict=True)`: the day the product is fixed it fails and tells you to remove the mark.

## 4. Situation catalog coverage

All 40 rows have a file; `test_scenarios_match_the_spec_catalog` parses the table out of the spec and checks files and texts 1:1 (`evals/datasets/situations/situations.yaml` is the same table as data, generated). Rows with a variant that pins a product bug or gap (`known_issue`) are marked below.

| Rows | Covered by |
| --- | --- |
| 1 to 5 greeting, vague idea, confusion, chit-chat, questions | cost per turn (0 or 1 or 2 calls), help cards, knowledge-base-only answers |
| 6 "like X" | note and original design (passes); research reaches R2 (BUG-2) |
| 7 to 11 long document, uploads, several apps, scope, unsupported type | extraction, one app recommended, phase one, plain refusal with the closest option |
| 12 sensitive domain | five domains each get a must-confirm card in Discover; research adds pointers |
| 13 to 19 contradiction, ignored card, you decide, change of mind, visual options, screen detail, language | resolution card, nudge then assume (silence: GAP-2), Hindi and Hinglish, screen detail (BUG-3), diffs (BUG-4, BUG-5) |
| 20 to 22 unsafe, secrets, injection | decline with a safe alternative, redaction everywhere, injection in an upload and in search results (GAP-1) |
| 23 to 24 abandon, start over | state persists with an open gate, stale Research Note refreshed after 14 days, archive and fresh session |
| 25 to 30 gates | typed yes with and without a gate, unclear approval, approve then change, typing during research or build (queue, Stop, app-type change), stale hashes, gate left open, reminders |
| 31 to 34 failures | timeout, invalid output, one repair, fallback; research failed, slow, off by policy, out of budget; dislike a research item; validation failure to one plain question (BUG-6) |
| 35 to 38 Enterprise | two stakeholders (GAP-3), split approvers, policy violation (GAP-4), budget cap (GAP-5) |
| 39 to 40 post hand-off | light change without a gate (second `spec_ready`), heavy change re-opens Gate 2 |

## 5. Datasets (`evals/datasets`)

* **Personas** (`personas/P01..P10`): hi-only; vague non-technical founder writing Hindi; chatty; confused writing Hinglish; pastes a long PRD; multi-app; unsupported AR game; sensitive health app; Enterprise services firm with policy and approvers; adversarial (injection in an upload and a pasted secret). Format: `evals/harness/persona.py`. Scripted users; the same files drive batch L1 live.
* **Golden briefs** (`briefs/<archetype>/`): `brief.yaml` (input), `expected.yaml` (slots, screens, flows, data, counts) and `APP_SPEC.md.golden` (the spec as the real engines render it in mock mode; not named `.md` because markdown lives under `docs/`). Regenerate with `python -m evals.runners.make_goldens` and review the diff; `tests/e2e/test_journeys.py::test_guided_journey` compares a fresh session with them byte for byte, `tests/e2e/test_goldens.py` runs the consumer simulator on every golden.
* **Injections** (`injections/INJ-01..09`): three in search results, three in uploaded documents (one with zero-width and bidi characters), three page-like (hidden element, HTML comment, markdown-image exfiltration). Each lists the literals that must never reach an output.
* **Situations** (`situations/situations.yaml`): the catalog as data.

## 6. Metrics (`evals/graders`)

| Metric | Definition | Target | Notes |
| --- | --- | --- | --- |
| Cards per Guided session | `DiscoveryState.card_count` (standard, compliance and resolution cards) | 7, cap 10 | mock baseline: mean 6.3, max 7 |
| Assumption rate | assumed slots over filled slots | report | high by design: `may` slots are always defaulted |
| First-pass validator rate | sessions whose build needed no repair round and asked no question | report | pipeline measure in mock mode; the real number is L3 |
| Valid proposals accepted vs dropped | the builder's own tally (`BuildResult.proposals_applied` / `dropped`) | report | small models drop more (see section 9) |
| Screens per app, tokens per slice | consumer simulator over the spec | slice at most 10k tokens | |
| Cost and latency per session | sum of call records | report | zero cost and fake-clock latency in mock mode |
| Injection pass rate | payloads that changed nothing (`evals/graders/injection.py`: no forbidden text anywhere, none reached a later prompt, no prompt or key leak, no state change, valid spec, same slots as the clean document) | 100% | detection rate is reported separately (a tripwire, not the defence) |
| Research additions accepted | included over all additions in the Plan view | report | |
| Context sufficiency probe | accuracy of a small-model call that sees ONLY a screen slice and answers concrete questions (route, recipe, visibility, flow, first data binding, where each action goes) | 100% offline | every wrong or "unknown" answer names a missing field |

The probe is `P1.sufficiency_probe` (`evals/probes/sufficiency/`: call spec, templates, models, a rule-based offline reader as its mock handler, question generator with ground truth from the full spec). It is an eval call, not a product call: it is not in `mde_prompts` or the catalog, and it runs through the real gateway. Offline it can only fail when a slice lacks a field (tested by stripping fields); live (L4) it also measures the model's reading.

## 7. Live batches (lead only)

`just live-eval` runs `python -m evals.runners.live_batch`. With no `--batch` it prints the plan and runs nothing. With `--batch L1|L2|L3|L4` it REFUSES, with the reasons, unless `LLM_MODE=live` is set in the outer environment, `MDE_FORBID_LIVE` is not 1, the ledger's tripwire is not 95% (nor 80% without `--must-run`) and the headroom under the effective cap covers the allowance. `--dry-run` prints what would run. A batch stops when the ledger delta reaches its allowance, records cassettes, prints the delta, and writes the raw result to `evals/reports/live-<batch>.json` (gitignored). Then run `just budget` and put the total in `PROGRESS.md`.

| Batch | Allowance (USD) | Runs |
| --- | --- | --- |
| L1 Discover | 1.20 | 10 personas, 2 runs each, up to the Brief |
| L2 Research | 1.00 | three Light research runs (commerce, booking, internal) |
| L3 End to end | 2.00 | five full guided sessions; golden outputs recorded; cost, latency, first-pass rate |
| L4 Safety and probes | 0.50 | nine injection payloads through the live models (search results are scripted over the live provider), the sufficiency probe on the golden specs, a critic check on a spec with cart and checkout removed |

(L0, the smoke batch, is `just live-smoke`; it ran live once on 2026-11-08, 7 of 7 passed, USD 0.0221, see [the gateway page](../architecture/llm-gateway.md).) The refusal paths and the full logic of each batch are tested offline (`tests/e2e/test_eval_runners.py`, mock stacks, zero cost). L1 to L4 have not run live yet, so the numbers in this plan are offline numbers.

## 8. Reports

`python -m evals.runners.mock_batch --out <name>` writes `docs/evals/reports/<name>.json` and `<name>.md` (deterministic, no timestamps) and raw outputs to `evals/reports/<name>/` (gitignored). [mock-baseline](reports/mock-baseline.md) is the first run: 16 sessions, 100% reached `spec_ready`, 6.3 cards per Guided session, 20 to 27 screens, probe 100%, injection pass rate 100%.

## 9. Honesty notes: where a small model is expected to be weak

Mock mode measures the pipeline and the content files, not Luna. Expect, and check in L3 and L4:

* **Spec proposals (C9a/b/c, C10)**: the most likely to produce invalid ids, fields or screens. The builder drops invalid proposals; the accepted share and the repair rounds (C13) are the numbers to watch. A low share means a heavier baseline and lighter proposals, not a bigger prompt.
* **The critic (C12)**: false positives send valid specs into repair loops (two rounds, then a question to the user); false negatives let mismatches through. The L4 critic check (a feature removed from the outline) is a smoke test, not a quality bar.
* **Slot extraction (C1, C2) in Hindi and Hinglish and from long pasted documents**: the mock understander is a keyword matcher and cannot predict this. Watch fallbacks (`llm_failed`) and "I did not catch that" turns in L1.
* **Research (C6 to C8)**: query quality, citations and extraction fidelity; high-impact facts need two sources or are marked unverified.
* **Reasoning tokens**: billed as output; a token cap hit becomes an `incomplete` response (one bounded retry), then a fallback. Latency after Gate 1 is the user-visible cost.
* **Injection**: structural defences hold regardless of the model; whether a model OBEYS a payload shows up only live (L4). The detection rate is a tripwire.

## 10. Product bugs and gaps found by this suite

**Status (lead, after the fix wave): every item below is FIXED and its pin removed.** BUG-1 (hash rule; `gates.assert_handoff_allowed` and `service._check_build` now use `mde_spec_loader.hashing.compute_spec_hash`), BUG-2, BUG-7 (API merge: references/brand URL to research; inbound text redacted before storage), BUG-3, BUG-4, BUG-5 (builderfix), BUG-6, GAP-1, GAP-3, GAP-5 (flowfix), GAP-2 and GAP-4 (discoveryfix, plus the flow routing `idle` to Discover), GAP-6 (corefix). The table is kept as the record of what the suite found; new findings are again pinned by a strict `xfail`. Original text: every item was pinned by a strict `xfail` (when fixed the test fails and the mark is removed); BUG-1 was shimmed meanwhile.

| Id | Severity | Finding | Pinned by |
| --- | --- | --- | --- |
| BUG-1 | blocker | The spec hash has two definitions: the loader and the builder hash the file with `hash:` blanked; the flow requires `sha256(spec_text)`. With the real builder no plan job ever reaches Gate 2 ("the builder's hash does not match its spec text"). | `tests/e2e/test_known_bugs.py` |
| BUG-2 | high | `FlowService` builds `ResearchRequest` without `references` and `brand_url`, so "like X" never triggers R2 and brand research never runs. | SIT-006 `reference_reaches_research` |
| BUG-3 | high | Screen-level changes never reach the spec: Discover keeps `screen_notes` that nothing reads, and the builder ignores `BuildRequest.change_ops` (`patch_screen`, `set_copy`, `add_screen`, `remove_screen`). | SIT-018 `note_reaches_the_spec`, `detail_after_handoff` |
| BUG-4 | medium | The Plan view's "what changed since Gate 1" lists block ids as removed (`Removed item: APP`, `DESIGN`, ...) and never says the look changed: the flow passes block ids the builder does not count, and not the first draft. | SIT-016 `plan_view_diff_since_gate_one_is_readable` |
| BUG-5 | medium (cost) | A patch build degrades to a full build whenever research switched a pack feature on: `structure_matches` compares source `default` screens with all baseline screens. Every writer re-runs for a one-token change. | SIT-016 `a_design_change_rebuilds_only_the_design_writer` |
| BUG-6 | medium | Answering the builder's `needs_answer` question with the option equal to the current value (`changed_slots` empty) never resumes the run: the session is stranded in DISCOVERING with no card and no gate. | SIT-034 `an_answer_equal_to_the_current_value_still_resumes` |
| BUG-7 | high (security) | The inbound-event journal stores the raw event before Discover redacts it: pasted keys, passwords and the full text of uploads sit verbatim in `session_event` (direction in), on both stores. | `tests/security/test_secrets.py` (memory and PostgreSQL) |
| GAP-1 | low | An injection caught in a search result is only logged; the event log does not note it (spec 9). | SIT-022 `search_result_is_noted_in_the_event_log` |
| GAP-2 | low | The `idle` UI action (silence on a pending card) is in the contract but handled nowhere. | SIT-014 `client_reports_silence` |
| GAP-3 | medium | `Policy.role_precedence` has no reader: conflicting Enterprise answers overwrite silently. | SIT-035 `role_precedence_resolves_the_conflict` |
| GAP-4 | medium | `banned_libraries` and `allowed_regions` are copied into the spec but never checked against what the user asks for (no explanation, no alternatives card). | SIT-037 `banned_library`, `data_region` |
| GAP-5 | medium | Nothing pauses Enterprise planning at the budget cap: the builder swallows `BudgetExceeded` and ships a deterministic plan, so the flow's `budget_reached` handler is unreachable; the flow never consults the budget before the Plan phase. | SIT-038 `pause_before_the_plan_phase` |
| GAP-6 | low | Log scrubbing covers structlog events only; stdlib `logging` lines (the builder uses them) are not scrubbed. | `tests/security/test_log_redaction.py` |

Two observations from the same run, both addressed since: the circuit breaker was per route, so a research outage (five failed searches) opened the shared route and the build that followed ran without LLM refinement (breakers are now keyed by route and tool class, D-064); and about 40% of a build's CPU time was the `stack_profile` validator re-scanning the whole spec for every proposal (`proposals.quick_findings`; the builder fix wave took it on, D-062).

## 11. Adding to the suite

* A scenario: copy a neighbour in `tests/scenarios`, keep the `id` equal to the file stem and `situation` equal to the catalog text; run `uv run pytest tests/scenarios -k <id> -q` and read the transcript.
* A persona: add `evals/datasets/personas/P11-*.yaml` (format in `evals/harness/persona.py`); `tests/e2e/test_eval_datasets.py` plays every file.
* An injection payload: add `INJ-nn-*.yaml` (channel `search_result`, `upload` or `page`; list the forbidden literals); the security suite and the eval runner pick it up (the dataset test pins the count at nine, update it with the plan).
* A golden brief: add `evals/datasets/briefs/<id>/brief.yaml` and run `make_goldens`.
* Secret-looking fixtures carry `# secret-scan: allow` on the same line (the formatter can move a trailing comment: assign the fixture to a variable first).
