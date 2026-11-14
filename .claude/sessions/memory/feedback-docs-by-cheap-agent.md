---
name: feedback-docs-by-cheap-agent
description: "Owner wants documentation writing delegated to a Haiku or other cheap agent, not done by the main model"
metadata:
  node_type: memory
  type: feedback
  originSessionId: e90a88de-5d81-49b5-b5f9-979c5bd7b48e
  modified: 2026-11-14T10:28:28.313Z
---

For docs work (progress ticks, current-state, README tables, reports), delegate to a Haiku-class or other cheap agent (Agent tool, `model: haiku`, docs-writer type) instead of writing it with the main model.

**Why:** the owner said "For the docs, use the Haiku or cheap agent for that" (2026-11-14, during the Spec agent build) to save cost.

**How to apply:** give the cheap agent a tight brief (files to edit, facts only from git log, DECISIONS and report JSONs, no invented numbers, no code edits) and commit only its docs files. Keep decisions (DECISIONS.md entries) and any numbers that need judgement with the main model. See [[feedback-save-outputs-in-docs]].