# GLOBAL RULES FOR EVERY BUILD TASK (v1, 2026-11-09)

These rules apply to **every task and every agent**, now and in future. If a task prompt conflicts with them, these win. The first action of every task is to make sure this text exists in the repo as `docs/implementation/GLOBAL_RULES.md` (create it from this text if missing; commit it as `docs: add global build rules`) and that the repo's `CLAUDE.md` has a one-line pointer to it, so the rules apply to later tasks automatically.

## A. Commit policy: nothing missed, everything revertible

1. **Start clean and record a baseline.** Run `git status`. If there are uncommitted changes that are not yours, commit them separately as `chore(wip): checkpoint existing changes before <task>`; never discard them and never mix them with task commits. Note the current branch and commit hash. Create a safety tag `pre/<task>-<yyyymmdd-hhmm>`.
2. **Work on a task branch** named `feat/<agent>-<task>` (follow the repo's worktree rule for parallel agents; each parallel agent has its own branch). Merge into `main` only when everything is green, with `git merge --no-ff` (never squash, never rebase shared history), so the whole task can be undone with one `git revert -m 1 <merge-hash>`.
3. **Small, atomic commits.** One reason per commit. Commit at least every 15 to 20 minutes of work, always before any risky step (migration, large move, deletion, dependency change), and always before a time or usage limit ends. No uncommitted work at the end of any session: `git status` must be clean (everything is committed or intentionally ignored).
4. **Green rule.** Every commit leaves the area it touches passing (tests, lint, types). Moves and renames use `git mv` in a commit that contains only the moves plus the config edits needed to stay green (check with `git diff -M --stat` that Git sees renames). Do not mix formatting-only, refactor, behavior and docs changes in one commit; tests go with the code they cover. Migrations, generated files, and decision/ADR/PROGRESS updates each get their own commits.
5. **Message format (Conventional Commits).**
   `type(scope): imperative summary` (72 characters or fewer), a blank line, then a body that says what changed and why, the decisions taken, the tests run with counts, the risks, and how to revert if it is not trivial. Footer lines: `Task: <prompt id>`, `Phase: <n>`, `Refs: D-nnn, ADR-00nn`.
   Types: `feat fix refactor test docs build chore perf revert wip`. Scope: the agent or package (`compass`, `web`, `gateway`, `persistence`). Use the repo's trailer or attribution convention if it has one.
6. **Tags.** After each green phase create an annotated tag `<agent>/<task>-p<n>` (for example `compass/ai-interview-p3`) with a one-paragraph description. Before risky steps create `pre/<what>-<yyyymmdd-hhmm>`. Never move or delete tags, branches, or other agents' files without the owner's yes.
7. **Never:** force-push; run `git reset --hard`, `git checkout .` or `git clean -fd` on anything not committed or backed up; rewrite or amend pushed commits (amend only the latest unpushed commit); commit secrets (`.env`, keys, tokens), caches, `.venv`, `node_modules`, data directories or large binaries (cassettes are allowed after the secret scan); commit conflict markers (run `git grep '^<<<<<<<'` before every merge commit).
8. **Remote / GitHub.** Run `git remote -v`. If a GitHub remote exists: push the task branch and tags after every phase and at the end with a plain `git push` (never `--force`); do not push to `main` directly unless the owner told you to; do not open pull requests unless asked. If no remote exists: keep committing locally, say so in the report, and never create or change remotes yourself.
9. **Make every commit revertible on its own.** `git revert <hash>` must work for a single commit. Keep migrations in their own commits with working downgrades.
10. **Report requirements** (end of every task): branch and tag names; `git log --oneline` for the task (hash and subject); whether it was pushed and where; working-tree status; and the **exact revert commands** for (a) the last commit, (b) the whole task (`git revert -m 1 <merge-hash>`), (c) returning to a tag without losing history (`git switch -c recover/<name> <tag>`).

## B. Repository layout (global)

    agents/<name>/     One self-contained folder per agent: its LLM prompt templates (.jinja), structured call
                       specs, schemas, examples and mocks, catalogs and data files, engine logic, store (ports,
                       implementations, migrations where the repo allows), API router and create_app(), tests,
                       evals, the agent's README, and a copy of its build prompts in docs/build-prompts/.
    apps/web/          The GLOBAL front end for all agents. Today: a static HTML/CSS/vanilla-JS test console.
                       Later: the React app. Never inside an agent folder.
    apps/<other>/      Other apps (context-api, llm-gateway-api, cli).
    packages/*         Shared infrastructure (core, contracts, llm-gateway, persistence, ...) and legacy
                       packages until their replacements pass their gates.
    docs/  scripts/  tests/ (cross-cutting only)

Rules:
- An agent imports shared packages only. Agents never import each other or any app.
- The front end never imports agent code. It talks to agents only through their HTTP API and shared JSON Schemas.
- Each agent exposes `create_app()` and a `router`, so the Context API can mount it later.
- Python package names stay `mde_<agent>`. The uv workspace members include `agents/*` and `apps/*`. Update import-linter contracts and CI paths for every new agent.
- Front-end structure: `apps/web/console/` (shell with an agent switcher; `shared/` for tokens and components; `agents/<name>/` for each agent's screens), `apps/web/app/` reserved for React, `apps/web/tests/` for UI smoke tests. Static files are served by an agent's dev server from a configurable directory (`MDE_WEB_DIR`, default `apps/web/console`) and later by the Context API.
- Legacy agents in `packages/` (for example `mde_discovery`) stay untouched until their replacement passes its gate; they are then retired in a separate task.

## C. Always
Log decisions in `DECISIONS.md` and ADRs; update `PROGRESS.md`; follow the repo's rules on secrets, live spend (the owner's caps are hard limits), memory (one test suite at a time), and the operating system; never print `.env`.
