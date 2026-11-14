"""What the run says about itself must be true: the manifest claims only the checks that ran, the weak-route banner
never counts more screens than exist, a lane's mechanical repairs reach the event feed and the saved self-check, and
the critic's vendor is computed from the gateway's route comparison, not typed."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mde_core.settings import load_settings  # noqa: F401  (fixtures build the service)
from mde_spec_agent.api import create_app
from mde_spec_agent.calls.base import NoCredit
from mde_spec_agent.lanes.screens.common import log_rcode
from mde_spec_agent.run import steps_code
from mde_spec_agent.service import SpecService
from mde_spec_core.callkit.ladder import RCodeLog
from mde_spec_core.synth import make_graph

FREIGHT = "thorough-freight-fleet-india"


# ---- the banner can never count more screens than exist --------------------------------------------------------------


def test_reasked_screen_ids_are_only_screens_of_this_graph() -> None:
    graph = make_graph("small")
    screens = list(graph.screens)[:3]
    noise = {"ENT-001", "US-001", "SCR-NOT-IN-THIS-GRAPH", "copy.key.1", *screens}
    assert steps_code.reasked_screen_ids(noise, graph) == set(screens)


def test_the_banner_numerator_cannot_exceed_the_screen_count() -> None:
    graph = make_graph("small")
    many = {f"SCR-{i:03d}" for i in range(500)} | set(graph.screens)  # the old "108 of 18" shape
    found = steps_code.reasked_screen_ids(many, graph)
    assert len(found) <= len(graph.screens)


# ---- a lane's repairs reach the event feed and the saved self-check ------------------------------------------------


class _Ctx(SimpleNamespace):
    def emit(self, type_: str, **data: Any) -> None:
        self.events.append((type_, data))


def _ctx() -> _Ctx:
    return _Ctx(rcode=RCodeLog(), events=[])


def test_log_rcode_emits_one_event_with_the_counts_by_kind() -> None:
    ctx = _ctx()
    rows = [
        {
            "kind": "drop_unknown_optional",
            "node": "SCR-001",
            "what": "a",
            "before": "x",
            "after": "",
        },
        {
            "kind": "drop_unknown_optional",
            "node": "SCR-002",
            "what": "b",
            "before": "y",
            "after": "",
        },
        {"kind": "exact_synonym", "node": "SCR-003", "what": "c", "before": "p", "after": "q"},
    ]
    assert log_rcode(ctx, rows, step="screens") == 3  # type: ignore[arg-type]
    assert ctx.events == [
        (
            "repair.rcode",
            {
                "step": "screens",
                "count": 3,
                "by_kind": {"drop_unknown_optional": 2, "exact_synonym": 1},
            },
        )
    ]
    ctx2 = _ctx()
    assert log_rcode(ctx2, [], step="screens") == 0  # type: ignore[arg-type]
    assert ctx2.events == []  # nothing repaired: nothing said


def test_refresh_rcode_evidence_keeps_a_repair_made_after_gate_3(
    service: SpecService, client: TestClient
) -> None:
    rid = client.post("/api/runs", json={"compass_id": "synth:tiny", "offline": True}).json()[
        "run_id"
    ]
    import time

    for _ in range(300):
        if client.get(f"/api/runs/{rid}").json()["status"] in ("awaiting_g2", "failed", "partial"):
            break
        time.sleep(0.2)
    path = "evidence/self-check.json"
    before = json.loads(service.store.read_text(rid, path))
    assert before["rcode"]["changes"] == 0
    log = RCodeLog()
    from mde_spec_core.callkit.ladder import FixKind

    log.record(FixKind.EXACT_SYNONYM, "SCR-001", "a synonym", "alpha", "beta")
    ctx = SimpleNamespace(run_id=rid, store=service.store, rcode=log)
    steps_code.refresh_rcode_evidence(ctx)  # type: ignore[arg-type]
    after = json.loads(service.store.read_text(rid, path))
    assert after["rcode"]["changes"] == 1 and after["rcode"]["counts"]["exact_synonym"] == 1
    assert after["rcode"]["rows"][0]["node_id"] == "SCR-001"
    assert {k: v for k, v in after.items() if k != "rcode"} == {
        k: v for k, v in before.items() if k != "rcode"
    }  # nothing else in the report changed


# ---- the critic's vendor is computed -------------------------------------------------------------------------------


def test_critic_vendor_is_same_unless_the_gateway_says_no_writer_route_shares_it(
    service: SpecService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the calls as built: the critic and the module writers both name the standard route -> same by construction
    assert service.critic_vendor() == "same"
    import mde_spec_agent.service as svc_mod

    monkeypatch.setattr(svc_mod, "routes_share_vendor", lambda *a, **k: False)
    assert service.critic_vendor() == "different"

    def broken(*a: object, **k: object) -> bool:
        raise ValueError("a route the gateway cannot resolve")

    monkeypatch.setattr(svc_mod, "routes_share_vendor", broken)
    assert service.critic_vendor() == "same"  # never claim independence on a doubt


# ---- the manifest claims only the checks that ran ------------------------------------------------------------------


def test_a_deep_run_stopped_before_verify_finished_never_claims_verify(
    service: SpecService,
    monkeypatch: pytest.MonkeyPatch,
    wait: Callable[..., dict[str, Any]],
) -> None:
    import mde_spec_agent.service as svc_mod

    class _NoMoney:
        critic_vendor = "same"

        async def canary(self, *a: object, **k: object) -> None:
            raise NoCredit("provider refused for credit")

        async def final_critic(self, *a: object, **k: object) -> None:
            raise NoCredit("provider refused for credit")

    monkeypatch.setattr(svc_mod, "load_verify_port", lambda *a, **k: _NoMoney())
    with TestClient(create_app(service, settings=service.settings)) as client:
        rid = client.post("/api/runs", json={"compass_id": FREIGHT, "offline": True}).json()[
            "run_id"
        ]
        wait(client, rid, ("paused_g15", "failed", "partial"))
        client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"})
        snap = wait(client, rid, ("awaiting_g2", "failed", "partial"))
        assert snap["status"] == "partial" and snap["partial_reason"] == "no_credit"
        manifest = json.loads(service.store.read_text(rid, "pack/MANIFEST.json"))
        assert manifest["checked_by"] == ["self_check"]  # Verify did not finish: it is not claimed
        assert manifest["critic_vendor"] is None
        assert snap["checked_by"] == ["self_check"]
