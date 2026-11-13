# 16. Spec proof plan: how we show it works before we approve it

- **Status:** v1, written 2026-11-13 with the Spec design ([doc 15](15-spec-agent-design.md)). Decisions **AD-75**, and **AD-77 to AD-82** for PS-10 ([doc 17](17-spec-content-model.md)). Same rule as Research (doc 14): **we do not approve anything unless we show that it will work.** A decision is **Proven** only after its gate passes; until then it is **Proposed** (or **Built** once the builder has implemented it).
- **Amended 2026-11-14 by [doc 18](18-spec-final-architecture.md):** PS-0 adds the free-tool smoke tests, the offline compile check of every fused schema against each profile's limits and the recorded writer limits (output limit, speed, nullable limit); PS-5 measures the first-try pass **per call kind, on nodes** (the 85% un-fuse rule) and reports the `weak_route` banner (15% of nodes); PS-6 (the consumer probe, the canary's method) runs in the proof and on sampled real runs, never in the delivery path; **PS-7 is now the planted-defect test of the self-check: the 22 structural defects of §5 plus 8 meaning defects, reported separately and with and without a proof-only critic arm**; PS-9 bypasses the result cache (its key carries the route fingerprint) and adds the 36 `SPC-FN` cases (**184 in all**); PS-10 adds the owner's comparison of code-made and model-written mock lists and assets on 5 previews and the palette and token quality (the owner reads 3 palettes); free LLM tiers only in a separate proof route map, with the four synthetic inputs until the owner confirms the nine exports hold no personal data (OI-43); `critic_vendor` applies only to the proof-only critic arm.
- **Amended later on 2026-11-14 by [doc 19](19-spec-big-apps-bench-and-result-first.md):** two gates are added, **PS-11 (the Spec Bench)** and **PS-12 (big apps: a synthetic 60-screen app and the real Thorough export with Verify, then the Uber Eats-class program if the budget allows)**; PS-7 also measures the Verify agent for deep; the suite is **200 cases** (SPC-BG-01 to BG-16 added); the budget stays a hard USD 5.00 cap with the allocations and drop order of doc 19 §5.
- **Who runs what:** the builder (the other Claude Code) runs the offline harness and the live steps inside the budget below; **the owner reads samples** (the G1.5 view of the Thorough run, five screen cards, ten consumer-probe outputs); the planning chat reads the results, applies the decision table (§7) and updates the decision statuses (§10).
- **Starting values, not limits:** the USD 5.00 proof budget and the 60-screen scope cap are the values we start with "for now" (owner, 2026-11-13). The budget is separate from Research's, restartable and recorded in its own spend record. Numbers marked **[J]** are architect judgment, written down first so a result cannot move the goalposts.
- **Models:** the proof runs on the **temporary Haiku-class route** through the gateway, as the weakest configured profile; the provider-switch test (PS-9) proves it is not the only one that works. The live-mode rule is unchanged: one switch, `LLM_MODE=live`.
- **Zero-model gates first:** PS-0 to PS-2 need no model and no money.

## 1. What "works" means: six questions
| Question | In plain words | How it is measured |
|---|---|---|
| **Sufficient?** | A weak consumer can build a real screen from the pack without asking | the consumer probe (PS-6), the developer test |
| **Free?** | The pack does not bind the front end | freedom schema and lint, the freedom probe (PS-4), the owner reads five cards |
| **Fits?** | Every file and task fits its budget; big apps work through slices | the packager gates on real and synthetic inputs (PS-2) |
| **Correct?** | Every node valid, traceable and honest; no invented Locked fact; research rules obeyed | validators V-01 to V-44, coverage matrix, trust grades (PS-3, PS-5, PS-7) |
| **Robust?** | Bad inputs, a failing route, a changed model, a crash do not break it | the edge-case suite and the provider-switch test (PS-9) |
| **Scales?** | Cost, time and rate limits behave as the model predicts | measured cost and latency, concurrency, queue, resume (PS-8) |
| **Worth it?** | Each content bundle of doc 17 earns its place, and none binds the front end | the ablation arms, the library-lookup probe, diversity and consistency (PS-10) |

