---
name: feedback-compass-necessary-questions
description: "Compass should ask only necessary questions; Brief detail comes from inference and approval, not more questions"
metadata:
  node_type: memory
  type: feedback
  originSessionId: b1c70918-007b-426a-a4d3-6fd76d81c6be
  modified: 2026-11-09T16:34:35.943Z
---

Ask only what only the user can answer and what changes what the downstream engine builds. The final Brief is the source of truth for downstream agents, so it must be detailed and logically correct, but that detail must not come from asking more questions: use multi-item cards, the user's own words, uploaded documents, and visible inferred assumptions the user approves at Gate 1. Polluted, contradictory or unrealistic input is corrected with the user's approval.

**Why:** the owner said unnecessary questions are not wanted while the Brief must still be detailed (2026-11-09).

**How to apply:** every planned question must pass a necessity test (serves an open, ask-eligible need above an impact floor, not answerable from facts or inference). Unasked details are drafted as `assumption`-origin facts, never Locked. See [[project-compass-v2-plan]].