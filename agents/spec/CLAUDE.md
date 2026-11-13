# mde_spec_agent

**Job:** run Spec (intake, plan, Product/Design/Screens lanes, assemble, gates), the resumable run store, the money wrapper (hard cap USD 5.00), the HTTP API and the Spec Bench backend (port 8130). Standalone: imports no other agent and nothing from `mde_flow`/`context-api`/`web`. Verify is injected through the `mde.spec.verify` entry point, never imported.

**Test:** `uv run pytest agents/spec -m "not integration and not live"`
