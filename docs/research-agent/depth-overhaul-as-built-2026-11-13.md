# Research agent depth overhaul: as built, what was measured, what is not proven (2026-11-13)

For the owner, in plain words. Branch `feat/research-depth`; decisions D-156 to D-170 in `docs/implementation/DECISIONS.md`;
the plan was `docs/research-agent/` plus the plan file named in D-156. Everything here was checked on that date; where a
number comes from a live run it names the run, and where something was not run live it says so.

## 1. What this is for

Nobody reads the Research output. Spec reads it, and through Spec an engine that writes an Expo / React Native app. A fact is
worth something only if Spec can turn it into a decision and the engine can build from it without guessing: exact, sourced,
tied to a Brief id. So the work aimed at **usable facts per dollar**, above all facts Spec may default on (official or
corroborated), and at the build-grade shapes the engine needs: provider dossiers, platform constraints, a compliance register.

## 2. What was wrong (measured before building)

| Finding | Evidence |
| --- | --- |
| 30 to 41% of facts were unusable because the checker said "partial" | 7 recorded runs; partial on 82% of workflows, 75% of provider facts, 64% of competitors |
| 94% of those partials already had the unsupported fields removed by code, yet stayed "partial" | 198 of 211 partial atoms |
| The Screens required slot could never fill (a bug) | `{state, content}` read as empty |
| Medium and Big were not deeper than Small | 41 and 39 planned topics left unrun at the old caps; the planner dropped priority-88 provider topics for a priority-45 empty one |
| No query ever asked for official pages | the authority registry's topics were never read |
| The planted-error test could not fail a checker that says "partial" to everything | (fixed first) |
| Serper's runs ended on fetch timeouts, not weak search | 79 of 83 fetches timed out |

## 3. What changed

* **Intake survives Compass changes** (`intake/formats.py`): any format version, a missing approval record is a visible flag
  (`input_binding`), never a refusal; one adapter and one fixture per known format; `compass-contract.md` says what Research needs.
  All nine inputs load (three needed it).
* **Provider credits, global** (Budget page and a bar chip): Tavily is read from Tavily's own `/usage`; Serper is counted. Tavily
  said 320 of 1,000 used after the work, our counter 323. The panel warns, it never blocks.
* **Evidence quality**: checker v3 (numbered sentences, country and company shown), tighten-and-ask-once-more, the Screens slot,
  the fixed planted-error test and the re-extraction experiment: `evidence-quality-2026-11-13.md`.
* **Discovery and planning** (`discovery-and-planning.md`): risk topics sit outside the cap, official-first queries, own-site
  follow-up for the most important providers, a discovery query on vendor topics, a second engine on them, registries for
  Portugal, Senegal, Mexico, Guatemala, Costa Rica, the EU and more India (each domain checked on the web first; one rejected,
  two unresolved), numbers across languages and currencies.
* **Build-grade content**: `provider_fact` and `platform_constraint` kinds with typed values, code-built dossiers, platform
  constraints, a compliance register, lane decisions, a ranked confirm list, and a reference `pack(need_card)` for Spec
  (`spec-contract.md`). The output stays `research.v5`, extended (D-162).
* **Robustness**: fetch retries, bounded text extraction, longer timeouts at scale, a failover cool-down, a model call that
  waits out an open circuit, a bounded "to confirm" protection in the main pack.

## 4. Live runs on the new code (all booked in the spend record; USD 4.68 of 5.50 in total, including the earlier 1.10)

| Run | Input and provider | Status | Searches / pages / seconds | USD | Credits | Required slots | Facts: official / single / unverified |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Small yoga, Tavily | partial (ceiling) | 40 / 60 / 197 | 0.394 | Tavily 44 | 12 of 19 | 73 / 18 / 15 (14%) |
| 2 | Small yoga, Tavily (after fixes) | partial (ceiling) | 52 / 80 / 242 | 0.492 | Tavily 54 | 15 of 19 (79%) | 103 / 31 / 16 (11%) |
| 3 | Medium Nashik, Tavily (old ceilings) | partial (ceiling) | 91 / 130 / 244 | 0.855 | Tavily 93 | 20 of 40 (50%) | 185 / 39 / 32 (12.5%) |
| 4 | Small yoga, Serper | partial (ceiling) | 52 / 79 / 241 | 0.135 | Serper 52 | 11 of 19 (58%) | 89 / 78 / 23 (12%) |
| 5, 6 | Big freight, Serper (fetch broken) | partial | 196, 189 / 15, 27 pages | 0.24, 0.31 | Serper 179, 181 | 7, 10 of 66 | 58, 99 facts |
| 7 | Big freight, Serper (after the fetch fix) | partial (page ceiling) | 199 / 300 / 434 | 0.517 | Serper 199, Tavily 12 | 55 of 67 (82%) | 307 / 205 / 61 (11%) |
| 8, 9, 10 | Medium Nashik, Serper | **failed** (three attempts) | 68 / 95 | 0.11 each | | | none |

