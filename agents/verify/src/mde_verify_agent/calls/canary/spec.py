"""ver.canary: the weakest configured consumer reads exactly one task card's reads. Route `fast` (the cheapest route is
the weakest consumer stand-in); no tools; the reads are untrusted pack text."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_core.verify_port import PortCallSpec
from mde_verify_agent.calls.canary.fallback import fallback
from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut
from mde_verify_agent.calls.canary.validate import validate

CALL_ID = "ver.canary"

SPEC = PortCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=CanaryIn,
        output_model=CanaryOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=4000,
        mock_handler="mde_verify_agent.calls.canary.mock:handle",
        untrusted_inputs=("criteria", "reads"),
        cacheable=False,
        repair=True,
        fallback="verify.canary",
    ),
    kind="VERIFY-CANARY",
    timeout_s=90.0,
    fallback=fallback,
    validators=(validate,),
)
