# Research inputs: nine Compass exports

Three complete real runs (below) and six more test inputs (a second table further down). All nine load and preview at
zero cost in the Research agent; `agents/research/tests/plan/test_nine_inputs.py` keeps that true.

## The three real runs

Three complete outputs of the Compass Context Engine, one per interview tier, for three unrelated ideas. They are the
input the research agent will be built and tested against: each is the **full output of a real session** with the live
model (gpt-6-luna), driven in a browser, every question answered, the Brief approved at Gate 1 with "Approve all" and
saved with the page's own "Download full output" button (the same file `GET /sessions/{id}/export` serves, format 2).

Recorded on 2026-11-10. The runs are kept as they came out of the product, not edited by hand; the limits that were
found in them are in [Known limits](#known-limits).

| File | Tier and size band | Idea | Brief | Interview | Structure |
| --- | --- | --- | --- | --- | --- |
| [quick-yoga-studio-bengaluru.json](quick-yoga-studio-bengaluru.json) | Quick, small (research depth light, 6 searches) | A neighbourhood yoga studio in Indiranagar, Bengaluru: members book classes and buy packs | v8, hash `a621e7b393ac`, 6 features (4 must), 4 roles, 33 facts, 5 research requests | 8 questions | 4 modules, 13 screens, 4 flows, 5 data kinds |
| [standard-produce-marketplace-nashik.json](standard-produce-marketplace-nashik.json) | Standard, medium (standard, 12 searches) | A farm-to-buyer marketplace around Nashik: farmers and farmer groups sell vegetables and grapes in bulk to restaurants and shops, with a local transport partner | v10, hash `aaf318ac34c2`, 9 features (6 must), 9 roles, 4 apps, 45 facts, 7 research requests | 13 questions | 5 modules, 28 screens, 7 flows, 10 data kinds |
| [thorough-freight-fleet-india.json](thorough-freight-fleet-india.json) | Thorough with a requirements document, large (deep, 20 searches plus a gap-fill round) | A freight and fleet company with 400 trucks and branches in six cities: customers book and track loads, dispatchers assign trucks and drivers, drivers update trips, fleet, finance and compliance teams | v13, hash `0e29e718c91b`, 30 features, 18 roles, 152 facts, 20 research requests | 30 questions plus a 56-statement document | 11 modules, 132 screens, 20 flows, 22 data kinds (partly fallback, see limits) |

## Six more test inputs

Added for the Research overhaul (D-154, D-156) to check that intake survives Compass format changes. **They are test
inputs, not product output:** three are real but older exports (format 1, or a trimmed export with no consumer views)
and three are synthetic, hand-made files in format 2 that a person wrote to cover a small, a medium and a big app in
countries the first three do not. The originals stay in [../outputs/](../outputs/); these are copies and must not be
edited by hand. `agents/compass/tests/test_research_inputs.py` checks only the first three runs. What Research does
with each is shown by the zero-cost preview (`GET /api/inputs`, or `uv run pytest agents/research/tests/plan/test_nine_inputs.py`).

| File | Origin and format | Seal (`input_binding`) | Views and structure | Brief | What Research makes of it |
| --- | --- | --- | --- | --- | --- |
| [delhi-restaurant-delivery.json](delhi-restaurant-delivery.json) | real, older, **format 1** | **no approved baseline**: `unverified_no_baseline` (the Brief carries a hash, nobody approved it) | all four consumer views; no structure block | v10, 3 features, 4 roles, 1 app, 53 facts, 12 requests, 2 named references | Small (focused, load 14); 10 topics run, 22 planned and not run; 10 of 12 requests carried by a topic that runs (one planned and not run, one with no topic); English and Hindi; a city and no country; the preview warns that the approval cannot be checked |
| [pune-clinic-appointments.json](pune-clinic-appointments.json) | real, older, **format 1** | approved baseline, hashes agree: `verified` | all four consumer views; no structure block | v10, 9 features, 4 roles, 3 apps, 72 facts, 5 requests | Medium (standard, load 29); 16 topics run; health data and sensitive data turn the trust checks on; English and Hindi; a city and no country |
| [hospital-chain-enterprise.json](hospital-chain-enterprise.json) | real, **format 2, trimmed**: only `export`, `session`, `brief`, `interview` | approved baseline, hashes agree: `verified` | **no consumer views, no structure**; `meta.locales` is a sentence, not codes | v7, 13 features, 10 roles, 1 app, 39 facts, 14 requests, no integrations | Medium (standard, load 27, computed from the Brief); the idea, languages (Marathi, Gujarati, Hindi, English) and geography are built from the Brief; India with a region; health data turns the trust checks on |
| [synthetic-small-bike-repair-porto.json](synthetic-small-bike-repair-porto.json) | synthetic, format 2 | approved baseline: `verified` | views and structure | v1, 7 features, 3 roles, 1 app, 17 facts, 5 requests, 1 integration | Small (focused, load 15); Portugal, `pt-PT` and English |
| [synthetic-medium-school-parents-dakar.json](synthetic-medium-school-parents-dakar.json) | synthetic, format 2 | approved baseline: `verified` | views and structure | v1, 13 features, 3 roles, 2 apps, 19 facts, 8 requests, 2 integrations | Medium (standard, load 28); Senegal, French and English; children's data turns the trust checks on |
| [synthetic-big-site-safety-central-america.json](synthetic-big-site-safety-central-america.json) | synthetic, format 2 | approved baseline: `verified` | views and structure | v1, 29 features, 11 roles, 3 apps, 39 facts, 12 requests, 3 integrations | Big (deep, load 71); **three countries** in one text ("Mexico, Guatemala and Costa Rica": the list is read, Mexico is the primary); Spanish and English |

The three older exports are why Research no longer refuses an export for its format, its approval or its hashes: see
[the Compass contract](../../research-agent/compass-contract.md) for the fields Research reads and what each missing field costs.

## What is in each file

One JSON object with these keys (the reading guide is also inside as `export.reading_guide`):

| Key | What it is | For the research agent |
| --- | --- | --- |
| `consumer_views.research` | **Start here.** Values only (no quotes, no history): `idea`, `geography`, `languages`, `audience`, `references`, `feature_intents`, `glossary`, `research_requests`, `search_hints`, `declined`, `modules`, `entities`, `domain_facts`, and `guidance` | `guidance` says how big the app is (`size_band`: small, medium, large), how deep to go (`depth`: light, standard, deep), the `search_budget`, whether to run a `gap_fill` round, the `max_requests`, the app's `category`, `subcategory`, `business_model`, `families`, and a `focus` list of what is worth looking up (landscape from the modules, domain from the entities, integrations, compliance, references, locales) |
| `consumer_views.blueprint`, `design`, `verify` | What the later engines read: the structure outline and one slice per module, the screens, the journeys with their acceptance lines | context only for research |
| `brief` | The approved Brief with `_hash` and `_section_hashes`: slots as sections, records (`scope` features, `roles`, `apps`, `integrations`), `domain_facts` (typed details with `kind` and `refs`), `assumptions` with their decision, `open_questions`, `research_requests`, `search_hints`, and the structure sections `modules`, `screens`, `flows`, `entities` | the source of truth |
| `structure` | The structure as one block: size band and where it came from, counts, an `outline`, one `slices` entry per module (screens, journeys, data, who may do what, the features served, the detail facts routed to it), the role by module matrix, navigation edges, the `issues` that were left and the research `guidance` | what to research per module |
| `interview` | Every question and answer (`qa`), every fact with its **verbatim quote**, origin and status (`facts`), the rolling summary, the planner batches | evidence: what the person actually said |
| `sources` | The documents read and their claims (only the Thorough run has one) | evidence |
| `brief_versions`, `change_sets`, `session`, `spend` | History, the change log, the session and what the calls cost | audit |
| `export` | format, version, Brief hash, the approved baseline (version and hash), `reading_guide` | check the hash before use |

## Reading order that works

1. Check `export.approved_baseline.hash == brief._hash` (recompute with `mde_compass.domain.hashing.brief_hash`). Research itself reads the result as `input_binding` and researches an unverified Brief anyway.
2. Read `consumer_views.research.guidance`, then the rest of that view.
3. Go to `brief.domain_facts` and `interview.facts` when a statement needs its quote.
4. Use `structure.slices` for per-module detail, and `structure.issues` to see what the checks left.

`agents/compass/tests/test_research_inputs.py` keeps these files honest: format 2, approved, hashes recomputed, the Brief
valid against the current model, the research view equal to what the code builds from the stored Brief, the size band and
depth matching, the structure block agreeing with the Brief, the people and features the person asked for present, and no
secret. Run it with `uv run pytest agents/compass/tests/test_research_inputs.py`.

## Known limits

Found while producing these runs. Most were fixed in the product the same day (commits `d97c269`, `e1cbf31`, `8c43177`,
see D-123 and D-124 in [DECISIONS.md](../../implementation/DECISIONS.md)); the files keep what the product produced at the
time, and the limits that remain are:

* **Thorough run: parts of its structure come from the deterministic fallback.** The session reached the per-session
  budget cap (USD 0.50 then, 1.00 now) before the Brief and its structure were built, so 8 module calls and the probes were
  refused and answered by the fallback (`brief.meta.interview.fallback_count` is 9). The features, roles, facts, quotes,
  constraints, integrations, research requests and guidance are complete and from the model; treat `structure` of this
  file (132 screens, which is more than the model drafts for this idea) as indicative only. The cause of the cost was a
  bug (every model call carried about 190K tokens of nested list history), fixed in `8c43177`; the same session now costs
  a fraction. **Re-record this run** with the fixed code when the live budget allows (about USD 0.2 to 0.3).
* The same bug made the Thorough file 4 MB and the Standard file 1.4 MB (the history of the feature and role lists is
  inside `brief` and `brief_versions`); the content is correct, the size is not what a new run gives.
* **Thorough run: 18 roles with singular and plural copies** (`dispatchers` and `dispatcher`, `drivers` and `driver`), because
  the roles came from the person's answer and from the document under different spellings.
* **Standard run:** `degraded` is true (the design readiness probe found no brand colours or look words), three structure
  errors were left (`screen_entity_access_conflict`: a screen shared by several roles shows data some of them may not
  see), and two features are the same thing under two names (`Online payment` and `Pay for orders online`).
* **"Can wait for the second version" is recorded as a fact but does not always set the feature's priority to `later`**
  (the Quick run keeps reminders and the attendance list as `nice`, the Standard run set live tracking to `later`).
* The planner repeats some questions (payment, "who needs a screen") and, at the end of a long interview, asks standard
  questions about things already answered; the answers here are consistent each time.
* `research_requests` topics written by the model sometimes read awkwardly ("yoga studio app apps").
* Two active facts of the Thorough run (typed answers to standard questions) have an empty `quote`; every other active fact carries its verbatim quote.
* The structure is a first draft for the person to review at Gate 1: the person pressed "Approve all", which accepts every
  proposed part, so none of these structures was reviewed module by module.

## Making a new one

Start the live server yourself, then walk the page and press **Download full output**:

```
! LLM_MODE=live uv run python -m mde_compass --memory --port 8110
```

or replay the structure alone on a saved ledger for about USD 0.02:
`uv run python -m mde_compass.live_structure --replay pune --band medium --mock`. Put the new file here, add it to `RUNS` in
the test, and update the table above.
