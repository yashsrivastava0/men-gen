import mde_research_agent


def test_package_identity() -> None:
    assert mde_research_agent.AGENT_ID == "research"
    assert mde_research_agent.CALL_PREFIX == "rsch."
    assert mde_research_agent.DEFAULT_PORT == 8120
    assert mde_research_agent.SCHEMA_VERSION == "research.v4"
    assert mde_research_agent.EVENT_SCHEMA_VERSION == "research.event.v1"
