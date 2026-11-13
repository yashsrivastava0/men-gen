# spec-agent

Package `mde_spec_agent` (`agents/spec/`). The third engine: it turns an approved Compass Brief (and Research) into one static Spec Pack for the front-end engine, and carries the standalone Spec Bench. Imports only `mde_core`, `mde_contracts`, `mde_llm_gateway`, `mde_spec_core` and `mde_compass_intake`; never another agent, `mde_flow`, `context-api` or `web` (contract `spec-agent-isolated`). Verify is loaded through the entry point `mde.spec.verify`, never imported.

- **Run** (`run/`): file store with single-flight per program, dense resumable events (`spec.event.v1`), checkpoint keys that carry versions and the route fingerprint, patches (a killed run resumes to identical bytes), the orchestrator (stages, gates G1.5 and G2, partial reasons), adaptive concurrency computed from the profile, the code steps and the pipeline.
- **Money** (`spend/`): the hard cap USD 5.00 (`SPEC_LIVE_BUDGET_USD`, tripwire `SPEC_TRIPWIRE_USD` 4.50, launch environment only), integer micro-USD, a file lock, reserve-before-call, fail-closed accounting, `POST /api/stop`, `scripts/spec_spend_check.py`. Every Spec and Verify call passes one wrapper.
- **Calls** (`calls/`, `lanes/`): the call runner (one re-ask per node, deterministic fallback, budget stops propagate) and the Product, Design and Screens lanes.
- **Derive** (`derive/`): facts, detail profile, R-INV inventory, release planner (pull-in rule, consent), seed graph.
- **Bench** (`bench/`, `views/`, `service.py`, `api/`): input discovery and auto-select, view data, the HTTP API ([api/spec-agent](../api/spec-agent.md)), `scripts/spec_app.py`. Front end: `apps/spec-bench/`.

Run: `just spec-dev` (or `scripts/spec_app.py start`), `uv run python -m mde_spec_agent bench auto|run`. Test: `just spec-test`.
