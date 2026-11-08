from __future__ import annotations

from pathlib import Path

import pytest

from mde_core.errors import ConfigError, UnknownModelPrice
from mde_llm_gateway.budget import TokenEstimator
from mde_llm_gateway.pricing import Pricing


def test_shipped_prices_match_the_brief(pricing: Pricing) -> None:
    luna = pricing.price_for("gpt-6-luna")
    assert str(luna.input_per_m) == "0.10"
    assert str(luna.cached_input_per_m) == "0.01"
    assert str(luna.output_per_m) == "0.50"
    assert luna.long_context_threshold == 272_000
    assert str(luna.long_input_multiplier) == "2.0"
    assert str(luna.long_output_multiplier) == "1.5"
    assert pricing.tool_price("web_search").per_call_micros == 10_000


def test_usage_cost_is_exact_micros(pricing: Pricing) -> None:
    # 200K input tokens at 0.10 USD/M = 20_000 micros; 1M output tokens at 0.50 = 500_000 micros.
    assert (
        pricing.usage_cost_micros(
            "gpt-6-luna", input_tokens=200_000, cached_input_tokens=0, output_tokens=0
        )
        == 20_000
    )
    assert (
        pricing.usage_cost_micros(
            "gpt-6-luna", input_tokens=0, cached_input_tokens=0, output_tokens=1_000_000
        )
        == 500_000
    )
    # 1 token = 0.1 micro, rounded UP to 1 (never under-counts).
    assert (
        pricing.usage_cost_micros(
            "gpt-6-luna", input_tokens=1, cached_input_tokens=0, output_tokens=0
        )
        == 1
    )


def test_cached_tokens_are_a_subset_of_input(pricing: Pricing) -> None:
    # 1000 input of which 400 cached: 600 * 0.10 + 400 * 0.01 = 60 + 4 micros.
    cost = pricing.usage_cost_micros(
        "gpt-6-luna", input_tokens=1000, cached_input_tokens=400, output_tokens=0
    )
    assert cost == 64


def test_cached_more_than_input_is_clamped(pricing: Pricing) -> None:
    cost = pricing.usage_cost_micros(
        "gpt-6-luna", input_tokens=100, cached_input_tokens=500, output_tokens=0
    )
    assert cost == 1  # 100 tokens at the cached rate


def test_long_context_multipliers_apply_to_the_whole_request(pricing: Pricing) -> None:
    at_threshold = pricing.usage_cost_micros(
        "gpt-6-luna", input_tokens=272_000, cached_input_tokens=0, output_tokens=1000
    )
    above = pricing.usage_cost_micros(
        "gpt-6-luna", input_tokens=272_001, cached_input_tokens=0, output_tokens=1000
    )
    assert at_threshold == 27_200 + 500
    # 2x input: 272_001 * 0.20 = 54_400.2 rounded up; 1.5x output: 1000 * 0.75 = 750
    assert above == 54_401 + 750


def test_web_search_calls_are_billed_per_call(pricing: Pricing) -> None:
    cost = pricing.usage_cost_micros(
        "gpt-6-luna", input_tokens=0, cached_input_tokens=0, output_tokens=0, tool_calls=3
    )
    assert cost == 30_000


def test_unknown_model_is_refused(pricing: Pricing) -> None:
    with pytest.raises(UnknownModelPrice):
        pricing.price_for("gpt-9-nova")
    with pytest.raises(UnknownModelPrice):
        pricing.usage_cost_micros(
            "gpt-9-nova", input_tokens=1, cached_input_tokens=0, output_tokens=1
        )
    with pytest.raises(UnknownModelPrice):
        pricing.worst_case_micros("gpt-9-nova", input_tokens=1, max_output_tokens=1)


