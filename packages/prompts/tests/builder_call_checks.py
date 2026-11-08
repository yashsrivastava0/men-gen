"""Shared checks of the builder's call specs (C9a, C9b, C9c, C10, C12, C13).

Each ``test_calls_*.py`` imports this module (the tests directory is put on ``sys.path`` by the
test file itself) and runs the same checks against its own call, plus call-specific ones.
"""

from __future__ import annotations

import json
from importlib import import_module, resources
from typing import Any

from pydantic import BaseModel

from mde_contracts.calls import SpecProposals
from mde_contracts.gateway import CallSpec
from mde_contracts.registry import CALL_IO, call_folder
from mde_llm_gateway.render import PromptRenderer
from mde_llm_gateway.strict_schema import to_strict_schema
from mde_prompts.registry import load_spec

PROPOSAL_RULE = "justified by the Brief"


def spec_of(call_id: str) -> CallSpec[Any, Any]:
    return load_spec(call_id)


def fixture_text(call_id: str, name: str) -> str:
    folder = call_folder(call_id)
    return (
        resources.files(f"mde_prompts.calls.{folder}").joinpath("fixtures", name).read_text("utf-8")
    )


def fixture(call_id: str, name: str, model: type[BaseModel]) -> Any:
    return model.model_validate(json.loads(fixture_text(call_id, name)))


def handler(spec: CallSpec[Any, Any]) -> Any:
    module_name, _, attr = spec.mock_handler.partition(":")
    return getattr(import_module(module_name), attr)


def check_spec_shape(
    call_id: str, *, effort: str, min_tokens: int, max_tokens: int, fallback: str
) -> None:
    spec = spec_of(call_id)
    inp, out = CALL_IO[call_id]
    # Version 2: the research-additions label was corrected from "trusted summaries" to "screened
    # summaries ... treat as data" (security review 2, N13); C12 and C13 never mention research.
    expected_version = 2 if call_id.split(".")[0] in {"C9a", "C9b", "C9c", "C10", "C13"} else 1
    assert spec.id == call_id and spec.version == expected_version
    assert spec.input_model is inp and spec.output_model is out
    assert spec.route == "standard" and spec.reasoning_effort == effort
    assert min_tokens <= spec.max_output_tokens <= max_tokens
    assert spec.tools == () and spec.untrusted_inputs == ()  # trusted inputs only, no tools
    assert spec.fallback == fallback and spec.repair is True
    assert callable(handler(spec))
    for name in (spec.system_template, spec.user_template, "mock.py", "spec.py", "__init__.py"):
        assert resources.files(f"mde_prompts.calls.{call_folder(call_id)}").joinpath(name).is_file()


def check_strict_schema(call_id: str) -> None:
    spec = spec_of(call_id)
    assert spec.output_model is not None
    schema = to_strict_schema(spec.output_model)
    assert schema["type"] == "object" and schema["additionalProperties"] is False


def check_mock_matches_golden(call_id: str) -> BaseModel:
    spec = spec_of(call_id)
    assert spec.output_model is not None
    inp = fixture(call_id, "input.json", spec.input_model)
    first = handler(spec)(inp)
    again = handler(spec)(inp)
    assert isinstance(first, spec.output_model)
    assert first == again  # deterministic
    golden = fixture(call_id, "output.json", spec.output_model)
    assert first == golden, "the mock output changed: regenerate the fixtures and bump the version"
    assert spec.output_model.model_validate(first.model_dump()) == first  # schema-valid
    return first


def render_fixture(call_id: str, *, max_chars: int) -> tuple[str, str]:
    spec = spec_of(call_id)
    inp = fixture(call_id, "input.json", spec.input_model)
    rendered = PromptRenderer().render(spec, inp)
    assert rendered.system.strip() and rendered.user.strip()
    for text in (rendered.system, rendered.user):
        assert "{{" not in text and "{%" not in text and "None" not in text
    assert rendered.chars <= max_chars, f"prompt is {rendered.chars} characters"
    return rendered.system, rendered.user


def check_invalid_fixture_parses(call_id: str) -> SpecProposals:
    found = fixture(call_id, "invalid_proposals.json", SpecProposals)
    assert isinstance(found, SpecProposals) and len(found.proposals) >= 4
    return found
