"""``cmp.read_chunk``: typed statements from ONE document chunk, each with a verbatim quote."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.read_chunk.fallback import fallback
from mde_compass.calls.read_chunk.schema import ReadChunkInput, ReadChunkOutput
from mde_compass.calls.read_chunk.validate import validate_output
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.read_chunk"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[ReadChunkInput, ReadChunkOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=2,
        input_model=ReadChunkInput,
        output_model=ReadChunkOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.read_chunk.mock:handle",
        tools=(),
        untrusted_inputs=("chunk_text",),
        cacheable=False,
        repair=True,
        fallback="compass.read_chunk",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(validate_output,),
)
