# Search provider facts: Tavily and Serper

What was checked for the two `SearchPort` adapters (`agents/research/src/mde_research_agent/adapters/search/tavily.py`
and `serper.py`), where, and what could not be confirmed. Opened on **2026-11-11**. No live call was made and no key was
used: everything below comes from public web pages.

Tags: **[O]** read on the provider's own official page. **[T]** third-party only (blogs, wrappers, forum posts); treat as
a lead, not a fact. **[U]** not found anywhere reliable.

How the pages were read: the Tavily OpenAPI page, credits page and rate-limits page were read as raw page text (Exa
fetch) and the OpenAPI text was read to its end. The serper.dev home page was read through a summarising fetch tool (the
page is 110 KB), plus a raw read of its first part; the pricing numbers were then matched against three independent 2026
reports that quote the same table. Serper has no public API reference (see below).

## Tavily

Sources (all **[O]**):

- Search reference and OpenAPI spec: <https://docs.tavily.com/documentation/api-reference/endpoint/search>
- Credits and pricing: <https://docs.tavily.com/documentation/api-credits>
- Rate limits: <https://docs.tavily.com/documentation/rate-limits>
- Best practices (cross-check of parameter wording): <https://docs.tavily.com/documentation/best-practices/best-practices-search>

### Request

| Fact | Value | Tag | Used by the adapter |
| --- | --- | --- | --- |
| Endpoint | `POST https://api.tavily.com/search`, JSON body, only `query` required | O | yes |
| Auth | HTTP bearer: `Authorization: Bearer tvly-...` (an invalid key is a 401) | O | header built per request |
| `search_depth` | `basic` (default), `fast`, `ultra-fast`, `advanced` | O | `basic`; constructor option |
| `topic` | `general` (default), `news`, `finance` | O | always `general` |
| `max_results` | integer, default 10, min 0, max 20 (the best-practices page says default 5; we always send a value) | O | clamped to 1..20 |
| `country` | lowercase country NAME from a fixed enum (about 190 names, e.g. `india`, `united arab emirates`); "boost", not a restriction; "available only if topic is `general`" | O | name looked up in `countries.yaml` |
| `language` | ISO 639-1 code (`en`, `fr`, `zh-cn`) or English name (`english`); by default only boosts matching-language results | O | ISO code sent |
| `filter_by_language` | boolean; strictly drops non-matching results; requires `language`, else 400; can return few or no results | O | opt-in constructor flag, off by default |
| `include_domains` | array, max 300 | O | yes |
| `exclude_domains` | array, max 150 | O | not used |
| `include_domains_mode` | `restrict` (default, hard filter) or `prefer` (also searches the wider web); sending it without `include_domains` is a 400 | O | yes, both modes native |
| `exact_match` | boolean; only results containing the quoted phrase(s) in the query; "wrap target phrases in quotes"; the best-practices page adds that it may return fewer or empty results | O | `exact_match` plus the query quoted |
| `time_range` | `day/week/month/year` or `d/w/m/y` | O | not used (see `start_date`) |
| `start_date`, `end_date` | `YYYY-MM-DD`; results with no detectable date are kept unless `filter_by_published_date` is true | O | `start_date` = today minus N calendar months |
| `include_published_date` | boolean, beta; adds `published_date` (best estimate, may be "last updated"); on automatically for `topic: news` | O | always `true` |
| `filter_by_published_date` | boolean; also removes undated results | O | never sent (undated sources are kept) |
| `include_raw_content` | `false`, `true`/`markdown`, or `text` ("plain text ... may increase latency") | O | `text` when `want_content` |
| `max_age_hours` | -1..720; "no extra credits"; non-paying plans may get 434 under load | O | never sent |
| `include_usage` | boolean; adds `usage: {credits: N}` to the answer | O | always `true`; `usage.credits` is booked in the credit ledger |
| `auto_parameters` | may silently switch to `advanced` (2 credits) | O | never sent |

### Response

`200` body (official example): `query`, `answer` (only with `include_answer`), `images`, `results[]`, `response_time`,
`usage` (only with `include_usage`), `request_id`. Each result: `title`, `url`, `content` (snippet), `score` (float),
`raw_content` (only when requested), `published_date` (string or null; the official example is RFC 2822,
`Tue, 11 Mar 2025 17:00:00 GMT`), `favicon`, `images`, `id`. **[O]**

### Errors

| Status | Official meaning | Adapter kind |
| --- | --- | --- |
| 400 | invalid request | `bad_request` |
| 401 | API key wrong or missing (`{"detail": {"error": "..."}}`) | `auth` |
| 422 | validation failed; `detail` is a list of `{loc, msg, type, input, ctx}` (`input` echoes request values) | `bad_request` (only `msg` is read) |
| 429 | excessive requests; `Retry-After` header = seconds, "when present" | `rate_limited`, `retry_after` from the header |
| 432 | key limit or plan limit exceeded | `quota` |
| 433 | pay-as-you-go limit exceeded | `quota` |
| 434 | flex-tier capacity (only when `max_age_hours` is set) | `rate_limited` (never expected: not sent) |
| 500 | server error | `http_error` |

