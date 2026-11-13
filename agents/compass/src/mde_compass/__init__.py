"""Compass: the first agent. It interviews the user and writes the Brief.

Layering (see docs/adr/0012): may import mde_core, mde_contracts, mde_llm_gateway and
mde_persistence only. Never mde_discovery, mde_research, mde_spec_builder, mde_flow or any app.
"""

AGENT_NAME = "Compass"
"""Persona name shown in the UI (one constant, easy to change)."""

AGENT_ID = "compass"
CALL_PREFIX = "cmp."
API_PREFIX = "/v1/compass"
DEFAULT_PORT = 8110
