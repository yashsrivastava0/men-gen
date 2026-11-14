# Spec output: quick-yoga-studio-bengaluru (MOCK)

This folder is one saved Spec run, copied here so you can read what the engine generates without starting the app. Spec turns an approved Compass Brief (and a Research note, when there is one) into a folder of plain files, the Spec Pack. The pack tells the front-end engine what the app is and does. It never says how the app should look in code.

**MOCK.** The text in this pack is placeholder text written by code. No model was called and nothing was spent. Read it for the shape of what Spec makes (the files, the ids, the links between them, the checks), not for the quality of the writing.

| | |
| --- | --- |
| Input | quick-yoga-studio-bengaluru |
| Run id | `run_01M7DDM79Q9CVRVD1FCV7JBMMX` |
| Mode | MOCK |
| Status | approved at Gate 2: this is the pack that would be handed to the front-end engine |
| Size profile | compact |
| Model calls | 11 (11 clean first try, 0 repaired, 0 fell back to the safe default) |
| Cost | USD 0.0000 |
| The pack | 69 files, about 81,802 tokens, content hash `b8bc64920e6bba691330e36c05ad0b527bc16fbf97a39bcaecd6f98205537dbe` |
| Checked by | self_check |
| Build tasks | 5 (the biggest reads 16,748 tokens; the budget is 28,000) |
| Started | 2026-11-14T16:18:09.847636+00:00 |

## Open these first

1. `pack/INDEX.md`: the map of the whole pack. Start here.
2. `pack/screens/`: one file per screen, what is on it and what each button does.
3. `pack/contract/`: the operations, resources, events and who may do what.
4. `evidence/self-check.json`: what the engine checked on its own work and what it flagged.
5. `pack/build/build-plan.json`: the build tasks the front-end engine would follow, in waves.

## What the checks said

The engine's own validators: passed (0 blocker, 2 info, 5 warning).
Which validators spoke: V-04 (5), V-36 (1), V-45 (1).

## What is in this folder

| Path | What it is |
| --- | --- |
| `pack/INDEX.md` | the map of the whole pack: what each folder holds and what to read first |
| `pack/AGENTS.md` | the short instructions the front-end engine reads before anything else |
| `pack/screens/` | one file per screen: what is on it, what each button does, each state, the copy |
| `pack/modules/` | one file per module: the screens that belong together and their stories |
| `pack/contract/` | the contract: operations (API), resources, events, who may do what, components |
| `pack/data-model/` | the data: entities, fields, statuses, who sees what |
| `pack/global/` | app-wide files: roles, navigation, routes, rules, glossary, design direction, copy |
| `pack/apps/` | one short file per app in the program |
| `pack/build/build-plan.json` | the build tasks, in waves, with what each task reads |
| `pack/prefix.bundle.md` | the shared reading every build task starts with |
| `pack/MANIFEST.json` | the list of every file here with its hash and size in tokens |
| `pack/index.json` | the same map as INDEX.md, for programs |
| `evidence/` | the engine's self-check, the Verify result when Verify ran, the Gate 2 summary |
| `graph.json` | the full reference graph the pack was written from (every id and link) |
| `run.json` | the run record: steps done, gates, flags, estimate |
| `report.json` | calls, repairs and cost by kind of call (`scripts/spec_run_report.py`) |
| `summary.json` | the same numbers in one small file |

Left out on purpose: the run's checkpoints, its event log and its input file. They stay in the run folder under `agents/spec/.runtime/runs/`, which is not in git.

## Check it yourself

```
uv run python -m mde_spec_core check docs/spec-agent/outputs/mock-2026-11-14/run_01M7DDM79Q9CVRVD1FCV7JBMMX/pack
```

That runs the pack linter and the spec check (it finds `graph.json` beside the pack). It costs nothing and calls no model. It prints `RESULT: OK` when nothing blocks.

The saving script is `scripts/spec_export_outputs.py` (or `python -m mde_spec_agent bench export`). Everything here is a copy: the pack's bytes are exactly the bytes the engine wrote.
