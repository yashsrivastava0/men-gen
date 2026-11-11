# Research Agent Cost, Architecture, and Scaling Review

**Date:** 2026-11-11  
**Scope:** `mde_research_agent` cost tracking, pipeline efficiency, scaling from Small to Medium and Big, and recommendations for the Claude Code builder.  
**Branch:** `review/research-cost-and-console`  
**Reference Run:** Small run `rrn_01M75913R4CZSXAA65P7TM0MW9` (Sunrise Yoga Studio Bengaluru, load score 17, `focused`).

---

## 1. Executive Summary: Where the Money Actually Went

The owner philosophy is: *"Build first, optimize later."* The research agent has been built, and its first live Small run (`quick-yoga-studio-bengaluru.json`) completed successfully. We analyzed the complete telemetry of that run: the SQLite-free append-only budget ledger (`data/.budget/research/ledger-*.jsonl`), the spend record (`agents/research/.runtime/spend-record.json`), the run manifest, compressed search/fetch cassettes, and the model cassettes (`cassettes/llm/*`).

### The Fundamental Cost Reality
Across the whole Small run, **total spend was $0.2503 (250,260 micro-USD)** over 76 seconds of wall-clock execution:

| Expense Category | Spend (USD) | Spend (Micros) | Share of Total Run Cost | Call Count |
| :--- | :--- | :--- | :--- | :--- |
| **Search Queries** | **$0.2280** | **228,000** | **91.1%** | 28 searches |
| **Language Model Calls** | **$0.0223** | **22,260** | **8.9%** | 59 calls |
| **Total** | **$0.2503** | **250,260** | **100.0%** | **87 total operations** |

**Crucial Takeaway:**  
Search queries drove **over 91%** of the entire cost. All 59 LLM calls combined (planning, query formulation, gap analysis, 7 extraction jobs, passage checking, and synthesis/assembly) cost only **$0.022**—an average of **$0.00038 (less than 4 hundredths of a cent) per LLM call**.

### Primary Findings
1. **Search Provider Price Asymmetry (8x Gap):**  
   Tavily costs **8,000 micro-USD ($0.008)** per search credit. Serper costs **1,000 micro-USD ($0.001)** per search credit. The app default (`data/providers.yaml`) is hardcoded to `tavily`. Simply switching the primary search provider to Serper (or selecting it via the Search Bake-off) immediately cuts total run cost by **up to 80%** (reducing Small run spend from $0.25 to ~$0.05).
2. **Extraction Call Waste (48.6% Zero-Yield Rate):**  
   18 of the 37 extraction calls returned zero items. In particular:
   - `screens`: 4 calls, 0 items extracted (100% waste, ~9,640 input tokens).
   - `flows`: 11 calls, only 3 items extracted (8 calls returned 0 items, ~19,000 input tokens wasted).
   - `local`: 4 calls, 1 item extracted (3 calls returned 0 items).
3. **Checker Call Fragmentation:**  
   Checking is performed topic-by-topic rather than across topics. 9 separate checker calls were made for 42 facts (average 4.67 facts/call). Three check calls checked only 1 single fact each. Because each call re-transmits the ~800-token system instructions and schema, checking single facts incurs an unnecessary 3x overhead in prompt tokens.
4. **Follow-up Query Redundancy:**  
   In Round 2, the gap analyst generated queries that largely repeated Round 1 searches with minor word shifts (e.g. Q24 vs Q5/6 for class packs; Q25 vs Q1/2 for timetables), incurring 7 additional $0.008 searches that produced few unique pages.
5. **Cold Search Cache:**  
   `CachingSearch` is an in-memory `OrderedDict`. When the app stops, restarts, or runs via CLI, the cache is completely empty, forcing re-billing for identical regional/tech queries.
6. **Accounting Misattribution in Failover Search:**  
   In `FailoverSearch`, when a second opinion query is sent to Serper but yields no new unique URLs, the 1,000 micro-USD cost is added to `cost_micros`, but `provider` remains `"tavily"`. This misattributed $0.003 of Serper spend to Tavily in the ledger.
