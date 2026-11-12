"""The test-only fake app serves the credit endpoints with seeded numbers, so the console's credit chip and Budget section
can be driven in a browser without a key, the network or the owner's ledger."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
H = {"Authorization": "Bearer dev-token"}


def load_fake():
    spec = importlib.util.spec_from_file_location(
        "fake_app_credits_under_test", HERE / "fake_app.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(**kw) -> argparse.Namespace:
    base = dict(
        empty=True,
        missing_folder=False,
        not_ready=False,
        slow=False,
        delay=0.0,
        max_concurrent=1,
        total="5.00",
        tripwire="4.50",
        spent=0.0,
        seed=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H
    )


async def test_the_fake_shows_the_owners_two_numbers_by_default(tmp_path: Path) -> None:
    app = load_fake().make_app(args(), tmp_path)
    async with client(app) as c:
        body = (await c.get("/api/credits")).json()
    tavily, serper = body["providers"]
    assert (tavily["used"], tavily["limit"], tavily["remaining"], tavily["level"]) == (
        93,
        1000,
        907,
        "ok",
    )
    assert (serper["remaining"], serper["level"]) == (2389, "ok")
    assert tavily["key_present"] and serper["key_present"]
    assert (
        body["primary"] == "tavily"
    )  # so the console can say what a run needs from the provider it will use
    assert (tmp_path / "data" / "provider-credits.json").is_file()  # the fake's own folder


async def test_the_fake_can_show_a_low_ledger_and_an_empty_one(tmp_path: Path) -> None:
    fake = load_fake()
    async with client(fake.make_app(args(credits="low"), tmp_path / "low")) as c:
        low = (await c.get("/api/credits")).json()["providers"][0]
    assert (low["used"], low["level"]) == (962, "red") and "may stop early" in low["note"]
    async with client(fake.make_app(args(credits="empty"), tmp_path / "empty")) as c:
        empty = (await c.get("/api/credits")).json()["providers"]
    assert [p["level"] for p in empty] == ["unknown", "unknown"]


async def test_asking_tavily_in_the_fake_is_answered_locally(tmp_path: Path) -> None:
    app = load_fake().make_app(args(), tmp_path)
    async with client(app) as c:
        res = (await c.post("/api/credits/refresh")).json()
    assert res["refresh"]["done"] is True
    tavily = res["providers"][0]
    assert tavily["source"] == "provider" and tavily["plan"] == "Free (test fake)"
    assert tavily["used"] == 93


async def test_the_real_app_has_no_seeded_numbers(tmp_path: Path) -> None:
    """Only the fake seeds a ledger: the same service built without it starts with no count at all."""
    from support.compose import build_app_test_service

    svc = build_app_test_service(tmp_path / "plain")
    snap = svc.credits_status()
    assert [p["level"] for p in snap["providers"]] == ["unknown", "unknown"]
    await svc.aclose()
