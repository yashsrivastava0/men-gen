"""proof.freedom_probe (PS-4): the weakest configured consumer arranges one card under a style seed. Route `fast`; no
tools; the card is untrusted pack text. Not part of the product catalog."""

from __future__ import annotations

from pathlib import Path

from evals.spec.calls.freedom.mock import fallback
from evals.spec.calls.freedom.schema import FreedomIn, FreedomOut
from evals.spec.calls.freedom.validate import validate
from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

CALL_ID = "proof.freedom_probe"

SPEC: SpecCallSpec[FreedomIn, FreedomOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=FreedomIn,
        output_model=FreedomOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=1200,
        mock_handler="evals.spec.calls.freedom.mock:handle",
        untrusted_inputs=("card",),
        cacheable=False,
        repair=True,
        fallback="proof.freedom",
    ),
    kind="PROOF-FREEDOM",
    timeout_s=90.0,
    fallback=fallback,
    validators=(validate,),
)
