"""spec.prod_module: PROD-MODULE, one call per module (or per part of a big module). Route `standard`; no tools; the
module's Compass slice, the Brief's facts and the Research pack are untrusted data (hard rule 5). The route is a name:
switching the model behind it is configuration."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

from .fallback import fallback
from .schema import ModuleIn, ModuleOut
from .validate import validate

CALL_ID = "spec.prod_module"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"

SPEC: SpecCallSpec[ModuleIn, ModuleOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=ModuleIn,
        output_model=ModuleOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=16000,
        mock_handler="mde_spec_agent.lanes.product.prod_module.mock:handle",
        untrusted_inputs=("slice", "facts", "research"),
        cacheable=False,
        repair=True,
        fallback="spec.prod_module",
    ),
    kind="PROD-MODULE",
    timeout_s=180.0,
    fallback=fallback,
    validators=(validate,),
)
