"""LLM Gateway: providers, pipeline, budget guard, retries, cassettes.

The only package that talks to an LLM and the only one that imports ``openai`` (the OpenAI provider
module is imported lazily, only in live mode). Public surface::

    from mde_llm_gateway import create_gateway, InProcessGatewayClient, SpecRegistry
    gateway = create_gateway(settings, registry)           # provider chosen by settings.llm_mode
    client = InProcessGatewayClient(gateway)               # implements mde_contracts GatewayClient
"""

from mde_llm_gateway.budget import BudgetConfig, BudgetGuard, Reservation, TokenEstimator
from mde_llm_gateway.client import InProcessGatewayClient
from mde_llm_gateway.factory import create_gateway
from mde_llm_gateway.gateway import Gateway, GatewayConfig
from mde_llm_gateway.ledger import FileLedger, Ledger, MemoryLedger
from mde_llm_gateway.pricing import Pricing
from mde_llm_gateway.registry import AnySpec, SpecRegistry, StaticRegistry
from mde_llm_gateway.retry import RetryPolicy
from mde_llm_gateway.strict_schema import to_strict_schema
from mde_llm_gateway.untrusted import UNTRUSTED_NOTICE, wrap_untrusted

__version__ = "0.1.0"

__all__ = [
    "UNTRUSTED_NOTICE",
    "AnySpec",
    "BudgetConfig",
    "BudgetGuard",
    "FileLedger",
    "Gateway",
    "GatewayConfig",
    "InProcessGatewayClient",
    "Ledger",
    "MemoryLedger",
    "Pricing",
    "Reservation",
    "RetryPolicy",
    "SpecRegistry",
    "StaticRegistry",
    "TokenEstimator",
    "create_gateway",
    "to_strict_schema",
    "wrap_untrusted",
]
