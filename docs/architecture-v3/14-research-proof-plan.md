# 14. Research proof plan: how we show it works before we approve it

- **Status:** v1, written 2026-11-11 with Research design v5 ([doc 13](13-research-agent-design.md)). Decision **AD-59**. Linked from doc 13 §14.
- **Owner's rule (2026-11-11):** "We do not approve anything unless we show that it will work." The builder's offline build proves the plumbing. **It proves nothing about the real web.** This document says what we run, what number counts as passing, and what we change when a number fails. A decision is **Proven** only after its gate passes; until then it is **Built** or **Proposed** (doc 13 header).
- **Who runs what:** the owner types the live commands (the builder's scripts refuse to start without the owner's switches) and does the spot-check; the builder runs the offline harness; the planning chat reads the results, applies the decision table (§9) and updates the decision statuses (§12).
- Numbers marked **[J]** are architect judgment and may be retuned with evidence; they are written down first so that a result cannot move the goalposts afterwards.

## 1. What "works" means: five questions
| Question | In plain words | How it is measured |
|---|---|---|
| **True?** | No hallucinated or wrong fact reaches Spec | quote and number checks in code; the checker; the planted-error test (§5); the owner's spot-check (§4) |
| **Enough?** | Detailed enough that Spec does not have to guess in the areas Research covers | required slots filled; lane quality; builder-need coverage; appendix share; the owner reads the report |
| **Useful?** | Spec does better with the research than without it | the consumer probe (§6) now, the real Spec later |
| **Affordable and fast?** | Cost and minutes stay inside the ceilings | cost and stage timings per run |
| **Safe?** | Pages cannot give it orders; failures degrade softly | injection tests, failure drills (§8) |

## 2. The gates
| Gate | What | Who and cost | Pass | If it fails |
|---|---|---|---|---|
| **PG-0** | Offline build (done): 1,136 research tests, 46 database tests, console checked in Chrome | builder | all green | proves plumbing only |
| **PG-1** | **Bake-off**, Tavily against Serper, about 40 searches built from the real Briefs | owner runs; about USD 0.40 | both providers return results; the primary's on-topic share and usable-page rate are not worse than the other's by more than 5 points; cost per query known | one unusable: single provider; both weak: stop and review the queries before spending more |
| **PG-2** | **Quick live run**, recorded | owner runs; about USD 0.28 | no crash; extraction quote pass 90% or more; fetch success 70% or more; 60% or more of searches used; 3 minutes (plus 30 s); cost under 0.45; events complete; the owner reads the report for 10 minutes and finds nothing obviously wrong or generic | decision table §9; do not run the larger runs until fixed |
| **PG-3** | **Standard and Thorough live runs**, recorded (the synthetic Brief only if money is left); plus the **planted-error test** (§5) on their output | owner runs; about USD 2.2 (stages B and C, §10) | the builder's measures (doc 13 §14: slots 70%, searches used 60%, norms with 3 sources 70%, recent items 70%); number rejections under 3%; checker `not_supported` under 10%; planted-error catch 90% or more; soft time met; cost within 1.5 times the estimate; no validator error; appendix and drop counters reported | decision table |
| **PG-4** | **Owner spot-check**, 60 items (§4) | owner, about 25 minutes | 95% or more supported by the quote; 90% or more true, current and relevant; **no fabricated fact** | the failing job or kind loses Default eligibility (R21) until fixed; fix; redo with 30 new items |
| **PG-5** | **Consumer probe**, with against without research (§6) | builder runs; about USD 0.5 | fewer "assumed" items on researched needs; invented facts not higher | the job or lane with no lift is cut or made optional |
| **PG-6** | **Full store against pack** (§7), only to calibrate the 40k threshold | builder runs; small | the pack scores within 5% of the full store | raise the routing threshold, or keep packs only above it |
| **PG-7** | **Failure drills**, offline (§8) | builder | every drill ends as specified | fix and re-run |

