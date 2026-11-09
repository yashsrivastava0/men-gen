# ADR 0015: AI-planned interview, Interview Memory and the `domain_facts` Brief section

- **Status:** accepted
- **Date:** 2026-11-09
- **Deciders:** lead, with the owner (build prompt 02; D-090)

## Context

Owner testing found Compass's questions generic: across five unlike ideas (Pune tiffin service, clinic appointments, B2B pharmacy inventory, children's language app, senior ride-sharing) 28 of 30 questions used a wording another idea also got word for word (`docs/compass/evidence/before-first-questions.json`). The cause is structural: the question *list* is chosen by `voi.plan_queue` over 44 slots and 17 frames and the model only rewords the chosen frame (`write_card`); there is no follow-up logic; cards are written one at a time with no view of the interview; and the Brief is built from catalog slots only, so anything the user says that fits no slot is lost. ADR 0013 fixed the Brief as slot-shaped; doc 11 section 12 item 9 said the model never chooses what to do next.

## Decision

1. **The model plans, code governs.** `cmp.plan_questions` returns project-specific batches (modes `initial`, `followup`, `replan`, `deepen`, `simplify`); `cmp.audit_questions` reviews them; code validators decide what may be shown, when to stop and what is stored. The model never controls the flow. This narrows doc 11 section 12 item 9 (see doc 11 section 17, AD-29 amended, AD-30 to AD-32).
2. **Needs Registry.** `catalog/slots.yaml` stays the list of what downstream agents need, and each need gains an `answer_spec` (sufficient answer, aspects, fact types, minimum facts). Per-need status (`satisfied | partial | missing | assumed`) is computed by code from facts and slots; the interpreter reports which aspects an answer covered, and code decides the status.
3. **Interview Memory in Postgres** (migration `0002`, append-only, with `downgrade`): `compass_qa` (question and answer ledger with versions and parents), `compass_facts` (atomic normalized statements with a verbatim quote, `unverified_quote`, supersession, effects on slots; rows are never deleted), `compass_interview_summary`, `compass_batches` (planner runs with an inputs hash for idempotent replans). `compass_questions` (cards shown) and `compass_claims` (document intake) stay; claims are projected into facts. Ports, in-memory fake and Postgres implementation are kept equal by the shared contract suite (ADR 0007).
4. **Slots are a view over facts, through the existing write path.** `SlotStore` stays the single writer of slots. A fact carries `effects` (slot, value, set/add/remove); projecting a fact writes the slot with provenance `ref = FCT-###` and the verified quote. No `Provenance` schema change: a non-empty `ref` already satisfies the evidence rule of ADR 0013. A fact whose quote is not found in its source is projected as `model_knowledge`, so its slot can reach `Default` but never `Locked`. Taps and assumptions are facts too (a tap's quote is the option label; an assumption has no quote), so the ledger is complete. Supersession marks old facts `superseded` or `disputed` and replays the remaining active facts so slot history is kept.
5. **Brief changes (additive; schema version stays 1).** A new list section `domain_facts` (typed facts with provenance and `consumers[]`) joins `SECTION_KEYS`; consumer views carry only the facts tagged for that consumer. An internal `meta.interview` block (counts, coverage, fallback count, question ids) is excluded from the hash, like `meta.approvals`, and from consumer views. **Hash stability:** an empty `domain_facts` is omitted from canonical JSON and from `section_hashes`, `changed_sections` compares the stored and the new hash, and a key absent on both sides means unchanged, and a regression test recomputes the hash of a stored pre-change Brief. `cmp.compile_brief` builds the Brief from the whole active ledger. A fact-coverage check (every active fact is used by a Brief item or marked `not_used` with a reason) is asserted by the full-interview tests (no gate rule); in production leftovers are placed in `domain_facts`, flagged and counted.
6. **A fallback is a failure.** If the planner fails validation after the gateway's one repair, a minimal deterministic card from the Needs Registry is used, flagged on the card (`fallback: true`, `origin: "fallback"`), in the call log (`outcome: fallback`), on the batch record (`used_fallback`) and in `meta.interview.fallback_count`, and counted; live batches must show zero. A rejected question is repaired once or dropped, never replaced by a canned one. Mock handlers produce clearly labelled `[mock]` questions built from the idea's terms.
7. **Transition.** During the build an engine flag (`legacy` or `planner`) keeps the 46 scenarios running on the old path until the planner path passes the same outcome checks; the legacy path is deleted before the work is merged to `main`. It is a scaffold, not a shipped alternative.

## Alternatives considered

- **Keep the frame list and only add follow-ups.** Fixes none of the genericity; the first questions stay the same for every idea.
- **Let the model run the whole interview (an agent loop).** Breaks hard rule 1 of the repo ("plain code controls flow, no agent loops"), cannot be bounded or replayed, and cannot guarantee coverage or budgets.
- **Replace slots by facts everywhere.** Would rewrite the Brief hash, gate blockers, Locked rules, consumer views and every scenario at once; the projector keeps one writer and moves risk to a small, testable module.
- **A `fact_ids` field on `Provenance`.** Changes a strict model that feeds the hash; the FCT id in the existing `ref` carries the same information.

## Consequences

- Easier: questions fit the project; every answer is kept, quoted and traceable; Research, Blueprint and Design receive details that fit no fixed field.
- Harder: more LLM calls per interview (planner, auditor, interpreter, summary, compile), so first-batch latency is measured and bounded; the planner's quality depends on the validators and the auditor; provenance and history views must resolve `FCT-*` refs through the fact's source.
- Work: new store tables and ports, new call folders (`plan_questions`, `audit_questions`, `update_summary`, `compile_brief`, `rephrase_question`; `understand_turn` v2), engine controller, scenario and persona migration, the global front end (`apps/web/`).
- Contracts: `mde_contracts` is untouched. `mde_compass` models gain additive optional fields (`Card`, `CardOption`, new events); ADR 0013 is amended by point 5, not superseded.

## References

doc 11 section 17; AD-29 (amended), AD-30 to AD-32; D-090; ADR 0007, 0012, 0013, 0014; `docs/compass/build-prompts/02-compass-ai-interview-prompt.md`.
