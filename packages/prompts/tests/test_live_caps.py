"""Output-token caps keep headroom over what the real model used (live evidence, D-082).

``max_output_tokens`` includes reasoning tokens. A cap below what the model needs is cut off
(``incomplete``), and the gateway pays for a second, escalated attempt: live batch L2 saw all three
``C9b`` calls retried at the old cap of 1800 (they used 4204 to 5337), each call taking about 50 s.

``LIVE_MAX_OUTPUT_TOKENS`` is the largest ``output_tokens`` (visible plus reasoning) seen in one
live call per spec. Extend it after every live batch; never lower a cap below it with headroom.
"""

from __future__ import annotations

import pytest

from mde_prompts.registry import load_spec

HEADROOM = 1.25

LIVE_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "C1.understand": 331,
    "C2.interpret_brief": 380,
    "C6.plan_research": 391,
    "C7.extract": 528,
    "C8.synthesize_research": 2184,
    "S1.web_search": 379,
    "C10.screen_briefs": 2041,
    "C11.write_summary": 290,
    "C12.critic": 874,
    "C13.repair": 418,
    "C9a.screens_nav": 1487,
    "C9b.data_api_roles": 5337,
    "C9c.design_tokens": 330,
}


@pytest.mark.parametrize(("spec_id", "seen"), sorted(LIVE_MAX_OUTPUT_TOKENS.items()))
def test_the_cap_keeps_headroom_over_the_largest_live_call(spec_id: str, seen: int) -> None:
    cap = load_spec(spec_id).max_output_tokens
    assert cap >= seen * HEADROOM, f"{spec_id}: cap {cap} is too close to {seen} used live"
