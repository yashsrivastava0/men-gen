---
name: reference-compass-checkpoint-v2
description: Git checkpoint of the finished Compass v2 work (before another AI's speed-v3 work) and how to revert to it
metadata:
  type: reference
---

Checkpoint made 2026-11-10 00:36, at commit `51e0c60` (Compass v2 complete offline: 3021 tests passed, integration 77, security 220; tags `compass/v2-p1`, `-p2`, `-p3`; decisions D-106 to D-110).

- Tag: `checkpoint/compass-v2-before-speed-v3-20261110-0036`
- Backup branch: `backup/compass-v2-20261110-0036`
- Work branch it was cut from: `feat/compass-ai-interview` (not merged to `main`; pushed to origin 2026-11-11)

Revert commands (non-destructive first):
- keep the other AI's work, go back on a new branch: `git switch -c recover/compass-v2 checkpoint/compass-v2-before-speed-v3-20261110-0036`
- see what changed: `git diff checkpoint/compass-v2-before-speed-v3-20261110-0036..HEAD --stat`
- undo their commits but keep history: `git revert --no-commit checkpoint/compass-v2-before-speed-v3-20261110-0036..HEAD`

**Why:** the owner handed a speed/AI-auto-answer/"Approve all" prompt to another AI ("Compass Speed v3", branch `feat/compass-speed-v3`) and wanted a safe point to return to.

**How to apply:** when the owner asks about the checkpoint or wants to revert, give these names and commands; never use `reset --hard` or force-push (GLOBAL_RULES). Related: [[project-compass-v2-plan]].