"""Spec proof tools (docs/architecture-v3/16, 17 section 10.4, 18 section 3.4, 19 section 7).

Each tool has a pure core, a runner that takes the guarded `CallRunner`, a deterministic offline stub (the call's mock
handler) and a JSON report writer. The tools only REPORT numbers against `spec-config.yaml` `proof:`; nothing here
declares a decision Proven. Live runs happen only when the lead sets `LLM_MODE=live` in the environment; this package
never sets it and never reads a key.
"""
