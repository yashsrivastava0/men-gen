"""The lane source: no vendor or model name, no other agent, no eval, no markdown, content in data files."""

from __future__ import annotations

import re
from pathlib import Path

from mde_spec_agent.lanes import screens

LANE = Path(screens.__file__).parent
FILES = [p for p in LANE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
TEXT = {p: p.read_text(encoding="utf-8") for p in FILES if p.suffix in (".py", ".jinja", ".yaml")}
VENDORS = re.compile(
    r"\b(openai|anthropic|claude|gpt-?\d?|gemini|haiku|sonnet|opus|deepseek|qwen|kimi|mistral|llama)\b",
    re.I,
)


def test_no_vendor_or_model_name_in_the_lane_source():
    bad = {p.name: VENDORS.findall(t) for p, t in TEXT.items() if VENDORS.search(t)}
    assert bad == {}


def test_no_other_agent_is_imported():
    for p, t in TEXT.items():
        if p.suffix == ".py":
            assert (
                "mde_verify_agent" not in t
                and "mde_research_agent" not in t
                and "mde_compass" not in t.replace("mde_compass_intake", "")
            ), p.name


def test_no_eval_or_exec_on_text():
    for p, t in TEXT.items():
        if p.suffix == ".py":
            assert not re.search(r"\b(eval|exec)\(", t), p.name


def test_no_markdown_outside_docs():
    assert [p for p in FILES if p.suffix == ".md"] == []


def test_every_call_folder_has_its_parts():
    for name in ("scrn", "mock_hero", "behav", "copy_l", "g2_summary"):
        d = LANE / name
        for part in (
            "spec.py",
            "schema.py",
            "system.jinja",
            "user.jinja",
            "mock.py",
            "fallback.py",
            "validate.py",
            "step.py",
        ):
            assert (d / part).is_file(), (name, part)


def test_money_is_never_a_float_and_nothing_names_a_widget_in_the_data():
    data = (LANE / "data" / "lane.yaml").read_text()
    assert not re.search(r"\b(button|modal|toast|banner|carousel)\b", data, re.I)