## 2. The gates
| Gate | What | Who and cost | Pass | If it fails |
|---|---|---|---|---|
| **PS-0** | **Offline build** of S0 and S1: shared packages, readers, libraries, derivation, packager, slicer, build plan | builder; no model | all unit tests green; import-linter green; the budget set is self-consistent (a one-screen task fits); every call schema compiles against every configured profile's limits (nullable count, depth, enum size); the nine exports and the synthetic graphs process end to end | fix; nothing else runs |
| **PS-1** | **Intake on the nine real exports** plus drift tests | builder; no model | all nine load into `SpecInput` (snapshot tests); an unknown version and renamed keys load with drift flags (SPC-IN-03); a Brief with no idea, no role or no feature fails readiness with a plain list; the readers of Compass in Research and in Spec agree on every field they share | fix the reader and its fixtures |
| **PS-2** | **Packager and budgets**, zero cost, on the three real exports (their structure taken as the proposal) **and** four synthetic realistic-density inputs: small (about 16 screens), medium (about 30), big (60 screens in one app, entities of 8 to 15 fields, checkout-class screens binding five operations), and the Uber Eats-class program of doc 15 §14.4 | builder; no model | **no budget breach after automatic re-sharding** (V-26); closure complete (V-27); the prefix bundle byte-identical across all tasks; two runs and two machine locales give identical bytes and hash (V-34); re-packaging for a second consumer profile changes only the sharding and the task cards, never the content (a screen that a heavier tokenizer pushes over its cap is reported, never hidden); the G2 content hash is identical across consumer profiles; the report lists tokens by file kind and by format | decision table; the first fix is the format or the shard rule, not the budget |
| **PS-3** | **Derivation and phases**, first model spend (the Product lane), on the nine inputs | builder; about USD 0.4 | no entity, role or flow without a screen (V-29); the pull-in rule holds on Thorough (the dispatch journey is in Phase 1); no Phase 1 node depends on Phase 2 (V-28); counts inside the class range or flagged; the report lists Compass's `core` against Spec's Phase 1; **the owner reads the Thorough G1.5 view for 10 minutes and says whether Phase 1 is a working product**; a user-stated `later` is never relabelled (SPC-IN-21), and `later` features keep outlines in the ledger with no screen nodes (V-19) | decision table; pull-in rule gaps become journey-completeness cases |
| **PS-4** | **Freedom** (schema, lint, probe) on Quick and Standard cards | builder runs; owner reads 5 cards; about USD 0.3 | schema validity 100%; no tier above its ceiling; lint warnings at most 3 per card [J] and every warning explained; the **freedom probe**: the same card built twice by the weakest consumer with different style seeds gives different region arrangements in at least 30% of screens [J]; **the owner finds no layout instruction in the 5 cards** | remove the field that dictated; tighten the schema; never add a "be creative" instruction |
| **PS-5** | **Writing quality per step** (S2 to S4 live): Quick three times, then Standard three times | builder; about USD 0.8 | first-try validator pass at least 85% [J]; repair rate at most 15%; `degraded` nodes at most 5%; **zero Locked nodes without evidence; zero research items below `official` or `corroborated` used as Default (R21); zero V-36 violations**; ungrounded optional fields at most 10% [J]; `claims_used` recorded for every research-derived value; copy keys resolve; bindings resolve (V-30) 100%; ranges across the three runs reported (a single run decides nothing) | decision table; first try a smaller call or a code rule, then a stronger route for that one step |
| **PS-6** | **Consumer probe** (§4) and the **developer test** on SCR-014 of the Standard export | builder; about USD 0.5; owner reads 10 outputs | blocking questions at most 0.3 per screen on average; assumptions at most 1 per screen; at least 90% of acceptance criteria judged satisfiable; the slice arm not worse than the whole-pack arm; the card-alone arm clearly worse (so the global layer matters); every gap found by the developer test becomes a required item (a `behaviors` category or a `rules` entry) | decision table: add the missing field class, never layout |
| **PS-7** | **Planted-defect test** of Verify (§5) | builder; about USD 0.2 | catch rate at least 90% overall and 80% per kind; false positives at most 5%; the repair loop ends inside 2 LLM rounds; `critic_vendor` reported honestly | stricter validators; a second vendor for the critic (OI-23 pattern) |
| **PS-8** | **Scale**: measured cost and latency per size; concurrency; queue; resume | builder; reuses PS-5 runs plus one synthetic Big run, about USD 0.5 to 1.0 | estimate within 1.5 times for cost and for time; under mock rate limits the concurrency adapts and **no run is lost**; two tenants share a queue fairly (neither waits more than twice the other for the same work); a run killed at a batch resumes and ends with the same bytes as an uninterrupted one; a second start for the same program joins or waits (one active run per program); the per-run report is complete | recalibrate the per-step token counts; lower per-call input before lowering quality |
| **PS-9** | **Edge-case suite** and **provider-switch test** (§6) | builder; mostly offline with mock routes; two live runs, about USD 0.6 | every fixture of doc 15 §12 and doc 17 §11 (148 cases) ends as its handling rule says; the same fixtures on two routes with different consumer profiles pass the same validators; switching the consumer re-packages at zero model cost with identical content; variance bands recorded | fix in the gateway adapter, never in Spec's prompts |
| **PS-10** | **The content model** (doc 17): the library-lookup probe on two real public catalogues, the ablation arms, between-app diversity and within-app consistency with COULD on and off, the pilot-first probe, mobile-fit hit rates, stories, blocks and mock coverage | builder; about USD 0.5; the owner reads 5 pilot screens and 10 probe outputs | recall at 3 at least 90% and wrong-gap rate at most 10% on held-out needs, and better than the card-only baseline; the planted gaps found; **each bundle earns its place in the ablation** (blocking questions down, or consistency or quality up); with COULD on, within-app consistency and between-app diversity not lower; the pilot-first arm more consistent than the arm without it; V-37 trips on at most about one real screen in ten before any threshold blocks; V-38 to V-44 pass on the nine exports and the synthetic inputs | cut or make optional the bundle that does not earn its place; enrich the needs (anatomy, gestures, roles), never name a component; lower the COULD cap |
After these: **one real Thorough run** (once, after Standard is healthy) and the owner's reading of its G2 summary.

