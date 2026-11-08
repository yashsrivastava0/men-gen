"""Money is integer micro-dollars (1 USD = 1_000_000 micros), never float (brief 5.3)."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

MICROS_PER_USD = 1_000_000


def usd_to_micros(usd: Decimal | str | int) -> int:
    """Exact conversion; rounds up to whole micros."""
    return int((Decimal(str(usd)) * MICROS_PER_USD).to_integral_value(rounding=ROUND_CEILING))


def micros_to_usd(micros: int) -> Decimal:
    return Decimal(micros) / MICROS_PER_USD


def format_usd(micros: int, places: int = 4) -> str:
    return f"${micros_to_usd(micros):.{places}f}"


def token_cost_micros(tokens: int, usd_per_million: Decimal | str) -> int:
    """Cost of ``tokens`` at ``usd_per_million`` USD per 1M tokens, rounded UP to a whole micro.

    Because 1 USD per 1M tokens equals exactly 1 micro-dollar per token,
    ``micros = tokens * usd_per_million``.
    """
    if tokens < 0:
        raise ValueError("tokens must be >= 0")
    return int((Decimal(tokens) * Decimal(str(usd_per_million))).to_integral_value(ROUND_CEILING))