7. **Schema / Model Discrepancy (RV-01):**  
   In `ScreenPatternData`, `purpose` was null on 2 items (`RI-005`, `RI-006`), triggering 4 `RV-01` schema validation errors because the JSON Schema expects a string.

---

## 2. What Was Measured and How

### Measurement Sources
All measurements were taken from genuine recorded run artifacts and offline doubles (no live LLM or search calls were initiated):
- **Budget Ledger:** `data/.budget/research/ledger-prd_01M755ESXKCDRKV2X3SKHEZXVV.jsonl` (174 ledger lines: 87 `reserve`, 87 `commit`).
- **Spend Record:** `agents/research/.runtime/spend-record.json` (Entry `spn_01M75913RSDCN8HKPXJCJZDREN`).
- **Committed Artifacts:** `research.json` (content hash `a05fbf86cd4f...`), `run.json`, `events.jsonl` (282 events).
- **Cassettes:** 59 LLM response cassettes under `cassettes/llm/*`, 28 search response cassettes under `cassettes/search/*`, and 40 fetch response cassettes under `cassettes/fetch/*`.
- **Pre-run Batch Estimator:** Output of `python -m mde_research_agent.live_runs --run all --dry-run`.
- **Compass Brief Inputs:** `quick-yoga-studio-bengaluru.json` (Small, load 17), `standard-produce-marketplace-nashik.json` (Medium, load 27), and `thorough-freight-fleet-india.json` (Big, load 83).

---

## 3. Where Money and Time Go

### Granular Cost Breakdown by Spec ID

| Operation / Spec ID | Step Category | Calls | Input Tokens | Output Tokens | Cost (Micros) | Cost (USD) | % Total Spend |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `rsch.search.tavily` | Search | 27 | 0 | 0 | 219,000 | $0.2190 | 87.51% |
| `rsch.search.tavily+serper` | Search (Failover/2nd) | 1 | 0 | 0 | 9,000 | $0.0090 | 3.60% |
| `rsch.extract.structure` | Extraction | 6 | 16,126 | 5,604 | 3,913 | $0.0039 | 1.56% |
| `rsch.check` | Checker v2 | 9 | 21,702 | 3,238 | 3,794 | $0.0038 | 1.52% |
| `rsch.extract.services` | Extraction | 5 | 13,749 | 3,667 | 2,862 | $0.0029 | 1.14% |
| `rsch.extract.flows` | Extraction | 11 | 26,358 | 1,412 | 2,511 | $0.0025 | 1.00% |
| `rsch.extract.data` | Extraction | 4 | 10,378 | 2,289 | 1,891 | $0.0019 | 0.76% |
| `rsch.assemble` | Synthesis/Assembly | 1 | 5,489 | 2,530 | 1,814 | $0.0018 | 0.72% |
| `rsch.write_queries` | Planning/Queries | 10 | 7,745 | 832 | 1,197 | $0.0012 | 0.48% |
| `rsch.extract.local` | Extraction | 4 | 9,285 | 671 | 1,160 | $0.0012 | 0.46% |
| `rsch.plan` | Planning | 1 | 1,419 | 1,345 | 815 | $0.0008 | 0.33% |
| `rsch.extract.screens` | Extraction | 4 | 9,642 | 64 | 697 | $0.0007 | 0.28% |
| `rsch.extract.features` | Extraction | 2 | 5,339 | 565 | 684 | $0.0007 | 0.27% |
| `rsch.extract.design` | Extraction | 1 | 2,822 | 381 | 474 | $0.0005 | 0.19% |
| `rsch.gap_analyst` | Planning/Follow-up | 1 | 1,505 | 593 | 448 | $0.0004 | 0.18% |
| **All Searches Combined** | **Search** | **28** | **0** | **0** | **228,000** | **$0.2280** | **91.10%** |
| **All LLM Calls Combined** | **Model** | **59** | **131,559** | **23,191** | **22,260** | **$0.0223** | **8.90%** |
| **Total** | | **87** | **131,559** | **23,191** | **250,260** | **$0.2503** | **100.00%** |