## 3. Metric definitions (so numbers mean one thing)
| Metric | Definition |
|---|---|
| First-try validator pass | nodes that pass every blocking validator when first written, over nodes written |
| Repair rate | nodes that needed a repair call, over nodes written |
| Degraded share | nodes marked `degraded`, over nodes in the pack |
| Blocking question | an entry in the probe's `blocking_questions` array (the consumer says it cannot proceed without it); a sample of 20 is read by a person to find false positives |
| Assumption | an entry in the probe's `assumptions` array |
| Acceptance satisfiable | the probe's per-criterion `satisfiable` flag, with its `how` |
| Locked without evidence | a Locked node with no user, document or policy provenance; **must be 0** |
| Ungrounded field | an optional field that is filled with no provenance and no library source, over optional fields filled (the over-filling of D-172) |
| Lint warnings per card | form-vocabulary hits in free-text fields of one card |
| Estimate error | actual over estimated, for cost and for wall-clock, per step and in total |
| Recall at 3 | for held-out needs, the share whose correct component is among the consumer's top three matches in the library |
| Wrong-gap rate | needs declared a gap although the library has a fitting component |
| Between-app diversity | how different the directions and designs of different apps from the same generator are (a critic, sampled by the owner) |
| Within-app consistency | how alike the screens of one app are in design language (a critic, sampled by the owner) |
| Mobile-fit hit rate | real screens that trip a V-37 limit, over screens |
| Variance band | min and max over repeated runs of the same input, reported next to the median |
| Cost per Phase 1 screen | total run cost over Phase 1 screens written |
| Task read total | Spec tokens plus the front end's interface allowance, per task, against `task_read_budget` |

## 4. The consumer probe (PS-6)
- **Who:** the **weakest configured consumer profile** (the temporary Haiku-class route today; re-run on each new consumer).
- **Samples:** Quick three screens (a list or detail, a form, a checkout); Standard six (including checkout and a status screen); Thorough nine (including a cross-app step and a heavy screen).
- **Input per arm:** *A* (the design) the task card's reads; *B* the whole pack, where it fits the window; *C* the card alone, with no prefix and no global files.
- **Ask (no code):** return `{outline[], blocking_questions[], assumptions[], acceptance_coverage[{criterion, satisfiable, how}]}`. A second pass asks for the screen's code for one sampled screen per size, to check it can be built at all; the code is not graded for looks.
- **Judge:** the counts above, plus the owner reading ten outputs.
- **The freedom probe** (PS-4) reuses the harness: the same card, two style seeds.
- **The developer test** is PS-6 on the Standard export's SCR-014; its questions are recorded, and each gap becomes a required `behaviors` category (V-35) or `rules` entry, as doc 15 §14.2 did for "interrupted external flow" and "pending timeouts".

## 5. The planted-defect test (PS-7)

