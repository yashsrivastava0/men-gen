"""``HttpGatewayClient``: the HTTP flavour of ``GatewayClient`` (brief 7.12).

Same protocol and the same exceptions as the in-process client. It needs ``spec.id``,
``spec.version``, ``spec.input_model`` and ``spec.output_model`` to (de)serialise; everything else
about the call is resolved server-side from the call id.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
from pydantic import BaseModel, ValidationError

from mde_contracts.gateway import BudgetStatus, CallContext, CallResult, CallSpec, I, O
from mde_core.errors import GatewayError
from mde_llm_gateway.errors import GatewayAuthError
from mde_llm_gateway.wire import ErrorEnvelope, RunRequest, context_to_wire, error_from_wire

DEFAULT_TIMEOUT_S = 300.0


class HttpGatewayClient:
    """Implements :class:`mde_contracts.gateway.GatewayClient` over ``POST /v1/runs``."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=timeout
        )
        self._headers = {"Authorization": f"Bearer {token}"}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ GatewayClient
    async def run(self, spec: CallSpec[I, O], input: I, ctx: CallContext) -> CallResult[O]:
        ctx.cancel.check()
        request = RunRequest(
            call_id=spec.id,
            version=spec.version,
            input=cast("dict[str, object]", input.model_dump(mode="json")),
            context=context_to_wire(ctx),
        )
        response = await self._send(
            "POST", "/v1/runs", ctx=ctx, json=request.model_dump(mode="json")
        )
        data = cast("dict[str, Any]", self._json_or_raise(response))
        output = data.pop("output", None)
        try:
            # Unparametrised on purpose: ``CallResult[BaseModel]`` would serialise ``output`` as a bare
            # BaseModel (``{}``); the unparametrised class serialises the real output model.
            result = cast(
                "CallResult[BaseModel]", CallResult.model_validate({**data, "output": None})
            )
            if output is not None and spec.output_model is not None:
                result = result.model_copy(
                    update={"output": spec.output_model.model_validate(output)}
                )
        except ValidationError as exc:
            raise GatewayError(
                f"gateway returned an unreadable result: {exc.error_count()} error(s)"
            ) from None
        return cast("CallResult[O]", result)

    async def budget_status(self) -> BudgetStatus:
        response = await self._send("GET", "/v1/budget")
        return BudgetStatus.model_validate(self._json_or_raise(response))

    # ------------------------------------------------------------------ internals
    async def _send(
        self, method: str, path: str, *, ctx: CallContext | None = None, json: object | None = None
    ) -> httpx.Response:
        task = asyncio.ensure_future(
            self._client.request(method, path, headers=self._headers, json=json)
        )
        waiter = asyncio.ensure_future(ctx.cancel.wait()) if ctx is not None else None
        try:
            pending: set[asyncio.Future[Any]] = {task}
            if waiter is not None:
                pending.add(waiter)
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if not task.done():  # the caller cancelled first: drop the request
                task.cancel()
                assert ctx is not None
                ctx.cancel.check()
            return task.result()
        except httpx.TimeoutException:
            raise GatewayError("gateway API timed out") from None
        except httpx.TransportError as exc:
            raise GatewayError(f"gateway API unreachable ({type(exc).__name__})") from None
        finally:
            if waiter is not None:
                waiter.cancel()
            if not task.done():
                task.cancel()

    @staticmethod
    def _json_or_raise(response: httpx.Response) -> object:
        if response.is_success:
            try:
                return response.json()
            except ValueError:
                raise GatewayError("gateway API returned a body that is not JSON") from None
        try:
            envelope = ErrorEnvelope.model_validate_json(response.content)
        except ValueError:
            if response.status_code == 401:
                raise GatewayAuthError("gateway API rejected the service token") from None
            raise GatewayError(f"gateway API returned HTTP {response.status_code}") from None
        raise error_from_wire(response.status_code, envelope)
