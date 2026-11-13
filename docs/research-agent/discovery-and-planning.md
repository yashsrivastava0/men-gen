# Discovery and planning (research-depth P4)

Status: built 2026-11-13 on branch `feat/research-depth`; decisions D-156 to D-166 (D-161 privacy guard, D-162 stay on
`research.v5`, D-163 ceilings and the both-engines rule are binding). Offline only: nothing here was run live. The package
page is `docs/packages/research-agent.md`; the plan this implements is the overhaul plan, phase P4.

This note says what the planner keeps, how a run asks for official pages, how it follows up on a provider, what the second
search engine is for, how the authority registries work, and how numbers are compared across languages. Every number named
below is data (`agents/research/src/mde_research_agent/data/`), not code.

## 1. The planner keeps what the Brief asks for

**What went wrong.** `_trim` gave every job one topic by evicting the lowest-ranked kept topic. On the Big input two provider
topics (priority 88) and two reference topics (85) were evicted for `trust/data_protection_law` (priority 45, no required slot).
Medium ran 16 topics and left 41 unrun, Big 26 and 39: the tracking and accounting providers and every "what a X record holds"
topic were among them. Medium and Big were not deeper than Small.

**What it does now** (`plan/build.py`, `data/ceilings.yaml`, `data/plan.yaml`, `data/jobs/*.yaml`):

* **Risk classes stay outside the cap.** A topic with `risk: regulation | integration | identity | data_protection | payments`
  is never evicted by the topic cap. The risk class comes from the template (`services/providers` is `integration`,
  `trust/data_protection_law` is `data_protection`, `trust/payment_security` is `payments`), from `plan.yaml risk_by_need` (a
  payments provider is `payments`, an identity provider `identity`, a government-portal provider `regulation`), and for rule
  topics only from evidence (`risk_from_candidate`): a rule topic that a compliance fact or a Brief request about rules
  produced is `regulation`; a topic from a loose fact tag is an ordinary topic under the cap.
* **The cap applies to the rest.** `soft.targets` is 12 (Small), 24 (Medium), 38 (Big). `risk_topics` (5, 18, 36) is only a safety
  valve for a Brief that asks for far too many: a risk topic past it competes under the cap like any other. The valve is set so
  that cap plus valve still gets about 1.6 round-one searches a topic from the 70% pool the director spends first.
* **A job keeps its best topic only when that topic has a required slot.** A job whose best topic has nothing required (trust)
  no longer pushes a researched topic out.
* **Brief value ranks the rest.** An entity the Brief names (and that is not a generic scaffold entity) gets `brief_entity: +12`,
  so "what a X record holds" outranks navigation, roles and module screens. Regulation and provider topics already rank high.
* **Ceilings (D-163).** Searches hard/soft: Small 40/28, Medium 96/64, Big 170/120. Hard pages: 64, 130, 230. Time ceilings are
  unchanged; a run past its soft seconds goes on only while the last round filled at least `min_round_gain` (10%) of what was
  still missing (round one: of all required slots), up to the hard seconds (`Director._time_stop`).
* **Pages in round one.** With 40 or 70 topics, every topic reading five pages would use the hard ceiling in round one: each topic
  now gets an equal share of 75% of the hard pages, never below `pages_per_topic.min` (`worker.pages_wanted`).
* **A named reference with no public footprint** (`plan.yaml named_reference_templates`, today `features:references`) that has been
  searched in two formulations and found nothing ends `not_found` (reason `no_public_footprint`) and leaves the required-slot gate
  (`TargetState.gate_excluded`: its slots stop counting in `slots_required`). It stays a gap in the output.
* **The preview lists what is not run, with its cost.** Each not-run topic has `est_searches` and `est_usd_micros`
  (`plan.yaml topic_cost`: 2.2 searches and 0.02 USD, measured on the seven recorded runs), the cap it lost to, plus
  `not_run_cost` (the sum) and `risk_topics` (by class).

### Topic counts (zero-cost preview of the nine inputs)

| Input | Size | Topics run, before | Not run, before | Topics run, after | Not run, after | of which risk |
| --- | --- | --- | --- | --- | --- | --- |
| delhi-restaurant-delivery | Small | 10 | 22 | 17 | 15 | 6 |
| quick-yoga-studio-bengaluru | Small | 10 | 32 | 15 | 27 | 3 |
| synthetic-small-bike-repair-porto | Small | 10 | 33 | 17 | 26 | 6 |
| pune-clinic-appointments | Medium | 16 | 27 | 34 | 9 | 10 |
| standard-produce-marketplace-nashik | Medium | 16 | 42 | 34 | 24 | 10 |
| hospital-chain-enterprise | Medium | 16 | 29 | 38 | 7 | 14 |
| synthetic-medium-school-parents-dakar | Medium | 16 | 40 | 36 | 20 | 12 |
| thorough-freight-fleet-india | Big | 26 | 40 | 54 | 12 | 16 |
| synthetic-big-site-safety-central-america | Big | 26 | 40 | 69 | 13 | 31 |