### Where Time Goes (Execution Profile)
- **Total Duration:** 76.25 seconds (wall clock: `12:23:53.87` to `12:25:10.12`).
- **Initial Planning (`rsch.plan`):** ~17 seconds. Luna spent 932 reasoning tokens determining targets and search allocations.
- **Query Writing (`rsch.write_queries`):** ~3.8 seconds. 10 query generation calls ran concurrently.
- **Topic Search & Extraction:** ~42 seconds. Topics ran in parallel under the concurrency semaphore (`lanes_search: 6`, `lanes_extract: 8`).
- **Gap Analysis & Round 2 Follow-up:** ~8 seconds.
- **Assembly (`rsch.assemble`) & Verification (RV-01..23):** ~5.5 seconds.

---

## 4. Cost Tracking Integrity Assessment

### Reconciliation Record
We performed a 3-way reconciliation across:
1. `data/.budget/research/ledger-prd_*.jsonl` (committed sums)
2. `agents/research/.runtime/spend-record.json` (`total_micros` and `by_step`)
3. `agents/research/.runtime/runs/rrn_*/research.json` (`budget.usd_micros`)

**Result:**  
- Ledger committed total: **250,260 micro-USD**.
- Spend record total: **250,260 micro-USD**.
- Research JSON budget: **250,260 micro-USD** (`$0.2503`).
- Step sums in spend record: `plan (815) + queries (1,645 [1,197 write_queries + 448 gap_analyst]) + search.tavily (219,000) + extract (14,192) + check (3,794) + search.tavily+serper (9,000) + assemble (1,814) = 250,260 micro-USD`.

**Verdict:** Zero drift exists between the ledger, spend record, and output artifacts. The transactional booking architecture (`GuardSpendBook` reserving and committing into the guard) works reliably.

### Cost Attribution Defect in `FailoverSearch`
A subtle accounting discrepancy was identified in `mde_research_agent/adapters/search/failover.py`:
```python
# failover.py line 81-82:
cost_micros=first.cost_micros + other.cost_micros,
provider=f"{first.provider}+{other.provider}" if extra else first.provider,
```
When Serper is queried as a second opinion (adding 1,000 micro-USD) but returns no *new* URLs beyond what Tavily already found (`extra` is empty), `provider` remains `"tavily"`. The worker books this cost under `rsch.search.tavily` for 9,000 micro-USD instead of 8,000. Three such calls occurred during the Small run, misattributing 3,000 micro-USD ($0.003) of Serper spend to Tavily.

---

## 5. Sizing & Scaling: How Small Scales to Medium and Big

### Input Characteristics

| Input Name | Brief File | Size Class | Load Score | Targets | Trust Job | Planned Searches (Soft / Hard) | Planned Pages (Soft / Hard) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Small** | `quick-yoga-studio-bengaluru.json` | Small (`focused`) | 17 | 10 | OFF | 20 / 32 | 24 / 40 |
| **Medium** | `standard-produce-marketplace-nashik.json` | Medium (`standard`) | 27 | 16 | **ON** | 40 / 64 | 60 / 96 |
| **Big** | `thorough-freight-fleet-india.json` | Big (`deep`) | 83 | 26 | **ON** | 80 / 120 | 140 / 210 |

### Scaling Mechanics
- **Load Boundaries:** `focused` below 22; `standard` 22 to 57; `deep` from 58. (Medium is 27, Big is 83).
- **Search Ceilings:** The engine hard limits scale from 32 (Small) -> 64 (Medium) -> 120 (Big).
- **Page Ceilings:** Scale from 40 (Small) -> 96 (Medium) -> 210 (Big).
- **Trust Job:** The Small run did not trigger trust checks (single-region, simple booking). Medium triggers trust checks due to payments + two-sided marketplace money. Big triggers trust checks due to enterprise identity, logistics compliance, and deep load. Trust topics add 2 extra targets with strict verification requirements.

### Cost Predictions for Medium and Big

