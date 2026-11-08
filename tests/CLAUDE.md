# tests/ (cross-package)

**Job:** everything that spans packages: `scenarios/`, `e2e/`, `security/`, `integration/`, `live/`, `cassettes/`. Per-package unit tests live in each package's own `tests/`. Full map, metrics and the list of product bugs found: [docs/evals/eval-plan.md](../docs/evals/eval-plan.md).

**Contents**
- `scenarios/`: one YAML per row of the situation catalog (spec section 9, 40 rows), played by `runner.py` (the file format is documented at its top) through the REAL Discover, Research and Spec Builder behind the real gateway in mock mode, the real flow, a fake clock. A meta-test pins the files to the spec table. `known_issue:` variants are strict xfails for product bugs.
- `e2e/`: "hi" to `spec_ready` per archetype (Guided) and two policies (Enterprise), through `Driver` (every journey over `FlowDriver` and over `HttpDriver`), goldens, eval datasets and the live runner's refusal paths and batch logic.
- `security/`: injection suite (9 payloads), pasted secrets everywhere, log redaction, tenant isolation on both stores, spec secrets, repo hygiene scans (`test_repo_hygiene.py`).
- `integration/`: the in-memory store against the persistence contract; whole journeys on PostgreSQL.
- `live/`: tests marked `live`; only the lead or `live-runner` runs them (`just live-smoke`).
- `cassettes/`: recorded provider responses (format notes in `FORMAT.txt`; nothing recorded yet).
- `conftest.py` puts the repository root on `sys.path` (`evals` is importable); the shared harness is `evals/harness`.

**Test:** `uv run pytest tests/scenarios tests/e2e tests/security -q` (offline); one row: `-k SIT-004`; PostgreSQL variants: `uv run python scripts/dev_db.py up`, then `-m integration`.

**Conventions:** see `.claude/rules/tests.md`. Offline unless `live`; no SQLite; never read `.env`; missing cassette fails, never goes live. Secret-looking fixtures carry `# secret-scan: allow` on the SAME line (run the formatter, then check the marker did not move; assign the fixture to a variable first if it did). A test that finds a product bug is a strict `xfail` with `BUG-n` or `GAP-n` in its reason; the package is never patched from here. Never print or assert on a secret value: compare, report the label.

**Owner:** `test-engineer`.
