"""P1.sufficiency_probe: a small model sees ONLY a spec slice and answers concrete questions.

Every wrong or "unknown" answer marks a field the slice is missing (spec section 12). This call
belongs to the evals (``evals/probes``), not to the product catalog: nothing in ``mde_prompts``
imports it. It is a normal call spec, so it runs through the real gateway: offline with its mock
handler (a rule-based reader of the slice text), live in batch L4 with the small model.
"""

from __future__ import annotations

from pathlib import Path

from evals.probes.sufficiency.models import ProbeInput, ProbeOutput
from mde_contracts.gateway import CallSpec

PROBE_ID = "P1.sufficiency_probe"

SPEC: CallSpec[ProbeInput, ProbeOutput] = CallSpec(
    id=PROBE_ID,
    version=1,
    input_model=ProbeInput,
    output_model=ProbeOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=900,
    mock_handler="evals.probes.sufficiency.mock:handle",
    tools=(),
    untrusted_inputs=("slice_text",),
    cacheable=False,
    repair=True,
    fallback=None,
)
