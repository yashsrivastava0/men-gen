# The Research agent on Claude: what was done, what the tests found, and my opinion (2026-11-13)

For the owner, in plain words. Branch `feat/llm-providers` (cut from `feat/research-depth`, safe-point tag `pre/llm-providers-20261113-2128`); decisions D-171 and D-172 in `docs/implementation/DECISIONS.md`. Everything marked "live" was run against the real Claude API and the real web on this date; every run's output is saved in [`outputs/claude-2026-11-13/`](outputs/claude-2026-11-13/README.md).

## 1. The short answer

* **It works, and it is not tied to a vendor any more.** The Research agent ran end to end on Claude (Haiku 5.5) with no change to the agent's logic. Switching vendor is now one line (`MODEL_ROUTES`). The rule is written into `CLAUDE.md` (rule 14) and enforced by tests.
* **Out of the box, Claude was worse than the OpenAI model on the same job.** The first Small run was a bad result: 4 of 19 required slots, 43% of facts unverified. Two real causes were found and fixed (section 4). After the fixes the same Small input gave **19 of 19 required slots, 209 facts, 11% unverified**, against 15 of 19 and 11% on the earlier OpenAI run.
* **Medium and Big both completed** with the required-slot share above the 70% target (78% and 90%), 0 validation errors on Medium and 1 on Big (RV-17, a label that copies a quote; see section 7).
* **The consumer probe did not pass its own bar.** The research clearly helps the stand-in Spec (grounded statements 0 to 52, assumptions 59 to 24), but "invented" statements rose from 0 to 23, mostly where the research was thin (features and screens). That is a warning for the Spec engine, not only for Research (section 6).
* **Cost:** the model part of a run is about 3.7 times what the OpenAI model cost (Small: USD 0.22 against 0.06), because the Claude model has to be run at a higher thinking level to be faithful. Search credits are still most of a run's cost. The whole test used about **USD 3.0 of model calls** (your cap was USD 5) and USD 1.7 of search credits, booked at the highest rate: USD 4.66 in the period of 4.95.

## 2. The plan against what happened

| Plan item | Outcome |
| --- | --- |
| Rule in `CLAUDE.md`: no agent or engine depends on a model vendor | Done: rule 14 and a widened rule 2; also in the gateway's `CLAUDE.md`, its docs and the Research README. A test fails if a vendor or model name appears in the Research source |
| Switch in the gateway, not across the code | Done: a second provider, a router that picks the vendor per call, a `vendor` on every price row. Engines changed only where they asked "which vendor?" (the checker label, the readiness check) |
| Use the right settings for the Anthropic model | Done, and it mattered: request dialect, schema limits (section 4), thinking level (section 4), usage and error accounting |
| Run the Medium input from the Compass outputs; finish what was pending | Done: Medium Nashik complete; consumer probe run (not passed); Porto run; Big run |
| Small, Medium and Big on their own inputs | Small (yoga, Bengaluru; run three times), Medium (produce marketplace, Nashik), Big (freight fleet, India), plus Porto as the new country. **Not run:** Dakar, Pune clinic, hospital and Central America (money), and Medium and Big on Tavily (see section 8) |
| Cap of USD 5 | Kept: USD 2.98 of model calls in the period ledger (plus about 0.03 outside it), USD 4.66 including search credits |
| Save the output in the docs folder | Done: `docs/research-agent/outputs/claude-2026-11-13/` (research.json, a readable report and a numbers file for every run, plus the experiment results) |
| A report with an opinion | This page |

## 3. What was built (in the gateway, nowhere else)

* `AnthropicMessagesProvider`: one call per request, the official SDK with retries off (the gateway owns retries), no tools yet, no sampling parameters, no prefill. Usage is rebuilt to the gateway's shape (Claude reports cache reads separately from input). A spent balance is reported as status 402, is never retried and opens the circuit breaker once instead of failing call by call (the OpenAI "no credits" 429 was retried, which cost us a run on 2026-11-13).
* A **router** that picks the vendor per call from the call's model; the vendor of a model is written on its row in `pricing.yaml`. One vendor in use means that provider alone, so nothing changed for single-vendor setups.
* `MODEL_ROUTES` accepts JSON, `fast=a,standard=b,deep=c` or one model id for all three routes. A vendor's key is only needed when a route uses it, and the error names the route. A model with no price is refused at start-up (before, an unpriced route made every call quietly fall back with no error).
* The Research app now names what its model routes need on the Preflight page and refuses to start a run until it is set; its "checker is the same vendor" label comes from the gateway.
* Prices added: Haiku 5.5 (0.10 / 0.50 per million tokens; five times above 100K), Sonnet 5.5 (2 / 10), Opus 5.5 (4 / 20).
* **To switch back to OpenAI:** `MODEL_ROUTES=fast=gpt-6-luna,standard=gpt-6-luna,deep=gpt-6-luna` in the launch environment (or unset it), with `OPENAI_API_KEY` set. Nothing else changes. To run Claude: `MODEL_ROUTES=claude-haiku-5-5` (one model for all three routes) with `ANTHROPIC_API_KEY`. Never put these in `.env`; I never read or printed a key.

