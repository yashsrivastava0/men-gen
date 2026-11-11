# Build prompt 04: Compass changes that Research needs (CR-1 to CR-3)

- **Task id:** `prompt-04`. **Status:** ready, not started. Written 2026-11-11 by the planning chat from [doc 13 v3 §15](../architecture/13-research-agent-design.md) and the Compass author's corrections ([notes/compass-as-built-2026-11-10.md](../notes/compass-as-built-2026-11-10.md) §8b).
- **Note (doc 13 v3):** Research no longer reads Compass's tier or `guidance.size_band/depth/search_budget/max_requests`; it computes its own research load from the Brief's content. Nothing in this task needs those fields to change.
- **Small task.** It changes Compass's `research` consumer view and the meta that feeds it. It does not change interview behavior. The owner decides when and how this reaches the builder; Research (prompt 03) does not wait for it.
- **No live calls.** Tests use mock or replay. Never set `ALLOW_LIVE_LLM`. Never read `.env`.
- Follow `prompts/00-global-rules.md` (baseline tag, small Conventional Commits, footers `Task: prompt-04`, `Refs: AD-44, AD-47`; local `main` only after tests pass; no push).

## Goal
Make the Compass output honest and complete enough for Research to trust it: say which parts came from a model and which from deterministic fallbacks, count every failure, and tag how much each value can be trusted.

## Changes
**CR-1. `quality` block in `consumer_views.research`** (values only, no quotes):
- `seed_source`: `model` or `template` (template when `seed_research` timed out, was refused or fell back).
- `failures`: the **true failure count**: every call that timed out, returned 0 tokens, was refused by a budget cap, or fell back to deterministic output, with the call names. **`meta.interview.fallback_count` must count timeouts and 0-token calls too** (today Quick's `seed_research` and `scan_ambiguity` took about 60 s with 0 tokens and `fallback_count` is 0).
- `structure_source`: `model`, `mixed` or `fallback` (Thorough's structure was mostly fallback).
- `degraded` and `probes`; **reconcile `meta.probes.design` with `readiness.design`** (Standard shows `probes.design: false` and `readiness.design: pass`).
- `closing_priority_applied`: whether the closing ranking set feature priorities (Thorough has 26 of 30 features `nice`).

**CR-2. Tier tags in the research view.** On features, roles, apps, geography, audience, integrations, glossary, references, entities and `domain_facts`, add `tier` (`locked`, `default`, `open`). Still no quotes, history or provenance.

**CR-3. Request origin and codes.** On each `research_request`, add `origin` (`model` or `template`). Add ISO 3166-1 country codes and BCP 47 language codes to `geography` and `languages` in the view (keep the existing fields).

## Out of scope
Interview behavior, question planning, the structure drafter, the Brief page, the other fixes in STATE's not-done list (role and feature dedupe, junk `open_questions`, priority from the closing ranking, Locked geography rule). Those are separate hardening work.

## Acceptance (offline)
- A mock run where `seed_research` times out yields `seed_source: template`, `failures` listing it, and `fallback_count` greater than 0.
- A Brief with a failing design probe shows the same value in `meta.probes.design`, `readiness.design` and `quality.probes`.
- The research view carries `tier` on every item listed in CR-2 and no quote text (the existing no-leak tests still pass).
- `agents/compass/tests/test_research_inputs.py` still passes after the three exports are regenerated or the test is updated to the new view shape (say which in the report). Hashes: the view is not part of the Brief hash; confirm that the Brief hash is unchanged.
- The Compass test suite passes in mock and replay mode.

## Report
Branch, commits, tests with counts, the exact new view shape (one printed example per tier, secrets removed), anything not done, and revert commands per global rules §A.10.
