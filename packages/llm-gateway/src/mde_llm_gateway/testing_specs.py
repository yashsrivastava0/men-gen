"""Test-only call specs, input/output models, mock handlers and templates.

Shared by the gateway tests and the gateway-API tests (the gateway must not import ``mde_prompts``).
Templates live in ``testing_fixtures/``. Nothing here is registered in production.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Literal, cast

from pydantic import Field

from mde_contracts.common import Frozen
from mde_contracts.gateway import (
    CallContext,
    CallSpec,
    Citation,
    FreeTextResult,
    LlmCallSink,
    WebSearchTool,
)
from mde_core.clock import Clock, FakeClock
from mde_core.settings import Settings
from mde_llm_gateway.budget import BudgetGuard
from mde_llm_gateway.gateway import Gateway, GatewayConfig
from mde_llm_gateway.ledger import Ledger, MemoryLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers.base import Provider
from mde_llm_gateway.providers.mock import MockProvider
from mde_llm_gateway.registry import AnySpec, SpecRegistry, StaticRegistry
from mde_llm_gateway.testing import FIXTURES_DIR, budget_config_for_tests

HANDLER_MODULE = "mde_llm_gateway.testing_specs"


class EchoIn(Frozen):
    question: str
    style: Literal["short", "long"] = "short"


class EchoOut(Frozen):
    answer: str = Field(min_length=3, description="The answer")
    confidence: float = Field(ge=0.0, le=1.0, description="0 to 1")
    note: str | None = None


class SearchIn(Frozen):
    query: str
    language: str = "en"


class ExtractIn(Frozen):
    page: str
    hint: str = ""
    notes: list[str] = []


class ExtractOut(Frozen):
    facts: list[str]


def echo_handle(input: EchoIn) -> EchoOut:
    return EchoOut(answer=f"echo: {input.question}", confidence=0.9)


def search_handle(input: SearchIn) -> FreeTextResult:
    return FreeTextResult(
        text=f"Results for {input.query}",
        citations=[Citation(url="https://example.com/a", title="A")],
        search_calls=1,
    )


def extract_handle(input: ExtractIn) -> ExtractOut:
    return ExtractOut(facts=[f"page has {len(input.page)} chars"])


def wrong_type_handle(input: EchoIn) -> FreeTextResult:
    return FreeTextResult(text="oops")


def bad_output_handle(input: EchoIn) -> dict[str, object]:
    return {"answer": "x"}


ECHO_SPEC: AnySpec = CallSpec(
    id="T1.echo",
    version=1,
    input_model=EchoIn,
    output_model=EchoOut,
    system_template="echo_system.jinja",
    user_template="echo_user.jinja",
    template_root=FIXTURES_DIR,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=300,
    mock_handler=f"{HANDLER_MODULE}:echo_handle",
    fallback="echo_fallback",
)  # type: ignore[arg-type]

CACHEABLE_SPEC: AnySpec = CallSpec(
    id="T2.cacheable",
    version=1,
    input_model=EchoIn,
    output_model=EchoOut,
    system_template="echo_system.jinja",
    user_template="echo_user.jinja",
    template_root=FIXTURES_DIR,
    route="fast",
    reasoning_effort="none",
    max_output_tokens=200,
    mock_handler=f"{HANDLER_MODULE}:echo_handle",
    cacheable=True,
)  # type: ignore[arg-type]

NO_REPAIR_SPEC: AnySpec = CallSpec(
    id="T3.norepair",
    version=1,
    input_model=EchoIn,
    output_model=EchoOut,
    system_template="echo_system.jinja",
    user_template="echo_user.jinja",
    template_root=FIXTURES_DIR,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=200,
    mock_handler=f"{HANDLER_MODULE}:echo_handle",
    repair=False,
)  # type: ignore[arg-type]

SEARCH_SPEC: AnySpec = CallSpec(
    id="T4.search",
    version=2,
    input_model=SearchIn,
    output_model=None,
    system_template="search_system.jinja",
    user_template="search_user.jinja",
    template_root=FIXTURES_DIR,
    route="deep",
    reasoning_effort="low",
    max_output_tokens=500,
    mock_handler=f"{HANDLER_MODULE}:search_handle",
    tools=(WebSearchTool(max_tool_calls=2, search_context_size="low"),),
)  # type: ignore[arg-type]

EXTRACT_SPEC: AnySpec = CallSpec(
    id="T5.extract",
    version=1,
    input_model=ExtractIn,
    output_model=ExtractOut,
    system_template="extract_system.jinja",
    user_template="extract_user.jinja",
    template_root=FIXTURES_DIR,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=400,
    mock_handler=f"{HANDLER_MODULE}:extract_handle",
    untrusted_inputs=("page", "notes"),
)  # type: ignore[arg-type]

BROKEN_TEMPLATE_SPEC: AnySpec = CallSpec(
    id="T6.broken",
    version=1,
    input_model=EchoIn,
    output_model=EchoOut,
    system_template="broken_system.jinja",
    user_template="broken_user.jinja",
    template_root=FIXTURES_DIR,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=100,
    mock_handler=f"{HANDLER_MODULE}:echo_handle",
)  # type: ignore[arg-type]

ALL_SPECS: tuple[AnySpec, ...] = (
    ECHO_SPEC,
    CACHEABLE_SPEC,
    NO_REPAIR_SPEC,
    SEARCH_SPEC,
    EXTRACT_SPEC,
    BROKEN_TEMPLATE_SPEC,
)


def sneaky_untrusted_tools_spec() -> AnySpec:
    """A spec that combines untrusted inputs with tools, built AROUND ``CallSpec.__post_init__``.

    ``CallSpec`` refuses to be constructed like this; the gateway must still reject such a spec if
    one ever reaches it (defence in depth), and the registry/API must surface the same error.
    """
    sneaky = cast("AnySpec", object.__new__(CallSpec))
    for f in dataclasses.fields(SEARCH_SPEC):
        object.__setattr__(sneaky, f.name, getattr(SEARCH_SPEC, f.name))
    object.__setattr__(sneaky, "id", "T7.sneaky")
    object.__setattr__(sneaky, "untrusted_inputs", ("query",))
    return sneaky


def make_test_registry(*extra: AnySpec) -> StaticRegistry:
    """A registry holding every test spec (plus any ``extra`` ones)."""
    return StaticRegistry((*ALL_SPECS, *extra))


def fake_api_key() -> str:
    """An obviously fake key, assembled at runtime so no key-shaped literal sits in the repo."""
    return "sk-" + "test-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4"


def make_settings(**overrides: object) -> Settings:
    """``Settings`` that ignore any ``.env`` file; keyword overrides use the field names.

    Under pytest ``MDE_FORBID_LIVE=1`` is set, so pass ``forbid_live=False`` explicitly to build
    live-enabled settings for gating tests.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg,arg-type]


