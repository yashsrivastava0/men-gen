import re
from pathlib import Path

import pytest

from mde_spec_core import tooling
from mde_spec_core.tooling import (
    SMOKES,
    ToolsError,
    default_tools,
    import_tool,
    installed_licence_text,
    installed_version,
    run_smoke,
)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PYTHON_TOOLS = [t for t in default_tools().tools if not t.proof_only]


def test_requested_tools_are_recorded():
    ids = {t.id for t in default_tools().tools}
    assert {
        "faker", "babel", "phonenumbers", "python-stdnum", "coloraide", "jsonschema", "pydantic",
        "openapi-spec-validator", "pyyaml", "jinja2", "promptfoo",
    } <= ids  # fmt: skip


def test_every_tool_has_source_and_date():
    for t in default_tools().tools:
        assert t.source_url.startswith("https://"), t.id
        assert ISO_DATE.match(t.checked_on), t.id
        assert t.licence_expected and t.licence_tokens, t.id


def test_promptfoo_is_proof_only_with_a_node_floor():
    t = default_tools().get("promptfoo")
    assert t.proof_only and t.smoke is None
    assert t.node_requirement == ">=22.22.0"


def test_every_smoke_named_in_data_exists_in_code_and_vice_versa():
    named = {t.smoke.id for t in PYTHON_TOOLS if t.smoke}
    assert named == set(SMOKES)
    with pytest.raises(ToolsError):
        run_smoke("nope")


@pytest.mark.parametrize("tool", PYTHON_TOOLS, ids=lambda t: t.id)
def test_installed_version_and_licence_match_the_record(tool):
    import_tool(tool)
    version = installed_version(tool.distribution)
    assert re.match(r"^\d+(\.\d+)*", version), version
    text = installed_licence_text(tool.distribution)
    assert any(tok.lower() in text.lower() for tok in tool.licence_tokens), (tool.id, text)


@pytest.mark.parametrize("tool", PYTHON_TOOLS, ids=lambda t: t.id)
def test_smoke_test_passes_offline(tool):
    result = run_smoke(tool.smoke.id)
    assert result.ok, (tool.id, result.observed)


def test_copyleft_packages_are_flagged():
    sheet = default_tools()
    assert sheet.get("python-stdnum").copyleft is True
    assert "LGPL" in installed_licence_text("python-stdnum")
    assert not any(t.copyleft for t in sheet.tools if t.id != "python-stdnum")


def test_indian_grouping_and_kannada_gap_are_exact():
    assert run_smoke("babel_indian_grouping").observed == "12,34,567.891"
    out = run_smoke("faker_indian_locales")
    assert out.ok and "kn_IN raises" in out.observed


def test_no_version_is_hard_coded_in_the_data_file():
    from mde_spec_core.tooling import TOOLS_PATH

    text = Path(TOOLS_PATH).read_text(encoding="utf-8")
    assert "installed_version" not in text
    assert tooling.installed_version("pyyaml") not in text.replace('"2026-11-14"', "")


def test_tooling_code_names_no_model_vendor():
    text = Path(tooling.__file__).read_text(encoding="utf-8").lower()
    for name in ("openai", "anthropic", "claude", "gemini", "gpt-"):
        assert name not in text