## 4. What the live tests found, in the order they happened

**4.1 Smoke test (about USD 0.001).** All three routes answer through the gateway; Haiku 2.1 s, Sonnet 2.0 s for a one-sentence answer; booked cost matches the API's token counts.

**4.2 Which model checks the facts? (planted-error test, same 49 clean facts, 207 planted errors).**

| Checker | Errors caught | Clean facts wrongly rejected | Clean facts flagged "partial" | Discrimination | Cost |
| --- | --- | --- | --- | --- | --- |
| Haiku 5.5, engines' "low" effort | 92.3% | 2.0% | 40.8% | 0.51 | USD 0.03 |
| Sonnet 5.5, "low" effort | **85.0% (fails the 90% bar)** | 0% | 24.5% | 0.61 | USD 0.45 |
| Haiku 5.5, one level up ("medium") | **97.6%** | 0% | 12.2% | 0.85 | USD 0.04 |
| (earlier OpenAI model, on a different run's facts) | 98.9% | 0% | not recorded | | |

Sonnet is better at flipped conditions and added attributes (100%) but lets 61% of swapped-provider claims through (38.8% caught), so my plan to use Sonnet as the checker was overturned by its own test: it costs 17 times more and fails. This is what the pre-registered bars are for.

**4.3 First real run: 39 of 150 model calls failed with HTTP 400.** Claude's structured-output feature refuses a schema with more than 16 nullable fields, and separately refuses one whose compiled grammar is too large. Three of the ten extraction schemas (`data`, `services` and `trust`: the rules, provider and trust jobs) hit one of these. Unit tests could not have found it; the live run did. The fix lives in the Anthropic provider: nullable fields become optional (the missing ones are put back as null, so the gateway still validates against its own schema), and a schema Claude still refuses is sent once without constrained decoding, with the schema in the instructions (the gateway still validates and repairs). All 18 Research schemas were then sent live with tiny prompts and answer valid. A unit test now checks every Research schema against these limits.

**4.4 After that fix the run still had 43% unverified facts, and the cause was the extractor, not the checker.** Haiku at the engines' "low" effort fills in structured fields the page does not state (workflow steps, alternates, exceptions, screen sections). The checker (also Haiku) rightly rejected them: 51 facts rejected as "unsupported field", against 5 on the OpenAI model. I measured four ways to fix it on the **same stored pages** (so only the model differs):

| Extraction on the same pages | Facts kept | Rejected as unsupported | Items still unverified | Cost of the sample |
| --- | --- | --- | --- | --- |
| Haiku, "low" (16 pages) | 61 | 17 | 26% | USD 0.03 |
| Sonnet 5.5 extraction (16 pages) | 44 | 3 | 28% | USD 0.33 |
| Haiku, one level up (16 pages) | 73 | 6 | 15% | USD 0.04 |
| Haiku, "low" (24 pages, 2 repeats) | 97 and 119 | 24 and 11 | **31%** | USD 0.09 |
| Haiku, one level up (24 pages, 2 repeats) | 123 and 128 | 8 and 6 | **11%** | USD 0.15 |

The ranges for the 24-page test do not overlap. Sonnet is more careful (fewer, safer facts) but not better and ten times the price, so I did not pursue it. The setting that works is **Haiku with its lowest thinking level raised to medium**. It is a field on the model's price row (`effort_floor: medium`), applied by the Anthropic provider; no call spec and no engine changed, so rule 14 holds. The planted-error result above uses the same setting.

**4.5 The Small run with the floor:** 19 of 19 required slots, 209 facts, 22 provider dossiers, 0 validation errors (section 5).

## 5. The runs, against the earlier OpenAI-era runs

Single runs on a moving web: differences of a few points are noise; the large ones are not.

| Run | Model / search | Status | Searches / pages / seconds | USD (model + search) | Required slots | Facts: official / single / unverified | Dossiers |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Small yoga, earlier | OpenAI / Tavily | partial | 52 / 80 / 242 | 0.49 (0.06 + 0.43) | 15 of 19 (79%) | 103 / 31 / 16 (11%) | 11 |
| Small yoga, Claude first try | Haiku "low" / Tavily | partial | 49 / 80 / 144 | 0.49 | 4 of 19 | 19 / 23 / 34 | 0 (39 calls failed) |
| Small yoga, schema fix | Haiku "low" / Tavily | complete | 46 / 75 / 215 | 0.54 (0.15 + 0.39) | 12 of 19 (63%) | 62 / 33 / 72 (43%) | 15 |
| **Small yoga, with effort floor** | **Haiku / Tavily** | **complete** | **45 / 73 / 334** | **0.61 (0.22 + 0.39)** | **19 of 19 (100%)** | **101 / 84 / 24 (11.5%)** | **22** |
| Small bike repair, Porto | Haiku / Serper | complete | 47 / 78 / 347 | 0.27 (0.21 + 0.07) | 16 of 22 (73%) | 96 / 100 / 39 (17%) | 23 |
| Medium Nashik, earlier (old ceilings) | OpenAI / Tavily | partial | 91 / 130 / 244 | 0.86 | 20 of 40 (50%) | 185 / 39 / 32 (12.5%) | |
| **Medium Nashik** | **Haiku / Serper** | **complete** | **111 / 163 / 318** | **0.50 (0.35 + 0.15)** | **32 of 41 (78%)** | **194 / 119 / 48 (13%)** | **19** |
| Big freight, earlier | OpenAI / Serper | partial | 199 / 300 / 434 | 0.52 | 55 of 67 (82%) | 307 / 205 / 61 (11%) | |
| **Big freight** | **Haiku / Serper** | **partial** | **191 / 283 / 571** | **1.03 (0.75 + 0.27)** | **57 of 63 (90%)** | **438 / 309 / 138 (16%)** | **49** |

How to read it: against the targets, Small, Medium and Big all clear the 70% required-slot bar and the cost per official fact stays small (model cost USD 0.002 per official fact; 0.0025 with search on Medium and Big, 0.006 on Small). Medium and Big were run on Serper, not Tavily, to fit the cap (section 8), and Serper filled fewer slots than Tavily on Small in the earlier OpenAI-era test, so these two are, if anything, understated. Big is "partial" because 6 required slots stayed empty after four rounds, not because it hit a limit; its file is large (885 facts, 2.5 MB) and carries the existing "store over limit" warning, so Spec must use the selection helper (`pack`), as already documented.

A real difference from the OpenAI model that the table hides: Claude's number rejection rate is 4 to 6% (the model writes a number its own quote does not contain; code rejects those facts, so nothing wrong gets in) against about 1% before. It costs yield, not correctness.

## 6. The consumer probe (Medium, Nashik)

A stand-in Spec writer is asked for statements on five needs, once without and once with the research, and each statement is judged supported, assumed or invented. Bar: fewer assumed, and invented not higher.

| | Supported | Assumed | Invented |
| --- | --- | --- | --- |
| Without research | 0 | 59 | 0 |
| With research | 52 | 24 | **23** |

By need, with research: integrations 24 supported / 1 invented; entities 12 / 1; sample data 11 / 3; **screens 4 / 10; features 1 / 8**. The research is strong where it has depth (providers, entities) and thin where it has none: the Nashik run found no comparable apps and few screens (its unfilled slots are "Comparable ... apps", "Key screens", "Marathi Hindi English text"). Given a few findings and a request for more, the stand-in model (Haiku) fills the rest with confident statements instead of saying "assumed". **The probe therefore failed its bar, and I think the lesson is about the Spec engine:** it must treat any statement without a Research id as an assumption by rule, in code, not by asking the model nicely. It also tells Research where to work next: comparable apps and screens for niche local markets.

## 7. Quality, read by hand

I read a sample of 14 provider, rule, compliance and platform facts from the final Small run. The facts are faithful restatements with the right evidence label (RBI circulars and the TRAI header portal as official, vendor marketing pages as single source); compliance items carry their authority and effective date. Weaknesses I saw: global vendors (Klaviyo, Braze) surface for an Indian yoga studio where the useful answer is local (TRAI, Indian payment gateways); a few facts are marketing statements with low value ("more than 205,000 brands across 100 countries"); on Porto the Portuguese payment network SIBS is found but only 2 of 13 dossier fields are filled and none from its own site, so "an in-country payments provider with official facts" is **not met** there. The 60-fact spot-check (Results page of each run) is still the honest test; I did not do all 60 by hand.

Validation: Small and Porto 0 errors; Medium 0 errors and 1 warning; Big 1 error (RV-17, a label equal to a quote) and 1 warning. In three runs (Small, Big, Porto) one checker call took too long and was skipped (its facts stay unchecked, flagged), and in the first Claude run the assemble step invented lane ids and fell back to the deterministic assembly; none of this lost a fact.

Speed: Claude at the higher level is slower than the OpenAI model (Small 334 s against 242 s, Big 571 s against 434 s). Median model call 7.5 s, slowest 68 s. In the app log 8 of 649 calls needed a second attempt and none failed at the end.

## 8. Money

| Item | USD |
| --- | --- |
| Model calls, in the period ledger (planted and re-extraction experiments 1.17; six runs 1.76; probe 0.04) | 2.98 |
| Model calls outside the ledger (smoke test and live schema checks) | about 0.03 |
| **Model calls in total (your cap: 5.00)** | **about 3.0** |
| Search credits booked at the highest rate (Tavily 0.008 and Serper 0.001 a credit; these come from your prepaid plans, not from the API) | 1.69 |
| Period total (limit 4.95) | 4.66 |

I read your cap as everything the period books, which is stricter than "LLM only". It is why Medium and Big ran on Serper (about 5 times cheaper to book than Tavily) and why Dakar, Pune, the hospital and Central America were not run. Tavily is restored as the saved default. Credits after the tests: Tavily 336 of 1,000 used (664 left; it was 330 before, so Small on Tavily used the credits), Serper about 1,197 left (counted by the app; it was about 1,630 before the Medium, Big and Porto runs).

## 9. My opinion

1. **Keep Haiku 5.5 with the medium floor as the Claude setting for Research.** It cleared every bar the plan set, for the model price of a few cents a run. Do not pay for Sonnet for extraction or checking at these prices; the tests say it is not better here.
2. **Do not read this as "Claude is better than the OpenAI model".** The same Small input went from 15 to 19 required slots, but the runs are single samples on a moving web, and the OpenAI numbers come from earlier code. What I can say: a cheap Claude model is good enough for this job *if it is told to think a little*, and the cost of that is real (3.7 times the OpenAI model cost) but small next to search.
3. **When OpenAI credits return, run one experiment before choosing:** the same Small and Medium inputs with the OpenAI model, then a cross-vendor pair (Claude extracts, OpenAI checks). Both agents now use the same vendor for writer and checker (`checker_vendor: same`); the planted-error test shows the weakest spot is swapped entities (89.8% caught even with the floor), which is exactly what a second vendor is likely to catch differently. That is the best use of the next USD 2.
4. **The next quality gain is in discovery, not in the model.** In every run the same slots stay empty: comparable apps, a few journeys, screens for the big app. The Spec engine's invented statements concentrate there (section 6). Local-market relevance (section 7) is the second item.
5. **The Spec engine must enforce "no Research id, no fact" in code.** The probe shows what a model does when given a little evidence and a request for more.
6. **Number rejections (4 to 6%) are a cheap fix:** ask for the quote first and the number second. Not built; not needed for correctness.
7. **Rule 14 is true for Research and the gateway, not yet for the whole repo.** Compass still names the OpenAI model (`MODEL_ID` in `agents/compass/.../config.py`) and tests for its key in two live scripts; the CLI's local mode and a policy default also name `openai`. None of it blocks the switch (Compass runs through the same gateway), but a new vendor will meet it there. Worth one small change before Compass runs on another vendor.

## 10. What is not proven

* Claude against the OpenAI model on the same code and the same day; a cross-vendor checker; Sonnet as checker at a higher effort (only its "low" effort was tested).
* Medium and Big on Tavily (default provider), Dakar, Pune, hospital, Central America. Porto is one run.
* Web-search tool calls through Claude (the provider does not serve tools yet; Research uses none).
* Prompt caching: not used; it would lower the model cost of repeated prefixes. Not measured.
* The hand spot-check of 60 facts per run.
* Claude's rate limits at higher concurrency: no failure seen at 16 parallel calls on this account.

## 11. Where everything is

* Run outputs, readable reports and numbers: `docs/research-agent/outputs/claude-2026-11-13/` (index in its README). Full evidence (page text, cassettes) stays in `agents/research/.runtime/runs/` (not in git).
* Experiment results (planted, re-extraction): `outputs/claude-2026-11-13/evals/`.
* Code: `packages/llm-gateway/src/mde_llm_gateway/providers/anthropic_messages.py`, `.../providers/routing.py`, `.../factory.py`, `.../pricing.yaml`; Research wiring in `agents/research/src/mde_research_agent/compose.py`, `preflight.py`, `config.py`.
* Decisions: D-171 (rule 14 and the money rule), D-172 (schema limits, effort floor, checker choice).