#### Medium Input (`standard-produce-marketplace-nashik.json`)
- **Expected Searches:** 48 to 56 searches (Soft target: 40; second-round follow-ups add ~10-14).
- **Expected Pages Read:** 65 to 80 pages.
- **Expected LLM Calls:** ~90 to 110 calls (16 targets * ~5 extraction/checking calls + plan/assemble).
- **Cost Prediction with Tavily Default:**
  - Search: 52 * $0.008 = $0.416
  - Second opinions / failovers (~10%): ~$0.010
  - LLMs: 100 calls @ ~$0.00045/call = ~$0.045
  - **Total Medium Cost (Tavily): $0.47 – $0.55**
  - **Confidence:** **HIGH (±10%)**
- **Cost Prediction with Serper Primary:**
  - Search: 52 * $0.001 = $0.052
  - LLMs: ~$0.045
  - **Total Medium Cost (Serper): $0.09 – $0.12** (78% savings)

#### Big Input (`thorough-freight-fleet-india.json`)
- **Expected Searches:** 90 to 110 searches (Soft target: 80; Hard: 120).
- **Expected Pages Read:** 130 to 160 pages.
- **Expected LLM Calls:** ~160 to 200 calls (26 targets across 4 rounds).
- **Cost Prediction with Tavily Default:**
  - Search: 100 * $0.008 = $0.800
  - Second opinions / failovers (~15%): ~$0.015
  - LLMs: 180 calls @ ~$0.00055/call (assembly token count increases to ~12k) = ~$0.100 - $0.130
  - **Total Big Cost (Tavily): $0.92 – $1.15**
  - **Confidence:** **MEDIUM-HIGH (±15%)**
- **Cost Prediction with Serper Primary:**
  - Search: 100 * $0.001 = $0.100
  - LLMs: ~$0.115
  - **Total Big Cost (Serper): $0.20 – $0.25** (77% savings)

### Calibration of `data/estimates.yaml`
Currently, `data/estimates.yaml` specifies:
```yaml
small:  usd_micros: {low: 200000, mid: 380000, high: 650000}    # ($0.20 / $0.38 / $0.65)
medium: usd_micros: {low: 450000, mid: 850000, high: 1400000}   # ($0.45 / $0.85 / $1.40)
big:    usd_micros: {low: 1200000, mid: 2000000, high: 3200000} # ($1.20 / $2.00 / $3.20)
```
- **Analysis:**  
  The estimates in `data/estimates.yaml` are **consistently 1.5x to 2x higher than actual costs** under Tavily pricing, and **5x to 10x higher** if Serper is used.
  - Small predicted mid: $0.38 -> Actual: $0.2503 (66% of mid estimate).
  - Medium predicted mid: $0.85 -> Modeled actual: ~$0.51 (60% of mid estimate).
  - Big predicted mid: $2.00 -> Modeled actual: ~$1.05 (52% of mid estimate).
- **Is this honest?** Yes. It represents a conservative safety envelope designed before live runs existed, preventing trips against the hard budget tripwire ($4.50) or live budget ($5.00). When the app runs with recorded spend entries, `Estimates.for_class` dynamically overrides `mid` with the measured median ($0.25 for Small).

---

## 6. Ranked Findings and Recommendations

We strictly enforce the constraint: **Never lower the quality of the final `research.v5` result that goes to the Spec engine.** Any proposed change must be verifiable against the repository's established quality telemetry.

### Category 1: Safe Optimizations (Zero Quality Risk)

#### Recommendation 1.1: Fix Misattribution in `FailoverSearch`
- **What:** In `mde_research_agent/adapters/search/failover.py`, set `provider = f"{first.provider}+{other.provider}"` whenever `other.cost_micros > 0`, even if `extra` hits are empty.
- **Saving:** $0 net (accounting accuracy fix; prevents misclassifying Serper spend as Tavily spend).
- **Quality Risk:** Zero. Does not alter query execution or hits.
- **How to verify:** Run unit test `tests/adapters/test_search_failover.py`.

