"""MockProvider: the default offline mode (brief 7.4).

Calls each spec's ``mock_handler`` (a dotted path ``"pkg.module:function"``, signature
``def handle(input: I) -> O | FreeTextResult``, D-021) and returns its deterministic, schema-valid
output as JSON. No network, no key, cost 0. Free-text (S1) handlers return ``FreeTextResult``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from importlib import import_module

from pydantic import BaseModel, ValidationError

from mde_contracts.gateway import CallMode, FreeTextResult
from mde_core.errors import ConfigError
from mde_llm_gateway.providers.base import ProviderRequest, ProviderResponse, synthetic_usage


def resolve_handler(path: str) -> Callable[[BaseModel], object]:
    """Import ``"pkg.module:function"``. Raises :class:`ConfigError` if it cannot be resolved."""
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        raise ConfigError(f"mock_handler must look like 'pkg.module:function', got {path!r}")
    try:
        handler = getattr(import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"cannot import mock handler {path!r}: {exc}") from exc
    if not callable(handler):
        raise ConfigError(f"mock handler {path!r} is not callable")
    return handler  # type: ignore[no-any-return]


class MockProvider:
    name = "mock"
    billable = False

    def __init__(self) -> None:
        self.mode: CallMode = "mock"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        spec = request.spec
        handler = resolve_handler(spec.mock_handler)
        result = handler(request.input)
        if inspect.isawaitable(result):
            result = await result
        if spec.output_model is None:
            if not isinstance(result, FreeTextResult):
                raise ConfigError(
                    f"{spec.id}: the free-text mock handler must return FreeTextResult"
                )
            return ProviderResponse(
                text=result.text,
                usage=synthetic_usage(request, result.text, tool_calls=result.search_calls),
                citations=list(result.citations),
                search_calls=result.search_calls,
            )
        if isinstance(result, FreeTextResult):
            raise ConfigError(
                f"{spec.id}: a structured call's mock handler returned FreeTextResult"
            )
        try:
            output = (
                result
                if isinstance(result, spec.output_model)
                else spec.output_model.model_validate(result)
            )
        except ValidationError as exc:
            raise ConfigError(
                f"{spec.id}: mock handler output does not match {spec.output_model.__name__}: "
                f"{exc.error_count()} error(s)"
            ) from exc
        text = output.model_dump_json()
        return ProviderResponse(text=text, usage=synthetic_usage(request, text))
