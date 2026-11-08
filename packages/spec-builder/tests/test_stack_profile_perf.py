"""The ``stack_profile`` validator is linear and cached (it used to dominate a build's CPU).

A build validates the whole spec after every proposal; the keyword lookups of every screen used to be
redone each time. ``capabilities`` now compiles each capability once per ``StackProfile`` and
remembers what every text mentions. The findings must stay exactly what they were: the first test
compares against the original scan on real and sample specs.
"""

from __future__ import annotations

import re
import time

import pytest
from helpers import make_note, make_request

from mde_content.stack import load_stack_profile
from mde_contracts.calls import ValidatorFinding
from mde_contracts.content import StackProfile
from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.proposals import quick_findings
from mde_spec_builder.testing import make_sample_spec, make_sample_stack_profile
from mde_spec_builder.validators import capabilities
from mde_spec_builder.validators._common import finding, id_list
from mde_spec_builder.validators.capabilities import stack_profile

_WORDS = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return " " + " ".join(_WORDS.findall(text.lower())) + " "


def _contains(normalized: str, term: str) -> bool:
    words = _WORDS.findall(term.lower())
    return bool(words) and f" {' '.join(words)} " in normalized


def reference(spec: AppSpec, profile: StackProfile) -> list[ValidatorFinding]:
    """The capability scan as it was written first: every term against every screen, every time
    (only the part of the validator that was slow)."""
    out: list[ValidatorFinding] = []
    corpora = [(cid, _normalize(text)) for cid, text in capabilities._corpora(spec)]
    for capability in profile.capabilities:
        terms = [capability.id, capability.label, *capability.keywords]
        hits = [cid for cid, norm in corpora if any(_contains(norm, t) for t in terms)]
        if not hits:
            continue
        if not capability.supported:
            out.append(
                finding(
                    "stack_profile",
                    f"{capability.label} is not supported by stack profile {profile.id} "
                    f"(needed by {id_list(hits)}){': ' + capability.note if capability.note else ''}",
                    *hits,
                )
            )
        elif capability.needs_dev_build:
            limit = f" {capability.preview_limit}" if capability.preview_limit else ""
            out.append(
                finding(
                    "stack_profile",
                    f"{capability.label} needs a development build (needed by "
                    f"{id_list(hits)}).{limit}",
                    *hits,
                    severity="warn",
                )
            )
    return out


def capability_findings(spec: AppSpec, profile: StackProfile) -> list[ValidatorFinding]:
    """What ``stack_profile`` reports from the capability scan (not the header or app checks)."""
    return [
        f
        for f in stack_profile(spec, profile)
        if "is flagged as needing" not in f.message
        and "looks like" not in f.message
        and "targets stack profile" not in f.message
    ]


@pytest.mark.parametrize("archetype", ["commerce", "food_delivery", "booking_services", "custom"])
def test_the_findings_are_the_ones_the_original_scan_gave_on_real_specs(archetype: str) -> None:
    profile = load_stack_profile()
    spec = build_baseline(make_request(archetype, research=make_note()))
    assert capability_findings(spec, profile) == reference(spec, profile)
    # words from the real profile in places the scan reads: some are flagged, some are errors
    risky = spec.model_copy(
        update={
            "behavior": spec.behavior.model_copy(
                update={"notifications": ["push notifications for offers", "Camera access"]}
            )
        }
    )
    got = capability_findings(risky, profile)
    assert got == reference(risky, profile) and got
    assert stack_profile(risky, profile) == stack_profile(risky, profile)  # cached, same answer


@pytest.mark.parametrize("seed", range(6))
def test_the_findings_match_on_sample_specs_and_the_sample_profile(seed: int) -> None:
    profile = make_sample_stack_profile()
    spec = make_sample_spec(8 + seed * 7, seed=seed)
    assert capability_findings(spec, profile) == reference(spec, profile)


def test_a_changed_text_is_read_again_and_a_changed_profile_is_not_confused_with_the_old_one() -> (
    None
):
    profile = make_sample_stack_profile()
    spec = make_sample_spec(5)
    before = capability_findings(spec, profile)
    screen = spec.screens[1].model_copy(update={"sections": ["fingerprint_prompt"]})
    edited = spec.model_copy(
        update={"screens": [screen if s.id == screen.id else s for s in spec.screens]}
    )
    after = capability_findings(edited, profile)
    assert after != before and after == reference(edited, profile)
    other = profile.model_copy(update={"capabilities": profile.capabilities[:1]})
    assert capability_findings(edited, other) == reference(edited, other)
    assert capability_findings(edited, profile) == after


def test_a_50_screen_spec_validates_fast_however_many_times_it_is_asked() -> None:
    spec = make_sample_spec(50)
    profile = load_stack_profile()  # the real, large profile
    limits = Limits(max_screens=100)
    quick_findings(spec, limits, profile)  # the first read of every text
    started = time.perf_counter()
    for _ in range(100):  # a build checks the spec after every proposal
        stack_profile(spec, profile)
    cached = time.perf_counter() - started
    # generous: the uncached scan took about 1.8 s for these 100 passes, this takes a few hundredths
    assert cached < 1.5, f"stack_profile took {cached:.2f}s for 100 passes over 50 screens"
    started = time.perf_counter()
    findings = quick_findings(spec, limits, make_sample_stack_profile())
    assert time.perf_counter() - started < 1.5
    assert findings == []
