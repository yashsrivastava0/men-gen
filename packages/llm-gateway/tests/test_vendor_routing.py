"""Vendor independence (rule 14): the vendor of a call follows its model, and the model follows the route.

Covers the router, the factory's per-vendor assembly, the ``MODEL_ROUTES`` spellings and the key
checks. No network, no key: clients are stubs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mde_core.clock import FakeClock
from mde_core.errors import ConfigError, LiveCallForbidden, UnknownModelPrice
from mde_core.settings import Settings, describe_presence
from mde_llm_gateway.factory import create_gateway, vendors_in_use
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.providers import ProviderCallError, ProviderRequest, ScriptedProvider
from mde_llm_gateway.providers.anthropic_messages import AnthropicMessagesProvider
from mde_llm_gateway.providers.openai_responses import OpenAIResponsesProvider
from mde_llm_gateway.providers.routing import RoutingProvider
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    EchoIn,
    EchoOut,
    build_test_gateway,
    fake_api_key,
    make_ctx,
    make_settings,
    make_test_registry,
)

GOOD = EchoOut(answer="hello there", confidence=0.5)
CLAUDE = {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "deep": "claude-haiku-5-5"}
MIXED = {"fast": "claude-haiku-5-5", "standard": "gpt-6-luna", "deep": "claude-haiku-5-5"}
OPENAI = {"fast": "gpt-6-luna", "standard": "gpt-6-luna", "deep": "gpt-6-luna"}


def live(**kw: object) -> Settings:
    base: dict[str, object] = {"llm_mode": "live", "forbid_live": False}
    return make_settings(**{**base, **kw})


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return Pricing.load()


def _scripted(name: str) -> ScriptedProvider:
    provider = ScriptedProvider(mode="live", billable=True)
    provider.name = name
    provider.set_default("*", GOOD)
    return provider


# ---------------------------------------------------------------------------- the router
async def test_the_router_sends_each_call_to_the_vendor_of_its_model(
    tmp_path: Path, pricing: Pricing
) -> None:
    openai_side, claude_side = _scripted("openai-responses"), _scripted("anthropic-messages")
    router = RoutingProvider({"openai": openai_side, "anthropic": claude_side}, pricing.vendor_for)
    assert router.vendors == ["anthropic", "openai"] and router.billable and router.mode == "live"
    settings = live(model_routes=MIXED)
    gw = build_test_gateway(
        router,
        settings=settings,
        ledger=FileLedger(tmp_path / "ledger.jsonl", clock=FakeClock()),
    )
    await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx(route_override="fast"))
    await gw.run(ECHO_SPEC, EchoIn(question="q"), make_ctx(route_override="standard"))
    assert [c.model for c in claude_side.calls] == ["claude-haiku-5-5"]
    assert [c.model for c in openai_side.calls] == ["gpt-6-luna"]


async def test_a_model_whose_vendor_has_no_provider_is_refused_before_any_call(
    pricing: Pricing,
) -> None:
    only_openai = _scripted("openai-responses")
    router = RoutingProvider({"openai": only_openai}, pricing.vendor_for)
    request = _request(model="claude-haiku-5-5")
    with pytest.raises(ProviderCallError, match="anthropic") as exc:
        await router.complete(request)
    assert not exc.value.retryable and not exc.value.maybe_billed and only_openai.calls == []


def _request(model: str) -> ProviderRequest:
    return ProviderRequest(
        spec=ECHO_SPEC,
        input=EchoIn(question="q"),
        model=model,
        system="s",
        user="u",
        effort="low",
        max_output_tokens=100,
        tools=(),
        schema={"type": "object"},
        schema_name="n",
        schema_hash="h",
        timeout_s=5.0,
    )


def test_a_router_needs_at_least_one_provider(pricing: Pricing) -> None:
    with pytest.raises(ValueError):
        RoutingProvider({}, pricing.vendor_for)


# ---------------------------------------------------------------------------- which vendors a config needs
def test_vendors_in_use_follow_the_routes_and_name_the_route(pricing: Pricing) -> None:
    assert vendors_in_use(live(model_routes=OPENAI), pricing) == {
        "openai": "route fast uses gpt-6-luna"
    }
    assert vendors_in_use(live(model_routes=CLAUDE), pricing) == {
        "anthropic": "route fast uses claude-haiku-5-5"
    }
    mixed = vendors_in_use(live(model_routes=MIXED), pricing)
    assert mixed == {
        "anthropic": "route fast uses claude-haiku-5-5",
        "openai": "route standard uses gpt-6-luna",
    }


def test_a_route_missing_from_model_routes_falls_back_to_the_default_model(
    pricing: Pricing,
) -> None:
    settings = live(model_routes={"standard": "claude-sonnet-5-5"})
    # fast and deep are not listed: they use ``openai_model`` (the default), so both vendors are needed.
    assert set(vendors_in_use(settings, pricing)) == {"openai", "anthropic"}


def test_a_model_with_no_price_is_refused_at_build_time_with_its_name(pricing: Pricing) -> None:
    with pytest.raises(UnknownModelPrice, match="gemini-9"):
        vendors_in_use(live(model_routes={**OPENAI, "deep": "gemini-9"}), pricing)


# ---------------------------------------------------------------------------- the factory
def test_a_claude_only_config_builds_without_any_openai_key(tmp_path: Path) -> None:
    settings = live(model_routes=CLAUDE, llm_budget_ledger=tmp_path / "l.jsonl")
    gw = create_gateway(settings, make_test_registry(), anthropic_client=object())
    assert isinstance(gw.provider, AnthropicMessagesProvider) and gw.guard.bills


def test_an_openai_only_config_builds_without_any_anthropic_key(tmp_path: Path) -> None:
    settings = live(model_routes=OPENAI, llm_budget_ledger=tmp_path / "l.jsonl")
    gw = create_gateway(settings, make_test_registry(), openai_client=object())
    assert isinstance(gw.provider, OpenAIResponsesProvider)


def test_a_mixed_config_builds_a_router_with_one_provider_per_vendor(tmp_path: Path) -> None:
    settings = live(model_routes=MIXED, llm_budget_ledger=tmp_path / "l.jsonl")
    gw = create_gateway(
        settings, make_test_registry(), openai_client=object(), anthropic_client=object()
    )
    assert isinstance(gw.provider, RoutingProvider)
    assert gw.provider.vendors == ["anthropic", "openai"]
    assert gw.provider.name == "routing(anthropic-messages+openai-responses)"


def test_a_recording_gateway_wraps_the_router(tmp_path: Path) -> None:
    settings = live(model_routes=MIXED, llm_budget_ledger=tmp_path / "l.jsonl")
    gw = create_gateway(
        settings,
        make_test_registry(),
        openai_client=object(),
        anthropic_client=object(),
        record_cassettes=True,
        cassette_dir=tmp_path,
    )
    assert gw.provider.name == "recording"


def test_a_missing_key_is_refused_only_for_a_vendor_a_route_uses(tmp_path: Path) -> None:
    ledger = tmp_path / "l.jsonl"
    with pytest.raises(ConfigError, match=r"ANTHROPIC_API_KEY.*route fast uses claude-haiku-5-5"):
        create_gateway(
            live(model_routes=CLAUDE, openai_api_key=fake_api_key(), llm_budget_ledger=ledger),
            make_test_registry(),
        )
    with pytest.raises(ConfigError, match=r"OPENAI_API_KEY.*route fast uses gpt-6-luna"):
        create_gateway(
            live(model_routes=OPENAI, anthropic_api_key=fake_api_key(), llm_budget_ledger=ledger),
            make_test_registry(),
        )
    # the mixed config needs both, and names the one that is missing
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        create_gateway(
            live(model_routes=MIXED, anthropic_api_key=fake_api_key(), llm_budget_ledger=ledger),
            make_test_registry(),
        )


def test_the_real_anthropic_client_is_built_with_retries_disabled(tmp_path: Path) -> None:
    settings = live(
        model_routes=CLAUDE,
        anthropic_api_key=fake_api_key(),
        llm_budget_ledger=tmp_path / "l.jsonl",
    )
    gw = create_gateway(settings, make_test_registry())
    assert gw.provider._client.max_retries == 0  # type: ignore[attr-defined]


def test_a_vendor_without_a_provider_is_a_clear_config_error(tmp_path: Path) -> None:
    pricing = Pricing.from_dict(
        {
            "models": {
                "mystery-1": {
                    "vendor": "mystery",
                    "input_usd_per_million": "1",
                    "cached_input_usd_per_million": "0.1",
                    "output_usd_per_million": "2",
                }
            }
        }
    )
    settings = live(
        model_routes={"fast": "mystery-1", "standard": "mystery-1", "deep": "mystery-1"},
        llm_budget_ledger=tmp_path / "l.jsonl",
    )
    with pytest.raises(ConfigError, match="no provider for it"):
        create_gateway(settings, make_test_registry(), pricing=pricing)


def test_live_is_still_the_only_switch() -> None:
    with pytest.raises(LiveCallForbidden):
        create_gateway(
            make_settings(llm_mode="live", model_routes=CLAUDE, anthropic_api_key=fake_api_key()),
            make_test_registry(),
            anthropic_client=object(),
        )  # MDE_FORBID_LIVE=1 under pytest


# ---------------------------------------------------------------------------- MODEL_ROUTES and the keys
def test_model_routes_accepts_json_pairs_and_one_model_for_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def routes(value: str) -> dict[str, str]:
        monkeypatch.setenv("MODEL_ROUTES", value)
        return Settings(_env_file=None).model_routes  # type: ignore[call-arg]

    assert routes('{"fast": "a", "standard": "b", "deep": "c"}') == CLAUDE_LIKE
    assert routes("fast=a, standard=b ,deep=c") == CLAUDE_LIKE
    assert routes("claude-haiku-5-5") == {
        "fast": "claude-haiku-5-5",
        "standard": "claude-haiku-5-5",
        "deep": "claude-haiku-5-5",
    }
    assert routes("") == OPENAI  # blank = the defaults
    for bad in ("fast=", "=x", "fast=a,standard", '{"fast": '):
        monkeypatch.setenv("MODEL_ROUTES", bad)
        with pytest.raises(ValueError):
            Settings(_env_file=None)  # type: ignore[call-arg]


CLAUDE_LIKE = {"fast": "a", "standard": "b", "deep": "c"}


def test_the_anthropic_key_is_a_secret_everywhere_a_key_is() -> None:
    key = fake_api_key()
    settings = make_settings(anthropic_api_key=key)
    assert key in settings.secret_values()
    assert key not in repr(settings) and key not in str(settings.model_dump())
    report = describe_presence(settings)
    assert report["ANTHROPIC_API_KEY"] == "present" and report["OPENAI_API_KEY"] == "absent"
    assert report["MODEL_ROUTES"] == "fast=gpt-6-luna, standard=gpt-6-luna, deep=gpt-6-luna"
    assert key not in " ".join(report.values())


# ---------------------------------------------------------------------------- doctor probe
def test_the_model_probe_asks_each_vendor_about_the_models_its_routes_use() -> None:
    import anthropic
    import httpx2
    import openai

    from mde_llm_gateway.doctor import probe_model

    def resp(status: int) -> httpx2.Response:
        return httpx2.Response(
            status, request=httpx2.Request("GET", "https://api.example/v1/models")
        )

    class Models:
        def __init__(self, outcome: Exception | None, asked: list[str]) -> None:
            self.outcome, self.asked = outcome, asked

        def retrieve(self, model: str) -> object:
            self.asked.append(model)
            if self.outcome is not None:
                raise self.outcome
            return object()

    class Client:
        def __init__(self, outcome: Exception | None, asked: list[str]) -> None:
            self.models = Models(outcome, asked)

    settings = live(model_routes=MIXED)
    asked_openai: list[str] = []
    asked_claude: list[str] = []
    out = probe_model(
        settings,
        client=Client(None, asked_openai),  # type: ignore[arg-type]
        anthropic_client=Client(None, asked_claude),  # type: ignore[arg-type]
    )
    assert out == "claude-haiku-5-5: available; gpt-6-luna: available"
    assert asked_claude == ["claude-haiku-5-5"] and asked_openai == [
        "gpt-6-luna"
    ]  # each asked once

    claude_only = live(model_routes=CLAUDE)
    for exc, words in (
        (anthropic.NotFoundError("x", response=resp(404), body=None), "NOT available"),
        (anthropic.AuthenticationError("x", response=resp(401), body=None), "ANTHROPIC_API_KEY"),
        (anthropic.PermissionDeniedError("x", response=resp(403), body=None), "not permitted"),
        (anthropic.InternalServerError("x", response=resp(500), body=None), "HTTP 500"),
    ):
        text = probe_model(claude_only, anthropic_client=Client(exc, []))  # type: ignore[arg-type]
        assert words in text and "claude-sonnet-5-5" in text
    assert openai.OpenAIError  # the OpenAI path keeps its own tests in test_units.py


def test_the_probe_names_a_model_with_no_price() -> None:
    from types import SimpleNamespace

    from mde_llm_gateway.doctor import probe_model

    ok = SimpleNamespace(models=SimpleNamespace(retrieve=lambda model: object()))
    text = probe_model(live(model_routes={**OPENAI, "deep": "gemini-9"}), client=ok)  # type: ignore[arg-type]
    assert (
        text
        == "gpt-6-luna: available; gemini-9: no price on file (every call to it would be refused)"
    )