## 3. Metric definitions (corrected)
The built `metrics.py` has two measures that cannot fail (§20 of doc 13). These are the definitions used here.
| Metric | Definition | Why |
|---|---|---|
| **Extraction quote pass rate** | quotes found on the first try, over all extractions; also reported after the one retry | The built "quote check" re-checks atoms that already passed, so it is always about 100% |
| **Number-check rejection rate** | extractions rejected `number_not_in_quote`, over all extractions | Measures invented or drifting numbers |
| **Fetch success** | selected pages that returned text, over selected pages, by failure reason | Shows whether JavaScript pages and walls are a problem |
| **Searches contributing** | searches whose results produced a kept item, over all searches | Measures the worthiness gate and query quality |
| **Required slots filled** | filled over required, per job and in total | Design and screens are reported separately because their yield is expected to be lower |
| **Appendix share** | items with `store: appendix`, over all items | If above 30% at standard or 60% at deep, compact the view further before raising the limit |
| **Lane quality** | the `lane_quality` value per lane | Tells Spec how far to trust each lane |
| **Depth meter** | per job: slots filled over required, items by kind, items with typed data; per lane: the count against the expectation for the class (doc 13 §9, prompt 06 section 1a D5) | Shows at a glance whether the result is detailed enough; far below expectation means look at extraction, reading and queries first |
| **Checker rates** | share of `supported`, `partial`, `not_supported`, `unchecked` | A rate near 0% rejections with a poor planted-error score means the checker rubber-stamps |
| **Stage timings and cost** | seconds and USD per stage (plan, each round, check, assembly) and per call kind | Decides where latency and cost work is worth doing |
| **Merge rate** | atoms over items, norm keys over norms | Shows over-merging or under-merging of the pure-code norm keys |

## 4. The owner's spot-check (PG-4)
- **Why you:** a model checking a model of the same vendor is a weak test. You judge whether a fact is true, current and relevant for your kind of app. About 25 minutes.
- **The sample:** 60 items, 20 from each of the Quick, Standard and Thorough runs, drawn by a fixed seed so the draw can be repeated. Per run: 8 high-impact items (fees, limits, rules, formats), 6 service or integration facts, 3 items with typed data (entity fields, screen sections), 3 ordinary features, flows or norms. The sheet comes from `GET /research/runs/{id}/spot-check` (doc 13 §10); until it exists the builder draws it with a script from `research.json` and the evidence. **In the standalone app (doc 13 §10.1) the spot-check runs inside the front end:** one item per card, six verdict buttons, saved automatically, a summary against these thresholds and an export.
- **What you see per item:** the claim, the typed data in short form, the best quote, the source domain, date and link, the `evidence` value.
- **Your verdict, one code per item:** `ok` (supported, true, current, relevant); `not relevant` (supported but useless for this app); `claim not in quote`; `wrong or outdated`; `wrong place or company`; `invented detail` (a field, type, number or step the quote does not state).
- **Pass:** supported by the quote means `ok` plus `not relevant`, at 95% or more; `ok` alone at 90% or more; **no `invented detail` on a high-impact item and fewer than 3 overall**. A single fabricated fact is a blocker: the grounding rules (RC-3) are not working.

## 5. The planted-error test (inside PG-3)
**Question:** do the code checks and the checker catch errors we plant? It uses recorded output and costs a few cents.
- **Sample:** 40 clean verified items (stratified like §4). For each, code produces corrupted variants of five kinds: a **number changed**; an **entity swapped** for another provider or company; a **condition flipped** (a "not", "only if" or "excl. tax" added or removed); a **region swapped** (currency, country or authority); an **unsupported attribute added** (a type, required flag or enumeration the quote does not state). That is 200 corrupted items and 40 clean ones, in a fixed seed.
- **Measured separately:** what the **code rules** catch (RV-18 numbers, RV-20 fit, field support) and what the **checker** catches on what code let through.
- **Pass:** 90% or more of all planted errors are caught overall and 80% or more per kind; no more than 5% of the clean items are wrongly rejected. If the checker's catch rate is under 90%, a second vendor is required (OI-23).
- **Honest limit:** planted errors are easier than natural ones. A pass shows the checker is not blind; the spot-check shows whether natural errors get through.

