"""spec.gloss_l: the glossary in the source language and every extra language, one call. Route `fast`; no tools; the
glossary words are the Brief's own, so they are untrusted data. The route is a name, never a model."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.design.gloss_l.fallback import fallback
from mde_spec_agent.lanes.design.gloss_l.schema import GlossIn, GlossOut
from mde_spec_agent.lanes.design.gloss_l.validate import validate

CALL_ID = "spec.gloss_l"
VERSION = 1
MAX_OUTPUT_TOKENS = 8000

SPEC: SpecCallSpec[GlossIn, GlossOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=GlossIn,
        output_model=GlossOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        mock_handler="mde_spec_agent.lanes.design.gloss_l.mock:handle",
        untrusted_inputs=("items",),
        cacheable=False,
        repair=True,
        fallback="spec.gloss_l",
    ),
    kind="GLOSS-L",
    timeout_s=90.0,
    fallback=fallback,
    validators=(validate,),
)
