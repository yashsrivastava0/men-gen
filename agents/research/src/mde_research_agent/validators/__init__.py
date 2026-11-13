"""The validator registry for Research (RV-01 to RV-23; RV-11 is the private-term check, back by the owner's instruction of 2026-11-13).

An internal registry that the Backbone's shared validator framework can adopt later: each validator is a function
``(ValidationContext) -> list[Violation]`` registered under its id.
"""

from mde_research_agent.validators.registry import REGISTRY, ValidationContext, Violation, run_all

__all__ = ["REGISTRY", "ValidationContext", "Violation", "run_all"]
