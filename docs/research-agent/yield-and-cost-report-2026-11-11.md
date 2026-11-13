# Research agent: yield and cost report for the owner (2026-11-11)

Plain words. Decisions D-142 to D-153; the one-page opinion is `second-opinion-cost-and-yield.md`. Same Brief every time: the Small yoga studio, Bengaluru. Money below is what the spend record booked (the ledger, which is the truth).

## What was wrong, in order of size

1. **The agent ran out of pages, quietly.** Small allowed 40 pages, ten topics wanted five each. By second 27 of 95 the pages were gone: the payments topic never read one, and round two paid 7 searches (22% of the run) that could read nothing. The run still said "complete". Refused pages (robots, no text) were counted as read.
2. **Seven of the ten "invented number" rejections were not invented.** One studio site publishes broken text (a rupee sign arrives as garbage), our cleaner turned 350 rupees into 1350, and the number check correctly refused every price. We now repair that kind of text exactly.
3. **The city was on every query.** "Instructor roster Bengaluru India" returned job ads. The same rule threw away the best follow-ups ("Exotel API documentation") because they had no city.
4. **Quoted searches asked for the whole sentence.** `"Mindbody" features pricing ...` was sent as one exact phrase, so both providers returned 0 or 1 result.

## What changed

Pages: refunded when unreadable, a spare takes their place, no paid round without pages, an honest `partial` when pages run out, Small ceiling 32/64. Text repair. Region scope per topic in data files (none for flows, screens and structure, country for providers and competitors, city for local pricing). Quoted phrases stay quoted. One relaxed retry before a paid second opinion, and every paid provider is named in the books. A provider item that has one unverified fee keeps its verified facts and loses only the fee (flagged `figures_dropped`, counted apart). Fields the checker empties no longer break the schema. Two false validation errors (RV-21) and a lost checker batch were fixed. A quality-gate tool compares two runs.

## Before and after (live, Small)

| Run | Search | Status | Required slots | Items | Cost USD | Cost per slot filled USD | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Before | Tavily | complete | 2 of 11 | 42 | 0.250 | 0.125 | 95 |
| A | Serper | partial (false RV-21, fixed) | 8 of 10 | 108 | 0.075 | 0.009 | 246 |
| B | Serper | complete | 3 of 10 | 114 | 0.068 | 0.023 | 204 |
| C | Tavily | partial (hit the search and page ceilings in round 3) | 8 of 10 | 113 | 0.316 | 0.040 | 195 |
| F | Tavily, final code | complete | 6 of 10 | 93 | 0.289 | 0.048 | 200 |
| D | Serper, final code | complete | 5 of 10 | 81 | 0.058 | 0.012 | 235 |
| E | Serper, final code | partial (fetch timeouts) | 2 of 10 | 40 | 0.038 | 0.019 | 270 |

Number rejections fell from 10 to 0 to 6 per run, and the figures kept by the repair are counted separately. The checker said "not supported" on under 1% of items in every run: that is unproven, not healthy.

## The gate, honestly

Against the old run the gate **passes** slots filled, items per slot and number rejection, and **fails** official share (38% to 11-17%) and the floor test (2 to 4 of 16 old strong pairs come back). Two reasons, neither is a loss I can show. The old "official" items were 16 screen patterns read from two app-store pages and one marketing domain, a lucky set; the new runs read other pages. And two runs of the same new code fail the gate against each other (slots 8 and 3, floor test 0.44 to 0.47). One run per side cannot judge this gate; it needs several runs per side and ranges. I did not tune anything to make the numbers pass.

## Search provider: Tavily stays

Serper is 4 to 8 times cheaper per run and cheaper per slot in every run, but its yield was not at least Tavily's (Tavily 8 and 6; Serper 8, 3, 5, 2). With Serper every page goes through our own fetcher; in run E 79 of 83 fetches timed out. Your rule was "adopt only if at least as many slots", so `settings.json` says `tavily`. Serper stays the failover and the bounded second opinion. Note: the choice in the Tools page applies only after the app restarts (the page now says so).

## What I refused

Batching checks across topics, a keyword prefilter, a persistent cache for now, any change to the slot rules, and "keep the fact, drop the number" as a general rule (it would have kept a wrong fact here). The repair is limited to provider and competitor items (`data/figure_drop.yaml`). The cost estimate formula is not done: measured runs now override the table, and it is a small follow-up.

## Cost of this task

USD 0.85 of live spend on top of the first run (USD 1.10 of 5.00 booked, 3.90 left). Tavily cost went up per run (0.29 to 0.32 against 0.25) because the agent now uses its budget instead of wasting it: it reads more pages and runs its rounds. Cost per slot filled fell by two thirds.

## Still needs a Medium or Big run

The Medium and Big ceilings were not run with the new code (their page ceilings already satisfy the new rule). A Serper run needs more time than a Tavily run (205 to 270 s against 195 s, hard limit 330 s for Small), so Medium on Serper would need a time check. The run's own cost is 1 to 6% under the ledger when a model call fails or is repaired. RV-17 flags an event whose label equals a quote of 31+ characters. Making the provider choice apply per run is a request for the front end builder.