> **Amended (doc 18 §3.4):** this is now the planted-defect test **of the self-check** (code, no agent). Add **eight meaning defects** to the 22 below and report catch rates separately: a changed Locked number (4% to 5%); two contradictory rules; a user-stated feature missing from the inventory; a role that can see what the Brief forbids; a journey without its payment-failure path; a story that contradicts a rule; a Default that contradicts an official Research fact; an `assumed` item shown as Locked. A proof-only arm adds a critic call to the same packs, which shows exactly what the removed Verify agent would have caught.

Twenty-two defects are planted in a good pack, spread over kinds: a missing mandatory state; a binding to a field that does not exist; an action leading to a screen that does not exist; role gating missing; a duplicate region key; a Phase 1 node depending on Phase 2; a missing token reference; a Locked node without evidence; a policy-Locked item without a sheet citation; a form-vocabulary violation in free text; an orphan screen; an entity without a screen; a missing copy key; an operation without an error model; volatile data in a body; a closure missing a field; a task over budget; an unresolved alias; an `unverified` research item used as a Default; an invented provider name; a `later` feature that has a screen node; a research-derived value that does not match its item. Verify must find them; false positives are counted on the unmodified pack.

## 6. The edge-case suite and the provider-switch test (PS-9)
- **One test per ID** of doc 15 §12 (SPC-IN, SPC-RS, SPC-SC, SPC-MD, SPC-FE, SPC-CH, SPC-OP, SPC-UX: 113 cases in doc 15; SPC-CL, SPC-MK, SPC-ST, SPC-DG, SPC-SZ: 35 more in doc 17 §11; 148 in all). The builder's fixture folder mirrors the IDs (`spc-in-01`, `spc-rs-07`, `spc-md-06`, ...); **a link check fails the build when an ID in doc 15 has no fixture or a fixture has no ID**.
- **Fixture kinds:** *real* (the nine exports), *mut* (mutation recipes: unknown version with renamed keys; approval hash removed; Hinglish quotes; a poisoned instruction in a quote; Quick plus extra features; Standard plus one feature; Standard minus a screen; a phone number inside the Brief; Arabic locale; Research hash changed), *syn* (a seeded generator with realistic density: tiny, no-backend, read-only, ERP with hundreds of entities, twenty integrations, 90 core screens, global-only providers, partial research store), *mock* (gateway behaviours: invalid JSON, truncation, refusal, a 429 storm, "no credits", a slow route, a process killed at batch 7, concurrent starts), *cfg* (consumer-profile and route variants: factor 1.3, no structured output, no caching, single vendor, larger and smaller windows, an old policy-sheet date).
- **Provider-switch test:** the same fixtures on two routes with different profiles. Both must pass the same validators; outputs are **validated, not compared**; re-packaging for the other consumer must cost no model call and keep the content hash.

## 7. Decision table: what we change when a number fails
| Signal | Threshold [J] | Action |
|---|---|---|
| A task read total over budget after re-sharding | any | fewer screens per task; shard the offending global file further; if one slice is over the cap, split the screen; raise `task_read_budget` only through a profile with a larger effective window |
| Consumers fail on packs near `whole_pack_tokens` | blocking questions rise with pack size | lower `whole_pack_tokens` |
| Blocking questions per screen | above 0.3 | read the questions; add the missing field class to the card schema or a required `behaviors` or `rules` entry; **never add layout** |
| Assumptions per screen | above 1 | the same |
| Freedom probe shows identical layouts | fewer than 30% differ | find the field that dictates; tighten the schema; no "creativity" instruction |
| Lint warnings | above 3 per card | tune the deny-list; check which words name content |
| Ungrounded optional fields | above 10% | tighten the call spec so unknown fields stay null, make provenance mandatory in the schema, check the effort floor in the gateway; never tune a prompt to one vendor |
| The budget set is inconsistent or a one-screen task does not fit | any | fix the caps, not the check; raise `task_read_budget` only through a profile with a larger effective window |
| First-try validator pass | below 85% | shrink the call (smaller batch), add few-shot from the libraries, move the rule into code; if one step stays weak on the temporary route, use the `standard` or `deep` route for that step only |
| Degraded nodes | above 5% | the same |
| Planted-defect catch | below 90% | stricter validators; a second vendor for the critic |
| False positives | above 5% | loosen the one validator; record why |
| Library-lookup recall at 3 | below 90%, or no better than the card-only baseline | enrich the needs (anatomy, gestures, roles, attribute buckets); never name a component |
| Wrong-gap rate | above 10% | the same |
| An ablation arm shows no lift | any bundle | cut it, or make it optional by profile |
| COULD lowers within-app consistency or between-app diversity | any drop | lower the COULD cap; tighten the basis rule |
| V-37 trips on more than about one real screen in ten | above 10% | loosen the limit before it blocks |
| The pilot-first arm is not more consistent | no gain | make pilot-first optional; check the form of the design-language record |
| Estimate error | above 1.5 times | recalibrate per-step token counts from the run report |
| Run time above target at the measured concurrency | | check the adaptation and the rate limits; ask for higher limits; reduce per-call input before reducing quality |
| Validators fail on one route only | | an adapter issue (structured output, caching, tokenizer): fix in the gateway layer, never in Spec's prompts |
| The owner says Phase 1 is not a working product | | a gap in the pull-in rule; add a journey-completeness case |
| Bytes differ across runs or locales | any | find the volatile field or the unstable ordering |
| Node counts vary more than 30% across three runs of one input | | make the plan step more deterministic; derive more in code |
| Actual cost per run above 3 times the estimate on the first live batch | | stop and review the price configuration and the call sizes before any further run |

