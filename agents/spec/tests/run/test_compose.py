import asyncio

from mde_spec_agent.calls.registry import all_calls, all_steps
from mde_spec_agent.testing import make_test_context
from mde_spec_core.synth import make_graph


def test_registry_is_consistent_even_before_the_lanes_are_filled():
    assert isinstance(all_calls(), list) and isinstance(all_steps(), dict)


def test_offline_context_is_mock_mode_with_a_fingerprint_and_the_cap(tmp_path):
    ctx = make_test_context(tmp_path, make_graph("tiny"))
    assert ctx.offline and ctx.runner is not None
    assert len(ctx.versions.route_fingerprint) == 24
    assert ctx.guard.record.cap_micros == 5_000_000
    asyncio.run(ctx.limiter.clean_window())