#### Recommendation 1.2: Fix ScreenPatternData Schema Optionality (RV-01)
- **What:** In `ScreenPatternData` (or the `research.v5` JSON schema), align the `purpose` field so `null` is accepted (e.g. `purpose: str | None = None` and `type: ["string", "null"]`).
- **Saving:** Prevents false-positive validation errors in RV-01.
- **Quality Risk:** Zero.
- **How to verify:** Run `uv run pytest agents/research/tests/validators/test_rv01_schema.py`.

#### Recommendation 1.3: Buffer and Batch Topic Checker Calls
- **What:** Currently, `check_topic` runs inside each topic's worker task, submitting batches of only whatever verified atoms that specific topic accumulated (often 1 or 2 items). Instead, collect verified atoms into a central topic pool or buffer up to `CHECK_BATCH = 15` before dispatching `rsch.check`.
- **Saving:** In the Small run, reduces 9 checker calls to 3 calls. Saves ~5,000 input tokens in repeated system instructions (~$0.001 per run, scaling to ~$0.005 on Big).
- **Quality Risk:** Zero. The exact same items, claim/quote/passage pairs, and verdicts are evaluated by the model.
- **Telemetry to monitor:** Checker `not_supported` rate and `unsupported_fields` rate must remain identical.

#### Recommendation 1.4: Persistent Disk Cache for `CachingSearch`
- **What:** Make `CachingSearch` write-through to a JSONL file in `RESEARCH_DATA_DIR` (similar to `spend-record.json`) rather than purely in-memory.
- **Saving:** 100% savings on re-run queries and cross-run common queries (e.g. payment gateway specs, WhatsApp Business limits) across sessions.
- **Quality Risk:** Zero, provided TTL is maintained (14 days general, 2 days for fee words).
- **Telemetry to monitor:** Search cache hit counter in Quality tab.

---

### Category 2: Probably Safe, Test First (High Savings, Low-Medium Quality Risk)

#### Recommendation 2.1: Promote Serper as Primary Search Provider via Bake-off
- **What:** In `data/providers.yaml`, make Serper the default, or execute the search bake-off to validate Serper's quality on India/regional queries.
- **Saving:** **Cuts total research run spend by 75% to 80%** (Saves ~$0.20 per Small run, ~$0.40 per Medium run, ~$0.85 per Big run).
- **Quality Risk:** Low to Medium. Serper returns Google SERP data which has excellent regional coverage in India, but snippet length and date metadata formatting may slightly differ from Tavily.
- **How to verify before adopting:**
  1. Replay the Small run queries against Serper offline using cassettes.
  2. Measure:
     - `fetch_success` rate (do URLs resolve cleanly without soft-404 or paywall blocks?).
     - `searches_contributing` (do Serper results yield verified quotes?).
     - `rv18` quote pass rate.
     - `norms_with_3_sources` (is corroboration density preserved?).

#### Recommendation 2.2: Pre-filter Pages Before Specialized Extraction Jobs
- **What:** In `worker.py`, before invoking specialized extraction jobs (`rsch.extract.screens`, `rsch.extract.flows`, `rsch.extract.local`), run a fast keyword/heuristic relevance check on the fetched page text. If a page has no workflow, action, or state verbs, skip `rsch.extract.flows`.
- **Saving:** Eliminates ~45% of extraction calls that return empty atom lists (saves ~30,000 to 50,000 tokens on Small; ~100,000 tokens on Big; ~$0.008 to $0.020).
- **Quality Risk:** Low, if keywords are conservative.
- **Telemetry to monitor:** Number of verified atoms extracted per topic; required slots filled.

#### Recommendation 2.3: De-duplicate Near-Identical Gap Analyst Queries
- **What:** In `worker.py` (Round 2 follow-ups), compute token-set overlap or Jaccard similarity between proposed gap queries and Round 1 queries. If similarity is > 0.70 (e.g. "Class packs purchase steps" vs "Class packs booking flow"), drop or refine the query instead of issuing another $0.008 search.
- **Saving:** Saves 3 to 5 redundant searches in Round 2 (~$0.024 to $0.040).
- **Quality Risk:** Low. Round 1 already fetched pages for those keywords.
- **Telemetry to monitor:** Required slots filled before and after Round 2.

---

### Category 3: Risky (Do NOT Adopt)

