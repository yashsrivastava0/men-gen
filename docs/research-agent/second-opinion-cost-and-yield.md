# Research cost and yield: second opinion (2026-11-11)

Checked against the Small run `rrn_01M75913R4CZSXAA65P7TM0MW9` (events, cassettes, `research.json`) and the code. N=1: one run, one live web day.

## Your findings

| # | Verdict | What the code and the run say |
| --- | --- | --- |
| 1 | Confirmed, one correction | Search is 228,000 of 250,260 micros. Only 4,000 of that is second opinions. The real search waste is round 2: 7 searches (56,000 micros, 22% of the run) that could read **no page**, see A. |
| 2 | Confirmed | 2 of 11 slots. T-09 "covered" has zero required slots. The checker gave 0 `not_supported` in 48 verdicts, so it is unproven, not healthy. The run says `complete` with 4 RV-01 errors. |
| 3 | Confirmed | `place: brief` on every template but three (`data/jobs/*.yaml`), `worker._check` demands the place (`core/anatomy.py`). "Instructor roster … Bengaluru India" returned naukri, indeed and linkedin job ads. Quoted entity plus city returned 0 to 1 hits from **both** providers. |
| 4 | Disproved as stated | T-07 was not dropped by logic. It was starved: `pages_read` hit 40/40 at seq 113 (27 s of 95 s) and `_read_page` returns `None` silently when `try_page()` refuses. razorpay.com was one explainer article, not provider docs. |
| 5 | Confirmed | `run_topic` picks 5 and never backfills. T-09 read 1 page after 4 robots skips. Skips also burn the page ceiling: 7 of the 40. |
| 6 | Half right | Three provider items died on `number_not_in_quote`, but because the quote holds the endpoint sentence while the fees sit in `data.fees` or the claim, not because the pages were resellers. Round 2 **was** right: the analyst proposed "Exotel … API webhook documentation" and "APNs … documentation", and the anatomy gate dropped both for lacking the city. T-02 got no round 2. |
| 7 | Confirmed | 18 of 37 empty (screens 4/4, flows 8/11, local 3/4): a symptom of 3, irrelevant pages. Label bug is `failover.py:81`. RV-01 is `purpose: null` on screens. |

## What you missed

- **A. The 40-page hard ceiling is the binding constraint on Small** (10 topics x 5 pages = 50 > 40, before any round 2). `RunBudget.exhausted()` ignores pages, so the director kept paying for searches nobody could read.
- **B. 7 of the 10 number rejections are not hallucinations.** absoluteyoga.in is double-encoded at the source (`â\x80\x99`, `â\x82¹`). We strip the control bytes and NFKC turns `¹` into `1`, so ₹350 reads "1350". The model was right, the check was right, we lost the only pricing facts of the one covered data topic. A decode repair fixes it. "Keep the fact, drop the number" would have kept a wrong fact here.

## Where I disagree

- Order "relaxed retry, then second opinion" is only cheap when the primary is the cheap provider. On Tavily the retry costs 8x the second opinion. I keep your order, bounded by the existing 15%, and the fix at the source (3) makes it rare.
- Serper cannot be judged by a search bake-off alone: 21 of the 40 pages came from Tavily's own page text, so Serper means every page goes through our fetcher (robots, walls). Only a full run decides, as you said.
- "Drop only the unverified number" is right for the 2 messaging items (sentence or data field), wrong as a general rule. I keep the invariant: no unverified figure survives, and I count salvaged items separately so the rejection rate is not flattered.

## What I do

Order by evidence: A (pages: refund skips, backfill, no paid round without page room, ceiling data), B (decode repair), 3 (region scope per template in data, entity follow-ups exempt), then relaxed retry, salvage, labels, estimate formula. Price last, from two live Small runs. I refuse: batching checks across topics, keyword prefilter, loosening any slot rule (that would flatter "slots filled").
