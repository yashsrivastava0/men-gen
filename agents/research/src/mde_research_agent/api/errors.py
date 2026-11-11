"""Errors as plain ``{"code", "message"}`` bodies; never a stack trace."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mde_core.errors import Conflict, NotFound
from mde_core.logging import get_logger
from mde_research_agent.intake import ExportRefused
from mde_research_agent.store.ports import RunActive

log = get_logger(__name__)


class Unauthorized(Exception):
    pass


def _body(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Unauthorized)
    async def _unauth(_: Request, __: Unauthorized) -> JSONResponse:
        return JSONResponse(_body("unauthorized", "Missing or wrong credentials."), status_code=401)

    @app.exception_handler(ExportRefused)
    async def _refused(_: Request, exc: ExportRefused) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message), status_code=422)

    @app.exception_handler(RunActive)
    async def _active(_: Request, exc: RunActive) -> JSONResponse:
        return JSONResponse(
            {**_body("run_active", str(exc)), "run_id": exc.run_id}, status_code=409
        )

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(_body("conflict", str(exc)), status_code=409)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(_body("not_found", "Not found."), status_code=404)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(_body("bad_request", "The request body is not valid."), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "bad_request", 413: "too_large"}.get(
            exc.status_code, "error"
        )
        return JSONResponse(
            _body(code, "That request cannot be served."), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("research_api_error", error=type(exc).__name__)
        return JSONResponse(_body("internal_error", "Something went wrong."), status_code=500)