## 6. The consumer probe (PG-5)
**Question:** does Spec do better with the research than without it? Spec does not exist yet, so the probe uses Luna as a stand-in for a Spec call, on recorded output, with no web calls.
- **Tasks (data files, five):** first-release feature list with reasons; main entities with fields, statuses and business rules with numbers; integration options per need with auth, fees and limits; the sections and states of the five key screens; realistic sample data and formats. Each answer must cite finding ids or write `ASSUMED`.
- **Arms:** A, the Brief only; B, the Brief plus the research (the whole compact store when it fits the threshold, else a pack). For a deep run, B is run both ways (PG-6).
- **Scoring:** a judge call sees each statement, its cited findings and their quotes. Counts: statements **supported by a cited finding**; statements marked `ASSUMED`; statements presented as fact with no support (**invented**); and the builder-need coverage (BN-02, 03, 04, 05, 07, 08, 09, 10) reached as `researched`, `defaulted` or `gap`. A human reads 10 of the "invented" verdicts to check the judge.
- **Pass:** B has more supported statements and fewer `ASSUMED` on needs the research covers, and **invented is not higher than A**. A lane with no lift is cut or made optional.
- **Cost:** about USD 0.5 on Standard; a deep run with the whole store may cost about USD 1. A batch API is used if the gateway offers one.
- **Limit:** the same vendor writes, checks and judges, so the judge is human-sampled; and Luna as Spec is a stand-in. The real test is the Spec build.

