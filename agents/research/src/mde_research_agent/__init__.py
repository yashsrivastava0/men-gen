"""Research: the second agent of the Context Engine.

After the user approves the Brief (Gate 1) it searches the web for what a model cannot know
(comparable products, regional providers and APIs, fees and rules as information, data shapes,
local formats), reads the pages, extracts typed facts that CODE verifies against the page text,
and hands Spec one structured playbook, ``research.json`` (schema ``research.v5``), indexed by the
Brief's own ids. It enriches and never decides: its items are Default or Open, never Locked.

Layering: may import mde_core, mde_contracts, mde_llm_gateway (and third-party libraries such as
httpx). Never another agent (mde_compass, mde_discovery, mde_spec_builder, ...), never the older
``mde_research`` package, never an app. It reads the Compass export as plain JSON.

Name: the distribution is ``mde-research-agent`` and the import is ``mde_research_agent`` because
the older engine in ``packages/research`` already owns ``mde_research`` (D-125).
"""

AGENT_NAME = "Research"
"""Name shown in the UI (one constant, easy to change)."""

AGENT_ID = "research"
CALL_PREFIX = "rsch."
API_PREFIX = "/v1/research"
DEFAULT_PORT = 8120

SCHEMA_VERSION = "research.v5"
EVENT_SCHEMA_VERSION = "research.event.v1"
