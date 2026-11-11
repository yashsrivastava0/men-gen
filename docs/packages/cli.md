# mde_cli: the `mde` command line

The terminal interface of the Context Engine (`apps/cli`, import `mde_cli`, built with Typer). There is no chat UI in this project: this CLI and the OpenAPI-documented [Context API](context-api.md) are the interface. The CLI is a **client**: it talks HTTP to the API (the wire contract is [../specs/events-and-api.md](../specs/events-and-api.md)), or, with `--local`, to the same API running inside its own process.

```powershell
uv run mde --help
uv run mde chat --local --memory         # an offline demo: no database, no key, no network
uv run python -m mde_cli --version       # the same command, as a module
```

Setup and the five commands to get going: [../runbooks/local-dev.md](../runbooks/local-dev.md).

## Conventions (every command)

| Rule | Detail |
| --- | --- |
| Offline by default | Nothing here calls a model. `mde sim --llm` is the only path that could, and it is fenced (below). |
| Never prints a value | Not the dev token, not an API key, not a database URL. `doctor` reports `present`/`absent` and variable *names*. A secret pasted into a chat line is masked in the script echo and is redacted by the API before anything is stored. |
| Friendly errors | A failure is one `error: ...` line (plus a `hint: ...`) on stderr, never a stack trace. `MDE_DEBUG=1` re-raises, for a developer. |
| No TTY needed | `--script` runs with no terminal at all (PowerShell, Git Bash, CI, a pipe). Output has no ANSI codes unless the stream is a terminal; long lines are never hard-wrapped. |
| Hindi and Hinglish | Output survives a console that cannot encode a character (it is replaced, not a crash). The CLI's own hints exist in English, Hindi and Hinglish (`--lang`). |

**Exit codes**

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | the command ran and found a problem: an expectation failed (`sim`), a contract was violated (`spec simulate-consumer`, `spec verify`), a service is down (`doctor`, `chat`), the API refused a request, or a `--script` run had an error or an unusable input |
| 2 | the input cannot be used: a bad option value, a file or script that cannot be read or parsed, an unknown persona or spec id |
| 3 | refused on purpose: `mde sim --llm` without live mode and budget |
| 130 | interrupted (Ctrl+C) outside a conversation |

## `mde chat`

The interview, the two gates and the hand-off in a terminal.

```powershell
uv run mde chat --local                              # in-process API on the local PostgreSQL, mock mode
uv run mde chat --local --memory                     # same, state in memory (nothing saved)
uv run mde chat                                      # against `just dev` at http://127.0.0.1:8100
uv run mde chat --url https://host --token ... --tenant acme --user ann --roles delivery_lead
uv run mde chat --local --memory --script journey.txt
uv run mde chat --session ses_01...                  # pick a saved session up where it stands
```

| Option | Meaning |
| --- | --- |
| `--url` | API base URL (default `http://127.0.0.1:8100`; env `MDE_API_URL`) |
| `--token` | the dev token (default: `API_DEV_TOKEN`, else the dev default; never shown, not even in `--help`) |
| `--tenant`, `--user`, `--roles` | the identity headers (defaults `local`, your OS user name made safe, none; envs `MDE_TENANT`, `MDE_USER`, `MDE_ROLES`). Checked locally against the shapes the API accepts |
| `--mode`, `--policy` | `guided` (default) or `enterprise`; a policy id overrides the mode's default |
| `--lang` | language of the CLI's own hints: `auto` (follow the session, default), `en`, `hi`, `hi-Latn` |
| `--local` | run the API in this process: the real Discover, Research, Spec Builder and flow, **mock mode** (forced whatever the environment says; no key is read), an ASGI transport instead of a socket, the in-process job worker. State is the local PostgreSQL (`DATABASE_URL`) |
| `--memory` | with `--local`: in-memory store instead of PostgreSQL (nothing to install, nothing kept) |
| `--projects-dir` | with `--local`: where hand-off git repositories are written (default `data/projects` of the main checkout) |
| `--script FILE` | non-interactive: one input per line (`-` reads stdin). Blank lines and lines starting with `#` are skipped. Deterministic |
| `--session ID` | resume a session instead of starting one (the past is not replayed; the pending card or open gate is shown) |
| `--wait SECONDS` | how long to wait for the plan job before giving up (default 600; the job keeps running) |
| `--verbose` | with `--local`: show the engines' log lines |

### What you type

| Moment | Input | Effect |
| --- | --- | --- |
| a question (card) | `1`, `2`, ... | taps that option. A pick-several card takes `1,3` |
| | `r` | the suggested answer (every suggested option on a pick-several card) |
| | `d` / `s` | "you decide" / skip |
| | `r*` | the suggested answer to **every** remaining question (handy in scripts) |
| | anything else | a message; on a text card it is the answer |
| a gate (the Brief, then the Plan) | `1` | "Looks right" (Enterprise: "Send for approval") |
| | `2` | "Change something": the next line is what to change; `2 make it blue` does both at once |
| | other text | a message (a typed "yes" counts only when the engine is confident) |
| any time | `stop`, `pause`, `resume`, `restart`, `finish` (finish with smart defaults), `quit`, `help` | as plain words |
| | `/status`, `/wait`, `/brief`, `/plan`, `/spec` (`/spec full`), `/upload FILE`, `/undo`, `/archive` | with a leading slash, so they never hijack free text |

