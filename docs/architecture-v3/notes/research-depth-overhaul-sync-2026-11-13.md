# Research depth overhaul and the model-vendor rule: sync from the builder repo (2026-11-13)

**What this is.** A read-only sync. While starting the Spec design I read the builder's own records and found our `STATE.md` was behind them. Everything below is **as the builder reports it** (its documents and decision log); I did not re-run anything and did not read any key. Files read: `docs/implementation/CURRENT_STATE.md`, `docs/research-agent/depth-overhaul-as-built-2026-11-13.md`, `docs/research-agent/spec-contract.md`, `docs/implementation/DECISIONS.md` (D-156 to D-172) in `/Users/coder/Desktop/project/men-gen`. The repo kept moving while I read (branch `feat/llm-providers` at the end; tips `5e6bba4`, `500f5c8`).

## 1. Corrections to what our STATE said
| STATE said | The builder's record says |
|---|---|
| "None of prompt 11's steps started"; "paste prompt 11 v3 to the builder" | **Done.** D-156: "The owner's prompt 11 v3 is the overhaul approval D-154 asks for." Branch `feat/research-depth`, cut from tag `pre/research-depth-20261112-2220`; decisions D-156 to D-170 |
| Medium and Big have never run with new code | Medium ran once (old ceilings) and Big ran on Serper (after a fetch fix); details in §3 |
| Builder tip `c47e14e`, nothing since 2026-11-11 | Moved on: the Research overhaul, then a gateway change (`feat/llm-providers`) |
| Live runs use the OpenAI model | **OpenAI account is out of credits** (HTTP 429 on every call, D-170). The gateway now also has a **Claude provider** and per-call vendor routing (D-171, D-172) |
| AD-63 additions "not built" | Mostly **Built** (§2). The independent checker, claim-fit repair (tighten and ask once more), official-first discovery, provider follow-ups, a query privacy guard, provider dossiers, platform constraints and a ranked confirm list exist; corroboration and quote-first extraction were **not** built |

