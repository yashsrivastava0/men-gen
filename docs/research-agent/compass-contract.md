# What Research needs from a Compass export

Research never imports Compass. It reads the JSON file a person downloads from Compass ("Download full output"), and it
must keep working when that file changes: a new Compass version, an older export, a trimmed file, a hand-made test
input. This page lists what Research reads, what each field means, which fields are optional and what gets worse
without them. The code is `agents/research/src/mde_research_agent/intake/`: `formats.py` is the **one** place that knows
the layout of an export, `reader.py` turns the Brief inside into a `ResearchInput` and knows no layout, `tolerant.py` is
the set of readers that make a wrong type an empty value.

The rule behind all of it: **Research refuses only a Brief with nothing to research.** A missing approval, a changed
hash, an older format, a missing view or a missing block is researched anyway, and the report says what could not be
checked (`input_quality.input_binding`, `input_quality.flags`).

## What is refused

| Case | Code | Plain reason |
| --- | --- | --- |
| No `brief` mapping in the file | `not_an_export` | This is not a Compass export (it has no Brief). |
| A Brief with no idea, no facts and no research requests | `unusable_brief` | The Brief has no idea, no facts and no research requests, so there is nothing to research. |

Nothing else is refused. In particular these are **not** refusals any more (D-156): format version other than 2, no
`approved_baseline`, a hash that does not match, no `consumer_views.research`. The inputs folder lists a file that makes
the reader fail in an unexpected way as `unreadable_export` with the error's class name (a bug to fix, never reported as
"not a Compass export").

## The seal: `input_binding`

Compass seals an approved Brief with a hash. Research checks three values when they are there: `export.approved_baseline.hash`,
the Brief's own `_hash` and `export.brief_hash`.

| `input_binding` | Meaning | Shown |
| --- | --- | --- |
| `verified` | A baseline hash exists and every hash present agrees. | nothing |
| `unverified_no_baseline` | No `approved_baseline.hash`: Research cannot tell whether the person approved this Brief. | a warning in the preview, `input_quality.input_binding` in `research.json`, a warning (not an error) from validator RV-13 |
| `unverified_hash_mismatch` | There is a baseline but the hashes differ: the Brief may have changed after approval. | the same |

There is no switch and no setting. RV-13 still fails (an error that refuses the commit) when `research.json` carries a
`brief_hash` that is not the hash of the Brief that went in; that is about Research's own output, not about the seal.
If the file has no hash at all, Research computes one from the Brief content (`brief_hash_computed` flag) and the state is
`unverified_no_baseline`.

## Envelope fields (read by `formats.py`)

