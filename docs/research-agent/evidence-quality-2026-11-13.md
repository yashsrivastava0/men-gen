# Research agent: evidence quality, what was measured and what changed (2026-11-13)

Plain words, with numbers. Decisions D-159, D-160, D-165. The raw results are in
[reports/evidence-quality-results-2026-11-13.json](reports/evidence-quality-results-2026-11-13.json). Every number below
comes from the Small yoga run F of 2026-11-11 (`rrn_01M75KFZTD99D80M21DM5BZWKQ`) or from the seven recorded runs, and every
live check cost model calls only (no search, no page fetch): USD 0.20 in all.

## The problem: "partial" turns usable facts into unusable ones

The checker says `supported`, `partial` or `not_supported` for every fact. Assembly calls a fact `unverified` when none of
its atoms was judged `supported`, and Spec may never use an unverified fact. On the seven recorded runs 30 to 41% of the facts
were unverified, nearly all of them because of a `partial` verdict.

| Kind of fact | Atoms judged partial (7 runs) |
| --- | --- |
| workflow | 82% |
| service option (provider facts) | 75% |
| competitor | 64% |
| role pattern | 62% |
| screen pattern | 38% |
| rule | 3% |
| compliance note | 8% |

So the loss is in exactly the structured kinds Spec needs for integrations, flows and screens. Two findings explain it:

1. **94% of the partial atoms (198 of 211) already had data fields that the checker said the page does not state**, and code
   had already removed those fields. The fact was cleaned but stayed flagged partial.
2. **A plain second question is not the answer.** When the checker was asked again about 20 earlier partials, 7 came back
   `supported` and 13 stayed partial: the verdict is noisy, so re-asking until it passes would only sample the noise.

## What changed

* **Checker v3** (`calls/check.py`). The claim's sentences are shown numbered, the item's country and company are shown, and
  the checker names the sentences the page does not support. A passage about another country or company is `not_supported`.
  The old v2 checker is kept as `SPEC_V2` so any experiment can compare.
* **Tighten, then ask once more** (`engine/checking.py`, `tighten_and_recheck`). For a partial fact that lost something
  (sentences the checker named, and/or data fields it named that code nulled), code removes exactly that (it only removes
  words, it never adds any) and the same checker is asked once more about what is left. The fact becomes `supported` only if the
  checker then says so with nothing more to null and every number is still in the quote; otherwise the original claim is
  restored and the fact stays partial. A partial with nothing removed is never asked again. What was dropped is kept in the
  evidence file (`claim_original`, `dropped_sentences`) and the item carries the flag `tightened`.
* **Screens required slot** (`core/slots.py`, `data/jobs/screens.yaml`). `values_of` read a screen state `{state, content}` as
  empty, so the Screens slot could never fill. Fixed, and the required part is what pages really state (three patterns with
  three actions); states stay a wanted, optional slot. Replaying the recorded atoms: filled in 4 of 7 runs (0 of 7 before).

## Does the checker catch planted errors? (the test the owner asked for)

`python -m mde_research_agent.eval planted`: 40 facts the checker had called supported, plus topped-up facts with a price, a
period or a qualifier; eight kinds of planted error (number changed, company swapped, condition flipped, country swapped,
unsupported attribute added, currency swapped, period changed, qualifier dropped). The old test could not fail a checker that
says "partial" to everything; the fixed one reports how many clean facts are flagged and a `discrimination` score, and fails a
degenerate checker. Run F, v3 checker, USD 0.008:

| Measure | Result | Bar |
| --- | --- | --- |
| Planted errors caught (code and checker) | 98.9% | 90% |
| Every gated kind | 100% | 80% |
| Clean facts rejected as not supported | 0% | 5% |
| Clean facts flagged on a re-check | 7% | (reported) |
| Discrimination (caught minus flagged) | 0.92 | 0.30 |
| Planted errors that climbed after tightening | 0 of 31 tried | none |

The v2 checker on the same sample (before the test's region-swap flaw was fixed): 89.9%, with 79% on company swaps. The
currency, period and qualifier kinds had only 1, 3 and 6 samples on this run, so they are reported but not gated; a run with
more prices (the Medium run) will test them.

## Does tightening help real facts? (re-extraction on the stored pages, three independent repeats)

`python -m mde_research_agent.eval reextract`: the extraction model is asked again about every topic and page that gave facts
(36 pairs; 6 topics of the run could not be rebuilt and are skipped), the same code checks quotes and numbers, and each checker
arm judges the same extraction. Share of facts (items) with no supported atom, which assembly calls unverified:

| Arm | Unverified items, range over 3 repeats | Median | Partial atoms (median) |
| --- | --- | --- | --- |
| A: v2 checker, no tightening (what the recorded runs did) | 23% to 29% | 26% | 28% |
| A3: v3 checker, no tightening | 21% to 28% | 25% | 27% |
| B: v3 checker, tighten and ask once more | 10% to 16% | 14.6% | 16% |

The ranges of B do not overlap the others; v3 alone changes nothing (its gain is the catch rate above). Per kind (partial
atoms over 3 repeats, A then B): screen patterns 31 of 82 then 11 of 84, workflows 11 of 40 then 8 of 40, role patterns 11 of
14 then 4 of 13, provider facts 10 of 26 then 6 of 26, competitors 28 of 29 then 19 of 29. Competitors stay mostly partial.

## What is not proven

* One run, one Brief (yoga, Bengaluru). The Medium and Big runs and the new-country runs will show whether the gain holds.
* The 14.6% is measured on re-extracted facts, not on a full run; the full live runs will report their own.
* Quote-first extraction (one fact, one quote, shorter claims) was designed (D-159) and not built: tightening reached the
  target on this sample, and competitors are the remaining weak kind. Provider facts will be extracted atomically by the new
  `provider_fact` kind instead (see the as-built note).
* The checker and the writer are the same model (the gateway has one vendor and one priced model). The planted-error test
  shows no need for a second one today.
