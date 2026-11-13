"""Rules of the repo for the proof tools: no vendor or model name, no key reads, no live gate, no markdown, guarded calls."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = [
    p
    for p in ROOT.rglob("*")
    if p.is_file()
    and p.suffix in (".py", ".jinja")
    and "tests" not in p.parts
    and "__pycache__" not in p.parts
]
DATA = [p for p in ROOT.rglob("*.json") if "fixtures" not in p.parts]

# model and vendor names (assembled from parts so this file does not match itself)
_NAMES = [
    "op" + "enai",
    "anthr" + "opic",
    "cla" + "ude",
    "gem" + "ini",
    "gp" + "t",
    "ha" + "iku",
    "son" + "net",
    "op" + "us",
    "lla" + "ma",
    "mist" + "ral",
    "qw" + "en",
    "dee" + "pseek",
    "co" + "here",
]
NAME_RE = re.compile(r"\b(" + "|".join(_NAMES) + r")", re.I)


def test_there_is_code_to_check() -> None:
    assert len(CODE) > 25


def test_no_model_or_vendor_name_in_code_prompts_or_data() -> None:
    hits = []
    for p in [*CODE, *DATA]:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if NAME_RE.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
    assert not hits, hits


def test_no_key_reads_and_the_live_mode_is_never_set() -> None:
    bad = re.compile(
        r"os\.environ|getenv|putenv|API_KEY|_KEY\b|\.env\b|dotenv|setdefault\([\"']LLM", re.I
    )
    hits = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in CODE
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if bad.search(line)
    ]
    assert not hits, hits
    # the mode comes from the settings object the caller passes; the default is mock
    src = (ROOT / "common.py").read_text(encoding="utf-8")
    assert 'load_settings(llm_mode="mock")' in src


def test_no_second_live_gate_wording() -> None:
    forbidden = re.compile(
        "|".join(
            [
                "allow" + "_live",
                "guard" + "_live",
                "second" + r"[ -]key",
                "live" + " switch",
                "live-call" + " hook",
            ]
        ),
        re.I,
    )
    hits = [
        str(p)
        for p in [*CODE, *DATA, *ROOT.rglob("*.json")]
        if forbidden.search(p.read_text(encoding="utf-8"))
    ]
    assert not hits


def test_no_markdown_files_inside_the_tools() -> None:
    assert not list(ROOT.rglob("*.md"))


async def test_every_call_goes_through_the_spend_guard(runner) -> None:
    from mde_spec_agent.spend import SpendGuard

    assert isinstance(runner._guard, SpendGuard)
    assert runner._guard.prices.input_usd_per_million in (
        "0",
        0,
    )  # offline prices are zero; live needs verified prices


def test_every_proof_call_has_a_spec_a_mock_handler_and_a_fallback() -> None:
    import importlib

    from evals.spec.calls.diversity.spec import SPEC as D
    from evals.spec.calls.freedom.spec import SPEC as F
    from evals.spec.calls.lookup.spec import SPEC as L

    for s in (D, F, L):
        mod, _, fn = s.spec.mock_handler.partition(":")
        assert callable(getattr(importlib.import_module(mod), fn))
        assert callable(s.fallback) and s.validators and s.spec.id.startswith("proof.")
        assert s.spec.route in ("fast", "standard", "deep") and not s.spec.tools
        assert s.spec.untrusted_inputs  # pack text and public docs are data: no tools


def test_the_gateway_registry_has_the_proof_and_verify_calls() -> None:
    from evals.spec.common import proof_call_specs

    ids = {s.id for s in proof_call_specs()}
    assert {
        "proof.freedom_probe",
        "proof.lookup_probe",
        "proof.diversity_critic",
        "ver.canary",
        "ver.final_critic",
        "ver.structure_critic",
    } <= ids
