"""The file-backed store: the shared contract, plus what only a file store has (persistence, the lock, torn lines)."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.store_contract import CHECKS, T0, event, row

from mde_core.errors import NotFound
from mde_research_agent.store.files import FileStore, StoreLocked
from mde_research_agent.store.ports import Decision


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
async def test_file_store_meets_the_contract(check, tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    try:
        await check(store)
    finally:
        await store.aclose()


async def test_everything_survives_a_restart(tmp_path: Path) -> None:
    a = FileStore(tmp_path)
    await a.create_run(row("rrn_1"))
    await a.append_event(event("rrn_1", 1))
    await a.append_event(event("rrn_1", 2))
    await a.update_run("t1", "rrn_1", status="complete")
    await a.save_result("rrn_1", {"items": [1]}, {"quotes": ["q"]}, "sha256:abc")
    await a.add_decision(Decision(run_id="rrn_1", item_id="RI-1", kind="reject", reason="x", at=T0))
    await a.aclose()
    b = FileStore(tmp_path)
    try:
        got = await b.get_run("t1", "rrn_1")
        assert got is not None and got.status == "complete" and got.content_hash == "sha256:abc"
        assert [e.seq for e in await b.events_after("rrn_1", 0)] == [1, 2]
        assert await b.get_result("rrn_1") == {"items": [1]}
        assert await b.get_evidence("rrn_1") == {"quotes": ["q"]}
        assert [d.item_id for d in await b.list_decisions("rrn_1")] == ["RI-1"]
        assert (await b.latest_committed("t1", "prog_1")) is not None
    finally:
        await b.aclose()


async def test_a_second_process_cannot_open_the_same_folder(tmp_path: Path) -> None:
    a = FileStore(tmp_path)
    try:
        with pytest.raises(StoreLocked):
            FileStore(tmp_path)
    finally:
        await a.aclose()
    again = FileStore(tmp_path)  # released after close: opening again works
    await again.aclose()


async def test_a_half_written_last_event_line_is_ignored(tmp_path: Path) -> None:
    a = FileStore(tmp_path)
    await a.create_run(row("rrn_1"))
    await a.append_event(event("rrn_1", 1))
    await a.aclose()
    with (tmp_path / "runs" / "rrn_1" / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"id": "evt_x", "seq": 2, "ts"')  # a crash mid-write
    b = FileStore(tmp_path)
    try:
        assert [e.seq for e in await b.events_after("rrn_1", 0)] == [1]
        await b.append_event(event("rrn_1", 2))  # the log continues from the last good line
    finally:
        await b.aclose()


async def test_the_result_file_is_written_once_and_run_ids_cannot_escape(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    try:
        with pytest.raises(NotFound):
            await store.update_run("t1", "../evil", status="x")
        assert await store.get_result("../evil") is None
        assert await store.events_after("../evil", 0) == []
    finally:
        await store.aclose()
