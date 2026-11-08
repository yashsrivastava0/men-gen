"""L0 live smoke batch (brief 9.4): model availability, strict JSON schema, reasoning effort,
web_search with citations, usage parsing incl. cached and reasoning tokens, max_tool_calls, and the
budget guard reconciling reservations against real usage.

LEAD / live-runner ONLY. Never run by subagents. Needs, in the OUTER environment:

    ALLOW_LIVE_LLM=1     (the root conftest then sets LLM_MODE=live and lets .env supply the key)

Run with ``just live-smoke`` (= ``uv run pytest tests/live -m live -k smoke -q``), then ``just budget``
and write the new total into docs/implementation/PROGRESS.md.

Spend control: every prompt is tiny, ``max_output_tokens`` is small, all calls use the scope
``test:l0-smoke`` (default cap USD 0.30) and the purpose ``must-run:l0-smoke`` (allowed up to the
80% tripwire). The module asserts that the ledger grew by less than USD 0.30 in total.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from mde_contracts.gateway import CallContext, WebSearchTool
from mde_core.money import micros_to_usd, usd_to_micros
from mde_core.settings import Settings, load_settings
from mde_llm_gateway.doctor import probe_model
from mde_llm_gateway.factory import create_gateway
from mde_llm_gateway.gateway import Gateway
from mde_llm_gateway.ledger import FileLedger, LedgerEvent
from mde_llm_gateway.registry import StaticRegistry
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    SearchIn,
)

# One event loop for the whole module: the module-scoped gateway holds pooled HTTP connections,
# which break ("Event loop is closed") if pytest-asyncio closes the loop between tests.
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

L0_CAP_MICROS = usd_to_micros("0.30")
SCOPE = "test:l0-smoke"
PURPOSE = "must-run:l0-smoke"

ECHO_LOW = dataclasses.replace(ECHO_SPEC, id="L0.echo_low", max_output_tokens=2000)
ECHO_NONE = dataclasses.replace(
    ECHO_SPEC, id="L0.echo_none", max_output_tokens=2000, reasoning_effort="none"
)
SEARCH = dataclasses.replace(
    SEARCH_SPEC,
    id="L0.search",
    max_output_tokens=2500,
    tools=(WebSearchTool(max_tool_calls=1, search_context_size="low"),),
)
SEARCH_GREEDY = dataclasses.replace(
    SEARCH,
    id="L0.search_greedy",
    tools=(WebSearchTool(max_tool_calls=1, search_context_size="low"),),
)


def ctx(name: str) -> CallContext:
    return CallContext(request_id=f"req_l0_{name}", budget_scope=SCOPE, purpose=PURPOSE)


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Live-enabled settings. Module-scoped fixtures run BEFORE the root conftest's per-test live
    guard, so they must not rely on its monkeypatching: skip unless the outer environment has
    ALLOW_LIVE_LLM=1, and pass the live switches explicitly (.env supplies only the API key)."""
    if os.environ.get("ALLOW_LIVE_LLM") != "1":
        pytest.skip("live smoke: ALLOW_LIVE_LLM=1 must be set in the outer environment")
    return load_settings(llm_mode="live", allow_live_llm=True, forbid_live=False)


@pytest.fixture(scope="module")
def ledger_path(settings: Settings) -> Path:
    return Path(settings.llm_budget_ledger)


