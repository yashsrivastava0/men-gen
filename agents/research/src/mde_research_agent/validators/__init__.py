"""The validator registry for Research (RV-01 to RV-17; RV-11 is reserved and does nothing).

An internal registry that the Backbone's shared validator framework can adopt later: each validator is a function
``(ValidationContext) -> list[Violation]`` registered under its id.
"""

from mde_research_agent.validators.registry import REGISTRY, ValidationContext, Violation, run_all

__all__ = ["REGISTRY", "ValidationContext", "Violation", "run_all"]
