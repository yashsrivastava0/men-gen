from pathlib import Path

import pytest

from mde_research_agent.adapters.recording import (
    RecordingFetch,
    RecordingSearch,
    ReplayFetch,
    ReplaySearch,
)
from mde_research_agent.core.hashing import content_hash
from mde_research_agent.fakes import FakeFetch, FakeSearch, FakeWorld
from mde_research_agent.metrics import run_metrics
from mde_research_agent.ports.search import SearchError, SearchRequest
from mde_research_agent.testing import load_fixture, run_engine


async def test_metrics_report_the_design_targets_on_a_clean_run() -> None:
    run = await run_engine(load_fixture("standard-produce-marketplace-nashik"))
    m = run_metrics(run.research, run.evidence)
    assert m["quote_check_pass"] == 1.0, "every committed quote is on its stored page"
    assert m["high_impact_corroborated_or_flagged"] in (None, 1.0)
    assert 0 < m["slots_filled_ratio"] <= 1 and m["items"] == len(run.research["items"])
    assert m["searches_contributing_ratio"] is not None and m["by_job"] and m["source_classes"]


async def test_a_recorded_run_replays_offline_with_the_same_hash(tmp_path: Path) -> None:
    export = load_fixture("quick-yoga-studio-bengaluru")
    world = FakeWorld(provider_content_every=3)
    live = await run_engine(
        export,
        search_port=RecordingSearch(FakeSearch(world), tmp_path),
        fetch_port=RecordingFetch(FakeFetch(world), tmp_path),
    )
    nothing = FakeWorld(generate=False)  # the replay must not need the web at all
    replay = await run_engine(
        export, world=nothing, search_port=ReplaySearch(tmp_path), fetch_port=ReplayFetch(tmp_path)
    )
    assert content_hash(live.research) == content_hash(replay.research)
    assert list((tmp_path / "search").glob("*.json.gz")) and list(
        (tmp_path / "fetch").glob("*.json.gz")
    )


async def test_replay_never_goes_live_for_something_not_recorded(tmp_path: Path) -> None:
    with pytest.raises(SearchError) as err:
        await ReplaySearch(tmp_path).search(SearchRequest(query="never recorded", country="in"))
    assert err.value.kind == "bad_request"
    assert (await ReplayFetch(tmp_path).fetch("https://nowhere.example/x")).reason == "error"
