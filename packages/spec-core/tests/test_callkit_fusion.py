from mde_spec_core.callkit import (
    FusionJob,
    SchemaMetrics,
    calls_needed,
    can_fuse,
    items_per_call,
    plan_fusion,
    unfuse_needed,
)
from mde_spec_core.callkit.schema_check import compare_limits
from mde_spec_core.config import default_config
from mde_spec_core.profiles import ConsumerProfile, SchemaLimits

CFG = default_config()
LIMITS = SchemaLimits(max_nullable_fields=16, max_depth=5, max_enum_size=100, max_union_params=16)


def prof(max_out, **kw):
    return ConsumerProfile(
        id="p",
        capability_class="weak",
        max_output_tokens=max_out,
        effective_context_tokens=100_000,
        schema_limits=LIMITS,
        **kw,
    )


def job(name, out=3000, inp=2000, nullable=2):
    return FusionJob(name, out, inp, metrics=SchemaMetrics(nullable_fields=nullable, depth=3))


def test_call_counts_differ_by_max_output():
    jobs = [job(f"j{i}") for i in range(4)]
    assert len(plan_fusion(jobs, prof(32000), CFG)) == 1  # 12k <= 16k
    assert len(plan_fusion(jobs, prof(8000), CFG)) == 4  # each 3k, cap 4k
    assert len(plan_fusion(jobs, prof(12000), CFG)) == 2


def test_output_rule_boundary_is_inclusive():
    assert can_fuse([job("a", 2000), job("b", 2000)], prof(8000), CFG).ok
    d = can_fuse([job("a", 2000), job("b", 2001)], prof(8000), CFG)
    assert not d.ok and any(r.startswith("output") for r in d.reasons)


def test_input_rule_uses_half_window_and_long_prompt_threshold():
    p = prof(32000)
    assert not can_fuse([job("a", 100, 60_000)], p, CFG).ok  # > 0.5 * 100k
    p2 = prof(32000, price={"long_prompt_threshold_tokens": 10_000})
    d = can_fuse([job("a", 100, 12_000)], p2, CFG)
    assert not d.ok and d.input_cap == 10_000


def test_fused_schema_must_compile_within_limits():
    jobs = [job("a", nullable=9), job("b", nullable=8)]  # 17 > 16
    d = can_fuse(jobs, prof(64000), CFG)
    assert not d.ok and any(r.startswith("schema") for r in d.reasons)
    assert can_fuse([job("a", nullable=8), job("b", nullable=8)], prof(64000), CFG).ok


def test_unset_schema_limit_is_unchecked_not_blocking():
    p = ConsumerProfile(id="p", capability_class="weak", max_output_tokens=32000)
    d = can_fuse([job("a"), job("b")], p, CFG)
    assert d.ok and d.unchecked
    assert compare_limits(SchemaMetrics(), SchemaLimits())  # everything reported unchecked


def test_unknown_max_output_is_conservative_not_a_crash():
    p = ConsumerProfile(id="p", capability_class="weak")
    d = can_fuse([job("a"), job("b")], p, CFG)
    assert not d.ok and d.output_cap is None and "unknown_max_output" in d.reasons[0]
    assert items_per_call(p, 100, 300, CFG) == 1
    assert len(plan_fusion([job("a"), job("b")], p, CFG)) == 2


def test_job_without_schema_does_not_fuse_with_others():
    a, b = FusionJob("a", 100, 100), FusionJob("b", 100, 100)
    assert not can_fuse([a, b], prof(32000), CFG).ok
    assert can_fuse([a], prof(32000), CFG).ok


def test_items_per_call_formula_and_clamp():
    assert items_per_call(prof(32000), 1000, 300, CFG) == 16
    assert items_per_call(prof(8000), 1000, 300, CFG) == 4
    assert items_per_call(prof(32000), 1000, 4, CFG) == 4  # kind cap
    assert items_per_call(prof(8000), 9000, 4, CFG) == 1  # at least one
    assert items_per_call(prof(32000), 0, 300, CFG) == 300  # avg clamped to 1 token, then cap


def test_calls_needed_follows_the_profile():
    assert calls_needed(300, prof(32000), 100, 300, CFG) < calls_needed(
        300, prof(8000), 100, 300, CFG
    )
    assert calls_needed(0, prof(8000), 100, 300, CFG) == 0
    assert calls_needed(300, prof(8000), 100, 300, CFG) == 8  # floor(4000/100)=40 -> ceil(300/40)


def test_unfuse_needed_uses_the_bar_of_the_detail_profile():
    assert unfuse_needed(0.84, "standard", CFG)
    assert not unfuse_needed(0.85, "standard", CFG)
    assert unfuse_needed(0.89, "deep", CFG) and not unfuse_needed(0.90, "deep", CFG)
    assert unfuse_needed(0.89, "no-such-profile", CFG)  # strictest bar for an unknown name
