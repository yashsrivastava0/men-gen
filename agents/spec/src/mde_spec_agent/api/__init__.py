"""The Spec Bench HTTP API and its static front end (one origin, loopback only). See ``docs/api/spec-agent.md``."""

from mde_spec_agent.api.app import create_app

__all__ = ["create_app"]
