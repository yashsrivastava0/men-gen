---
name: research-v5-state
description: "Research agent v5 (prompt 06) standalone app: where it is, how the owner runs it, first live run results (Small/yoga, 2026-11-11), what is not verified"
metadata:
  node_type: memory
  type: project
  originSessionId: 91921156-dbc8-4a2d-89be-c1a44314f20e
  modified: 2026-11-11T12:32:19.834Z
---

Branch `feat/research-agent-v5` (tag `pre/research-v5-20261111-1512`), pushed to origin 2026-11-11. App: `scripts/research_app.py start|stop|status|logs|restart`, address http://127.0.0.1:8120/, UI `apps/research-console/`, data `agents/research/.runtime/`, inputs `docs/compass/research-inputs/`. Budget USD 5.00 total (tripwire 4.50), own ledger file per period (shared ledger untouched), gateway cap auto-raised to 6.50.

**First live run (owner, 2026-11-11, Small / yoga Bengaluru):** run `rrn_01M75913R4CZSXAA65P7TM0MW9`, 95 s, USD 0.25 (search about 87% of it), status complete. 5 of 8 acceptance rows FAIL: slots filled 18% (target 70), first-try quote pass 81% (90), number rejection 17% (3), searches contributing 54% (60), norms with 3+ sources 0% (70). 7 of 10 topics ended `not_found` even where items were verified (the slot rules want e.g. 3 competitors with 5+ features each, and only 2 pages were read for 5 queries). Place words ("Bengaluru India") are appended to payment/SMS provider queries; one payments topic read zero pages; an Instagram reel counted as a "blog" source. Medium and Big not run yet. Budget left about USD 4.75.

**Why:** the owner tests it alone from a front end with live models; the runbook `docs/runbooks/research-agent.md` has their 10-step checklist.

**How to apply:** the app is live-only and turns live mode on by itself: start it with `uv run python scripts/research_app.py start` (no switches). It spends only when the owner clicks Run and confirms. API reads of that run need headers `Authorization: Bearer dev-token`, `X-Tenant-Id: demo` (not `dev`, which gives 404), `X-User-Id: dev`. Run folders hold cassettes, so slot/assembly changes can be replayed offline. See [[live-mode-is-one-switch]].