## 7. Full store against pack (PG-6)
Run the probe on the Thorough research twice: the whole main store as the prefix, and a pack per task (the records that apply to the task's Brief ids, one hop, a title index). If the pack scores within 5% of the whole store, packs are safe above the threshold. If the whole store is clearly better at 60k or 80k tokens, raise the threshold and the `store_tokens` limit together (OI-22). If the store is far below 40k, this gate is skipped.

## 8. Failure drills (PG-7, offline)
| Drill | Expected |
|---|---|
| The primary provider returns errors for a topic | failover to the other provider for that topic; event `warning`; run continues |
| Both providers fail | the topic is `provider_down`; run `partial`; no exception reaches the caller |
| A page is JavaScript-only, a scanned PDF, or a cookie wall | `page.skipped` with a reason; no high-impact claim from a snippet |
| A page carries injected instructions | ignored and counted; no tool, URL or action field in any output |
| The checker rejects the only item of a required slot | slot reopens; a follow-up query runs |
| The checker call fails | items stay `unverified`, flagged `unchecked`; nothing silently trusted |
| The store exceeds `store_tokens` | lowest-ranked findings move to the appendix with a reason; counts conserved; nothing deleted |
| The time limit is hit while checks run | checks and assembly stop with the run; status `partial` with a `budget_stop` warning |
| A run is killed mid-way | after the stale period it is marked `failed: stalled` with a terminal event and the program can start a new run |
| A tiny app with nothing worth searching | light mode: about 8 searches at most, `complete`, skipped topics are gaps |
| The same Brief with a different tier value | identical result (the tier is never an input) |

## 9. Decision table: what we change when a number fails
| Signal | Threshold [J] | Action |
|---|---|---|
| Extraction quote pass rate (first try) | below 90% | switch extraction to **citation by sentence id** (the model names the sentence, code copies it); re-measure |
| Fetch success on selected pages | below 70% | use the provider's page text first; add Tavily extract or a rendered fetch for the key official domains; re-measure |
| Searches contributing | below 60% | cut variants from 3 to 2; tighten the worthiness gate; re-measure |
| Required slots filled | below 70% overall | find the failing jobs; lower the minimum of slots that are chronically `not_found`; **never pad** |
| Yield of the design or screens job | under 30% of the core jobs' yield | make the job optional for the focused class; keep platform guidelines only |
| Norms with 3 or more sources | below 70% | check norm-key merging (under-merging) and competitor discovery; read more competitor pages per topic |
| Number-check rejections | above 3% of extractions | check number normalization first (currency, lakh, crore, ranges), then the prompt |
| Checker `not_supported` | above 10% | the passage windows or the extraction prompt are wrong; read 20 rejections |
| Checker catch rate on planted errors | below 90% | second vendor (OI-23) or a stricter prompt plus a code rule |
| Checker rejects almost nothing and the catch rate is low | under 1% rejections | the checker rubber-stamps: second vendor |
| Spot-check supported | below 95% | find the failing job or kind; remove its Default eligibility (R21); fix; redo with 30 new items |
| Spot-check, any fabricated high-impact item | one | blocker: tighten RC-3 (grounding); redo the spot-check |
| Appendix share | above 30% (standard) or 60% (deep) | compact the view further; consider raising `store_tokens` after PG-6 |
| Deep p50 time | above 8 min | more lanes up to the gateway limits, then **progressive release** |
| Cost per run | above 1.5 times the estimate | cut variants and pages; check the checker's share; fewer follow-ups |
| Consumer probe lift | assumed not lower, or invented higher | cut or make optional the job or lane that shows no lift; record it in doc 13 |
| Pack against whole store | pack worse by more than 5% | raise the threshold; keep packs only above it |
| Light mode | a tiny app uses more than 3 searches or 90 s | tighten the rule |
| Merge rate | items far fewer than atoms for unrelated things | norm keys over-merge: tighten the Jaccard threshold |

## 10. Live protocol (order, commands, budget)
0. **Builder (prompt 06, complete version):** the budget restart and spend record, the live-only standalone app with its own front end, the engine changes; it leaves the app running and tells you the address.
1. **Stage A (owner, about USD 0.7), from the app:** Tools, provider bake-off (about USD 0.40), then run the **small** input. The CLI scripts still work: 
   - `! LLM_MODE=live ALLOW_LIVE_LLM=1 LLM_BUDGET_USD=<cap the builder reports for USD 5.00> uv run python -m mde_research_agent.live_bakeoff --write-default`
   - `! LLM_MODE=live ALLOW_LIVE_LLM=1 LLM_BUDGET_USD=<cap the builder reports for USD 5.00> uv run python -m mde_research_agent.live_runs --run quick --record`
2. **Stage B (owner, from the app, about USD 0.7):** run the **medium** input.
3. **Stage C (owner, from the app, about USD 1.4):** run the **big** input, only after the small and medium runs passed PG-2. The synthetic Brief only if money is left at the end.
4. **Stage D:** the planted-error test and the consumer probe (the builder's harness, on the recorded runs) and **your spot-check inside the app**.
5. **Planning chat:** read the one-page metrics tables and the reports, apply §9, write what is Proven and what is cut, update §12.
**Send me:** the one-page metrics table of each run, the `research_report.md` files or their paths, the spot-check sheet with your verdicts, and anything that looked wrong.
**Budget (owner, 2026-11-11): USD 5.00 in total for this proof work. It can be restarted, every cent is recorded, and it is never exceeded.**
- **Restart** means the counter for this work goes back to 0.00 and the earlier USD 2.37 is archived, not deleted. The builder does this first (prompt 06, RC-0) and reports the exact settings.
- **Record:** every live command appends a line to a spend record (date, what ran, dollars by step: search, extraction, check, assembly, probe, and the running total since the restart) and prints the running total.
- **Limits (owner: "there is no limit of it"):** the only money limit is the total of 5.00: a tripwire at 4.50 (finish the run in progress and start nothing new) and a hard stop at 5.00. **No per-run cap and no call-count cap.** The loop safety ceilings of doc 13 §7.1 stay as runaway guards, not as limits on quality. The repo guard keeps its own margin (about an 80% tripwire and a 95% effective cap in the builder's numbers), so the builder sets it so that 5.00 is the real ceiling. Research spend is counted on its own (`rsch.` ids), so other work does not eat it; tell me if another session spends on the same ledger.

| Stage | What | Estimate (USD) | Allowance (USD) |
|---|---|---|---|
| A | Bake-off and the recorded Quick run | 0.40 + 0.28 = 0.68 | 0.85 |
| B | Standard run after the P0 fixes, check-all included | 0.57 + 0.15 = 0.72 | 0.90 |
| C | Thorough run, check-all included | 1.15 + 0.30 = 1.45 | 1.70 |
| D | Planted-error test and the consumer probe on Standard | 0.05 + 0.45 = 0.50 | 0.60 |
| Reserve | One re-run of a failed stage | | 0.60 |
| **Total** | | **3.35** | **4.65** (0.35 unallocated) |

The estimates are the builder's dry-run numbers plus my check-all estimate; **they are unmeasured until stage A.**
- **Rules:** after each stage compare actual with estimate. Within 1.3 times: go on. Above 1.3 times: stop and re-plan with me before the next stage. A stage that reaches its allowance finishes the run in progress and stops.
- **Order:** cheap first; Thorough only after Quick and Standard pass; the synthetic Brief only if money is left at the end.
- **If money runs short, cut in this order:** the synthetic run, the consumer probe on a deep run, then fewer variants and pages in Thorough. **Never cut the planted-error test or the spot-check:** they cost almost nothing and are the real proof.
- **Savings built in:** every run is recorded, so fixes after the search step replay the web and cost only model calls (RC-7); checks run inside topics; at most one re-run per stage.
**Cassettes:** the app records every run (searches, pages and model calls) automatically; there is no replay in the app, only in the developer scripts. For changes after the model calls that read pages (extract, check, assemble), the builder replays the recorded web and runs those calls live (RC-7: `--replay-web --live-on-miss`). A change to the plan or the query writer changes the queries, so those runs need new searches (small).

## 11. What these gates do not prove
- **Quality on kinds of app we did not test.** Three real Briefs and one synthetic one are a tiny sample; passing means no gross failure, not that every domain works.
- **The end result in Spec.** The probe is a stand-in. Doc 13 §14 keeps "items used or consciously rejected downstream" for when Spec exists.
- **Regional-language quality.** Not a focus (owner); not tested beyond quote checks in other scripts.
- **Behavior under load** (several runs at once, rate limits), long-term freshness of the facts, and legal correctness of anything on the `confirm` list.
- **Commercial terms** of the search providers at production volume.

## 12. Status of the decisions
| Decision | Status | Gate that moves it to Proven |
|---|---|---|
| AD-44 Compass view as the input contract | Built | PG-2, PG-3 |
| AD-45 Foreground run, visible | Built (console checked in Chrome on fakes) | PG-2 |
| AD-46 Worthiness gate, entity-anchored queries | Built | PG-2, PG-3 (searches contributing) |
| AD-47 Typed output | Built; v5 additions Proposed | PG-3 |
| AD-48, AD-52 Providers behind ports; source policy | Built; primary and failover Proposed | PG-1, PG-7 |
| AD-49 Depth from content (load) | Built; class margin Proposed | PG-2, PG-3 |
| AD-50 Slot-driven loop, evidence-aware stopping | Built | PG-3 |
| AD-51 Output for Spec, live events | Built; additions Proposed | PG-3 |
| AD-53 Jobs organized by Spec decisions | Built; design and screens reshaped Proposed | PG-3, PG-5 |
| AD-54 Baseline privacy; trust job | Built; amended (trust job Proposed) | PG-3 |
| AD-55 Three caps, nothing dropped silently | Cap 1 Built; caps 2 and 3 Proposed | PG-3, PG-5, PG-6 |
| AD-56 Truth layer | Proposed | PG-3 (planted errors), PG-4 |
| AD-57 Category adaptation | Proposed | PG-2, PG-3 |
| AD-58 Speed and cost | Parallel and cache Built as is; the rest Proposed | PG-3 (timings), PG-7 |
| AD-59 This proof plan | Written | the gates themselves |