Reading it: Medium now runs about 34 to 38 topics (about 24 under the cap plus the topics of a risk class), Big 54 to 69. At about
2.2 searches a topic that is 75 to 84 searches for Medium (hard 96) and 119 to 152 for Big (hard 170); the three-country Big input is the
largest because its regulation and provider topics are expanded per country; the director's round-one
pool (70% of the hard searches) then gives each topic about 1.7 searches. If the owner wants a smaller Medium or Big, change
`soft.targets` or `risk_topics` in `ceilings.yaml`: one line each.

## 2. Official-first discovery

Authority domains in `data/authority/*.yaml` used to classify a page after the search; they were never sent to a provider. A
topic flagged `official_first: true` in the job data (provider documentation, rules, data-protection law, payment security, store
disclosure) now asks for its official pages by name in round one.

* **One variant, never an extra.** The `official` query variant REPLACES one descriptive variant, so the searches of a topic do
  not grow (`calls/queries.py`, `deterministic_variants`; `worker.write_variants` keeps it through the `[: want + 1]` trim and
  puts the deterministic one in place when the model wrote none).
* **Domains are chosen by code** (`core/sources.official_domains_for`): the registry entries of the topic's country, its bloc
  (the EU file applies to every member state) and `global.yaml` whose `topics:` keywords all appear in the topic's words (label,
  question, anchors, terms), country's own domains first, at most 20. A platform topic names its owner's domain
  (`domain_restrict`). The model never chooses a domain.
* **Tavily** gets `include_domains` with `include_domains_mode: prefer`. **Serper** has no prefer mode: it gets a `site:`-restricted
  query only on topics of a risk class (provider, rule and regulation topics; an empty answer is relaxed by the failover) or
  topics that name one owner domain. Elsewhere it gets the plain variant.