| Field | Meaning | Optional? | Without it |
| --- | --- | --- | --- |
| `brief` | The Brief (the approved record of what the person asked for) | **required** | refused (`not_an_export`) |
| `export.format_version` | The export format (1, 2, ...) | yes | flag `compass_format_unknown`; any value, including a future one, is read (flag `compass_format_<n>`) |
| `export.approved_baseline.{hash,version}`, `export.brief_hash`, `brief._hash`, `export.brief_version`, `brief.version` | The seal and the Brief version | yes | `input_binding` is unverified; version 0 |
| `export.session_id`, `brief.meta.session_id`, `brief.meta.program_id` | The program id of the run (the caller's wins, then `program_id`, then the session) | yes | the program id is empty and the caller must give one |
| `consumer_views.research` | Compass's own reading of the Brief for Research: `idea`, `geography`, `languages`, `audience`, `glossary`, `declined`, `guidance.focus` | yes | every value is built from the Brief instead (below), flag `research_view_missing`; `guidance.focus` is empty (nothing reads it today) |
| `consumer_views.research.quality` | A quality block (not produced yet) | yes | flag `no_quality_block`; Research derives its own trust flags from the Brief |
| `spend.by_call` | What each Compass model call cost; a call with no tokens failed | yes | `quality.failures` is empty; `structure_source` cannot say whether the structure came from a model |
| `structure.counts`, `brief.modules` | The structure Compass drafted | yes | `structure_source` is `unknown`; the structure is a hint only and never enters the load or a query |
| `consumer_views.{blueprint,design,verify}`, `interview`, `sources`, `session`, `brief_versions`, `change_sets`, `contributors` | Material for later engines and audits | yes | not read |
| any other key, anywhere | A newer Compass | n/a | ignored |

When the research view is missing, the same values come from the Brief:

| Value | From the view | Else from the Brief |
| --- | --- | --- |
| idea | `idea.{summary,category,subcategory,business_model}` | `brief.summary`, `brief.idea.{summary,category,subcategory,business_model}` |
| locale codes | `languages.locales` | `brief.experience.locales`, then `brief.meta.locales`, then `brief.search_hints.languages` |
| search languages | `languages.search_languages` | `brief.search_hints.languages`, then the locale codes |
| geography | `geography` | `brief.context.geo` (always read first when present: it carries the tier and the confidence) |
| audience | `audience.segments` | `brief.audience.segments` |
| glossary terms | `glossary[].term` | `brief.glossary[].term` |
| declined items | `declined[].what` | `brief.declined[].what` |

**Locales may be a sentence.** A list of codes (`en`, `hi`, `pt-PT`, `hi-Latn`) is kept as written. Anything else is
read as prose: written codes (`pt-PT`), language names from `data/languages.yaml` (`Marathi, Gujarati, Hindi and English`
gives `mr`, `gu`, `hi`, `en`) or a bare list of codes (`en, hi and mr`). A two-letter word in a sentence (`in`, `no`, `or`) is never a
code. Flag `locales_parsed_from_text` says it happened.

## Brief fields (read by `reader.py`)

Each section is read tolerantly: a section of the wrong type is empty, a record without its id is skipped, and
unknown keys are ignored. A slot is `{value, tier, confidence, ...}`; a plain value is read as a `default` tier value.

| Section | Fields read | Meaning | Without it |
| --- | --- | --- | --- |
| `idea` | `summary`, `category`, `subcategory`, `business_model` (slots) | what the app is, in the person's words | topics use the first feature names as the category; queries are worse |
| `context.geo` | `{city, region, country}` (slot; a plain string is read as "city, country") | where the app is used | no place: queries carry no place, flag `geo_uncertain` |
| `scope` | per feature: `id`, `name`, `intent`, `priority` (must, nice, later), `scope_status`, `tier` | the features; the load score and the journey topics | the load is lower; fewer journey topics |
| `roles`, `apps` | `id`, `name_in_user_words` or `name`, `goal` / `purpose`, `tier` | who uses it, which apps exist | fewer role topics; app count is 0 |
| `modules`, `entities`, `flows` | `id`, `name`, `apps`/`roles`, `key`, `module`, `role`, `features` | the structure (a hint) | fewer screen, entity and journey topics |
| `integrations`, `references` | `name`, `preference`, `label`, `tier` | named services and named apps | provider topics come from capability signals instead; no reference topics |
| `domain_facts` | `id`, `kind`, `type`, `statement`, `topics`, `entities`, `refs`, `tier`, `quote` | typed details the person gave | rule and compliance topics and the load lose their facts |
| `research_requests` | `id`, `topic`, `question`, `decision_unlocked`, `entity`, `geography`, `source_languages`, `terms`, `references`, `priority`, `shown_to_user_as`, `origin` | what Compass thinks is worth looking up | topics are rebuilt from the Brief (`plan_source: rebuilt`) |
| `search_hints` | `disambiguated_names`, `synonyms`, `local_competitors`, `languages` | words for queries and local names | queries use the category phrase only |
| `data.sensitive` | list of flags (`personal_ids`, `financial`, ...) | sensitive data | trust checks do not turn on from this signal |
| `experience["brand.name"]` | the brand name (slot) | the person's own brand | the brand is still found from facts that say "called ..." |
| `open_questions` | only to detect junk tokens | never a search seed | nothing |
| `meta` | `degraded`, `probes`, `interview.fallback_count`, `locales` | how well Compass did | no degraded flag |

**Research requests.** Compass writes requests (a topic line, a question, the decision it unlocks, usually a long
`entity` text). A request that is filler (repeated words, the same audience list in most of them) or too thin (no named
thing) is **ignored**: the topics are rebuilt from the Brief and the preview says so. Every other request is carried by
exactly **one** planned topic (`plan/routing.py`, no model): a request that names one of the person's reference apps goes
to that app's own topic; otherwise the job is chosen by the words in `request_jobs` in `data/plan.yaml` (a word in the topic
line counts three times as much as one in the question; the words of the app's own category are ignored), and inside the
job the topic that shares the most words with the request, else a topic about the app as a whole (`request_catch_all`).
A request that matches nothing is `unmapped`, one whose topic is planned but ranked below what the size runs is
`not_run`. The preview lists every request with its status and topic. To change where a kind of request goes, change a word
list in the data file.

**Countries.** `context.geo.country` may name several countries ("Mexico, Guatemala and Costa Rica", also with
` y `, ` et `, ` e `, `&`, `/`, `;`). `ResearchInput.geography.countries` lists each one with its alpha-2 code from
`data/countries.yaml` (`null` when the catalog does not know it); `geography.country` and `country_code` are the first one
named (the primary), so a single-country Brief reads exactly as before. The plan carries all of them in
`Place.countries`; the primary one gives the query words and the Tavily `country` filter. A country whose `name` is
`null` in `countries.yaml` (one Tavily's `country` list does not have) is sent without a `country` and the result says the
locale is weak.

**What must never leave in a query.** At intake Research builds the list of private terms from the Brief
(`core/privacy.py`, words in `data/privacy.yaml`): the brand and the names of the person's own business, phone numbers,
e-mail addresses, street-level addresses, the entities of facts typed as a person or as sensitive. The query gate in the
worker refuses a query that contains one (`private_term`, counted, never logged), the search wrapper repeats the check
just before the provider, and validator RV-11 checks every recorded query. A stated city or country is never a private
term. This is the owner's rule of 2026-11-13 and replaces the earlier decision AD-54.

## Adding a new Compass version

1. **Adapter.** Change `intake/formats.py` only: where the envelope, the view or the seal moved, read the new place (keep
   the old one as the fallback). `reader.py` must not learn a key name: `tests/intake/test_formats.py` fails if it names an
   envelope key.
2. **Fixture.** Add one small export (under 60 KB) to `agents/research/tests/fixtures/formats/`, named
   `format<N>_<what>.json`, trimmed from a real file: drop `provenance`, `history`, section hashes and long lists, keep every
   field the tables above name.
3. **Contract test.** Add one row to `EXPECTED` in `agents/research/tests/intake/test_formats.py` (format, binding, load
   class, locale codes, country codes, whether the research view is present). The same file already proves that unknown
   keys are ignored, every optional block may be missing and no section can crash the reader.
4. If the file belongs to the nine inputs, add it to `docs/compass/research-inputs/` and to the table in its README, and to
   `NINE` in `agents/research/tests/plan/test_nine_inputs.py` (expected size class, binding and countries).
5. Update this page if a field was added, moved or made optional.

Run `uv run pytest agents/research/tests/intake agents/research/tests/plan -q`.
