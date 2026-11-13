# Spec proof tools (`evals/spec/`)

The harness for the proof gates of [doc 16](../architecture-v3/16-spec-proof-plan.md), [doc 17 section 10.4](../architecture-v3/17-spec-content-model.md), [doc 18 section 3.4](../architecture-v3/18-spec-final-architecture.md) and [doc 19 section 7](../architecture-v3/19-spec-big-apps-bench-and-result-first.md). It is built and tested **offline**; the live gates run it later with `LLM_MODE=live`, set by the lead (rule 13: that is the only switch; this code never sets it and never reads a key).

**What it does:** it measures and reports. It compares every number with the thresholds in `packages/spec-core/src/mde_spec_core/data/spec-config.yaml` `proof:` (read at run time, never copied), says "single run" when it is one, and **calls nothing proven**. The decision table (doc 16 section 7) is applied by people.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `pass` / `fail` | a live measurement repeated at least twice (`runs >= 2`), or a deterministic code measurement (the self-check arm of PS-7) |
| `single_run` | a live measurement from one run: the numbers are real, a single run decides nothing; the gate record carries `would_pass` |
| `not_measured` | an offline stub result (the call's mock handler answered), or a gate with nothing to measure (no graph, one pack only) |

A gate that mixes arms takes the worst: `not_measured`, then `fail`, then `single_run`, then `pass`. All offline numbers are marked `OFFLINE STUB` in the report: they prove the harness, not the model.

## Money

Every model call goes through Spec's own guarded runner (`mde_spec_agent.calls.base.CallRunner` behind a `SpendGuard` and its own spend record in the work folder; the Verify port gets the same runner through `RunnerCall`, exactly as the service does). Money is integer micro-USD, summed from the runner's calls; zero offline. `make_proof_runner(workdir, core=None)` is mock by default; a live runner needs `core=load_settings()` in an environment where the lead set `LLM_MODE=live`, and verified writer prices in the consumer profiles (otherwise it refuses, as the service does).

## The tools

| Module | Gate | Core | Offline stub |
| --- | --- | --- | --- |
| `consumer_probe.py` | PS-6 | arms A (the task card's reads), B (the whole pack, only where it fits `whole_pack_tokens`; else skipped with the reason), C (the card alone); blocking questions and assumptions per screen, acceptance satisfiable share; slice-not-worse-than-whole and card-alone-clearly-worse; samples of 3, 6 and 9 screens chosen by code; the developer test hook; `owner-reading/probe-outputs.json` with 10 outputs | the canary's own mock reader (an id used but never defined is a blocking question) |
| `freedom_probe.py` | PS-4 | the same card arranged twice under two style seeds (closed list of arrangement tokens in `data/arrangements.json`, plus the region order); share of screens that differ; schema validity, tier ceilings and form-word warnings per card (V-24, V-25, V-38) with the explained list; `owner-reading/cards.json` with 5 cards | picks from a hash of the style seed |
| `planted_defects.py`, `planters.py` | PS-7 | 22 structural (the lead's `planted.run_planted`) and 8 meaning defects (real planters) against three arms: the self-check (code), a proof-only critic arm (the Verify final critic through its port), Verify for deep (structure critic, canary, final critic); catch rate overall and per kind (bars: 90 and 80 percent) for the structural kinds, the meaning kinds separately with no bar, false positives on the unmodified pack, rounds used, `critic_vendor` | the mock critic |
| `lookup_probe.py`, `catalogues.py` | PS-10 | held-out needs (split by id hash) mapped to the public catalogue of two real libraries and a synthetic one; recall at 3, wrong-gap rate, planted gaps found, against a card-only baseline | a shape-name match |
| `ablation.py` | PS-10 | the consumer probe on cumulative arms: MUST only, plus `usually`, stories, emotion, COULD, mock and assets; which bundle earns its place | the same mock reader (it cannot show a lift) |
| `diversity.py`, `synthetic.py` | PS-10 | between-app diversity (direction axes, seed hues, COULD signature) and within-app consistency (shapes, intents, motion intents per kind of moment), measured separately with COULD off and on; an optional critic call | word overlap |
| `report.py` | all | `run_all(source, out_dir, runner=None)` writes `proof-report.json` and `proof-report.txt` | an offline mock runner |

`common.py` holds thresholds, verdicts, the runner and report writing; `packs.py` loads a pack (from a graph, a pack folder, or one of the three real exports through the derive pipeline) and picks samples by card format.

## How the planted defects work

* **Structural (22):** the lead's `mde_spec_core.planted.STRUCTURAL` run through `planted.run_planted`. A plant whose planter cannot change this pack (the ids of two kinds collide with a derived graph) is `not_applicable`, never counted. A plant whose validator already reports on the unmodified pack with nothing new counts as missed (conservative) and is named in `clean_pack.masked_kinds`.
* **Meaning (8):** real planters in `planters.py` (`planted.py` is untouched): a Locked number changed (4 to 5); a rule contradicting another (same subject, "no such fee"); a user-stated feature and its only screens removed (references cleaned, so structure stays valid); a role widened to `manage` where the Brief says view; a journey without its payment-failure path; a story that contradicts a rule; a Default screen that says the opposite of an official Research item; an assumed item shown as Locked. `prepare_baseline` first makes the clean copy carry what they need (a Locked number, a payment-failure path); `brief_for` builds the approved Brief from the CLEAN graph, so a critic can see the departure.
* **A critic arm "catches" a kind** when it reports a finding the clean pack does not produce. Each row also says whether the text Verify's digest shows changed (`critic_input_differs`): if not, no model could see it.

## What the offline run already shows (harness findings for the lead)

* Verify's digest (`structure_digest`) shows no story text, no screen purpose or claims and no tier, so three of the eight meaning kinds (story contradicting a rule, Default contradicting an official fact, an assumed item shown as Locked) are invisible to the critic whatever the model. The assumed-as-Locked one is still caught by code (V-17) in the check report the critic receives.
* The self-check cannot judge meaning: on the synthetic pack it catches three of eight meaning kinds, by accident (V-45 sees the two contradictory rules; V-04 sees the screens left unreachable when a feature's screen is removed; V-17 and V-24 see the assumed item shown as Locked).
* The real derived baselines are not clean packs: every screen has V-14 (copy keys are written later by the lanes), and some have V-04 or V-40. PS-7 and its false-positive rate mean something only on a finished pack (after the live lanes) or a synthetic one.

## Running it

Offline (what the tests do): `uv run pytest evals/spec -q` (about two minutes; `conftest.py` puts the repository root on the path, `evals` is not an installed package).

Live (the lead, after `just budget`, with `LLM_MODE=live` in the environment; each call below names the gate it serves):

```python
from pathlib import Path
from mde_core.settings import load_settings
from evals.spec.common import make_proof_runner
from evals.spec import report, packs

out = Path("data/spec-proof/2026-11-xx")
runner = make_proof_runner(out, core=load_settings())            # live only if the environment says so
rep = report.run_all(None, out, runner)                          # the three real baselines
# finished packs of a live Spec run instead of the baselines:
rep = report.run_all({"quick": quick_pack, "standard": std_pack, "thorough": thorough_pack}, out, runner,
                     planted_graph=thorough_graph,              # PS-7 on the finished graph of the deep pack
                     packs_live=True,                           # the packs were written by a live lane (diversity)
                     critic_vendor="same",                      # or "different", as the gateway computed it
                     canary_scope="changed", runs=1)            # "all" for the full Verify-for-deep canary
```

One gate at a time (async, each takes the runner): `consumer_probe.run_consumer_probe(runner, pack, "standard")` and `developer_test(runner, standard_pack, "SCR-014")`; `freedom_probe.run_freedom_probe(runner, pack, "standard")`; `planted_defects.run_planted_defects(runner, graph, critic_vendor=..., canary_scope=...)`; `lookup_probe.run_lookup_probe(runner, pack)`; `ablation.run_ablation(runner, pack, "standard")`; `diversity.run_diversity(pack_a, pack_b, runner, packs_live=True)`. Each has a `write_report(report, out_dir)`. Repeat runs with `runs=` (and `run_index=` on the consumer probe) to get `pass` or `fail`.

Call counts at the default sample sizes (for the price check): PS-6 12 calls on Standard (arms A and C) and 6 on Quick; PS-4 6 on Standard; lookup 5; ablation 36; diversity 2; PS-7 about 30 critic calls for the proof-only arm and about 30 times (2 plus the changed cards) for Verify for deep, plus 1 and 4 for the clean pack.

## Fixtures

`evals/spec/fixtures/catalogues/<library>.json`: `{library, source_url, checked_on, components[{name, purpose, source_url, shapes_it_can_show[], confidence, notes}], excluded[], notes}`, snapshots of the public documentation of **React Native Paper** (`oss.callstack.com/react-native-paper`) and **React Native Elements** (`reactnativeelements.com/docs`), read on 2026-11-14. The purpose of each component is the first descriptive sentence of its documentation page; components whose page could not be read, or whose first line does not say what the component is for, are listed under `excluded` with the reason and are not in the answer key. The `shapes_it_can_show` mapping to `mde_spec_core.vocab` shapes is the snapshot author's, with a confidence; the lookup probe scores only high and medium confidence. The synthetic catalogue (`data/synthetic-catalogue.json`, 25 components, four planted gaps) is used only to check that gaps are found.
