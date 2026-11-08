# mde_prompts

**Job:** the registry of call specs C1..C14 plus `S1.web_search`: templates, mock handlers, fixtures. Never touches the network. No prompt text lives anywhere else.

**Layout:** `src/mde_prompts/calls/<call_id>/{spec.py, system.jinja, user.jinja, mock.py, fixtures/}`; a registry module exposes all specs to the gateway (passed in at construction). `scripts/gen_prompt_catalog.py` builds `docs/prompts/prompt-catalog.md` from the specs.

**Test:** `uv run pytest packages/prompts` (every spec: mock handler output validates, converts to strict schema, templates render, untrusted+tools rejected)

**Conventions**
- `CallSpec` itself is defined in `mde_contracts`; this package only instantiates it.
- Bump `version` on any prompt or schema change; keep fixtures beside the spec; see `.claude/rules/prompts.md` and the `add-llm-call` skill.
- Appendix A of the brief is the prompt-writing guide (one job per call, 2 to 3 short examples for C1 and C14 incl. Hindi and Hinglish, extraction: "copy only what the content states; null when unknown").
- Ownership by call id: `discovery-engineer` (C1, C2, C3, C5, C11, C14), `research-engineer` (S1, C6, C7, C8), `builder-engineer` (C9a/b/c, C10, C12, C13). Stay in your own call folders; the registry file is edited by whoever adds a spec, merged by the lead.