Runs 8 and 9 failed for a reason outside the research code: **OpenAI answered "You have no credits remaining"** (HTTP 429) to
every call, which opened the gateway's circuit breaker and stopped both runs after one minute. No further live call is
possible until credits are added to the OpenAI account (this is the account balance, not the repo's budget guard).

## 5. Before and after, against the targets

| Target | Before (2026-11-11, Small) | After | Verdict |
| --- | --- | --- | --- |
| Small required slots >= 70% in 3 of 3 runs | 6 to 8 of 10 | 79% in the best of 2 Tavily runs (63% in the first) | **not shown 3 of 3** |
| Small unusable (unverified) under 15% | 30 to 41% | 14% and 11% | met in 2 of 2 |
| Small default-grade facts (official or corroborated) >= 25 | 16 | 73 and 103 | met (see the caveat below) |
| Payments: an in-country provider with official facts | none (Google, Zoho, Visa, Dodo) | Cashfree and PhonePe, 69% of dossier fields filled, 91 to 92% official | met on Small (Tavily); Medium found global providers |
| Zero validation errors, honest status | | Small and Medium 0; Big 3 errors (fixed after the run, not re-run) | partly |
| Cost per default-grade fact <= USD 0.013 | 0.018 to 0.020 | Small 0.0048, Medium 0.0046, Big 0.0017 | met |
| Cost per run: Small 0.32 to 0.40 | 0.29 to 0.32 | **0.49** (52 searches, platform and provider follow-ups) | **over** (explained, D-163) |
| Medium and Big: complete or honest partial, slots >= 70%, no risk topic trimmed | never run | Medium 50% (old ceilings), Big 82%; no risk topic trimmed | Big met, **Medium not met** |
| A dossier per integration, >= 60% from official sources where docs exist | none | payments yes on Small; many providers 8 to 20% (named, not followed up) | **partly** |
| A compliance register per regime | none | 1 to 3 entries, regimes not named | **not met** |
| Checker: planted catch >= 90% (80% per kind), false rejection <= 5% | never run | 98.9%, 0%, 0 of 31 planted errors climbed | met |
| Consumer probe: fewer ASSUMED, invented not higher | never run | **not run** (OpenAI credits) | **not done** |

Caveat on "default-grade": it counts facts about a provider taken from that provider's own site (`official_inferred`) and
provider documentation. That is what Spec needs for integrations, but it is why the number is high; the owner's spot-check
(60 facts) of run 2 or 7 is the honest test of whether those facts are right.

## 6. What the downstream engines get that they did not

* Spec: provider dossiers with typed fees and limits (each field has its own quote, an evidence label, or an explicit null with
  a reason), platform constraints (for example in-app payments under store rules, with "needs a development build" where Expo
  states it), a ranked confirm list, lane decisions that say what Research gave or the gap, and `pack(need_card)` as the
  selection contract.
* The app engine: a facts' typed amounts are verified against the quote by code (a wrong amount or currency rejects only
  that fact); numbers cannot drift across currencies; conflicting fees stay two facts.

## 7. What is not proven, and what to do next

* **Medium on the final code was never run** (OpenAI credits ran out). The 50% slots figure is from the old ceilings, which
  were raised afterwards. Run it first.
* **Big on Tavily was never run**; Big's 82% is Serper after the fetch fix. Serper filled fewer slots than Tavily on Small
  (58% against 79%, one run each), so by your rule Tavily stays the saved default. Serper is a good cheap second engine.
* The store limit, the label and RV-21 fixes, and the circuit wait were made after the last successful run and are tested
  offline only.
* The compliance register is thin; recency and effective dates were not extended; the bounded second-source pass for
  corroboration and quote-first extraction were not built (tightening reached the target on its sample); a run cannot be
  resumed (D-167); the checker and the writer are the same model (one vendor in the gateway).
* The console's new views (credits, requests per topic) were not opened in a browser (playwright is not installed here).
* Porto, Dakar, Pune, hospital, Delhi and Central America were previewed (zero cost) and not run live.

Money to finish the proof, once OpenAI credits are added: Medium on Tavily about USD 1.1 (or 0.4 on Serper), the consumer probe
0.15, Porto 0.45, Big on Tavily about 1.9. The new live runs used 4.68 of the 5.50 total, and the Tavily plan has about 680 of
1,000 credits left this cycle.

## 8. How to run and read it

`uv run python scripts/research_app.py start` (with `RESEARCH_LIVE_BUDGET_USD=5.50 RESEARCH_TRIPWIRE_USD=5.00` for this
budget); the Budget page shows money and search credits; `uv run python scripts/research_app.py credits show|set`; evals:
`uv run python -m mde_research_agent.eval planted|probe|reextract` (app stopped, live mode on). To spot-check, open the
Results page of run `rrn_01M796H29N35AX60V2R0D5RN3S` (Small, Tavily) or `rrn_01M799TH7SBM1RWFAZ0MQET5SH` (Big) and use the spot-check for 60 facts.