Typing during the plan is allowed (the API queues it). **Ctrl+C** at the prompt leaves; Ctrl+C while waiting for the plan stops *watching*, not the plan (`/wait` watches again, `stop` cancels it).

### What it prints (the transcript shape)

```
In-process API, mock mode (no key, no network). State: in memory (nothing is saved).
Session ses_01M6... (guided).
Assistant: Hi! What would you like to build?
  You could say: An online shop | Food ordering | Bookings | ...
> An online shop for handmade candles          <- the script echo (a secret is masked here)
Assistant: Noted.

Question 1 of 10
  It sounds like an online shop ... Is that right?
  1) Yes, an online shop  (suggested)
  2) Not quite, start with basic building blocks
  Type a number to choose. d = you decide, s = skip, or write your own answer.
> r*
...
=== Brief, version 1 (hash c35798f34d43) ===
In short: ...
  What you are building: ...
      [x] Browse products  [x] Search and filters  [ ] Favorites ...
What I assumed (tell me if any of these is wrong): ...
  1) Looks right
  2) Change something
  Type 1 or 2. You can also type a change in your own words.
> 1
-- now: PLANNING (research)
Assistant: Great. I'm researching similar apps and then building your plan.
Working on it. Press Ctrl+C to stop watching (the work carries on).
  [research 2/4] Looking at similar apps
  [build 4/10] Writing each screen
Plan ready (version 1, hash 446940444a5c).
=== Plan, version 1 (hash 446940444a5c) ===
Screens: 27 in total ... Research: ok / What research added (with sources) ...
Connections to other services (real or mocked): ...   Who sees what: ...
  1) Looks right
> 1
Spec ready.
  path:    APP_SPEC.md
  repo:    data/projects/local/app_01...
  version: 1
  hash:    446940444a5c...   (64 hex digits)
  commit:  6afb0686...
-- script finished: session ses_01M6... is HANDED_OFF (spec_ready: 1, problems: 0)
```

The Brief page shows rows, the features with `[x]`/`[ ]`, the look's colours and what was assumed; the Plan page adds the screen map, the data in plain words, what research added **with its sources** and whether each item is verified, real versus mocked connections, who sees what, the diff since the Brief, the backlog and, in Enterprise, the approvers. Progress events are one line each, read from the SSE stream with `Last-Event-ID` replay (a `--local` run polls `follow=false`, because an ASGI transport buffers a response); every wait is bounded.

**Enterprise.** `1` at a gate is "Send for approval" when other people must decide: the owner submits and the page shows `Approvals: client_product_owner [waiting], delivery_lead [waiting]`. Each approver runs `mde chat --session ses_... --user <their id> --roles <their role>` and types `1`; `/wait` watches for their decisions. The owner is the `requester`; nobody else is.

**A script run** exits 1 when something went wrong (an API error, a number out of range), so CI notices. A lost connection ends a script at once; an interactive chat keeps going.

## `mde sim`

The persona simulator: **scripted** by default (free, deterministic), plays the YAML personas of `evals/datasets/personas` against the API with the step vocabulary documented in [../../evals/CLAUDE.md](../../evals/CLAUDE.md) and `evals/harness/persona.py` (`mde_cli.persona` is its own small player; the CLI never imports `evals`).

```powershell
uv run mde sim --local --memory                       # all ten personas, in process
uv run mde sim --local --memory --persona P04         # one (an id, its start, or a fragment)
uv run mde sim --persona all --url http://127.0.0.1:8100
uv run mde sim --local --memory --personas my\folder --transcript
```

Steps: `user` (a message), `upload`, `tap` (`recommended`, `option` by id or label, `you_decide`, `skip`, `other_text`), `finish` (the suggested answer until the Brief), `approve: brief|plan` (every required role approves, each as a person of its own: `u-<role>` holding that role), `plan` (wait for the plan job), `ui` (any UI action). The `expect` block (`state`, `spec_ready`, `max_cards`, `cards_shown_max`, `slots`, `notices`, `text_contains`) is checked over what the API showed.

```
P01-hi-only                   ok    DISCOVERING   cards 0   spec_ready 0    0.2s
P04-confused-hinglish         ok    HANDED_OFF    cards 3   spec_ready 1    5.0s
P09-enterprise-services-firm  FAIL  PLAN_REVIEW   cards 15  spec_ready 0   11.2s
    - state 'PLAN_REVIEW', expected 'HANDED_OFF'
9 of 10 personas met their expectations.
```

Exit 0 when every persona met its expectations, else 1 (2 for an unknown persona or a malformed file). `--transcript` prints each conversation as `mde chat` shows it.

**`--llm` (a model plays the persona)** spends real money, so it is refused (exit 3, nothing runs, no session is created) unless **all** of this holds, checked by `mde_cli.llm_user.check_live_allowed` before anything else:

