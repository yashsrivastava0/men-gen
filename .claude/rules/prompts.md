---
paths:
  - "packages/prompts/**"
  - "docs/prompts/**"
---

# Call-spec and prompt rules

- One job per call. Every call is a `CallSpec` in `packages/prompts/src/mde_prompts/calls/<call_id>/` with `spec.py`, `system.jinja`, `user.jinja`, `mock.py`, `fixtures/`.
- **Bump `version`** on any change to a prompt template, an input/output model or a schema. Keep golden fixtures next to the spec and regenerate the catalog (`uv run python scripts/gen_prompt_catalog.py`).
- Every spec ships a **mock handler** (`mock_handler` dotted path) returning deterministic, schema-valid output derived from the input, so the whole product runs offline with no key.
- Every spec has a **deterministic fallback** name that the owning engine registers (bank card, canned reply, template).
- Never inline prompt text in an engine or in Python; templates are `.jinja` only.
- Fields that carry web, upload or other re-read text go in `untrusted_inputs`; such a spec **must not enable tools** (hard error in the gateway).
- Always set `max_output_tokens` (it includes reasoning tokens). Pass only the context the call needs (slots, last few turns, rolling summary), never whole transcripts.
- Field descriptions in output models are part of the prompt: short, concrete, with allowed values. Output models must convert to OpenAI strict schema (no open objects, shallow nesting).
- Appendix A of `docs/implementation/BRIEF.md` is the prompt-writing guide.
