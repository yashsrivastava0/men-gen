"""Importable test helpers: an in-memory ``Store``, scripted engines and a wired ``FlowService``.

Used by this package's unit and property tests, and by the API tests later. Everything is
deterministic (``FakeClock``, ``SequentialIds``) and offline.
"""

from mde_flow.testing.builders import (
    PolicyBook,
    enterprise_policy,
    guided_policy,
    make_brief,
    make_plan,
    make_spec,
)
from mde_flow.testing.fakes import (
    FakeBudgetGate,
    FakeDiscoveryEngine,
    FakeProjectRepo,
    FakeReportingResearch,
    FakeResearchEngine,
    FakeSpecBuilder,
)
from mde_flow.testing.kit import (
    Driver,
    FlowKit,
    make_flow,
    make_flow_kit,
    principal,
    use_store_factory,
)
from mde_flow.testing.peek import Peek, TrackingStore
from mde_flow.testing.store import InMemoryStore

__all__ = [
    "Driver",
    "FakeBudgetGate",
    "FakeDiscoveryEngine",
    "FakeProjectRepo",
    "FakeReportingResearch",
    "FakeResearchEngine",
    "FakeSpecBuilder",
    "FlowKit",
    "InMemoryStore",
    "Peek",
    "PolicyBook",
    "TrackingStore",
    "enterprise_policy",
    "guided_policy",
    "make_brief",
    "make_flow",
    "make_flow_kit",
    "make_plan",
    "make_spec",
    "principal",
    "use_store_factory",
]