* **Language.** The variant is written in the language the country's authorities write in (`language:` in the authority file,
  else the country's main language in `countries.yaml`): `fr` for Senegal, `pt`, `es`. Descriptive variants and global vendor
  documentation stay English. `QueryIn.language`, `official_intent` (the intent words of that language, `intent_words.yaml`, now
  with `fr`, `pt`, `es`) and `official_domains` carry it to the model; the deterministic builder writes the same variant.
  Claims stay English with source-language quotes (the extraction prompt is the P5 owner's). The call spec `rsch.write_queries`
  is version 2.
* RV-14 needs no change: the official variant has an anchor, the place and an intent word of its language.

## 3. A provider's own site

`EntityRecord.domain` was set only when the page an entity was found on was already its own site, and `Target.domain_restrict`
was never read for providers. After round one (`Director._provider_followups`), for each discovered provider a services topic
needs, up to `provider_followups` (Small 3, Medium 5, Big 8, `ceilings.yaml`):

1. the provider's domain is resolved from the domains the searches returned, most often returned first, with `own_site` matched
   against **every** entity of the run (`Director.resolve_entity_domains`), not the topic's own entity (a provider topic has none);
2. three queries (`plan.yaml entity_followup_words`: documentation, pricing, help) are sent with the domain restricted, read as
   part of the topic that found the provider (up to 5 pages, 5 per domain), covered topics included;
3. every search and page goes through `try_search` and `try_page`, so the follow-up counts against the ceilings.

A provider whose domain was not found gets no follow-up. The gap analyst's follow-ups are unchanged and now see more entity
domains. A page on a discovered provider's own domain is classified `official_inferred` (section 5).

## 4. Both engines on vendor topics

`engines: both` in the job data (the providers template) plus `plan.yaml vendor_needs` (payments, messaging, maps and location,
tracking, identity, accounting). After the first query of such a topic in round one, the worker sends the same plain query to
the OTHER configured provider (`RunContext.second_search`, the failover's second port, wired in `compose.py` and
`ResearchService`) and fuses the lists with `core/rrf.fuse_engines`: each engine's lists are fused and averaged over the
variants that engine was asked, then the engines' scores are added, so a page both engines return scores about twice a page one
returns and agreement counts whatever the number of variants. Tavily stays primary for page text; a Serper hit has no text, so
its URL goes through the fetcher.

* It is **one search against the ceiling and two providers' cost**: the pair is admitted once (`try_search` of the primary),
  `RunBudget.add_second_search_cost` books the second provider's cost under its own name and counts `second_searches`.
* No new `SearchRequest` field and no change of `search_key` (D-163): the second query is a plain request; its cassettes are
  written to `cassettes/second/`, a folder of their own, because a request is keyed without its provider.
* A failure of the second engine is counted (`search_second`) and ignored; the first engine's result stands.
* **Replay.** A recorded run's second-engine queries are in `cassettes/second/`; the replay path in `live_runs.py` does not read
  them yet, so a replay of a run that used both engines runs without the second list.

## 5. Authority registries and inferred official sources

Seeded on 2026-11-13: `pt`, `sn`, `mx`, `gt`, `cr`, `eu` and more India sources. Every domain was requested with a plain page
fetch and/or found by a web search restricted to that domain, and had to name itself as the body. Each entry records:

* `fetch: ok | blocked | unresolved`: `ok` our plain reader gets the page; `blocked` a real official site whose pages our reader
  cannot get (403, an incomplete TLS chain, an empty shell); `unresolved` the name did not resolve for the reader although pages of
  the domain are in the search index. A blocked or unresolved domain still counts as official, but its page is read only when the
  search provider returned the text (`core/sources.is_walled`, used by the triage): our fetcher never tries it.
* `verified_on: 2026-11-13` (the loader refuses a non-`ok` entry without a date) and a per-file `language`.

Rejected: `arai.in` (TLS error; the Automotive Research Association of India publishes at `araiindia.com`, which is listed
instead). The owner's list of 2026-11-13 is otherwise all present; the statuses are in the files. These are seed lists to be
reviewed (open item OI-18); they hold domains, never claims.

**Pattern inference** (`data/source_classes.yaml official_suffix_patterns`, `core/sources.classify_source`): a host whose public
suffix looks like a government (`gov`, `gov.*`, `gob.*`, `gouv.*`, `govt.*`, `go.*`), an education body (`edu`, `edu.*`, `ac.*`,
`nic.in`) or an intergovernmental one (`europa.eu`, `int`) that is not in the registry is `official_inferred`, and so is a
provider's own domain for facts about itself, matched against the entities of the run. A pattern with a country code does not
count for another country's topic (a Brazilian ministry is not an official source on an Indian rule). Code decides the class, never
the model. `official_inferred` counts as `official` in the evidence ladder and in confidence (`evidence: official` keeps its
meaning) and stays visible as the source class in `sources[]`, so Spec and the owner can tell it from a registry match.

## 6. Numbers across languages (RV-18)

`core/numbers.py` with `core/currencies.py` and the data files `currencies.yaml` and `number_words.yaml`:

* **Decimal comma**: `12,50 EUR` is 12.5 and `2,5%` is 2.5 (one or two digits after a comma, or four or more); `5,000` is still a
  thousands comma; `1.234,56` and `1.234.567` are read as written in pt and es.
* **Space thousands**: a thin, no-break, narrow no-break or plain space between groups of exactly three digits after one to three
  leading digits (`5 000`, `1 234 567`, `1 234,56`). `2024 500` is two numbers (the old check joined it into 2024500).
* **Currency**: a currency written next to an amount is a figure of its own (`c:INR`). When a claim states a currency, the quote
  must state the same one: `$350` is not backed by `₹350`, `EUR` not by `USD`, a claim currency the quote omits is a mismatch.
  An ambiguous token agrees with every currency it can mean: `$` with USD or MXN, `FCFA` with XOF and XAF. A token with no number
  beside it is not an amount. The rejection is `number_not_in_quote`, with `Rejected.detail = currency_mismatch` counted apart in
  the run's internal counters.
* **Typed values**: `amount`, `currency`, `period` and `unit` are numeric fields (so a typed value cannot bypass RV-18; an amount
  is a string, an int or a `Decimal`, never a float), and the `currency` of a typed value is grounded against the quote.
* **Number words** of English, French, Spanish and Portuguese in a quote satisfy a digit; a claim is asked only for English words.

## 7. Multi-country Briefs

`scope: country_each` (or `per_country: true` on a template that keeps its own breadth) expands a regulation or provider
topic once per country the Brief names, for the best `per_country_max` (3) candidates; the rest stay one shared topic. Each per-country topic carries
its country's code, name and language, so the search filter, the place words, the authority domains and the official variant's
language are that country's. A topic that carries a Brief request is never spread (a request is carried by one topic). Norms,
design, screens and flows stay shared.

## What this does not do

* No live run: the numbers above are plans, not measurements. Whether the official variant and the own-site follow-up raise
  official facts is for the first live runs.
* The replay path does not read `cassettes/second/` yet (section 4).
* The authority lists are seed lists; `fetch` is what one plain request saw on 2026-11-13 and can change.
