"""The seed_research templates render through the real gateway renderer with user words wrapped as data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.seed_research.schema import SeedResearchInput
from mde_compass.calls.seed_research.spec import SPEC
from mde_llm_gateway.render import PromptRenderer

EXAMPLES = sorted(
    (Path(__file__).parents[3] / "src/mde_compass/calls/seed_research/examples").glob("*.json")
)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_templates_render_with_untrusted_wrapping(path: Path) -> None:
    inp = SeedResearchInput.model_validate(json.loads(path.read_text(encoding="utf-8"))["input"])
    prompt = PromptRenderer().render(SPEC.spec, inp)
    assert "<untrusted_content" in prompt.user and "None" not in prompt.user
    for line in inp.exclusions:
        assert line in prompt.user
    assert "ONLY apps the user named" in prompt.system
    assert SPEC.spec.tools == () and "exclusions" in SPEC.spec.untrusted_inputs