All **[O]**. Any other 4xx/5xx and any 3xx becomes `http_error` with the status; redirects are never followed.

### Credits and price

- Search cost: `basic`, `fast`, `ultra-fast` = **1 credit**; `advanced` = **2 credits**. The spec states it in the
  `search_depth` text; the credits page lists only basic and advanced. **[O]**
- Raw content: the credits page says "your search depth determines the cost" and lists no surcharge for
  `include_raw_content`; the adapter books 0 extra credits. This is an absence of evidence: **[U]** whether a surcharge
  exists. Check the first live invoice.
- Price per credit **[O]**: pay as you go USD **0.008**; Project (4,000 credits, USD 30) 0.0075; Bootstrap (15,000, USD
  100) 0.0067; Startup (38,000, USD 220) 0.0058; Growth (100,000, USD 500) 0.005; free Researcher plan 1,000 credits per
  month. `provider_prices.yaml` books USD 0.008 = 8,000 micros per credit (the highest rate, so the ledger never
  under-counts).
- Rate limit **[O]**: per organisation, shared by all keys: free plan 100 requests per minute; pay-as-you-go and
  Project/Bootstrap/Startup 1,000; Growth and Scale 1,500; every request counts once whatever its parameters.

### Design assumptions versus the docs

All four assumptions of the design (doc 13) are confirmed by the official spec, now **[O]** (they were **[V]** from search
excerpts before): `language` and `filter_by_language` exist; `include_domains_mode` (`restrict`/`prefer`) exists, so
prefer is native and needs no emulation; `exact_match` exists (the quotes are still required). One correction: `country` is a
closed list of names, so a country outside `countries.yaml` (and Tavily's list, for example Hong Kong) cannot be sent and
the result is `locale_weak`.

### Not verified (Tavily)

- Whether `include_raw_content` costs extra credits (see above).
- The maximum query length (the pages opened do not state one); an over-long query would come back as a 400.
- Local-language quality of `country`/`language` boosting: only a live bake-off can say.
- The exact `published_date` formats beyond the one RFC 2822 example; the parser also accepts ISO dates and relative text.
- Which plan the owner's key is on (limits and per-credit price depend on it).

## Serper

Sources: <https://serper.dev> **[O]** (home page: pricing table, FAQ, free allowance, sample response). Serper has **no
public API reference**: `https://serper.dev/playground` asks for a login, `https://serper.dev/api-docs` does not exist,
`docs.serper.dev` does not resolve. Everything about the request format below is **[T]** from wrappers, MCP servers and
tutorials (LiteLLM's Serper page, `garylab/serper-mcp-server`, NativePort, webscraping.cc, GitHub code that calls the API).

### Request

| Fact | Value | Tag |
| --- | --- | --- |
| Endpoint | `POST https://google.serper.dev/search`, JSON body | T (many consistent sources; not on serper.dev's public pages) |
| Auth | header `X-API-KEY: <key>` | T |
| Body | `q` (required), `gl` (country, lowercase alpha-2), `hl` (language), `num`, `page` (1-based), `autocorrect` (boolean), `tbs` (`qdr:h`, `qdr:d`, `qdr:w`, `qdr:m`, `qdr:y`), `location` | T |
| `num` maximum | 100 | T; Google stopped serving 100 results per page in September 2025, so what `num` > 10 returns today is **[U]** |
| Response | `searchParameters` (echo), `organic[]` with `title`, `link`, `snippet`, `position`, optional `date`, `sitelinks`; also `knowledgeGraph`, `answerBox`, `peopleAlsoAsk`, `relatedSearches`; top-level `credits` (integer credits charged) | T |
| `organic[].date` | free text from Google: `Mar 10, 2022`, `3 days ago`; localised when `hl` is not English; absent for most results | T (the home page sample shows `"date": "Mar 10, 2022"` **[O]**) |
| Domain restriction | no parameter; use Google's `site:` operator in `q` | T (standard Google syntax) |
| Native domain "prefer" | none | U |
| Page text | none; links and snippets only | O (sample response) |

### Credits and price

- Pricing **[O]** (home page, "excluding sales tax/VAT/GST"): Starter USD 50 for 50k credits (USD 1.00 per 1,000), Standard
  USD 375 / 500k (0.75), Scale USD 1,250 / 2.5M (0.50), Ultimate USD 3,750 / 12.5M (0.30). Credits valid 6 months. New
  accounts get 2,500 free queries, no card. Credits are deducted "every time you receive successful responses"; at zero
  balance new queries are refused. Refund within 7 days if under 20% used.
- Rate limits **[O]**: 50 queries per second (Starter), 100 (Standard), 200 (Scale), 300 (Ultimate); more on request.
- One credit per query (up to 10 results): implied by the table (credits equal queries) and stated by third parties; the
  page never says it in words. **[T]**
- `num` above 10 costs 2 credits: **[T]** only (a Hacker News comment of May 2025 and 2026 pricing blogs). It is **not on
  serper.dev**. The adapter books 2 credits for `num` > 10 unless the response carries its own `credits` count, which
  then wins (`credits` is **[T]**, seen in several published sample responses).
- `provider_prices.yaml` books USD 1.00 per 1,000 = 1,000 micros per credit (the Starter rate, the highest). Free-tier
  calls are cheaper than booked; override with `cost_per_call_micros=0` if the ledger should not count them.

### Errors

Serper's error statuses are not documented on its public pages **[U]**. Observed in third-party code and guides **[T]**:
an exhausted balance answers with a body whose message is `Not enough credits` (status reported as 400, 403 or 429 in
different sources); a bad key is 401 or 403 (`Unauthorized.`); an invalid body is 400. The adapter therefore:

- classifies any 400/402/403 (and a 429 that mentions credits) whose message says "not enough credits" as `quota`,
  whatever the status;
- 401/403 otherwise as `auth`; 429 as `rate_limited`; 400/413/422 as `bad_request`; other statuses as `http_error`.
- `Retry-After`: Serper does not document it **[U]**; the adapter reads the header (seconds or HTTP date) when present.

### Design assumptions versus the docs

The design had Serper "entirely unverified". Now: pricing, free allowance, rate limits and the page-text limitation are
**[O]**; the endpoint, key header and parameters are **[T]** only. `gl` and `hl` exist as assumed.

### Not verified (Serper)

- Every request parameter and the endpoint, against an official source (needs a logged-in look at the playground).
- Credits for `num` > 10, and whether `num` > 10 returns more than 10 results today.
- The HTTP status for an exhausted balance and for a bad key; the shape of 429 and whether it has `Retry-After`.
- That the `credits` field is always present in the answer.
- Whether `autocorrect: false` is accepted (the adapter sends it only for exact-match queries).
- Date text for non-English `hl` (unparseable text becomes `published = None`, never a guess).

## How the adapters apply this

| `SearchRequest` | Tavily | Serper |
| --- | --- | --- |
| `country` ("in") | `country: "india"` from `countries.yaml`; unknown code: not sent, `locale_weak` | `gl: "in"`; not a 2-letter code: not sent, `locale_weak` |
| `language` ("hi") | `language: "hi"` (boost); `filter_by_language` only if the adapter was built with it; not ISO 639-1 (for example `hi-Latn`): not sent, `locale_weak` | `hl: "hi"` (`zh-cn` sent as `zh-CN`); not ISO 639-1: not sent, `locale_weak` |
| `domain_mode` restrict | `include_domains` + mode `restrict` (max 300) | `(site:a.com OR site:b.com) query` (max 10 domains) |
| `domain_mode` prefer | `include_domains` + mode `prefer` | not supported, nothing sent, `applied` has no `domain_mode` (capability `domain_prefer=False`) |
| `exact` | `exact_match: true`; the quoted phrase(s) of the query stay as written, else the whole query is quoted | the same quoting, `autocorrect: false` |
| `recency_months` N | `start_date` = today minus N calendar months (exact for any N) | `tbs=qdr:m` for N <= 1, else `qdr:y`; `applied["recency"]` says `1m` or `12m` (2 to 11 months becomes 12, coarser; above 12 becomes 12, narrower) |
| `want_content` | `include_raw_content: "text"`, text in `SearchHit.content` | ignored (`page_content=False`) |
| `max_results` | clamped 1..20 | `num`, clamped 1..100; hits trimmed to `max_results` |
| cost (`cost_micros`) | 8,000 per call at `basic` (16,000 at `advanced`) | 1,000 per call up to 10 results, 2,000 above (or the response's `credits`) |

`SearchResult.applied` reports what was really sent. Keys: `country`, `language`, `domain_mode`, `domains`, `exact`,
`recency`. Tavily reports the country name (`india`), Serper the code (`in`). A requested key that is missing from
`applied` was not applied; `locale_weak` is only about country and language.

Ranks are contiguous over the kept hits (hits without a URL are dropped before numbering). Dates: ISO, RFC 2822,
`Mar 12, 2025`, `12 March 2025` and relative text (`3 days ago`, measured from the injected `today`); anything else,
or a date more than a day in the future, is `None`.

## To check in the first live bake-off

1. Serper: send one call with `gl`, `hl`, `tbs`, `num=10` and `autocorrect=false` and confirm no 400; read the `credits`
   field; send one with `num=20` and compare `credits` and the number of `organic` hits.
2. Serper: with a deliberately wrong key and, if possible, an empty balance, record the real statuses and bodies.
3. Tavily: compare the credits reported (`include_usage`) for `basic` with and without `include_raw_content: "text"`.
4. Tavily: confirm that `country` plus `language` plus `include_domains_mode: prefer` is accepted together.