## 2. What the overhaul built (from the builder's as-built note)
Intake that survives Compass changes (`intake/formats.py`; the export's binding to its Brief is a flag, `input_binding`, never a refusal; all nine inputs load); provider credits tracked globally (advisory); checker v3 plus tighten-and-ask-once-more; the Screens slot bug fixed (`{state, content}` was read as empty); risk topics outside the topic cap, official-first queries, own-site follow-ups for the most important providers, registries for more countries; new kinds `provider_fact` and `platform_constraint` with typed values (money is a decimal string; code rejects a wrong amount or currency); code-built dossiers, platform constraints, a compliance register (thin), lane decisions, a ranked confirm list; the output stays `research.v5`, extended additively (D-162); **`pack(research, need_card)`** as the selection contract for Spec. Fetch retries, bounded text extraction, longer timeouts at scale, a failover cool-down, a circuit that waits instead of silently falling back.

## 3. Live runs on the new code (USD 4.68 of 5.50 booked, including the earlier 1.10)
| Run | Input and provider | Result | Searches / pages | USD | Required slots |
|---|---|---|---|---|---|
| 1 | Small yoga, Tavily | partial (ceiling) | 40 / 60 | 0.394 | 12 of 19 |
| 2 | Small yoga, Tavily, after fixes | partial (ceiling) | 52 / 80 | 0.492 | 15 of 19 (79%) |
| 3 | Medium Nashik, Tavily, old ceilings | partial | 91 / 130 | 0.855 | 20 of 40 (50%) |
| 4 | Small yoga, Serper | partial | 52 / 79 | 0.135 | 11 of 19 (58%) |
| 5, 6 | Big freight, Serper, fetch broken | partial | 196, 189 / 15, 27 | 0.24, 0.31 | 7, 10 of 66 |
| 7 | Big freight, Serper, after the fetch fix | partial (page ceiling) | 199 / 300 | 0.517 | 55 of 67 (82%) |
| 8 to 10 | Medium Nashik, Serper | **failed** (OpenAI "no credits") | | 0.11 each | |
Facts (official / single source / unverified): run 2 103 / 31 / 16 (11% unverified); run 3 185 / 39 / 32; run 7 307 / 205 / 61 (about 570 facts, 300 pages). **Small unusable share fell from 30 to 41% to 11 to 14%**; payments on Small found in-market providers (Cashfree, PhonePe) with 91 to 92% official fields; planted-error catch 98.9%, 0% false rejection. **Not met:** Small slots at least 70% in 3 of 3 runs; Small cost 0.49 against a 0.32 to 0.40 target (explained, D-163); Medium (50%, old ceilings); compliance register thin; **consumer probe not run**; Medium on the final code never run. Caveat from the builder: "default-grade" counts a provider's own-site facts (`official_inferred`), so the owner's 60-fact spot-check is the honest test.

## 4. The model-vendor rule and the Claude route (D-171, D-172)
- **Rule 14 (owner instruction):** every agent and engine, now and later, runs on OpenAI, Claude, Gemini or a Chinese model **without code changes in the engine**; engines name a **route**, never a model or vendor; vendor, model, price, effort, schema dialect, token accounting and error mapping live **only in the gateway and its config** (`MODEL_ROUTES`, `pricing.yaml` with a `vendor` per model, an `effort_floor` per model).
- **Prices the builder read on 2026-11-13** (not checked by me): Haiku 5.5 USD 0.10 / 0.50 per million tokens **and times five above 100K prompt tokens**; Sonnet 5.5 2 / 10; Opus 5.5 4 / 20; cache reads at a tenth of input. The Anthropic provider sends **no cache markers** today (not built on purpose), is non-streaming and uses no tools.
- **A separate Claude test allowance:** "USD 5 max overall" for testing the Research agent on Claude, its own ledger period.
- **Live findings on Claude (D-172):** (1) the structured-output API **refuses a schema with more than 16 nullable (`anyOf`) parameters** or whose compiled grammar is too large; 39 of 150 extraction calls failed at first; the gateway now turns nullables into optional properties and, if still refused, sends the schema in the instructions without constrained decoding. (2) **Haiku at low effort over-fills structured fields** the source does not state (43% of facts unverified against 11% on the earlier model); the fix is an effort floor in the gateway (`medium` for Haiku 5.5), not a prompt change. (3) **A bigger model was not a better checker**: Sonnet 5.5 at low effort caught 85% of planted errors (38.8% of swapped-provider claims) against 97.6% for Haiku at its floor; all three routes ran on Haiku for the Claude tests. A cross-vendor checker is **not proven**.

## 5. What Research now promises Spec (`spec-contract.md`)
Every item has a Brief id or `NEW` (never empty); an evidence label computed by code (`official`, `corroborated`, `single_source`, `unverified`); atoms are single facts; unknown stays an explicit null with a reason (R27); money is a string; quotes never reach the file. Parts Spec reads: `lane_decisions[]`, `dossiers[]`, `dossiers_by_integration`, `platform_constraints[]`, `compliance_register[]`, ranked `confirm[]`, `conflicts[]`, `items[]`, `norms[]`, `slices{}`, `profile` (store tokens, appendix items), and `pack(research, need_card)`. **Not proven:** depth by size is defined in `data/depth.yaml` but some items are data-only with no enforcement; no Medium or Big run has been read against it; real dossier completeness and the real share of provider facts reaching `official` on Medium and Big are unmeasured.

## 6. What it changes for the Spec design (doc 15)
Spec reads Research through this contract (doc 15 §3.2); a Big store far exceeds the 40k limit so `pack` is the norm; **providers differ in schema limits, over-filling, long-prompt prices, caching and critics**, so doc 15 §11 treats schemas as a portable subset, requires explicit nulls and provenance (no over-filling), keeps every call under the long-prompt threshold, assumes no caching until a profile enables it, and lets PS-7 (not assumption) decide whether a stronger route is a better critic. The temporary model for the next live runs is a Haiku-class route through the gateway's Claude provider (owner, 2026-11-13).

## 7. Still open on the Research side
Owner's two one-minute checks (provider dashboards against our counters); the owner's 60-fact spot-check; Medium on the final code (about USD 1.1 on Tavily or 0.4 on Serper), the consumer probe (0.15), Porto (0.45), Big on Tavily (about 1.9); a run cannot be resumed (D-167); writer and checker are one model per route.