def test_unknown_tool_is_refused() -> None:
    pricing = Pricing.from_dict(
        {
            "models": {
                "m": {
                    "input_usd_per_million": "1",
                    "cached_input_usd_per_million": "0.1",
                    "output_usd_per_million": "2",
                }
            }
        }
    )
    with pytest.raises(UnknownModelPrice):
        pricing.worst_case_micros("m", input_tokens=1, max_output_tokens=1, max_tool_calls=1)


@pytest.mark.parametrize(
    "body",
    [
        {"input_usd_per_million": "1", "output_usd_per_million": "2"},  # no cached price
        {
            "input_usd_per_million": "0",
            "cached_input_usd_per_million": "0",
            "output_usd_per_million": "2",
        },  # zero price
        {
            "input_usd_per_million": "-1",
            "cached_input_usd_per_million": "0.1",
            "output_usd_per_million": "2",
        },
    ],
)
def test_missing_or_non_positive_price_is_rejected(body: dict[str, object]) -> None:
    with pytest.raises(UnknownModelPrice):
        Pricing.from_dict({"models": {"m": body}})


def test_bad_yaml_values_raise_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Pricing.from_dict(
            {
                "models": {
                    "m": {
                        "input_usd_per_million": "abc",
                        "cached_input_usd_per_million": "1",
                        "output_usd_per_million": "1",
                    }
                }
            }
        )
    path = tmp_path / "p.yaml"
    path.write_text("- not a mapping", encoding="utf-8")
    with pytest.raises(ConfigError):
        Pricing.load(path)


def test_worst_case_formula(pricing: Pricing) -> None:
    # 10_000 in x 0.10 = 1000 micros; 2_000 out x 0.50 = 1000 micros.
    assert (
        pricing.worst_case_micros("gpt-6-luna", input_tokens=10_000, max_output_tokens=2_000)
        == 2_000
    )
    # With 2 search calls: + 2 * (10_000 fee) and 2 * 12_000 assumed content tokens * 0.10 = 2400.
    with_tools = pricing.worst_case_micros(
        "gpt-6-luna", input_tokens=10_000, max_output_tokens=2_000, max_tool_calls=2
    )
    assert with_tools == 1_000 + 2_400 + 1_000 + 20_000


def test_worst_case_applies_long_context_early(pricing: Pricing) -> None:
    # 220_000 tokens is above 80% of 272_000, so the multipliers are assumed.
    cost = pricing.worst_case_micros("gpt-6-luna", input_tokens=220_000, max_output_tokens=0)
    assert cost == 44_000


def test_negative_counts_rejected(pricing: Pricing) -> None:
    with pytest.raises(ValueError):
        pricing.usage_cost_micros(
            "gpt-6-luna", input_tokens=-1, cached_input_tokens=0, output_tokens=0
        )
    with pytest.raises(ValueError):
        pricing.worst_case_micros("gpt-6-luna", input_tokens=1, max_output_tokens=-1)


def test_estimator_is_conservative_and_calibrates_downward_only() -> None:
    est = TokenEstimator()
    assert est.chars_per_token == 3.0
    assert est.estimate(3000) == 1000 + TokenEstimator.OVERHEAD_TOKENS
    est.observe(8000, 4000 + TokenEstimator.OVERHEAD_TOKENS)  # 2.0 chars/token (e.g. Hindi)
    assert est.chars_per_token == pytest.approx(1.8)
    est.observe(40000, 4000 + TokenEstimator.OVERHEAD_TOKENS)  # 10 chars/token: ignored (min wins)
    assert est.chars_per_token == pytest.approx(1.8)
    est2 = TokenEstimator()
    est2.observe(10_000, 1_000 + TokenEstimator.OVERHEAD_TOKENS)  # 10 chars/token
    assert est2.chars_per_token == 3.0  # never less conservative than the default
    est3 = TokenEstimator()
    est3.observe(100, 1_000 + TokenEstimator.OVERHEAD_TOKENS)
    assert est3.chars_per_token == 1.5  # floor
    with pytest.raises(ValueError):
        est.estimate(-1)
