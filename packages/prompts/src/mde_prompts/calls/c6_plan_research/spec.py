"""C6.plan_research: which research jobs to run and up to ``max_queries_per_job`` generic queries each.

``max_queries_per_job`` comes from the engine (the depth cap spread over the web jobs that apply, D-078:
2 at Light, up to 8 when few jobs apply), so a Deep plan can carry 20 searches. The engine trims the
answer to the depth cap and the remaining budget, drops jobs the Brief does not justify, and
enforces query privacy again in code. The deterministic fallback
``c6_deterministic_plan`` is ``mde_prompts.calls.c6_plan_research.plan.deterministic_plan``.
"""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C6Input, C6Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C6Input, C6Output] = CallSpec(
    id="C6.plan_research",
    version=2,
    input_model=C6Input,
    output_model=C6Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="low",
    max_output_tokens=3000,  # up to 20 queries over five jobs, plus reasoning
    mock_handler="mde_prompts.calls.c6_plan_research.mock:handle",
    cacheable=False,
    repair=True,
    fallback="c6_deterministic_plan",
)
