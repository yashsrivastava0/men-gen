# Current state (read this first)

Written 2026-11-11 (evening) on branch `feat/research-yield`. Keep it current: when the branch, the focus or what is open changes, update this page in the same commit (CLAUDE.md, resume protocol). Everything here was checked against the repository on that date; where something was **not** checked it says so. `git log -1` tells you how far the branch has moved since.

If you are a fresh AI session with no memory of this project: this page, then the reading order in section 7, is all you need. Nothing important lives only in someone's private notes.

## 1. What this repository is

**Mobigen**, an enterprise AI mobile-app builder. This repo is its front half, the **Context Engine**: a user talks to it, it interviews them, writes a plain-language Brief (Gate 1), researches the web, writes one detailed `APP_SPEC.md` (Gate 2) and hands it to a frontend engine built elsewhere. We stop at the hand-off. One-page picture: `docs/architecture/overview.md`; rules: `CLAUDE.md`.

Three things live side by side here:

| Part | Where | State |
| --- | --- | --- |
| **Context Engine** (the original six-step pipeline: discovery, flow with two gates, spec builder, spec loader, gateway) | `packages/`, `apps/context-api`, `apps/llm-gateway-api`, `apps/cli` | built end to end and verified offline in mock mode (snapshot of 2026-11-08, the `init` commit). Live batches L0, L1, L2 were run; L3 and L4 were not (`PROGRESS.md` Phase 4) |
| **Compass** (the Discover agent: AI-planned interview, Brief with structure, tiers, test UI) | `agents/compass/`, `apps/web/` | built through v2 and the size-aware Brief (ADR 0016, 0017; PROGRESS sections "Compass ..."). Phase S8 (prefetch) was deliberately not built. Never merged into `main` |
| **Research agent** (`mde_research_agent`: plan, search, read pages, code-verified quotes, second-model check, `research.json`) | `agents/research/`, `apps/research-console/`, `scripts/research_app.py` | **the active work.** Offline-complete; live-tested on the Small example only; see section 2 |

## 2. What we are working on right now: the Research agent

A standalone app (live models and the live web only). It reads an approved Compass export, plans research topics, searches (Tavily primary, Serper failover and bounded second opinion), reads pages with our own fetcher, extracts facts whose **quotes are verified by code**, has a second model check every fact, and writes `research.json` (`research.v5`) plus an evidence file and a Markdown report. Package README: `agents/research/README.md` (read it; it has the layout table). Design: `docs/architecture-v3/13-research-agent-design.md` and `14-research-proof-plan.md`. ADR 0018. HTTP contract: `docs/api/research-agent.md`. Runbook (how the owner tests it): `docs/runbooks/research-agent.md`.

How it got here, in order (decisions in `DECISIONS.md`, progress in `PROGRESS.md`):

