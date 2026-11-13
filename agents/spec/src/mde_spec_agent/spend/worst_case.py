"""Worst-case cost of one call, in integer micro-USD (doc 19 5): input x input price + max output x output price,
plus the long-prompt surcharge from the profile. Everything is Decimal / int; never float.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal


@dataclass(frozen=True)
class Prices:
    """Per-million prices as exact decimal strings (from a consumer profile or the price list)."""

    input_usd_per_million: str
    output_usd_per_million: str
    long_prompt_threshold_tokens: int | None = None
    long_prompt_multiplier: str | None = None


def _micros(tokens: int, usd_per_million: Decimal) -> int:
    # 1 USD per 1M tokens == exactly 1 micro-USD per token
    return int((Decimal(tokens) * usd_per_million).to_integral_value(rounding=ROUND_CEILING))


def worst_case_micros(input_tokens: int, max_output_tokens: int, prices: Prices) -> int:
    if input_tokens < 0 or max_output_tokens < 0:
        raise ValueError("token counts must be >= 0")
    in_p = Decimal(prices.input_usd_per_million)
    out_p = Decimal(prices.output_usd_per_million)
    if (
        prices.long_prompt_threshold_tokens is not None
        and prices.long_prompt_multiplier is not None
        and input_tokens > prices.long_prompt_threshold_tokens
    ):
        mult = Decimal(prices.long_prompt_multiplier)
        in_p *= mult
        out_p *= mult  # the whole request is billed at the multiplied rates
    return _micros(input_tokens, in_p) + _micros(max_output_tokens, out_p)
