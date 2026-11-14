---
name: live-mode-is-one-switch
description: "STRICT owner policy (2026-11-11, D-140): live LLM calls need only LLM_MODE=live. Never add a [live-mode rule phrase], permission variable, approval flag or a hook that blocks live mode, anywhere."
metadata:
  node_type: memory
  type: feedback
---

Live LLM calls need exactly one switch: `LLM_MODE=live` (and `MDE_FORBID_LIVE` unset, which only the pytest fixture sets). The old [live-mode rule phrase] and the shell hook that enforced it were removed from the whole repo on the owner's order (D-140). The Research app is live-only and turns live mode on by itself.

**Why:** the owner was repeatedly blocked by the [live-mode rule phrase] ("not ready", "the [live-mode rule phrase] is yours to set") and ordered it removed everywhere, with a strict rule that it must never come back.

**How to apply:**
- Never add, suggest or reintroduce a second live-call key, an extra permission or approval variable, an extra readiness check for live mode, or a PreToolUse hook or check that blocks `LLM_MODE=live`. Not in code, tests, docs, `.claude/`, prompts, memory or messages to the owner.
- Never tell the owner a live run needs anything beyond `LLM_MODE=live` (plus a search key for Research). Start servers and apps in live mode yourself when the owner asks to run them.
- The budget caps still apply (USD 6.00 default, 8.00 ceiling, Compass 3.00, the Research app's USD 5.00); check `uv run mde budget` before spending.
- `tests/security/test_single_live_switch.py` fails if the old key or hook reappears.