1. `LLM_MODE=live` is in the *environment*;
2. the gateway's own live check passes (`MDE_FORBID_LIVE` is not set; the pytest fixture sets it, so no test can go live);
3. the budget ledger is intact and below the 80% tripwire (`just budget` / `mde budget` shows it).

Every call then goes through the gateway (budget guard, ledger, retries) with the versioned call spec `X1.sim_user` (templates in `mde_cli/data/llm_user/`, a mock handler, and a deterministic fallback: if a call fails the person leaves). The run is reported without pass or fail. **Only the lead runs it live** (see [../runbooks/live-llm-budget.md](../runbooks/live-llm-budget.md)); the tests exercise the loop through a gateway in mock mode.

## `mde budget`

```powershell
uv run mde budget [--scope eval] [--json] [--ledger PATH]
```

The ledger summary: spend, percent of the cap, reserved, tripwire, live calls, spend by scope. It is `mde_llm_gateway.budget_cli` (what `just budget` runs), so the numbers and the text are identical. Read-only: no lock, no files created.

## `mde doctor`

```powershell
uv run mde doctor [--live] [--url http://127.0.0.1:8100]
```

Settings present or absent (never values), the variable *names* of the dotenv file (`MDE_ENV_FILE`, default `.env`; an empty value disables it), duplicates, whether the database answers, with `--url` whether an API is ready, and the model **only** with `--live` in live mode (a metadata call that costs nothing). Exit 1 when something it checked is down. The logic of `scripts/doctor.py`, plus the API check.

## `mde spec ...`

```powershell
uv run mde spec simulate-consumer APP_SPEC.md [--budget 10000]
uv run mde spec verify APP_SPEC.md [--hash <the hash an approval is bound to>]
uv run mde spec show APP_SPEC.md [--screen SCR-001 | S3 | FLW-001] [--get SCR-002 --get SUMMARY]
```

All three use the Spec Loader. `simulate-consumer` wraps `mde_spec_loader.simulate_consumer` with the output and exit codes of `scripts/simulate_frontend_consumer.py` (0 ok, 1 contract violation, 2 unreadable). `verify` recomputes the hash by the loader's rule (the file with the front-matter `hash:` blanked); a file checked out with CRLF still verifies. `show` prints the index (sections and blocks with token sizes), a tiered slice, or the exact text of ids.

## Layout

```
apps/cli/src/mde_cli/
  main.py          the Typer app: thin commands, heavy imports inside them
  chat.py          ChatSession (what each input means) and the keyboard loop
  conversation.py  Conversation: sessions, the five inbound events, the de-duplicated event log, bounded waits (shared by chat and sim)
  client.py        ApiClient over httpx: typed calls, error mapping, idempotent retries, SSE with replay
  render.py        events, cards and the two gate pages as plain text
  local.py         --local: the API in process (create_app, ASGI transport, worker)
  persona.py, sim.py, llm_user.py     the persona player, its report, the fenced model-driven user
  diagnostics.py   budget and doctor
  specs.py         spec simulate-consumer / verify / show
  common.py, errors.py, console.py, runtime.py, strings.py (+ data/)   identity and token, exit codes and the friendly wrapper, output, the loop runner, the hint strings
```

Two design points. The chat is a plain loop on the main thread (blocking `input()`, one `run_until_complete` per step on a loop that lives for the whole command), because `asyncio.run` turns Ctrl+C into a cancellation that cannot interrupt `input()` and a keyboard thread would block the interpreter at exit. The loop is a `SelectorEventLoop` on every platform: psycopg's async mode cannot run on Windows' default Proactor loop (D-042), and creating the loop directly means no global policy is touched.

## Tests

```powershell
uv run pytest apps/cli -q                                  # about 3.5 minutes; includes PostgreSQL
uv run pytest apps/cli -q --cov=apps/cli/src --cov-report=term-missing
uv run pytest apps/cli -q -m "not integration"             # no database needed
```

Typer's `CliRunner` runs every command; `mde chat --local --memory --script` plays a Guided journey from "hi" to `spec_ready` on the real stack and asserts the transcript shape; a real localhost server (`mde_context_api.testing.serve_app`) proves the network path including live SSE; `@pytest.mark.integration` tests run `--local` on an ephemeral PostgreSQL database (never `mde`), including a session resumed by a second run. An autouse fixture fails any test that reaches a non-loopback host. Secret handling is tested by comparing output against a key assembled at run time. The HTTP variants of the end-to-end journeys are `evals.harness.driver.HttpDriver` in `tests/e2e` ([../evals/eval-plan.md](../evals/eval-plan.md)).

## Known gaps

* `.importlinter` lists `mde_cli` and `mde_context_api` as siblings, which forbids a static import; `local.py` loads the API by name. Putting `mde_cli` one layer above the API in that contract would allow ordinary imports.
* `/upload` sends text and Markdown only (the API takes images only as an opaque reference, which a terminal has no way to produce).
* The plan wait polls in `--local` (an ASGI transport buffers a whole response) and streams over a real connection.
