"""Pricing (brief 7.9): exact integer micro-dollar costs from ``pricing.yaml``.

Rules that make this safe:

* Unknown model, missing price, unknown tool or a non-positive price => :class:`UnknownModelPrice`
  and the call is refused. There is no default price.
* Every cost is rounded UP to a whole micro-dollar per component (never under-counts).
* Long context (more than ``threshold_tokens`` input tokens) multiplies input AND output rates for
  the whole request (OpenAI's model page: "applied to the entire request").
* The *worst case* used for reservations over-estimates on purpose: it applies the long-context
  multipliers from 80% of the threshold and assumes extra content tokens for every tool call.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import cast

import yaml

from mde_core.errors import ConfigError, UnknownModelPrice
from mde_core.money import token_cost_micros, usd_to_micros

_LONG_CONTEXT_MARGIN = Decimal("0.8")  # worst case applies multipliers from 80% of the threshold


@dataclass(frozen=True)
class ModelPrice:
    model: str
    vendor: str  # which provider serves this model (a key of the factory's vendor table)
    input_per_m: Decimal
    cached_input_per_m: Decimal
    output_per_m: Decimal
    long_context_threshold: int
    long_input_multiplier: Decimal
    long_output_multiplier: Decimal
    # The lowest reasoning effort worth sending to this model (neutral scale none/low/medium/high), "" for none.
    # Measured, not assumed: see the row's comment in pricing.yaml.
    effort_floor: str = ""


@dataclass(frozen=True)
class ToolPrice:
    name: str
    per_call_micros: int
    assumed_content_tokens: int


def _decimal(value: object, what: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"pricing: {what} is not a number: {value!r}") from exc
    if not out.is_finite():
        raise ConfigError(f"pricing: {what} is not finite")
    return out


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"pricing: {what} must be a mapping")
    return cast("dict[str, object]", value)


class Pricing:
    """Price list. Build with :meth:`load` (package default) or :meth:`from_dict` (tests)."""

    def __init__(
        self, models: dict[str, ModelPrice] | None = None, tools: dict[str, ToolPrice] | None = None
    ) -> None:
        self._models = dict(models or {})
        self._tools = dict(tools or {})

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> Pricing:
        if path is None:
            text = resources.files("mde_llm_gateway").joinpath("pricing.yaml").read_text("utf-8")
        else:
            text = Path(path).read_text("utf-8")
        return cls.from_dict(_as_dict(yaml.safe_load(text), "document"))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Pricing:
        models: dict[str, ModelPrice] = {}
        for name, raw in _as_dict(data.get("models", {}), "models").items():
            body = _as_dict(raw, f"models.{name}")
            lc = _as_dict(body.get("long_context", {}), f"models.{name}.long_context")
            try:
                price = ModelPrice(
                    model=name,
                    vendor=str(body.get("vendor", "")).strip().lower(),
                    input_per_m=_decimal(body["input_usd_per_million"], f"{name}.input"),
                    cached_input_per_m=_decimal(
                        body["cached_input_usd_per_million"], f"{name}.cached_input"
                    ),
                    output_per_m=_decimal(body["output_usd_per_million"], f"{name}.output"),
                    long_context_threshold=int(cast("int", lc.get("threshold_tokens", 0))),
                    long_input_multiplier=_decimal(lc.get("input_multiplier", 1), f"{name}.lc_in"),
                    long_output_multiplier=_decimal(
                        lc.get("output_multiplier", 1), f"{name}.lc_out"
                    ),
                    effort_floor=str(body.get("effort_floor", "")).strip().lower(),
                )
            except KeyError as exc:
                raise UnknownModelPrice(f"pricing for {name}: missing {exc.args[0]}") from exc
            if min(price.input_per_m, price.cached_input_per_m, price.output_per_m) <= 0:
                raise UnknownModelPrice(f"pricing for {name}: prices must be positive")
            if price.long_input_multiplier < 1 or price.long_output_multiplier < 1:
                raise ConfigError(f"pricing for {name}: long-context multipliers must be >= 1")
            if price.effort_floor not in ("", "none", "low", "medium", "high"):
                raise ConfigError(
                    f"pricing for {name}: effort_floor must be none, low, medium or high"
                )
            if not price.vendor:
                raise ConfigError(
                    f"pricing for {name}: missing vendor (the provider that serves the model)"
                )
            models[name] = price
        tools: dict[str, ToolPrice] = {}
        for name, raw in _as_dict(data.get("tools", {}), "tools").items():
            body = _as_dict(raw, f"tools.{name}")
            if "per_call_usd" not in body:
                raise UnknownModelPrice(f"pricing for tool {name}: missing per_call_usd")
            per_call = usd_to_micros(_decimal(body["per_call_usd"], f"{name}.per_call"))
            tools[name] = ToolPrice(
                name=name,
                per_call_micros=per_call,
                assumed_content_tokens=int(cast("int", body.get("assumed_content_tokens", 0))),
            )
        return cls(models, tools)

    # ------------------------------------------------------------------ lookup
    def price_for(self, model: str) -> ModelPrice:
        try:
            return self._models[model]
        except KeyError:
            raise UnknownModelPrice(
                f"no price on file for model {model!r}; the call is refused",
                details={"model": model},
            ) from None

    def vendor_for(self, model: str) -> str:
        """The vendor that serves ``model`` (from the price list: one row per model, one place to add)."""
        return self.price_for(model).vendor

    def tool_price(self, tool: str) -> ToolPrice:
        try:
            return self._tools[tool]
        except KeyError:
            raise UnknownModelPrice(
                f"no price on file for tool {tool!r}; the call is refused", details={"tool": tool}
            ) from None

    def known_models(self) -> list[str]:
        return sorted(self._models)

    # ------------------------------------------------------------------ costs
    def usage_cost_micros(
        self,
        model: str,
        *,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        tool_calls: int = 0,
        tool: str = "web_search",
    ) -> int:
        """Real cost of one provider response, rounded up to whole micro-dollars.

        ``input_tokens`` includes the cached ones: a provider that reports cache reads separately
        (Anthropic) is normalised to this shape inside its provider, never here.
        ``output_tokens`` includes reasoning tokens (billed as output).
        """
        if min(input_tokens, cached_input_tokens, output_tokens, tool_calls) < 0:
            raise ValueError("token and tool-call counts must be >= 0")
        price = self.price_for(model)
        cached = min(cached_input_tokens, input_tokens)
        uncached = input_tokens - cached
        in_mult, out_mult = Decimal(1), Decimal(1)
        if price.long_context_threshold and input_tokens > price.long_context_threshold:
            in_mult, out_mult = price.long_input_multiplier, price.long_output_multiplier
        cost = token_cost_micros(uncached, price.input_per_m * in_mult)
        cost += token_cost_micros(cached, price.cached_input_per_m * in_mult)
        cost += token_cost_micros(output_tokens, price.output_per_m * out_mult)
        if tool_calls:
            cost += tool_calls * self.tool_price(tool).per_call_micros
        return cost

    def worst_case_micros(
        self,
        model: str,
        *,
        input_tokens: int,
        max_output_tokens: int,
        max_tool_calls: int = 0,
        tool: str = "web_search",
    ) -> int:
        """Conservative upper bound for one provider call (what ``reserve`` holds)."""
        if min(input_tokens, max_output_tokens, max_tool_calls) < 0:
            raise ValueError("counts must be >= 0")
        price = self.price_for(model)
        extra_tokens = 0
        tool_fee = 0
        if max_tool_calls:
            tp = self.tool_price(tool)
            extra_tokens = max_tool_calls * tp.assumed_content_tokens
            tool_fee = max_tool_calls * tp.per_call_micros
        total_input = input_tokens + extra_tokens
        in_mult, out_mult = Decimal(1), Decimal(1)
        threshold = price.long_context_threshold
        if threshold and Decimal(total_input) >= Decimal(threshold) * _LONG_CONTEXT_MARGIN:
            in_mult, out_mult = price.long_input_multiplier, price.long_output_multiplier
        # Price every input token at the uncached rate (never cheaper than reality).
        cost = token_cost_micros(total_input, price.input_per_m * in_mult)
        cost += token_cost_micros(max_output_tokens, price.output_per_m * out_mult)
        return cost + tool_fee
