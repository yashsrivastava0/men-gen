"""The offline eval runner: every dataset through the real product in mock mode.

    uv run python -m evals.runners.mock_batch                       # all sections
    uv run python -m evals.runners.mock_batch --only briefs,probe   # some sections
    uv run python -m evals.runners.mock_batch --out mock-latest     # report name

Writes ``docs/evals/reports/<name>.json`` (the compact, deterministic result) and
``docs/evals/reports/<name>.md`` (the summary people read). Raw per-session outputs (transcripts,
the specs themselves) go to ``evals/reports/<name>/`` which is gitignored. Nothing in the reports
depends on the wall clock, so a rerun of unchanged code gives identical files.

Mock mode is honest about what it cannot say: cost is zero, latency is the fake clock, and the
mock handlers are deterministic, so first-pass and probe rates measure the PIPELINE and the content
files, not a model. The live batches (``evals.runners.live_batch``) measure the model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import structlog

from evals.graders.injection import load_payloads, run_payload
from evals.graders.metrics import SessionMetrics, aggregate, collect_metrics
from evals.graders.sufficiency import ProbeReport, offline_probe_client, run_probe
from evals.harness.briefs import load_briefs
from evals.harness.catalog import ROOT, parse_situation_table
from evals.harness.driver import FlowDriver
from evals.harness.journeys import ENTERPRISE, Journey, play_journey
from evals.harness.persona import load_personas, play_persona
from evals.harness.stack import build_stack
from evals.runners._io import say

REPORTS = ROOT / "docs" / "evals" / "reports"
RAW = ROOT / "evals" / "reports"
SECTIONS = ("briefs", "enterprise", "personas", "injections", "probe")

HONESTY = [
    "Mock mode: cost per session is zero and latency is the fake clock; neither says anything about "
    "the real model. Read cost and latency from a live batch (L3).",
    "The mock handlers are deterministic and schema-valid by construction, so the first-pass "
    "validator rate and the proposal acceptance share here measure the pipeline and the content "
    "files, not Luna. Expect a small model to be weakest at spec proposals (C9, C10), the critic "
    "(C12) and repairs (C13); batch L3 and L4 report that.",
    "The sufficiency probe is answered offline by a rule-based reader of the slice text, so it "
    "can only fail when the slice lacks a field. A live run (L4) adds the model's own reading errors.",
    "Injection payloads are blocked structurally (screen, untrusted blocks, code validation); the "
    "detection rate is reported separately and is a tripwire, not a guarantee.",
]


def _save_raw(raw: Path, name: str, spec_text: str) -> None:
    """Raw outputs stay out of the repo: ``evals/reports/`` is gitignored."""
    raw.mkdir(parents=True, exist_ok=True)
    safe = name.replace(":", "-")
    (raw / f"{safe}.APP_SPEC.txt").write_bytes(spec_text.encode("utf-8"))


async def _journey_metrics(name: str, journey: Journey, raw: Path) -> tuple[SessionMetrics, str]:
    with tempfile.TemporaryDirectory() as tmp:
        stack = build_stack(Path(tmp) / "projects")
        driver = FlowDriver.for_user(stack)
        result = await play_journey(driver, journey)
        metrics = await collect_metrics(name, driver, stack)
        _save_raw(raw, name, result.spec.spec_text)
        return metrics, result.spec.spec_text


async def run_mock_batch(only: set[str], name: str) -> dict[str, Any]:
    raw = RAW / name
    sessions: list[SessionMetrics] = []
    out: dict[str, Any] = {"run": {"id": name, "mode": "mock", "sections": sorted(only)}}
    probe_rows: list[dict[str, Any]] = []
    probe_client = offline_probe_client()

    if "briefs" in only or "probe" in only:
        for brief in load_briefs():
            metrics, spec_text = await _journey_metrics(f"brief:{brief.id}", brief.journey, raw)
            report: ProbeReport | None = None
            if "probe" in only:
                report = await run_probe(probe_client, spec_text, limit=None)
                probe_rows.append({"brief": brief.id, **report.as_dict()})
            if "briefs" in only:
                sessions.append(
                    await _with_probe(metrics, report) if report is not None else metrics
                )
    if "enterprise" in only:
        for journey in (ENTERPRISE[0], ENTERPRISE[2]):
            metrics, _ = await _journey_metrics(f"enterprise:{journey.name}", journey, raw)
            sessions.append(metrics)
    persona_rows: list[dict[str, Any]] = []
    if "personas" in only:
        for persona in load_personas():
            with tempfile.TemporaryDirectory() as tmp:
                stack = build_stack(Path(tmp) / "projects")
                run = await play_persona(stack, persona)
                metrics = await collect_metrics(f"persona:{persona.id}", run.driver, stack)
                persona_rows.append(
                    {
                        "id": persona.id,
                        "title": persona.title,
                        "language": persona.language,
                        "mode": persona.mode,
                        "state": run.state,
                        "spec_ready": run.spec_ready,
                        "cards_asked": metrics.cards_asked,
                        "problems": run.problems,
                    }
                )
                if metrics.screens:
                    sessions.append(metrics)
    injection_rows: list[dict[str, object]] = []
    if "injections" in only:
        for payload in load_payloads():
            with tempfile.TemporaryDirectory() as tmp:
                outcome = await run_payload(payload, Path(tmp))
            injection_rows.append(outcome.as_row())

    summary = aggregate(sessions)
    if injection_rows:
        summary["injection_pass_rate"] = round(
            sum(1 for r in injection_rows if r["passed"]) / len(injection_rows), 4
        )
        summary["injection_detection_rate"] = round(
            sum(1 for r in injection_rows if r["flagged"]) / len(injection_rows), 4
        )
    out["metrics"] = summary
    out["sessions"] = [s.as_dict() for s in sessions]
    out["personas"] = persona_rows
    out["injections"] = injection_rows
    out["probe"] = probe_rows
    out["datasets"] = {
        "personas": len(load_personas()),
        "briefs": len(load_briefs()),
        "injections": len(load_payloads()),
        "situations": len(parse_situation_table()),
    }
    out["honesty"] = HONESTY
    return out


async def _with_probe(metrics: SessionMetrics, report: ProbeReport) -> SessionMetrics:
    from dataclasses import replace

    return replace(metrics, probe_accuracy=round(report.accuracy, 4))


def render_markdown(result: dict[str, Any]) -> str:
    run = result["run"]
    m = result["metrics"]
    cards = m["cards_per_guided_session"]
    lines = [
        f"# Eval report: {run['id']} (mock mode)",
        "",
        "Generated by `python -m evals.runners.mock_batch`. Deterministic: no wall-clock values. "
        "Read the honesty notes before quoting a number.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Sessions measured | {m['sessions']} |",
        f"| Reached `spec_ready` | {_fmt(m['reached_spec_ready'])} |",
        f"| Cards per Guided session (mean / max; target {cards['target']}, cap {cards['cap']}) | "
        f"{cards['mean']} / {cards['max']} |",
        f"| Guided sessions within the card cap | {_fmt(cards['within_cap'])} |",
        f"| Assumption rate (assumed slots / filled slots) | {_fmt(m['assumption_rate'])} |",
        f"| First-pass validator rate | {_fmt(m['first_pass_validator_rate'])} |",
        f"| Specs with zero validator errors | {_fmt(m['validators_clean_rate'])} |",
        f"| Proposals applied / dropped (accepted share) | {m['proposals']['applied']} / "
        f"{m['proposals']['dropped']} ({_fmt(m['proposals']['accepted_share'])}) |",
        f"| Screens per app (mean, min, max) | {m['screens_per_app']['mean']}, "
        f"{m['screens_per_app']['min']}, {m['screens_per_app']['max']} |",
        f"| Tokens per slice (mean, max) | {m['tokens_per_slice']['mean']}, "
        f"{m['tokens_per_slice']['max']} |",
        f"| LLM calls per session (mean) | {m['per_session']['llm_calls_mean']} |",
        f"| Cost per session, micro-dollars (mean) | {m['per_session']['cost_micros_mean']} |",
        f"| Latency per session, ms (mean; fake clock) | {m['per_session']['latency_ms_mean']} |",
        f"| Research additions accepted | {_fmt(m['research_additions_accepted'])} |",
        f"| Sufficiency probe accuracy | {_fmt(m['sufficiency_probe_accuracy'])} |",
        f"| Injection pass rate (payloads that changed nothing) | "
        f"{_fmt(m.get('injection_pass_rate'))} |",
        f"| Injection detection rate (flagged as suspicious) | "
        f"{_fmt(m.get('injection_detection_rate'))} |",
        "",
        "## Datasets",
        "",
        "| Dataset | Items |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in result["datasets"].items()],
    ]
    if result["personas"]:
        lines += [
            "",
            "## Personas",
            "",
            "| Persona | Language | Mode | End state | Cards | Problems |",
        ]
        lines += ["| --- | --- | --- | --- | --- | --- |"]
        for p in result["personas"]:
            problems = "; ".join(p["problems"]) or "none"
            lines.append(
                f"| {p['id']} | {p['language']} | {p['mode']} | {p['state']} | {p['cards_asked']} "
                f"| {problems} |"
            )
    if result["probe"]:
        lines += ["", "## Context sufficiency probe", ""]
        lines += [
            "| Brief | Screens probed | Questions | Correct | Unknown | Wrong | Missing fields |"
        ]
        lines += ["| --- | --- | --- | --- | --- | --- | --- |"]
        for r in result["probe"]:
            missing = ", ".join(r["missing_fields"]) or "none"
            lines.append(
                f"| {r['brief']} | {len(r['screens'])} | {r['asked']} | {r['correct']} | "
                f"{r['unknown']} | {r['wrong']} | {missing} |"
            )
    if result["injections"]:
        lines += [
            "",
            "## Injection payloads",
            "",
            "| Payload | Channel | Changed nothing | Flagged |",
        ]
        lines += ["| --- | --- | --- | --- |"]
        for r in result["injections"]:
            lines.append(f"| {r['id']} | {r['channel']} | {r['passed']} | {r['flagged']} |")
    lines += ["", "## Honesty notes", ""]
    lines += [f"- {note}" for note in result["honesty"]]
    return "\n".join(lines) + "\n"


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2%}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every eval dataset offline (mock mode)")
    parser.add_argument("--only", default=",".join(SECTIONS), help=f"comma list of {SECTIONS}")
    parser.add_argument("--out", default="mock-latest", help="report name (no extension)")
    args = parser.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    unknown = only - set(SECTIONS)
    if unknown:
        parser.error(f"unknown sections: {sorted(unknown)}")
    os.environ["LLM_MODE"] = "mock"
    os.environ["MDE_FORBID_LIVE"] = "1"  # this runner is offline by construction
    os.environ["MDE_ENV_FILE"] = ""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    result = asyncio.run(run_mock_batch(only, args.out))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{args.out}.json").write_bytes(
        (json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    )
    (REPORTS / f"{args.out}.md").write_bytes(render_markdown(result).encode("utf-8"))
    say(f"wrote {REPORTS / (args.out + '.json')} and {REPORTS / (args.out + '.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