#### Recommendation 3.1: Do NOT Lower Passage Window Size (150 words)
- **Why it is tempting:** Shortening `PASSAGE_WORDS` from 150 to 50 would save input tokens on every checker call.
- **Why it is rejected:** 150 words is necessary to establish surrounding context (negation, conditions, exceptions). Truncating context directly causes false `not_supported` checker rejections and violates RV-21. Spend is already minuscule ($0.0038).

#### Recommendation 3.2: Do NOT Skip Model-Checking for "Official" Sources
- **Why it is tempting:** Official provider pages might seem trustworthy enough to skip the second model check.
- **Why it is rejected:** LLM extractors frequently hallucinate specific numbers (e.g. pricing tiers or percentages) even when reading official docs. RV-18 (numbers in quote) and checker v2 caught 16 invalid claims in the Small run. The check must remain universal.

---

## 7. What is Justified Spend and Must Not Be Cut

The following elements of the architecture cost money or compute, but deliver critical quality protections that directly safeguard downstream Spec generation:
1. **Verbatim Quote Verification in Code (`core/quotes.py`):** NFKC normalization, whitespace folding, and string indexing. This guarantees zero hallucinated quotes reach the output.
2. **RV-18 Number Verification in Quote:** In the Small run, code rejected 10 facts where extracted numbers did not appear in the quote. This completely eliminates hallucinated pricing or limits.
3. **Dual-Model Checking on Reserved Route (`rsch.check` on Luna standard route):** Catches subtle model misinterpretations and nulls unsupported attributes without dropping the entire finding.
4. **Main Store & Appendix Separation (RV-22):** Preserves all discovered findings without silent deletion.
5. **Failover Search Bounded Second Opinion (15%):** Ensures thin search results are rescued by a second provider when needed.

---

## 8. What Could Not Be Measured

1. **Live Serper Quality Yield on Full Production Run:**  
   Because live runs are restricted to the owner, Serper was only measured on 1 live second-opinion call in the Small run and offline fixtures. Whether Serper yields higher or lower quote pass rates across all 10 topics remains unmeasured live.
2. **True Medium and Big Wall Latency Under Concurrency:**  
   While model costs scale linearly with target count, wall-clock latency depends on provider rate limits, asyncio gather behavior under network jitter, and memory usage.
3. **Real Invoice Reconciliation:**  
   The actual credit consumption on the owner's Tavily dashboard (whether raw content or chunk count incurred unexpected credit surcharges) cannot be checked without billing invoice access.

### Recommended Next Run for the Owner
To complete calibration, the owner should run the **Medium input** (`standard-produce-marketplace-nashik.json`) from the app:
- **Expected Cost:** ~$0.50 (Tavily) or ~$0.12 (Serper).
- **Expected Time:** ~4 to 5 minutes.
- **Key Metrics to Inspect in Quality Tab:**
  - `quote_pass_first_try` (target: > 85%)
  - `number_rejections`
  - `checker_not_supported_rate` (healthy range: 5% - 20%; below 1% indicates rubber-stamping)
  - `required_slots_filled`

---

## 9. Implementation Checklist for the Claude Code Builder

When the builder implements pipeline optimizations, we recommend the following order:

1. [ ] **Fix `failover.py` accounting attribution:** Ensure Serper costs are booked under `rsch.search.serper` or `rsch.search.tavily+serper`.
2. [ ] **Fix `ScreenPatternData.purpose` schema:** Allow `null` in JSON schema to eliminate the 4 RV-01 validation errors.
3. [ ] **Cross-topic checker batching:** Buffer verified items across topics up to `CHECK_BATCH = 15` before calling `rsch.check`.
4. [ ] **Pre-filter extraction candidates:** Add simple relevance gating on page text before invoking `rsch.extract.screens` and `rsch.extract.flows`.
5. [ ] **Persistent search cache:** Add disk serialization for `CachingSearch` across runs.
6. [ ] **Run Search Bake-off Tool:** Compare Tavily and Serper head-to-head on the console, and switch default to Serper if quality scores are within 5%.
