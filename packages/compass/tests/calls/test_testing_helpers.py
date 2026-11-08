"""Smoke-test the call test helpers with a throwaway call defined in a temp package."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner, CompassCallSpec
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_contracts.gateway import CallSpec
from mde_core.errors import ProviderError

CALL_MODULE = """
from pydantic import BaseModel

class In(BaseModel):
    text: str

class Out(BaseModel):
    answer: str

def handle(inp: In) -> Out:
    return Out(answer="mock:" + inp.text)
"""


@pytest.fixture
def spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pkg = tmp_path / "cmp_probe_call"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(textwrap.dedent(CALL_MODULE))
    (pkg / "system.jinja").write_text("You answer briefly.")
    (pkg / "user.jinja").write_text("Text: {{ text }}")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("cmp_probe_call.mod", None)
    from cmp_probe_call.mod import In, Out  # type: ignore[import-not-found]

    core = CallSpec(
        id="cmp.probe_helper",
        version=1,
        input_model=In,
        output_model=Out,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=pkg,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=300,
        mock_handler="cmp_probe_call.mod:handle",
        fallback="probe",
    )
    return (
        CompassCallSpec(
            spec=core, lane="writer", timeout_s=5, fallback=lambda i: Out(answer="fb:" + i.text)
        ),
        In,
        Out,
    )


async def test_make_client_runs_the_real_mock_pipeline(spec) -> None:
    cs, In, _Out = spec
    runner = CallRunner(make_client([cs]))
    res = await runner.run(cs, In(text="hello"), session_id="ses_1", tenant_id="t")
    assert res.output.answer == "mock:hello" and not res.used_fallback


async def test_scripted_gateway_drives_fallbacks(spec) -> None:
    cs, In, Out = spec
    gateway = ScriptedGateway(ProviderError("down"), Out(answer="second"))
    runner = CallRunner(gateway)  # type: ignore[arg-type]
    first = await runner.run(cs, In(text="a"), session_id="ses_1", tenant_id="t")
    second = await runner.run(cs, In(text="b"), session_id="ses_1", tenant_id="t")
    assert first.used_fallback and first.output.answer == "fb:a"
    assert not second.used_fallback and second.output.answer == "second"
