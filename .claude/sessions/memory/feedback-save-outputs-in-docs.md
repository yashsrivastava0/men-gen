---
name: save-outputs-in-docs
description: Owner wants the outputs of agent runs saved in the docs folder, readable without the app
metadata:
  node_type: memory
  type: feedback
---

When the owner has an agent (Research, Compass) run for them, save what it delivered under `docs/<agent>/outputs/<dated folder>/` (the deliverable json, a readable report, a numbers file, an index README), not only in the git-ignored runtime folder.

**Why:** the owner said (2026-11-13) that wherever the agent's output goes, it must also be saved in a docs folder; they read results there, like `docs/compass/outputs/`.

**How to apply:** after each live run copy `research.json`, render the report, write `summary.json`; keep large evidence in `.runtime`. Check `scripts/docs_check.py` still passes. See [[llm-providers-2026-11-13]].