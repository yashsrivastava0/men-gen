# mde_discovery

**Job:** the Conversation Engine's Discover step (spec section 3): the five-step turn loop that fills the slot table and ends in the Brief. Code decides what to ask; the LLM only helps with language. Talks to LLMs only through the `GatewayClient` protocol. Full description: `docs/packages/discovery.md`.

**Components:** `IntentRouter` (deterministic fast paths: greeting, "you decide", "finish with defaults", stop/pause/restart words incl. Hindi and Hinglish, starter chips), `Understander` (C1), `BriefInterpreter` (C2), `SlotStore` (spec 3.3 rules, provenance, consistency), `QuestionSelector` (order, preconditions, caps, assumptions), `CardRenderer`, `CardWriter` (C5 fallback), `Responder` (C3), `BriefCompiler` (deterministic rows + C11 sentence), `ChangeInterpreter` (C14), `SafetyScreen`, `PolicyGuard` (banned libraries and disallowed regions of the policy, deterministic, data in `mde_content/data/policy_alternatives.yaml`). `OrgProfileMapper` (Enterprise only: the tenant's Org Profile from `DiscoveryRequest.org_profile` into `assumed` slots with source `org_profile`, mapping in `data/org_profile.yaml`). Shape: `(DiscoveryState, InboundEvent) -> DiscoveryResult` (state and events out, nothing written).

**Test:** `uv run pytest packages/discovery packages/prompts` (offline). Coverage: `--cov=packages/discovery/src`.

**Conventions**
- Greeting is canned (no LLM, 6 starter chips from content). A tap costs 0 LLM calls, typed text 1 (C1), the first idea or an upload 2 (C1 + C2), a question or chat 2 (C1 + C3). Tests assert these numbers.
- `ask_if` uses the safe evaluator in `mde_content.expr` (`==`, `!=`, `in`, `not in`, `and`, `or`, `not`); **never `eval`**. The context is the filled slots plus `sensitive.domains`.
- Caps: 10 cards, 30 turns (Guided only), "Finish with smart defaults". Every LLM call has a deterministic fallback (`llm.FALLBACKS`, named in `spec.fallback`). Secrets are redacted before storage. Sensitive-domain flags create must-confirm compliance cards here, not in research.
- Content is data: no card id, archetype, feature or theme is named in code except the situation cards (`Q-nudge-02`, describe-the-look). Phrase tables are `mde_prompts/calls/c1_understand/lexicon.yaml`; engine copy is `data/copy.yaml` (en, hi, hi-Latn). The Brief rows, sentence pieces and assumption reasons (`reason.<slot id>`) are content-bank `ui_copy` (`questions/shared.yaml`), always in the session language.
- Silence: `UiAction(idle)` on a pending card nudges once (a message beside the same card), then assumes the recommended option; must-confirm, contradiction, company-rule and help cards are never assumed; no pending card or an open gate is a no-op. Company-rule cards (`Q-policy-region`, `Q-policy-lib:<library>`) are built from the policy and the data file, answered only by a tap.
- Call specs C1, C2, C3, C5, C11, C14 live in `mde_prompts` and are yours; the keyword logic (`keyword.py`, `snippets.py`, `template.py`, `rules.py`) is both the mock handler and the engine fallback. Engines never import each other; this package never imports the gateway.
- Org Profile: seeded in `DiscoverEngine.start` only, Enterprise only, always `assumed` (never `confirmed`), screened and policy-checked first, never `idea.*`; must-confirm slots are still asked. Details and the key table: `docs/packages/discovery.md`.
- Test helpers are in `mde_discovery.testing` (importlib mode cannot import sibling test modules).
- Owner: `discovery-engineer`.
