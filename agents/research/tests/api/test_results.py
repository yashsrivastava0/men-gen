"""Results endpoints (overview, review, sources, report, metrics, compact, decisions) and the spot-check, end to end over the
offline doubles; plus the deterministic report."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx
from support.compose import build_app_test_service
from support.testing import load_fixture, run_engine

from mde_research_agent.api import create_app
from mde_research_agent.config import ResearchSettings
from mde_research_agent.report import render_report
from mde_research_agent.spotcheck import QUOTAS, draw, summarize

FIX = Path(__file__).resolve().parent.parent / "fixtures"
H = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
GOLDEN = FIX / "golden" / "report-tiny.txt"


async def finished_app(tmp_path: Path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    shutil.copy(
        FIX / "exports" / "standard-produce-marketplace-nashik.json", inputs / "nashik.json"
    )
    svc = build_app_test_service(tmp_path, inputs_dir=inputs)
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app = create_app(svc, settings=ResearchSettings(), ui_path=ui, sweep_seconds=3600)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H)
    iid = (await c.get("/api/inputs")).json()["inputs"][0]["id"]
    run = (await c.post("/api/runs", json={"input_id": iid})).json()
    await svc.wait(run["run_id"], 120)
    return svc, c, run["run_id"]


async def test_overview_review_sources_report_and_metrics_of_a_committed_run(
    tmp_path: Path,
) -> None:
    svc, c, rid = await finished_app(tmp_path)
    ov = (await c.get(f"/v1/research/runs/{rid}/overview")).json()
    assert ov["schema"] == "research.v5" and "items" not in ov and ov["item_count"] > 0
    assert ov["lane_quality"] and ov["bn_coverage"] and ov["metrics"]["extraction"]["extracted"] > 0
    assert ov["input_name"] == "nashik.json" and ov["spend_micros"] > 0

    page = (await c.get(f"/v1/research/runs/{rid}/review?limit=10&offset=0")).json()
    assert page["total"] == ov["item_count"] and len(page["items"]) == min(10, page["total"])
    first = page["items"][0]
    assert (
        first["best_quote"]["text"] and first["sources"] and first["lane"] and "_text" not in first
    )
    assert [i["rank"] for i in page["items"]] == sorted(i["rank"] for i in page["items"])
    assert set(page["facets"]) == {"job", "lane", "evidence", "flag", "impact", "kind", "store"}

    job = next(iter(page["facets"]["job"]))
    filtered = (await c.get(f"/v1/research/runs/{rid}/review?job={job}&limit=200")).json()
    assert filtered["total"] == page["facets"]["job"][job] and all(
        i["job"] == job for i in filtered["items"]
    )
    word = first["label"].split()[0]
    found = (await c.get(f"/v1/research/runs/{rid}/review?q={word}&limit=5")).json()
    assert found["total"] >= 1
    assert (await c.get(f"/v1/research/runs/{rid}/review?limit=1000")).status_code == 422

    src = (await c.get(f"/v1/research/runs/{rid}/sources?limit=5")).json()
    assert src["total"] > 0 and {"id", "url", "domain", "class", "used_items"} <= set(
        src["items"][0]
    )

    rep = await c.get(f"/v1/research/runs/{rid}/report")
    assert (
        rep.headers["content-type"].startswith("text/markdown")
        and "## Lane quality" in rep.text
        and first["label"] in rep.text
    )

    m = (await c.get(f"/v1/research/runs/{rid}/metrics")).json()
    assert set(m["acceptance"]) >= {
        "extraction_quote_pass_first_try",
        "fetch_success",
        "slots_filled",
    }
    await svc.aclose()


async def test_rejections_are_an_overlay_the_report_and_review_show(tmp_path: Path) -> None:
    svc, c, rid = await finished_app(tmp_path)
    page = (await c.get(f"/v1/research/runs/{rid}/review?limit=1")).json()
    item = page["items"][0]
    before = (await c.get(f"/v1/research/runs/{rid}/result")).json()
    assert (
        await c.post(f"/v1/research/runs/{rid}/items/{item['id']}/reject", json={"reason": "wrong"})
    ).status_code == 200
    after = (await c.get(f"/v1/research/runs/{rid}/review?limit=1")).json()["items"][0]
    assert after["rejected"] is True
    assert (
        before == (await c.get(f"/v1/research/runs/{rid}/result")).json()
    )  # the committed file never changes
    assert "rejected by you" in (await c.get(f"/v1/research/runs/{rid}/report")).text
    row = await svc.get_row("t1", rid)
    decisions = (await c.get(f"/v1/research/programs/{row.program_id}/decisions")).json()[
        "decisions"
    ]
    assert [d["item_id"] for d in decisions] == [item["id"]]
    applied = (
        await c.get(f"/v1/research/programs/{row.program_id}/latest?apply_decisions=1")
    ).json()
    assert (
        item["id"] in applied["rejected_ids"]
        and "withdrawn" in next(i for i in applied["items"] if i["id"] == item["id"])["flags"]
    )
    compact = (await c.get(f"/v1/research/programs/{row.program_id}/latest?view=compact")).json()
    assert compact["items"] and set(compact["sources"]) and len(str(compact)) < len(str(before))
    await svc.aclose()


async def test_a_run_that_is_not_committed_has_no_results() -> None:
    from support.compose import build_service

    svc = build_service()
    app = create_app(svc, settings=ResearchSettings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H
    ) as c:
        assert (await c.get("/v1/research/runs/rrn_nope/overview")).status_code == 404


async def test_spot_check_is_seeded_stratified_resumable_and_summarised(tmp_path: Path) -> None:
    svc, c, rid = await finished_app(tmp_path)
    a = (await c.post("/api/spot-checks", json={"run_ids": [rid], "seed": 7})).json()
    b = (await c.post("/api/spot-checks", json={"run_ids": [rid], "seed": 7})).json()
    assert [x["key"] for x in a["cards"]] == [x["key"] for x in b["cards"]], (
        "the same seed gives the same sample"
    )
    other = (await c.post("/api/spot-checks", json={"run_ids": [rid], "seed": 8})).json()
    assert len(a["cards"]) == min(20, sum(1 for _ in a["cards"])) and a["seed"] == 7
    assert [x["key"] for x in other["cards"]] != [x["key"] for x in a["cards"]] or len(
        a["cards"]
    ) <= 3
    card = a["cards"][0]
    assert (
        card["claim"] and card["quote"] and "domain" in card["source"] and card["stratum"] in QUOTAS
    )

    for i, cd in enumerate(a["cards"]):
        r = await c.put(
            f"/api/spot-checks/{a['id']}/verdicts/{cd['key']}",
            json={"verdict": "ok" if i else "invented_detail"},
        )
        assert r.status_code == 200
    assert (
        await c.put(
            f"/api/spot-checks/{a['id']}/verdicts/{card['key']}", json={"verdict": "nonsense"}
        )
    ).status_code == 409
    again = (await c.get(f"/api/spot-checks/{a['id']}")).json()  # resumable: verdicts are saved
    assert len(again["verdicts"]) == len(a["cards"]) and again["summary"]["answered"] == len(
        a["cards"]
    )
    s = (await c.get(f"/api/spot-checks/{a['id']}/summary")).json()
    assert s["invented_total"] == 1 and s["thresholds"]["supported"] == 0.95
    md = await c.get(f"/api/spot-checks/{a['id']}/export?format=md")
    assert (
        md.text.startswith("# Spot-check")
        and (await c.get(f"/api/spot-checks/{a['id']}/export")).json()["id"] == a["id"]
    )
    assert any(x["id"] == a["id"] for x in (await c.get("/api/spot-checks")).json()["checks"])
    assert (await c.get("/api/spot-checks/chk_missing")).status_code == 404
    await svc.aclose()


def test_the_draw_tops_up_short_strata_and_says_so() -> None:
    items = [
        {
            "id": f"RI-{i:03d}",
            "job": "features",
            "kind": "competitor",
            "impact": "normal",
            "label": "l",
            "claim": "c",
            "data": {},
        }
        for i in range(30)
    ]
    cards = draw(items, "rrn_1", 3)
    assert len(cards) == 20 and all(c["stratum"] == "ordinary" for c in cards)
    assert sum(1 for c in cards if c["topped_up"]) == 17  # only 3 ordinary slots were asked for
    s = summarize(
        {
            "id": "x",
            "seed": 3,
            "run_ids": ["rrn_1"],
            "cards": cards,
            "verdicts": {c["key"]: "ok" for c in cards},
        }
    )
    assert s["passed"] and s["supported_pct"] == 1.0
    bad = summarize(
        {
            "id": "x",
            "seed": 3,
            "run_ids": ["rrn_1"],
            "cards": cards,
            "verdicts": {
                **{c["key"]: "invented_detail" for c in cards[:3]},
                **{c["key"]: "ok" for c in cards[3:]},
            },
        }
    )
    assert (
        not bad["passed"]
        and bad["worst"]
        and bad["invented_total"] == 3
        and not bad["pass"]["no_invented"]
    )


async def test_the_report_is_deterministic_and_matches_the_golden_file() -> None:
    run = await run_engine(load_fixture("unit-converter-tiny"))
    a = render_report(run.research, run.evidence, set(), run.ctx.catalog.lanes.kinds)
    b = render_report(run.research, run.evidence, set(), run.ctx.catalog.lanes.kinds)
    assert a == b
    for section in (
        "## Lane quality",
        "## To confirm",
        "## Gaps",
        "## Validation",
        "## Fallbacks used",
    ):
        assert section in a
    if os.environ.get("UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.write_text(a, encoding="utf-8")
    assert a == GOLDEN.read_text(encoding="utf-8"), (
        "report changed: review it, then run with UPDATE_GOLDEN=1"
    )


async def test_review_can_fetch_items_by_id(tmp_path: Path) -> None:
    svc, c, rid = await finished_app(tmp_path)
    page = (await c.get(f"/v1/research/runs/{rid}/review?limit=5")).json()
    want = [i["id"] for i in page["items"][:2]]
    got = (await c.get(f"/v1/research/runs/{rid}/review?ids={','.join(want)}")).json()
    assert got["total"] == 2 and {i["id"] for i in got["items"]} == set(want)
    await svc.aclose()