def make_ctx(
    *,
    scope: str = "test:unit",
    purpose: str = "test",
    request_id: str = "req_test",
    session_id: str | None = None,
    extra_scopes: tuple[str, ...] = (),
    **kwargs: object,
) -> CallContext:
    """A ``CallContext`` for tests (scope ``test:unit`` has the 0.30 USD test cap)."""
    return CallContext(
        request_id=request_id,
        budget_scope=scope,
        purpose=purpose,
        session_id=session_id,
        extra_scopes=extra_scopes,
        **kwargs,  # type: ignore[arg-type]
    )


def build_test_gateway(
    provider: Provider | None = None,
    *,
    clock: Clock | None = None,
    ledger: Ledger | None = None,
    cap_usd: str = "6.00",
    settings: Settings | None = None,
    registry: SpecRegistry | None = None,
    sink: LlmCallSink | None = None,
    config: GatewayConfig | None = None,
    seed: int = 0,
) -> Gateway:
    """Assemble a :class:`Gateway` for tests: in-memory ledger unless one is given, seeded jitter.

    ``provider`` defaults to :class:`MockProvider`. The guard bills iff the provider is billable.
    """
    the_clock: Clock = clock or FakeClock()
    the_provider: Provider = provider or MockProvider()
    the_settings = settings or Settings(_env_file=None)  # type: ignore[call-arg]
    the_ledger = ledger or MemoryLedger(clock=the_clock)
    guard = BudgetGuard(
        budget_config_for_tests(cap_usd),
        Pricing.load(),
        the_ledger,
        bills=the_provider.billable,
        clock=the_clock,
    )
    return Gateway(
        registry=registry or make_test_registry(),
        provider=the_provider,
        guard=guard,
        settings=the_settings,
        sink=sink,
        clock=the_clock,
        config=config,
        rng=random.Random(seed),  # noqa: S311
    )
