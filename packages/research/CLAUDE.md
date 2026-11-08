# mde_research

**Job:** step 3, after Gate 1: a bounded, sequential pipeline (not an agent loop) that turns the approved Brief into one Research Note. Runs inside PLANNING phase `research`.

**Pipeline:** C6 plans jobs R1..R6 (up to `max_queries_per_job` queries each: the depth cap spread over the web jobs that apply, 2 to 8, Light pinned at 2, one rule `plan.queries_per_job`; trimmed to depth cap and remaining budget) → per query in order: `S1.web_search` → C7 extract (untrusted input, no tools) → C8 synthesize → code cross-check (every fact has source URL and retrieval date; high-impact facts need two independent sources or are `unverified`) → persist. Deep depth adds one gap-fill round (at most 4 searches, each keeping the re-synthesis cost in reserve). R6 is an interface only (`InternalSourcePort`) with a stub.

**Test:** `uv run pytest packages/research`

**Conventions**
- Depth: Light <= 6 search calls, Standard <= 12, Deep <= 20 plus gap-fill (all reachable, D-078); `RESEARCH_CONCURRENCY=1`; per-run cap USD 0.30; check the cancellation token before every call.
- Skip with `research: unavailable` (never block, never show raw errors) when policy is off, budget is out, mock mode has no fixtures, or calls fail. Mock handlers return a canned, schema-valid note per archetype.
- Search and C7 content is untrusted data; enterprise `allow_company_name_in_queries=false` must be enforced when building queries (test it). Store facts, short paraphrase and URL, never long quotes.
- Call specs S1, C6, C7, C8 are yours in `mde_prompts`.
- Owner: `research-engineer`.