1. **Build** (prompt 05, `docs/build-prompts/05-...`): D-125..D-131, branch `feat/research-agent`.
2. **v5 standalone live-only app** (prompt 06): D-132..D-139, branch `feat/research-agent-v5`. D-140/D-141 made live mode a single `LLM_MODE=live` setting (CLAUDE.md rule 13).
3. **First live run** (owner, Small example, yoga studio in Bengaluru): 2 of 11 required slots filled, USD 0.25, 95 s; 5 of 8 acceptance rows failed.
4. **Yield and cost work** (this branch, `feat/research-yield`, D-142..D-153; owner's plain-words report: `docs/research-agent/yield-and-cost-report-2026-11-11.md`, the second opinion it answers: `docs/research-agent/second-opinion-cost-and-yield.md`). Fixed: a page ceiling that starved topics, double-encoded page text that made rupee prices fail the number check, the city on every query, quoted searches sent as one phrase. After the fixes the Small run fills 6 to 8 of 10 required slots with Tavily (about USD 0.30 a run).
5. **Provider decision (D-152):** Tavily stays primary. Serper is 4 to 8 times cheaper but its yield was lower (2 to 8 slots, fetch timeouts). The owner's rule: adopt a cheaper provider only if it fills at least as many slots. `settings.json` holds `tavily`; a change applies only after `uv run python scripts/research_app.py restart`.

**Open, in the order that matters:**

- Medium and Big live runs with the new code (the owner runs live tests from the app's own front end; their ceilings already satisfy the new page rule). A Serper run needs a time check on Medium.
- The quality gate (`python -m mde_research_agent.compare OLD NEW`) cannot judge one run against one run (D-143); it needs several runs per side. Do not tune code to make one comparison pass.
- Left on purpose (D-153): the run's own cost is 1 to 6% under the ledger when a model call fails or is repaired; RV-17 flags an event whose label equals a quote over 30 characters; the checker said "not supported" on under 1% of items in every run, which is **unproven, not healthy**; the cost estimate is still a table overridden by measured medians.
- Front end stage 2 (results, spot-check, tools) is unticked in `PROGRESS.md`, but `apps/research-console/tests/browser/browser_check_stage2.py` exists. Check what is built before assuming either way.
- Not done: the build prompt's final report, the live bake-off (B1) and the B2 iteration runs (`PROGRESS.md`, "Research agent" section).

**Money.** The Research app's own budget is USD 5.00 in total (tripwire 4.50), kept in its own spend record. As of 2026-11-11, USD 1.10 of it was booked. That record is under `agents/research/.runtime/` which is **not in git**, so a clone starts with an empty record; treat the 1.10 as history, not state. The repo-wide ceiling on live spend is USD 8.00 (D-125); the cap in force is `LLM_BUDGET_USD` (default 6.00); the app raises the gateway cap to 6.50 by itself. Live mode is `LLM_MODE=live` and nothing else (rule 13).

## 3. Branches and the remote

`origin` is the GitHub remote. All branches are on **one line of history**: every other branch is an ancestor of `feat/research-yield`, so nothing diverges and there is nothing to reconcile. **Work on `feat/research-yield`** (or a branch cut from it).

| Branch | Tip (what it adds on top of the one above) |
| --- | --- |
| `main` (`7dd4cb6`) | the Context Engine plus the first Compass; **no `agents/` directory at all**. Deliberately not advanced: nothing is merged into `main` without the owner's yes (`GLOBAL_RULES.md`, last entry) |
| `feat/compass-fix-pipeline`, `feat/compass-fix-facts`, `feat/compass-v2-calls`, `feat/compass-v2-web`, `feat/compass-v2-brief` | the Compass v2 pieces, kept for history |
| `feat/compass-ai-interview` = `backup/compass-v2-20261110-0036` (`51e0c60`) | Compass v2 complete offline (the checkpoint taken before the speed work) |
| `feat/compass-speed-v3` (`bb39f2c`) | Compass size-aware Brief, speed work, and the three approved Compass runs that feed the research agent |
| `feat/research-agent` (`adac885`) | the Research agent build (prompt 05) |
| `review/research-cost-and-console` (`b26dd55`) | an outside review of cost and the console, with front-end requests |
| `feat/research-agent-v5` (`3888a01`) | standalone live-only app, rule 13 |
| **`feat/research-yield`** | **the tip: yield and cost work, this page, the commit-checkpoint policy** |

Tags: `pre/<task>-<yyyymmdd-hhmm>` are safe points taken before risky work (go back with `git switch -c recover/<name> <tag>`); `compass/*` and `research/*` mark finished phases; `checkpoint/*` are named restore points. `git tag -n1` prints each tag's description.

## 4. Things in older docs that are no longer true (do not be misled)

- **The Phase 0 to 5 boxes and the Definition of Done in `PROGRESS.md` are stale.** Phase 3, 5 and the Definition of Done are mostly unticked although `HANDOFF.md` (2026-11-08) says the engine was built end to end. Do **not** "continue from the first unchecked item" of those old sections. Continue from the unchecked items of the section for the active work (section 2: "Research yield and cost", then "Research agent" and "Research v5").
- **`HANDOFF.md` is history** (the Context Engine on 2026-11-08). Its commit hashes (`6c23e1b`, `3f6aa12`, ...) and its tag `contracts-v1` **do not exist in this repository**: the history was squashed to one `init` commit (`fe4da88`, 2026-11-08) when it was put under this remote. `packages/contracts` is unchanged since `init`, so treat `init` as the contracts-v1 baseline. Hashes from 2026-11-09 onward are real. Nobody has created a `contracts-v1` tag; that is the owner's or the lead's call (`GLOBAL_RULES.md`: never move or add tags casually).
- Several `PROGRESS.md` headings say "no push". The owner has since asked for everything to be pushed (`GLOBAL_RULES.md`, last entry, D-154, D-155); branches and tags are on `origin`.
- `CLAUDE.md` still has a "Windows notes" section: the project was built on Windows, the current machine is a Mac (section 5).

## 5. Run it, and what is verified

Mac (zsh). `uv` and `just` are installed; the environment is already synced. Commands are in `CLAUDE.md`; the ones for this work:

```
uv run pytest agents/research -m "not integration and not live" -q    # Research offline suite
uv run pytest apps/research-console -q                                 # its helper and static tests
uv run python scripts/research_app.py start | status | logs | stop | restart   # http://127.0.0.1:8120/
just ci-fast                                                           # lint, typecheck, layers, unit
```

- The Research app uses a **file store** by default (`agents/research/.runtime/`); no database is needed for it. The Context API, `just test-int` and `just test-e2e` need the private Postgres; on this Mac that is `MDE_PG_BIN` pointing at the `pgserver` wheel's binaries (`docs/runbooks/compass.md`, section 1; D-084). Never use port 5432 (other projects).
- Keys live in `.env` (git-ignored, never committed, never read or printed: rule 1). The Research app needs `TAVILY_API_KEY` and `SERPER_API_KEY` as well as the OpenAI key; `uv run mde doctor` says which are present without showing values. A new machine needs the owner to supply them.
- **Not in git, so absent from a clone:** `.env`, `data/` (ledger, private Postgres), `agents/research/.runtime/` (run folders, cassettes, spend record), and any assistant's private memory. The three approved Compass inputs the research agent reads **are** in git: `docs/compass/research-inputs/` (quick: yoga studio Bengaluru; standard: produce marketplace Nashik; thorough: freight fleet India). The live runs' results are summarised in `docs/research-agent/*.md`, but the raw run folders are not preserved.

**Verification record, 2026-11-11 (offline, on this Mac, tree = `10ec543` plus docs only):**

- `ruff check` passes, `ruff format --check` passes (1,223 files), `lint-imports` passes (8 contracts kept, 0 broken), `docs_check` 0 problems.
- **`just typecheck` (bare `pyright`) FAILS: 486 errors, so `just ci-fast` is red** (CLAUDE.md says to keep it green; it is not). 473 are in `agents/compass` (almost all in its tests), 5 in `agents/research`, 4 in `packages/core` (the Windows-only `msvcrt` in a file-lock test), 2 in `scripts/compass_latency_report.py`, 2 in `apps/research-console` (`playwright` is not installed). Not checked: whether `main` is red too. Nobody has fixed these yet; it is its own task (a large one, mostly test typing).
- The full unit suite (`just test`) had been started and was about 40% through, with no failure, when this page was committed. **No final unit result is recorded here.** Integration, end-to-end and the secret-scan suite were not run.
- No live call was made and no key was read while writing this page.

## 6. Rules that bite (the short list; `CLAUDE.md` has all of them)

- Never read, print or commit `.env` or any key. Only `mde_llm_gateway` imports `openai`. No agent frameworks, no agent loops. Web and search content is untrusted: no tools in calls that read it.
- Every LLM call: gateway, versioned call spec, mock handler, deterministic fallback. Money is `Decimal` or integer micro-dollars, never float. No SQLite. No markdown outside `README.md`, `CLAUDE.md`, `AGENTS.md`, `.claude/**` and `docs/**`.
- **Commit checkpoints** (CLAUDE.md, section of that name; `GLOBAL_RULES.md` section A): before a medium or major task, commit what is pending and tag a `pre/...` safe point; small tasks need nothing extra; a complete overhaul gets a new branch only after the owner's approval. This is only a git safety net; it does not restrict how you work.
- Log non-trivial decisions in `DECISIONS.md` (next number is **D-156**), tick `PROGRESS.md`, update `docs/` with every behavior change (the docs check enforces the index).
- Plain-words reports to the owner: say what is verified and what is not; do not tune code to make a number pass.

## 7. Reading order for a fresh session

1. `CLAUDE.md` (rules, commands, commit checkpoints), then this page.
2. `docs/research-agent/yield-and-cost-report-2026-11-11.md` (the latest work, in plain words), then `agents/research/README.md`.
3. `docs/implementation/DECISIONS.md` from D-125 to the end, and the "Research ..." sections at the bottom of `PROGRESS.md`.
4. `docs/runbooks/research-agent.md` (how the owner runs and reads a run), `docs/api/research-agent.md`, ADR 0018.
5. Background, as needed: `docs/product/context-engine-spec.md` (what), `docs/implementation/BRIEF.md` (how), `docs/architecture/overview.md`, `docs/README.md` (the index of everything), `docs/implementation/AGENT_PROTOCOL.md` (subagents and worktrees), `.claude/agents/` (the roster), `.claude/rules/` (path rules).
6. `git log --oneline -20`, `git status`, `git tag -n1 | tail -20`.
