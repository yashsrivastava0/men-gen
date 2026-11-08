# ADR 0010: Research runs after Gate 1, as a bounded sequential pipeline

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (spec revision 3, section 4; implemented by the research engineer, D-046, D-047)

## Context

Revision 3 of the spec moved research out of the interview. Researching during Discover would mean joins, late findings and interruptions mid-conversation, spend on ideas the user abandons, queries written before the planner sees the whole Brief, and the question of whether a company name may leave the building in a search query. A free-roaming research agent was the obvious alternative; the cost evidence the spec cites (agents use about 4 times the tokens of chat, multi-agent research about 15 times) does not fit narrow questions about features, brand, flags and integrations.

## Decision

Research is **step 3**, starts only when Gate 1 approves the Brief, and runs inside the PLANNING state, phase `research`, before the Spec Builder. It is a **fixed pipeline, not an agent loop**, and it never blocks the plan:

1. **C6** plans jobs R1 to R6 in one call (up to `max_queries_per_job` generic queries each, trimmed to the depth cap and the remaining budget; code re-validates the plan and has a deterministic fallback plan). The spec says "at most two queries each"; the number is now derived from the depth, see the note at the end (D-078).
2. For each query, in plan order (`RESEARCH_CONCURRENCY=1` by default): **S1** hosted web search through the gateway, code cleans and screens the result, **C7** extracts facts (untrusted input, no tools), code validates every fact.
3. R6 (internal documents) is an interface only (`InternalSourcePort`, a null stub); internal text never goes to a search provider.
4. A code **cross-check**: every fact has a source URL and a retrieval date; a high-impact fact is `verified` only with two independent registrable domains, otherwise it is marked unverified and never included by default.
5. **C8** synthesizes one Research Note (Deep adds one gap-fill round); code validates and numbers it. The note is stored with its status (`ok`, `partial`, `unavailable`).

**Bounds.** Depth dial: Light (Guided) 6 search calls, Standard 12, Deep (Enterprise) 20 plus a gap-fill round. The Standard and Deep ceilings are reachable (D-078; they were not under D-046, which capped a plan at ten searches). A research run has its own budget scope (`research:<run>`, USD 0.30) inside the session scope. The cancellation token is checked before every call.

**Failure is soft.** Policy off, no budget, a failing provider, a circuit breaker or an empty result produce `unavailable` (or `partial`) with a reason and a plain `Notice`; the build continues from Domain Pack defaults. Raw errors never reach the user.

**How findings reach the user.** Research never edits the spec silently. Each suggestion carries an `applies_to` (a pack feature as `scope.features:<id>`, a spec section, a slot id, or none); what the builder applies is tagged `source: research` and listed at Gate 2 under "What research added" with an include or exclude toggle (a normal change). Queries stay under the privacy rules: with `allow_company_name_in_queries=false` the brand, reference and organisation names never appear in a query or in what the planner sees.

## Alternatives considered

- **Research during the interview.** Rejected for the reasons above (spend on abandoned ideas, vaguer queries, interruptions).
- **A free-roaming research agent.** Several times the cost for questions this narrow, and an unbounded loop around untrusted web content. Rejected; also forbidden by the stack.
- **Parallel searches by default.** Faster, but harder to cap and to reproduce. The concurrency knob exists; the default is 1.
- **Researching the technology stack on the open web.** Feasibility comes from the curated Stack Profile, which is more reliable.

## Consequences

- A longer wait after Gate 1: the flow streams plain-language progress events and offers one Stop button; Light is the Guided default.
- Research-driven additions are first seen at Gate 2, one tap to toggle.
- The budget gate can pause the session before research starts; resuming reuses research that was already done.
- Known limits: a live C8 cannot yet name real pack feature ids (the `available_features` contract field is not added, D-048), so `scope.features:<id>` is reliable only in mock mode; real-model citation quality is unmeasured until live batch L2.

## References

Spec section 4, brief 8.3, [research package](../packages/research.md), [flow-and-gates](../architecture/flow-and-gates.md), DECISIONS D-046, D-047, D-048.

## Update 2026-11-08: the per-job query cap follows the depth (D-078)

D-046 recorded a known limit: with two queries for each of five web jobs a plan could not exceed ten searches, so the Standard (12) and Deep (20) ceilings were enforced but unreachable. That limit is lifted. The number of queries one job may carry is now `clamp(ceil(depth_cap / active_web_jobs), 2, 8)`, with Light pinned at 2: 2 at Light, 3 at Standard and 4 at Deep with all five web jobs, and more when a policy that bars company names leaves only R1, R4 and R5 (Standard 4, Deep 7). One shared function computes it; the engine passes it to C6 as `max_queries_per_job` (C6 version 2) and validates the plan with it, and the deterministic planner, which now has enough distinct, privacy-safe query templates for every job, only reads it. The depth caps themselves, the first-query-of-every-job-first trimming, the research budget scope (USD 0.30, Deep 20 searches is 265,500 micro-dollars at planning cost) and the soft-failure rules are unchanged. The Deep gap-fill round (at most four more searches, then C8 again) now keeps the cost of the re-synthesis in reserve before each gap search. This deviates from the spec's wording in section 4.2 ("at most two queries each") and is logged as D-078.
