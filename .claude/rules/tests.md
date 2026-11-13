---
paths:
  - "tests/**"
  - "packages/*/tests/**"
  - "apps/*/tests/**"
  - "agents/*/tests/**"
  - "evals/**"
---

# Test rules

- **Offline only** unless a test is marked `@pytest.mark.live`. The root autouse fixture forces `LLM_MODE=mock`, removes `OPENAI_API_KEY`, and sets `MDE_FORBID_LIVE=1`; any live attempt outside a `live` test must fail.
- Markers: `integration` (needs the Postgres from `just db-up`), `e2e` (mock mode, through the API), `live` (real LLM, budget-guarded, lead or `live-runner` only).
- **No SQLite**, not even for tests. Integration tests use real Postgres via an ephemeral database per session.
- Never read `.env` or any key value. Tests that scan for secrets compare hashes, never print values.
- Replay tests use cassettes in `tests/cassettes/`; a missing cassette fails with a clear message and never goes live.
- Write the test first for subtle behavior (state machine, gates, budget guard, validators). Use `hypothesis` stateful tests for flow invariants.
- Deterministic: fake clocks, seeded ids, no network, no sleeping on wall clock.
- pytest runs with `--import-mode=importlib`: test modules cannot import each other or be imported by spawned workers, so shared helpers and multi-process workers live in a real package module (`mde_core.testing`, `mde_flow.testing`, `mde_context_api.testing`, ...), D-017.
- Leave no global state changed (logging config, environment, working directory): a test that does makes unrelated tests fail in a full run (D-038). Tests of one package must not depend on what another package has or has not merged (D-053).
- A test that finds a product bug is a strict `xfail` with `BUG-n` or `GAP-n` in its reason; whoever fixes the bug removes the pin in the same commit (a stale pin fails the suite as XPASS). Secret-looking fixtures carry `# secret-scan: allow` on the same line.
- Across a seam between two packages, at least one test runs the real producer into the real consumer, not a fake that shares the consumer's assumption (D-061, ADR 0006); a fake of a port is validated against the real store's contract checks (ADR 0007).
- Coverage targets (>= 85%): `flow`, `llm-gateway`, `spec-loader`, `spec-builder` validators, `discovery` slot/selector code.
