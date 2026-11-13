"""Rule 14: the Research agent does not depend on a model vendor.

It names routes (fast, standard, deep); the gateway knows models, vendors, prices and keys. This test
keeps vendor and model names out of the agent's source, and checks that what the agent asks the gateway
(what is missing, whether the checker shares a vendor with the writer) follows the routes, not a string.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from mde_core.settings import Settings
from mde_research_agent.compose import readiness
from mde_research_agent.config import ResearchSettings

SRC = Path(__file__).resolve().parents[1] / "src"
VENDOR_WORDS = re.compile(
    r"\b(openai|anthropic|claude|haiku|sonnet|opus|gemini|gpt-\d|deepseek|qwen|mistral|llama)\b",
    re.I,
)
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".jinja", ".js", ".html", ".json", ".css"}

CLAUDE = {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "deep": "claude-haiku-5-5"}


def test_no_vendor_or_model_name_appears_in_the_research_source() -> None:
    hits: list[str] = []
    for path in SRC.rglob("*"):
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if VENDOR_WORDS.search(line):
                hits.append(f"{path.relative_to(SRC)}:{number}")
    assert not hits, (
        "a model vendor or model name is in the Research source (rule 14): " + ", ".join(hits)
    )


def _core(**kw: object) -> Settings:
    return Settings(_env_file=None, llm_mode="live", forbid_live=False, **kw)  # type: ignore[call-arg,arg-type]


def _cfg() -> ResearchSettings:
    return ResearchSettings(_env_file=None, tavily_api_key=SecretStr("t" * 20))  # type: ignore[call-arg]


def test_readiness_names_the_vendor_key_a_route_needs_and_nothing_else() -> None:
    assert readiness(_cfg(), _core(model_routes=CLAUDE)) == [
        "ANTHROPIC_API_KEY (route fast uses claude-haiku-5-5)"
    ]
    assert readiness(_cfg(), _core(model_routes=CLAUDE, anthropic_api_key="k" * 20)) == []
    # the same agent on the other vendor needs the other key, with no change in the agent
    assert readiness(_cfg(), _core()) == ["OPENAI_API_KEY (route fast uses gpt-6-luna)"]
    assert readiness(_cfg(), _core(openai_api_key="k" * 20)) == []


def test_readiness_names_an_unpriced_model() -> None:
    routes = {**CLAUDE, "standard": "gemini-9"}
    missing = readiness(_cfg(), _core(model_routes=routes, anthropic_api_key="k" * 20))
    assert missing == ["a price for gemini-9 in the gateway's pricing.yaml (route standard)"]


def test_readiness_without_core_is_only_about_search_keys() -> None:
    assert readiness(_cfg()) == []
    assert readiness(ResearchSettings(_env_file=None)) == ["TAVILY_API_KEY or SERPER_API_KEY"]  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("routes", "same"),
    [
        (CLAUDE, True),  # a Haiku writer and a Sonnet checker are one vendor
        ({"fast": "claude-haiku-5-5", "standard": "gpt-6-luna", "deep": "claude-haiku-5-5"}, False),
        ({"fast": "gpt-6-luna", "standard": "gpt-6-luna", "deep": "gpt-6-luna"}, True),
    ],
)
def test_the_checker_label_follows_the_routes_vendors(routes: dict[str, str], same: bool) -> None:
    from mde_llm_gateway.factory import routes_share_vendor

    assert routes_share_vendor(_core(model_routes=routes), "standard", "fast") is same


def test_every_research_call_spec_fits_every_providers_schema_limits() -> None:
    """The first Claude run lost 39 extraction calls to a 400: two schemas had 18 nullable fields, the limit is 16.
    Each spec's schema, as the provider would send it, must stay inside the limits, and an answer with the
    unwrapped fields left out must come back valid for the spec's own output model."""
    import json

    from mde_llm_gateway.providers.anthropic_messages import (
        MAX_OPTIONAL_PARAMS,
        MAX_UNION_PARAMS,
        _count_optionals,
        _count_unions,
        restore_omitted_nulls,
        to_anthropic_schema,
    )
    from mde_llm_gateway.strict_schema import to_strict_schema
    from mde_research_agent.calls.registry import all_specs

    checked = 0
    for spec in all_specs():
        model = spec.spec.output_model
        if model is None:
            continue
        strict = to_strict_schema(model)
        sent = to_anthropic_schema(strict)
        assert _count_unions(sent) <= MAX_UNION_PARAMS, spec.id
        assert _count_optionals(sent) <= MAX_OPTIONAL_PARAMS, spec.id
        checked += 1
    assert checked >= 10  # the extraction jobs, plan, queries, gaps, assemble, check, judges

    # a model that leaves out every optional field: the restored answer still has the original shape
    spec = next(s for s in all_specs() if s.id == "rsch.extract.data")
    assert spec.spec.output_model is not None
    strict = to_strict_schema(spec.spec.output_model)
    required_only = {"items": []}
    restored = json.loads(restore_omitted_nulls(json.dumps(required_only), strict))
    assert restored == required_only
