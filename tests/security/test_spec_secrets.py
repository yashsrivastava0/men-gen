"""The spec file never holds a secret, and the validator that guards that really fires.

* a key-like string planted anywhere in a valid spec is an ``error`` of the ``no_secrets`` validator
  (so such a spec can never be accepted), for the high-signal kinds and for a password assignment;
* the real specs produced by the journeys (guided and enterprise) pass a secret scan;
* the redaction helpers find what they should and leave ordinary text alone.
"""

from __future__ import annotations

import pytest

from evals.harness.journeys import ENTERPRISE, GUIDED
from mde_content.stack import load_stack_profile
from mde_contracts.policy import Limits
from mde_core.redact import find_secrets, redact_text
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_builder.validators import run_validators

PLANTS = {
    "openai": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",  # secret-scan: allow
    "aws": "AKIAIOSFODNN7EXAMPLE",  # secret-scan: allow
    "github": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # secret-scan: allow
    "assignment": "api_key = Tr0ub4dor3xyzQ9abc",  # secret-scan: allow
}


@pytest.mark.parametrize("kind", PLANTS)
def test_a_planted_secret_makes_the_spec_invalid(kind: str) -> None:
    spec = make_sample_spec(8)
    clean = run_validators(spec, stack_profile=load_stack_profile(), limits=Limits())
    assert [f for f in clean if f.severity == "error"] == []
    screen = spec.screens[0].model_copy(
        update={"purpose": f"Welcome the shopper. Use {PLANTS[kind]} for the service"}
    )
    dirty = spec.model_copy(update={"screens": [screen, *spec.screens[1:]]})
    findings = run_validators(dirty, stack_profile=load_stack_profile(), limits=Limits())
    errors = [f for f in findings if f.severity == "error" and f.validator == "no_secrets"]
    assert errors, f"a planted {kind} secret was not caught"
    assert PLANTS[kind] not in "\n".join(f.message for f in errors)  # the finding never echoes it


def test_the_rendered_sample_spec_is_free_of_secret_patterns() -> None:
    assert find_secrets(render_spec(make_sample_spec(12))) == []


def test_redaction_removes_what_it_finds_and_leaves_ordinary_text() -> None:
    text = f"key {PLANTS['openai']} and token {PLANTS['github']}, but sk-learn is fine"
    clean, kinds = redact_text(text)
    assert PLANTS["openai"] not in clean and PLANTS["github"] not in clean
    assert set(kinds) >= {"openai_key", "github_token"}
    assert "sk-learn is fine" in clean
    assert redact_text("An online shop for handmade candles, open 9 to 5")[1] == []


@pytest.mark.e2e
@pytest.mark.parametrize("journey", [GUIDED[0], ENTERPRISE[0]], ids=lambda j: j.name)
async def test_journey_specs_pass_a_secret_scan(journey, tmp_path, guard) -> None:  # type: ignore[no-untyped-def]
    from evals.harness.driver import FlowDriver
    from evals.harness.journeys import play_journey
    from evals.harness.stack import build_stack

    stack = build_stack(tmp_path / "projects")
    result = await play_journey(FlowDriver.for_user(stack), journey)
    assert find_secrets(result.spec.spec_text) == []
