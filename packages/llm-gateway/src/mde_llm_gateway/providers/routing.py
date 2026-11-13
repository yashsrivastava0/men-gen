"""RoutingProvider: one provider per model vendor, chosen PER CALL by the request's model.

The model of a call comes from the route (``MODEL_ROUTES``: fast / standard / deep -> a model id) and
the vendor of a model comes from the price list (``pricing.yaml``), so switching one route to another
vendor, or all of them, is a configuration change and touches no engine (rule 14). A router is only
built when the configured routes need more than one vendor; with one vendor the factory returns that
vendor's provider directly.

The router makes no call itself and adds no retry: it hands the request to the vendor's provider,
which makes exactly one call. A model whose vendor has no provider here (no key, or not built) is a
non-retryable error raised before any call, so nothing is billed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from mde_contracts.gateway import CallMode
from mde_llm_gateway.providers.base import (
    Provider,
    ProviderCallError,
    ProviderRequest,
    ProviderResponse,
)


class RoutingProvider:
    billable = True

    def __init__(self, providers: Mapping[str, Provider], vendor_of: Callable[[str], str]) -> None:
        if not providers:
            raise ValueError("a RoutingProvider needs at least one vendor provider")
        self._providers = dict(providers)
        self._vendor_of = vendor_of
        self.mode: CallMode = "live"
        self.name = "routing(" + "+".join(sorted(p.name for p in self._providers.values())) + ")"

    @property
    def vendors(self) -> list[str]:
        return sorted(self._providers)

    def provider_for(self, model: str) -> Provider:
        vendor = self._vendor_of(model)
        provider = self._providers.get(vendor)
        if provider is None:
            raise ProviderCallError(
                f"model {model!r} is served by vendor {vendor!r}, which has no provider in this "
                f"gateway (available: {', '.join(self.vendors)}); the call was not made",
                retryable=False,
            )
        return provider

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return await self.provider_for(request.model).complete(request)
