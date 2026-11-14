---
name: project-compass-v2-plan
description: "Owner's Compass v2 requirements (tiers 7/12/25+, submit-once batches, closing question, auto-answer) and the approved plan"
metadata:
  node_type: memory
  type: project
  originSessionId: b1c70918-007b-426a-a4d3-6fd76d81c6be
  modified: 2026-11-09T16:34:40.269Z
---

Approved 2026-11-09 on branch `feat/compass-ai-interview`. Plan file: `~/.claude/plans/pasted-content-id-8041-your-task-velvety-adleman.md` (phases P0 to P7).

Owner requirements: user picks a tier FIRST (about 7, hard max 10 even with follow-ups or a confusing user; 12; 25+), then gives the idea; a batch of about 3 questions is shown, the user answers all and submits ONCE, only then the engine processes; the LAST question is always a detailed, project-specific priority question; multi-select plus custom input where several answers can be true (no domain hardcoding); per-question and "decide the rest" auto-answer; enterprise uploads (PDF/DOCX) must drive questions and the Brief; edits and the front end optimised; Brief detailed and correct.

Assumed (to log in DECISIONS): hard caps Quick 10, Standard 15, Thorough 30; closing question stored as `mode="replan"` + `Card.origin="closing"` (no DB migration); Brief detail via `DomainFact.kind/refs`, no new sections.

**Why:** owner testing found a thin Brief, one-card-at-a-time reshuffling of questions, slow generation. See [[feedback-compass-speed-over-cost]] and [[feedback-compass-necessary-questions]].

**How to apply:** check `docs/implementation/DECISIONS.md` (D-106 onward) and `PROGRESS.md` for what is already done before resuming.

**Status (2026-11-09 night):** P0 to P4 and the CALLS, BRIEF and WEB streams are merged on `feat/compass-ai-interview` (tags `compass/v2-p1`, `-p2`, `-p3`; D-106 to D-110; offline 3021 passed). NOT done: live batches (need `LLM_MODE=live`, about USD 2.4 left), engine use of `cmp.decide_answers`, follow-up folding into the plan call, planner prefetch / audit policy / read-preview, panel edits writing facts, document confirm card shown. Branch is not merged to `main` and not pushed.