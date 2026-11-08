"""The sample generator is valid by construction: it passes all nine validators at any size."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec, make_sample_stack_profile
from mde_spec_builder.validators import run_validators
from mde_spec_loader import SpecIndex


@settings(max_examples=30, deadline=None)
@given(st.integers(min_value=1, max_value=65), st.integers(min_value=0, max_value=200))
def test_every_sample_passes_every_validator(n: int, seed: int) -> None:
    spec = make_sample_spec(n, seed=seed)
    assert len(spec.screens) == n
    findings = run_validators(
        spec, stack_profile=make_sample_stack_profile(), limits=Limits(max_screens=100)
    )
    assert findings == [], findings


@pytest.mark.parametrize("n", [1, 2, 11, 12, 19, 20, 51, 52, 53, 80])
def test_sample_sizes_around_the_module_boundaries(n: int) -> None:
    spec = make_sample_spec(n)
    assert [s.id for s in spec.screens] == [f"SCR-{i:03d}" for i in range(1, n + 1)]
    findings = run_validators(
        spec, stack_profile=make_sample_stack_profile(), limits=Limits(max_screens=200)
    )
    assert findings == []


def test_default_cap_flags_a_51_screen_sample() -> None:
    spec = make_sample_spec(51)
    findings = run_validators(spec, stack_profile=make_sample_stack_profile(), limits=Limits())
    assert [f.validator for f in findings] == ["limits"]
    assert findings[0].target_ids == ["SCR-051"]


def test_sample_content_is_realistic_and_complete() -> None:
    spec = make_sample_spec(50)
    assert len(spec.flows) >= 5 and sum(f.primary for f in spec.flows) == 1
    assert len(spec.build.steps) == 11
    assert {r.id for r in spec.roles.roles} == {"ROLE-customer", "ROLE-admin"}
    assert len(spec.data.entities) >= 15 and len(spec.api.operations) >= 40
    per_screen = SpecIndex.from_text(render_spec(spec)).index()
    sizes = [b.tokens for b in per_screen.blocks if b.kind == "screen"]
    assert 150 <= sum(sizes) / len(sizes) <= 700  # same order of magnitude as spec 6.2's estimate


def test_sample_is_deterministic_and_seeded() -> None:
    assert make_sample_spec(15, seed=4) == make_sample_spec(15, seed=4)
    assert make_sample_spec(15, seed=4) != make_sample_spec(15, seed=5)


def test_sample_rejects_zero_screens() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        make_sample_spec(0)


def test_one_screen_sample_has_a_navigation_and_no_flow() -> None:
    spec = make_sample_spec(1)
    assert [s.id for s in spec.screens] == ["SCR-001"] and spec.flows == []
    assert spec.build.steps[5].screens == ["SCR-001"] and spec.build.steps[5].flow is None


def test_sample_header_hash_is_the_rendered_hash() -> None:
    spec: AppSpec = make_sample_spec(8)
    assert len(spec.header.hash) == 64
    assert SpecIndex.from_text(render_spec(spec)).verify(spec.header.hash)
