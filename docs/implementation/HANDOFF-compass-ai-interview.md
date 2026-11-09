# Handoff: Compass AI-planned interview (task `compass-ai-interview`)

Written 2026-11-09 at the end of a long session, for the next AI session. Read this first, then `docs/implementation/PROGRESS.md` (section "Compass: AI-planned interview"), `docs/implementation/DECISIONS.md` (D-090 to D-105) and `docs/adr/0015-ai-planned-interview-and-interview-memory.md`. The build prompt is `docs/compass/build-prompts/02-compass-ai-interview-prompt.md`. Design: `docs/architecture-v3/11-discovery-agent-design.md` section 17.

## 1. What the task is

Mobigen's Context Engine has a Compass agent (import `mde_compass`, folder `agents/compass`) that interviews the user and writes a Brief. The owner found its questions generic (same questions for a food app and a clinic app; the model only reworded a fixed list of 44 slots / 17 frames). Goal: the LLM plans project-specific question batches, follow-ups come from the answers, every Q&A becomes a stored fact (Interview Memory), and the Brief is compiled from the whole fact ledger. Principle: the model proposes, code governs (validators, stop rules, storage, budgets). A fallback is a failure (flagged, counted, must be 0 live).

## 2. Where things are (git)

- Branch `feat/compass-ai-interview` (about 95 commits ahead of `main`). NOT merged, NOT pushed (owner chose local only). Last commit at the end of this session: `8c364eb feat(compass): faster first questions and a plain-language wait hint`.
- Annotated tags: `compass/ai-interview-p0, p0b, p1, p2, p3, p4, p5, p6, p6b, p7-flip`; safe points `pre/compass-ai-interview-20261109-1237`, `pre/compass-ai-interview-restructure-20261109-1243`, `pre/compass-ai-interview-flip-20261109`.
- One extra commit on the branch by the owner side: `67f8805 feat(web): optimize agent static frontends with Claude flight recorder` (dark theme, in-memory HTTP/SSE "Flight Recorder", "Copy for Claude Code" diagnostics button). Do not remove it.
- Merge when the owner agrees: `git switch main && git merge --no-ff feat/compass-ai-interview`. Undo: `git revert -m 1 <merge>`. Recover pre-flip state: `git switch -c recover/pre-flip pre/compass-ai-interview-flip-20261109`.
- All helper worktrees were removed (`git worktree list` shows only the main checkout).
- Commit footer convention: `Task: compass-ai-interview`, `Phase: n`, then `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never force-push, never `reset --hard`.

## 3. What was built (phases 0 to 7)

- 0/0b: docs (GLOBAL_RULES, doc 11 s17, ADR 0015, AD-29..32), restructure `packages/compass` -> `agents/compass`, UI -> global `apps/web/`.
- 1/2: Interview Memory (Postgres tables `compass_qa`, `compass_facts`, `compass_interview_summary`, `compass_batches`, Compass migration `0002`, DELETE-blocking trigger on facts; in-memory twin; shared contract suite), pure engine modules (quotes, facts, fact_projector, needs, terms, question_validators, context_pack, interview_policy), Needs Registry (`catalog/slots.yaml` with `answer_spec` for 44 needs).
- 3: calls under `agents/compass/src/mde_compass/calls/`: `plan_questions`, `audit_questions`, `understand_turn` v2, `update_summary`, `compile_brief`, `rephrase_question` (17 profiles in `config.CALL_PROFILES`).
- 4: engine: `engine/interview.py` (InterviewController), `interview_compile.py`, `question_pipeline.py`, `service.py` wiring; round 0 as two chained turns (playback, then internal `prepare_questions`); follow-ups, replan, stop rules, budgets (Quick 5 / Standard 12 / Thorough 25), edit, pivot, mid-interview upload, help ("I do not understand" / "I do not know"), Q&A list route `GET /sessions/{id}/qa`, `domain_facts` + `meta.interview` in the Brief (hash-stable).
- 5: UI in `apps/web/console/agents/compass/` (groups, answer-all form with live layout switch, why-popover, Ask me more, Q&A editor, MOCK banner, fallback badge).
- 6: scenarios on both engines, SB-QP-01..21, property tests, metrics. 16 known defects are pinned as strict xfail (BUG-19..28, GAP-1..7; BUG-20 was fixed later; see D-103).
- 6b: `python -m mde_compass.live_batches --batch E|F|G [--dry-run|--mock|--replay] [--only id,id] [--allowance-usd X]`, spend read from the gateway ledger (`ledger_spend.py`).
- 7 (flip): `InterviewPolicy.engine` default is now `"planner"`. `"legacy"` remains as a rollback (`COMPASS_INTERVIEW_ENGINE=legacy`); the legacy path is NOT deleted yet (Phase 8).

## 4. Budget and live-call rules (important)

- Compass ceiling USD 3.00 (stop at 2.85, per session 0.50, reserve 0.30; D-093). The repo-wide gateway cap is USD 6.00. The owner said: budget max USD 4, aim under 3, do not worry about cost. Live spend so far about USD 0.55 (ledger `data/.budget/ledger.jsonl`; `uv run mde budget`).
- A live call needs `LLM_MODE=live` AND `ALLOW_LIVE_LLM=1` in the process environment (hard rule 3, D-069: the second key is the owner's decision). In the finished session the owner explicitly and repeatedly authorised live calls and a settings entry was used; later the owner asked to remove the key and deleted/restored `.claude`. In a NEW session the variable is probably unset: if live testing is wanted, ask the owner to set it (or run commands with the `!` prefix). Do not look for a way around the hook `.claude/hooks/guard_live.py`. Never read `.env`.
- The ledger exists (`budget_cli init` was run). Cassettes: none are committed; recorded ones were deleted because prompts changed. Replay tests skip when no cassettes exist.

## 5. Live findings (D-104, D-105) and fixes already committed

1. `plan_questions` at effort high hit its timeout every time -> every session fell back. Now effort low, 5000 tokens, route standard, 90 s. Also `understand_turn` low (60 s), `compile_brief` low, `seed_research` medium.
2. A single slip voided whole batches (priority out of range, bad `recommended`, options on free types, too many groups, empty follow-up, over-long pitch). Now tolerated/cleaned.
3. Planner asked mostly free text; prompt now asks about two in three `choose_one`/`pick_many` with three options.
4. Same-need near-duplicates inside one batch: `same_need_similarity_min` 0.6.
5. Document claims close the need named by `slot_hint` (aspects `slot:*`).
6. Latency: repair round only when fewer than `want_min` questions survived (`repair_only_when_short`). First batch about 35 s (was 61 to 73 s). The 10 s target is NOT met. Scenarios can override policy via `policy:` in YAML (SB-QP-13 keeps the repair round).
7. UI: the line under the typing dots is now visible with staged plain-language messages (6/18/45/90 s), en and hi strings added.
8. Fixes before live: BUG-25 (validation context gets document evidence and hints), BUG-26 (card answers list up to 16 other open needs; confident quoted side-updates close their need), "finish with defaults" and "that's enough" now stop the planner interview, layout switch re-sends the batch, logging-state leak in property tests fixed.

Live results: batch E 23/23 checks, 0 fallbacks, project-term share 1.0, cross-idea overlap 0.0998. Batch F (two interviews, 25 to 27 Q&A, 16 and 33 minutes) reached Brief review, coverage 100%, quotes verified 100%, needle in Brief, pivot ok. Batch G: PRD 17 claims -> 17 verified facts; Hinglish 12 Q&A in Hindi. Re-run of F after the later fixes was NOT done (about 50 minutes, about USD 0.25).

## 6. Open items (not fixed)

- Design probe often fails after two rounds -> Brief marked degraded (3 of 4 interviews).
- 1 to 3 repeated questions in long interviews; interviews overrun budget (25 to 27 vs 25; BUG-19).
- Live model missed one contradiction (pharmacy), both facts stay active.
- One Locked slot (`idea.category`) cited non-active facts after a pivot (consider a post-pass that downgrades Locked slots whose facts are not all active and verified).
- `consumer_views_clean` batch check reports quotes in consumer views; likely slot values equal to the user's own words, check needs refining.
- `fallback_count` still 1 to 7 per session in F/G before the profile fixes; re-measure.
- Pinned xfails: BUG-19, 21, 22, 23, 24, 27 (a side message while a question is on screen loses the pending question; a re-show fix broke the Hinglish persona and was reverted), BUG-28 (safety screen declines any idea mentioning "games"), GAP-1 (mock planner re-proposes asked needs), GAP-2 scenario stale (the feature exists), GAP-3 (no either/or card after a contradiction), GAP-4, GAP-5, GAP-7.
- Hinglish reaches the planner as `hi`, never `hi-Latn`.
- Phase 8 not done: delete the legacy path (pre-tag first; also port or delete 4 `engines: [legacy]` scenarios and the `LEGACY_ONLY` set), run `just ci`, final report, merge `--no-ff`, push commands are only listed (no push).
- `just ci` fails at its repo-wide `typecheck` step on pre-existing pyright errors in test files (about 298; D-092) and a Windows-only msvcrt test; not caused by this task. `ruff`, `lint-imports` (7 kept), `pyright agents/compass/src apps/web/tests`, `tests/security` (220) all pass.
- Known doc stale spot: some docs still describe BUG-20 as open; DECISIONS D-103/D-105 are authoritative.

## 7. Running things

- PATH: `export PATH="$HOME/Library/Python/3.9/bin:$HOME/.local/bin:$PATH"` (uv is not on the default PATH). Use `uv run --offline ...`.
- Offline tests (about 1 minute): `uv run --offline pytest agents/compass apps/web/tests -q -p no:logging -m "not integration and not live"` -> 2601 passed, 3 skipped, 15 xfailed at the last run. Integration (private Postgres 54329 must be up): `-m integration` -> 77 passed. E2E 221 passed.
- Do not run `tests/live` with the key set (the live smoke test would spend and, because the cap is cumulative, fails after batches).
- App (mock, free): `uv run python -m mde_compass --memory --port 8139` then `http://127.0.0.1:8139/ui/` (trailing slash). Live, with persistence: set the two variables, then `uv run python -m mde_compass --port 8139` (run `just compass-migrate` once). API calls need headers `Authorization: Bearer dev-token`, `X-Tenant-Id: demo`, `X-User-Id: demo-user`; history `limit` max 200. Port 8110 had a stale server not started by the agent (kill it only with the owner's say-so).
- Live batches (owner/lead only): `LLM_MODE=live ALLOW_LIVE_LLM=1 uv run python -m mde_compass.live_batches --batch E` (E about 3 minutes, F about 50 minutes, G about 20 minutes). Reports: `agents/compass/evals/reports/live-<batch>.json` (gitignored).
- A scratch driver for the HTTP app exists only in the old session's scratchpad; recreate if needed (create session `{mode, locale, user_label}`, post `card_answer` events, read new events from `/history`).

## 8. Environment gotchas

- macOS, zsh: quote globs; BSD sed has no `\b`; a ruff-format hook rewraps files, so patch with Python reading the current text.
- Run one test process at a time (little RAM). `just migrate` runs the MAIN repo migration (it was run once by mistake on the dev DB; harmless); Compass uses `just compass-migrate`.
- Strict gateway schemas: max 5 nesting levels, all fields required; the gateway caps untrusted input at 20,000 chars.
- The agent roster in `.claude/agents` and the permission classifier: writing `.claude/settings.local.json` was denied once as self-modification and allowed after explicit owner instruction; the owner later deleted it. Respect any new owner instruction about the key.

## 9. Suggested next steps

1. Ask the owner whether to enable the key again; if yes, re-run batch E then F (after the latest fixes) and compare fallbacks and probes.
2. Fix the open items in section 6, highest value first: degraded design probe, Locked-after-pivot post-pass, repeats, budget overrun, contradiction detection.
3. Phase 8: delete legacy, refresh docs, `just ci-fast`, final report, merge `--no-ff` after the owner agrees.
