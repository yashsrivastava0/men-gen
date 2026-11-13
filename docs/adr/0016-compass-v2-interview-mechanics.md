# ADR 0016: Compass v2 interview mechanics: tiers with hard caps, a reserved closing question, submit-once batches, `DomainFact.kind/refs`

- **Status:** accepted
- **Date:** 2026-11-09
- **Deciders:** lead, with the owner (message of 2026-11-09 evening; D-107)

## Context

Owner testing of the AI-planned interview (ADR 0015) found: the Brief is thin for a source of truth; answering one card of a batch makes the engine replace the rest of the batch with new questions (`_drop_answered`, follow-ups inserted first); the tiers are 5 / 12 / 25 with no hard cap and are checked only at the end of a batch (BUG-19: questions that wait are not charged); nothing reserves a last question; features arrive as a single-select card; first questions take 35 to 60 s and the turn that plans the next batch 40 to 60 s; "You decide" on a planner card writes nothing and there is no "decide the rest". The owner wants only the questions that are necessary, a Brief detailed enough for downstream engines, speed first (cost is not a concern; USD 3.00 is the ceiling, D-093).

## Decision

1. **Tiers with hard caps.** The person picks a tier before the idea: Quick target 7 / hard cap 10, Standard 12 / 15, Thorough 25 / 30. The cap counts every question put to the person: base questions, follow-ups, the gap question and the closing question. Batches are 3 / 4 / 5 questions. A pure `Room(target, hard_cap, base, followups, closing_committed)` in `engine/interview_policy.py` is the one place the arithmetic lives: base questions stop at `target - 1`; follow-ups use only the slack (`hard_cap - target`) and never take a base place; `base + followups <= hard_cap - 1`; one place is always kept for the closing question; nothing follows it. A question is charged when it is **planned** (committed = asked plus waiting), which fixes BUG-19.
2. **The closing question** is always last: one detailed, project-specific `priority` card (what matters most for the first version, plus a custom "anything else / what success looks like"), built by planner mode `closing` from a slot snapshot, validated and audited, with a deterministic fallback built from the Brief's own features. It is stored as `QaRecord.mode = "replan"` with `Card.origin = "closing"`: no migration, because `compass_qa.mode` has a database CHECK. `PlanRequest.mode` is a wider `PlanMode` that adds `closing`; stored modes (`PlannerMode`) are unchanged.
3. **Submit once.** The multi-question form is the only layout; single-question batches (closing, gap) use it too. Nothing is processed before the person submits. `BatchAnswer` (batch id validated) gains `decide_rest`, and an item action `none` ("none of these"). `QuestionBatch` gains an optional `progress` (`tier`, `target`, `hard_cap`, `committed`, `closing`). The server still accepts `card_answer` for older clients. Follow-ups are folded into the next batch's single plan call (`PlanRequest.followup_candidates`), not planned one answer at a time. Speed comes from parallel calls and a read-preview cache, never from committing early.
4. **Necessity.** Only what the person alone can answer is asked: each need gets `ask_policy: ask | infer`, a validator rejects a question that serves no open, ask-eligible need above an impact floor, and typical details of unasked needs are drafted as `assumption`-origin facts (capped, never Locked) shown at Gate 1.
5. **Auto-answer.** "You decide" uses the planner's recommended option, else the catalog default, else the new call `cmp.decide_answers` (batch, mock, deterministic fallback); "decide the rest" runs that call once for every open must/should need, then derives the closing answer from the ledger. A user value always beats an assumption.
6. **Brief detail without new sections.** `DomainFact` gains optional `kind` and `refs` (feature, role or app ids), omitted from the dump when empty so every stored hash is unchanged. `compile_brief` v2 runs per section group in parallel, may extend list slots with fact-cited, deduplicated items, and places facts in code (a fact the model forgot counts as unplaced). Superseded and disputed facts and scan findings become `AssumptionRecord`s without a slot (`basis` `correction` or `review`) that the person approves at Gate 1. Probes run after compile and never end as a silent "degraded".

## Alternatives considered

- **New Brief sections (screens, flows, entities) and new `FeatureRecord`/`RoleRecord` fields.** They change the hash of every stored Brief with features and touch four lists that must stay in sync; `kind`/`refs` on the existing facts give the same grouping at no hash cost.
- **Add `closing` to the stored `PlannerMode`.** Needs migration `0003` and a database CHECK change for one label; the card origin carries it.
- **Process each card as it is answered (debounced).** Reproduces the owner's complaint; a read-preview cache gives the speed without committing early.
- **Ask more questions to get detail.** Contradicts the necessity rule and the 7 / 10 cap; inference with approval is cheaper for the person.

## Consequences

- Easier: a bounded interview (provable cap, closing always last), one place for the budget, a richer and honest Brief, faster turns.
- Harder: about 25 scenarios pin the old numbers and `shown_max: 1` and are rewritten (a legacy policy preset keeps the old ones running); `compass_qa.mode` still cannot hold `closing`, so reports must read the card origin.
- Work: `Room` and wire fields first (additive, behavior-neutral), then the engine (lead only: `interview.py`, `interview_policy.py`, `interview_state.py`, `service.py`, `config.py`), with three parallel streams (calls, Brief, web) built against this wire.
- Contracts: `mde_contracts` is untouched; `mde_compass` models gain additive optional fields only.

## References

ADR 0012, 0013, 0015; doc 11 section 17; D-093, D-104, D-105, D-106, D-107; `docs/implementation/PROGRESS.md` (Compass v2).
