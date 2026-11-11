# ADR 0018: The Research agent (`mde_research_agent`): orchestrator-worker with code-verified quotes, bounded by data-defined ceilings

- **Status:** accepted
- **Date:** 2026-11-11
- **Deciders:** lead, with the owner (answers of 2026-11-11: package name, checker model, live-spend ceiling; build prompt 05; D-125 to D-131)

## Context

Compass hands over an approved, hash-bound Brief (export format 2). Spec needs a playbook that tells each of its lanes what comparable apps do, how common and how recent each thing is, and what is uncertain, with every claim traceable to a page. The older `packages/research` is a bounded sequential pipeline that produces one Research Note; it is wired into `mde_flow` and has about 258 tests. The design for the new agent is `docs/architecture-v3/13-research-agent-design.md`.

## Decision

1. **A new package, `mde_research_agent`** (`agents/research/`, D-126), beside Compass. The older package is untouched and not wired in here. Layering: core, contracts and gateway only; a new import-linter contract forbids importing any other agent, app or the older package.
2. **Orchestrator-worker with plain code control.** A director (a pure state machine) runs a fixed pipeline per topic in parallel workers: queries, search, fuse and triage, read, extract, coverage audit, gap analyst follow-ups (distinct queries, at most 2 or 3 per topic), checker, then one assembly call. No agent loops, no frameworks. Every model call is one structured, schema-validated call through the gateway with a versioned spec, a mock handler and a deterministic fallback; every fallback is a counted, flagged failure.
3. **Quotes are verified by code, not by a model.** Every extracted item carries a verbatim quote that must be found in the fetched page text after a defined normal form (NFKC, zero-width removal, quote and dash folding, case fold, decomposed marks, ellipsis-stitched fragments, offset map). Web text is untrusted: no tools on any call that reads it, one `text` field per call, and no URL, action or tool fields in any output schema.
4. **Load from content, not from the tier.** Focused, standard or deep follows from what the Brief contains; RV-04 refuses a result if the tier changed anything. Soft targets and hard limits per class live in data (`ceilings.yaml`); the hard limit is never exceeded (RV-12) and a hard stop commits `partial`.
5. **Output is indexed by the Brief's ids and carries prevalence ("seen in n of N", levels core, common, advanced, emerging, limited evidence with at least 3 sources) and recency**, so Spec can weigh norms instead of copying one page. It is immutable once committed and content-hashed (volatile run data sits in a separate block that is excluded from the hash); rejections by the person are an overlay.
6. **Search behind a port, measured, not assumed.** `SearchPort` with Tavily and Serper adapters (capability maps, `locale_weak`, cost); a live bake-off decides the default, which is data (`providers.yaml`). Fetch is our own `FetchPort` (SSRF checks on every hop, robots, size and time caps, page lock per URL so concurrent topics never orphan atoms).
7. **Determinism.** An `as_of` date, ids assigned after sorting, stage decisions that never depend on completion order, a chaos test under random delays, and cassettes for models (gateway), search and fetch: replaying a recorded run gives the same content hash, and a missing cassette is a hard failure.
8. **Streaming.** `research.event.v1` events are stored with a per-run sequence and streamed over resumable SSE (`Last-Event-ID`); every detail field is on a per-type allowlist (RV-17); a pure reducer folds events into the snapshot a reload reads.
9. **Spend.** Writers use `fast` and `deep`; the `standard` route is reserved for the checker (D-127). Search cost is booked in the shared ledger through the guard. A `research_run` scope kind caps each run; Research has its own configured total; the repo ceiling is raised to USD 8.00 by the owner (D-125).
10. **Front end.** Research screens live in `apps/web/console/agents/research/` as "extras" of the Compass entry; the only server change outside the agent is the Compass CSP `connect-src` naming the Research origin (D-129).

## Alternatives considered

- **Extend `mde_research`:** it is wired into the flow and its tests, and its Research Note has no per-id indexing or prevalence; changing it would move that contract. Rejected; a clean replacement can retire it later.
- **A model-judged quote check:** cheaper to write, but a model can approve a paraphrase; the whole point is that code decides.
- **A tool-using research agent:** unbounded cost and a prompt-injection surface on every page. Rejected by the repo's rules.
- **Tier as the depth input:** couples research cost to a marketing label instead of the idea's actual content (the real exports show a Standard idea with more rule facts than a Thorough one in places).
- **A new `check` route in the contracts:** a breaking contract change for one call; the reserved `standard` route does the same job.

## Consequences

- Spec can trust every item's quote and see how common it is; weak evidence is flagged, not hidden.
- Cost is dominated by search, not by the model; the ceilings and the shared ledger bound it, and a run can end `partial` on purpose.
- Two research packages exist until the older one is retired in a separate task.
- A checker from the same model family as the writers is flagged (`checker_vendor: same`); a different checker is one route setting and one price row.
- English-only labels; intent words for other languages are flagged `reviewed: false` until a native speaker reviews them.
