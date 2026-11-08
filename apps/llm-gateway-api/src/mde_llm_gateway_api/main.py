"""ASGI entry point: ``uvicorn mde_llm_gateway_api.main:app`` (``just gateway-dev``)."""

from __future__ import annotations

from mde_llm_gateway_api.app import create_app

app = create_app()