## 8. Live protocol (order, budget, stop rules)
**Order:** PS-0, PS-1, PS-2 (no model) → price check → PS-3 → PS-4 → PS-5 on Quick three times → PS-6 on Quick → PS-5 on Standard three times → PS-6 on Standard → PS-7 → PS-8 (the synthetic Big run only after Standard is healthy) → PS-9 → PS-10 → one Thorough run.
**Price check first:** one Quick screens batch is run, its real cost is computed, every estimate is recomputed from it; if it is more than three times the estimate, stop.
**Preflight** before any live run: the route map and consumer profiles present and dated; vendors configured (and `critic_vendor` stated); keys present (never printed); the account has credit; the budget record exists.
**Budget (starting values; USD):** PS-3 0.4 · PS-4 0.3 · PS-5 0.8 · PS-6 0.5 · PS-7 0.2 · PS-8 0.8 · PS-9 0.6 · PS-10 0.5 · reserve 0.9 = **5.00**, restartable and recorded. At doc 07's recorded prices these are generous (a Big run about 0.3); at ten times those prices they are tight, which is why the price check comes first.
**Stop rules:** never exceed the budget; a stop ends the run `partial` and resumable (doc 15 §8.4); a credit or rate-limit stop waits and resumes, it does not fall back silently to a weaker route.

## 9. What these gates do not prove
- **Visual quality of the generated apps.** That is the front-end engine's work and the owner's judgment at G3; PS-4 only shows that Spec does not dictate it.
- **Categories we did not test.** Nine inputs are a small sample; passing means no gross failure.
- **Load across real tenants.** Fairness and rate limits are tested with mock limits only.
- **Stronger or other models beyond the provider-switch test.**
- **The truth of Research's facts** (doc 14) and **native-language copy quality** (needs native reviewers, OI-33).
- **That the front end's real component library answers needs the way the two tested catalogues do.** PS-10 tests two public catalogues, not the one in production.
- **Backend consistency.** Contract v1 is reconciled at G3 (doc 04 §7).

## 10. Status of the decisions
| Decision | Status | Gate that moves it to Proven |
|---|---|---|
| AD-65 Role | Proposed | PS-6 |
| AD-66 Freedom contract | Proposed | PS-4 |
| AD-67 Inputs, adapters, trust | Proposed | PS-1, PS-3, PS-9 |
| AD-68 Size, screens, phases | Proposed | PS-3 and the owner's reading |
| AD-69 The Spec Pack | Proposed | PS-2, PS-6 |
| AD-70 Consumption contract | Proposed | PS-2, PS-6 |
| AD-71 Developer readiness (BN-15 to BN-17) | Proposed | PS-5, PS-6 |
| AD-72 Inside Spec | Proposed | PS-5, PS-9 (resume) |
| AD-73 Verify stays thin | Proposed | PS-7 |
| AD-74 Scale and cost | Proposed | PS-8 |
| AD-75 This proof plan | Proposed | itself |
| AD-76 Model and provider independence | Proposed | PS-9 |
| AD-77 Three voices and the rule of silence | Proposed | PS-4, PS-10 |
| AD-78 The bridge to any component library and tool | Proposed | PS-10 |
| AD-79 The why layer (personas, stories, journeys) | Proposed | PS-10 |
| AD-80 The mock data model | Proposed | PS-10 |
| AD-81 Designing for a 2026 mobile app; the mobile-fit rule | Proposed | PS-4, PS-10 |
| AD-82 Content depth by size and the big-app tactic | Proposed | PS-10 |