@pytest.fixture(scope="module")
def gateway(settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> Gateway:
    long_dir = tmp_path_factory.mktemp("l0-templates")
    (long_dir / "sys.jinja").write_text("Reply with JSON only. " + ("Reference text. " * 400))
    (long_dir / "usr.jinja").write_text("Question: {{ question }}")
    registry = StaticRegistry(
        [
            ECHO_LOW,
            ECHO_NONE,
            SEARCH,
            SEARCH_GREEDY,
            dataclasses.replace(
                ECHO_SPEC,
                id="L0.long_prefix",
                max_output_tokens=2000,
                system_template="sys.jinja",
                user_template="usr.jinja",
                template_root=long_dir,
            ),
        ]
    )
    gw = create_gateway(settings, registry)
    assert gw.guard.bills, "live smoke must run against the billing guard and the shared ledger"
    return gw


@pytest.fixture(scope="module", autouse=True)
def l0_total_spend_is_under_30_cents(ledger_path: Path) -> Iterator[None]:
    """The whole batch must add less than USD 0.30 to the ledger."""
    before = FileLedger(ledger_path).peek().spent_micros
    yield
    delta = FileLedger(ledger_path).peek().spent_micros - before
    print(f"\nL0 smoke spent USD {micros_to_usd(delta):.4f} (limit 0.30)")
    assert delta < L0_CAP_MICROS, f"L0 overspent: {micros_to_usd(delta)} USD"


def _ledger_line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _ledger_events(path: Path, skip_lines: int = 0) -> list[LedgerEvent]:
    lines = path.read_text(encoding="utf-8").splitlines()[skip_lines:]
    return [LedgerEvent.model_validate_json(x) for x in lines if x.strip()]


# ---------------------------------------------------------------------------- the batch
def test_model_is_available(settings: Settings) -> None:
    """models.retrieve is free. If the model id is unavailable: stop, do NOT fall back."""
    answer = probe_model(settings)
    print(f"\n{answer}")
    assert answer.endswith("available") and "NOT" not in answer


async def test_strict_json_schema_call(gateway: Gateway, ledger_path: Path) -> None:
    before = FileLedger(ledger_path).peek().spent_micros
    result = await gateway.run(
        ECHO_LOW, EchoIn(question="Say hello in three words."), ctx("strict")
    )
    assert result.status in ("ok", "repaired") and isinstance(result.output, EchoOut)
    assert 0.0 <= result.output.confidence <= 1.0 and len(result.output.answer) >= 3
    assert result.mode == "live" and result.model == "gpt-6-luna"
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
    assert result.usage.cost_micros > 0
    after = FileLedger(ledger_path).peek().spent_micros
    assert after - before == result.usage.cost_micros  # the ledger holds exactly what was billed
    print(f"\nstrict schema: repaired={result.repaired} usage={result.usage}")


async def test_reasoning_effort_none_and_low(gateway: Gateway) -> None:
    question = EchoIn(question="What is 17 plus 25? Answer in one short sentence.")
    low = await gateway.run(ECHO_LOW, question, ctx("effort_low"))
    none = await gateway.run(ECHO_NONE, question, ctx("effort_none"))
    for result in (low, none):
        assert isinstance(result.output, EchoOut) and result.usage.output_tokens > 0
    print(
        f"\nreasoning tokens: low={low.usage.reasoning_tokens} none={none.usage.reasoning_tokens}"
    )
    assert low.usage.reasoning_tokens >= 0 and none.usage.reasoning_tokens >= 0


async def test_web_search_returns_citations_and_bills_the_call_fee(gateway: Gateway) -> None:
    result = await gateway.run(
        SEARCH,
        SearchIn(query="Which city hosted the 2024 Summer Olympics? Cite your source."),
        ctx("search"),
    )
    assert result.output is None and result.text and result.text.strip()
    assert result.search_calls >= 1
    assert result.citations, "web_search must return at least one url_citation or source"
    assert all(c.url.startswith("http") for c in result.citations)
    assert result.usage.tool_calls == result.search_calls
    assert result.usage.cost_micros >= result.search_calls * 10_000  # 0.01 USD per call
    print(
        f"\nweb_search: calls={result.search_calls} citations={len(result.citations)} "
        f"usage={result.usage}"
    )


async def test_max_tool_calls_is_respected(gateway: Gateway) -> None:
    result = await gateway.run(
        SEARCH_GREEDY,
        SearchIn(
            query="Search separately for: the tallest mountain, the longest river, and the "
            "largest desert. Use a separate search for each and cite each."
        ),
        ctx("max_tool_calls"),
    )
    print(f"\nmax_tool_calls=1 -> billed search calls: {result.search_calls}")
    assert 1 <= result.search_calls <= 1


async def test_usage_fields_parse_including_cached_and_reasoning_tokens(gateway: Gateway) -> None:
    """A >1024-token identical prefix twice: the second call MAY report cached tokens."""
    spec = gateway.registry.get("L0.long_prefix")
    first = await gateway.run(spec, EchoIn(question="Say one word."), ctx("long1"))
    second = await gateway.run(spec, EchoIn(question="Say one word."), ctx("long2"))
    for result in (first, second):
        u = result.usage
        assert u.input_tokens > 1000
        assert 0 <= u.cached_input_tokens <= u.input_tokens
        assert 0 <= u.reasoning_tokens <= u.output_tokens
    print(
        f"\ncached tokens: first={first.usage.cached_input_tokens} "
        f"second={second.usage.cached_input_tokens}"
    )
    if second.usage.cached_input_tokens:
        assert second.usage.cost_micros < first.usage.cost_micros + 10  # the discount applied


async def test_budget_guard_reconciles_reservations_with_real_usage(
    gateway: Gateway, ledger_path: Path
) -> None:
    skip = _ledger_line_count(ledger_path)
    status_before = gateway.budget_status()
    result = await gateway.run(ECHO_LOW, EchoIn(question="Name a colour."), ctx("reconcile"))
    events = _ledger_events(ledger_path, skip)
    reserves = {e.reservation_id: e for e in events if e.event == "reserve"}
    commits = [e for e in events if e.event == "commit"]
    assert len(commits) >= 1 and {c.reservation_id for c in commits} <= set(reserves)
    for commit in commits:
        worst = reserves[commit.reservation_id].worst_case_micros
        assert worst is not None and commit.cost_micros is not None
        assert commit.cost_micros <= worst, "the worst-case estimate must never be below real cost"
        usage = commit.usage
        assert usage is not None
        recomputed = gateway.guard.pricing.usage_cost_micros(
            "gpt-6-luna",
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            tool_calls=usage.tool_calls,
        )
        assert recomputed == commit.cost_micros
    status_after = gateway.budget_status()
    assert status_after.reserved_micros == 0  # every reservation closed
    assert status_after.spent_micros - status_before.spent_micros == result.usage.cost_micros
    assert status_after.live_calls == status_before.live_calls + len(commits)
    estimator = gateway.guard.estimator
    print(
        f"\nreconcile: cost={result.usage.cost_micros} micros, "
        f"calibrated chars/token={estimator.chars_per_token:.2f}, "
        f"effective cap USD {micros_to_usd(status_after.effective_cap_micros)}"
    )
    assert json.loads(status_after.model_dump_json())["tripwire"] in ("ok", "warn_50")
    assert Decimal(status_after.spent_micros) < Decimal(status_after.effective_cap_micros)
