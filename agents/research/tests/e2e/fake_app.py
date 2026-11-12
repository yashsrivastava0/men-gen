"""TEST-ONLY fake Research app: the real app (``create_app``) over the offline doubles, to drive the front end.

    uv run python agents/research/tests/e2e/fake_app.py --port 8140

The server, the API, the queue, the budget and the static front end (``apps/research-console``) are the real ones; the
web, the models and the store are the doubles of ``tests/support`` (no network, no key, no cost). The front end shows a
loud "TEST FAKE" banner when ``GET /api/fake`` answers, which only this script adds (the real app answers 404).

Flags
    --port N            listen on 127.0.0.1:N (default 8140)
    --empty             an inputs folder with no files in it
    --missing-folder    the inputs folder does not exist
    --not-ready         no gateway and a list of what is missing (every start is refused with 409 not_ready)
    --slow              wait between searches and page reads so a run lasts long enough to watch (--delay sets it)
    --delay SECONDS     the wait for --slow (default 0.6)
    --max-concurrent N  runs at once (default 1: a second start queues)
    --total USD         the budget total (default 5.00); --tripwire USD (default 4.50)
    --spent USD         book this much spend before starting (a fresh data folder only)
    --seed N            commit extra runs at startup: one with N synthetic items (hostile text in the first three), one partial
    --credits MODE      the search-credit ledger the console shows: seeded (default: Tavily 93 of 1,000 used, Serper 2,389
                        left), low (Tavily 962 of 1,000 used: amber/red advice) or empty (no count yet). Tavily's own count
                        (GET /usage) is answered by a local stand-in; a run books one credit per search to Tavily.
    --data-dir PATH     keep everything here (a restart with the same folder finds the old runs, which show as
                        interrupted if the process was killed mid-run); default is a new temporary folder

This file must never be imported from ``src/`` (a test checks that the app cannot reach any double).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
REPO = HERE.parents[3]
sys.path.insert(0, str(TESTS))  # the doubles import as ``support.*``

REAL_EXPORTS = REPO / "docs" / "compass" / "research-inputs"
UI = REPO / "apps" / "research-console"
XSS_SUMMARY = (
    "<img src=x onerror=alert(1)> Book classes <script>alert(2)</script> "
    '<a href="javascript:alert(3)">click</a> javascript:alert(4)'
)


def _load(name: str) -> dict[str, Any]:
    return json.loads((REAL_EXPORTS / f"{name}.json").read_text(encoding="utf-8"))


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def write_inputs(folder: Path) -> None:
    """The three real exports plus the edge cases the front end must show as skipped files or handle inertly."""
    folder.mkdir(parents=True, exist_ok=True)
    for src in REAL_EXPORTS.glob("*.json"):
        shutil.copy(src, folder / src.name)
    yoga = _load("quick-yoga-studio-bengaluru")
    shutil.copy(
        folder / "quick-yoga-studio-bengaluru.json", folder / "zz-copy-of-yoga-studio.json"
    )  # a duplicate
    (folder / "notes.json").write_text("this is not json at all", encoding="utf-8")
    (folder / "binary.json").write_bytes(b"\xff\xfe\x00\x80")  # not UTF-8
    (folder / "README.md").write_text("A readme is not an export.", encoding="utf-8")
    _dump(folder / "unrelated.json", {"hello": "world", "items": [1, 2, 3]})
    v1 = json.loads(json.dumps(yoga))
    v1["export"]["format_version"] = 1
    _dump(folder / "old-format-1.json", v1)
    mismatch = json.loads(json.dumps(yoga))
    mismatch["brief"]["_hash"] = "0" * 64
    _dump(folder / "hash-mismatch.json", mismatch)
    unapproved = json.loads(json.dumps(yoga))
    unapproved["export"].pop("approved_baseline")
    _dump(folder / "not-approved.json", unapproved)
    # An approved export whose text is hostile: it must render as plain text and do nothing.
    xss = json.loads(json.dumps(yoga))
    idea = xss["consumer_views"]["research"]["idea"]
    idea["summary"] = XSS_SUMMARY
    idea["category"] = "<b onmouseover=alert(5)>yoga</b> javascript:alert(6)"
    for f in xss["consumer_views"]["research"].get("feature_intents", [])[:2]:
        f["name"] = "<img src=x onerror=alert(7)> " + str(f.get("name", ""))
    _dump(folder / "xss-<img src=x onerror=alert(8)>.json", xss)


def build(args: argparse.Namespace, root: Path) -> Any:
    from support.compose import build_app_test_service
    from support.fakes import FakeWorld

    inputs = root / "inputs"
    if args.missing_folder:
        inputs = root / "no-such-folder"
    elif args.empty:
        inputs.mkdir(parents=True, exist_ok=True)
    elif not inputs.exists():
        write_inputs(inputs)
    world = FakeWorld(provider_content_every=3)
    world_ref = world
    if args.slow:
        world.jitter = lambda: float(args.delay)
    kwargs: dict[str, Any] = {
        "inputs_dir": inputs,
        "world": world,
        "max_concurrent": args.max_concurrent,
        "total_usd": args.total,
        "tripwire_usd": args.tripwire,
        "cost_micros": 8000,
    }
    if args.not_ready:
        kwargs["no_gateway"] = True
        kwargs["missing"] = ["TAVILY_API_KEY or SERPER_API_KEY"]
    svc = build_app_test_service(root, **kwargs)
    if args.spent and svc.record is not None and not svc.record.data["entries"]:
        from mde_core.money import usd_to_micros

        eid = svc.record.start_entry("smoke", label="Earlier live checks (test fake)")
        svc.record.add_cost(eid, "plan", usd_to_micros(args.spent))
        svc.record.finish_entry(eid, "complete")
    svc._fake_world = world_ref  # type: ignore[attr-defined]
    seed_credits(svc, str(getattr(args, "credits", "seeded") or "seeded"))
    return svc


def seed_credits(svc: Any, mode: str) -> None:
    """The credit ledger of the fake app: baselines, a stand-in for Tavily's own count, and metered fake searches.

    The ledger lives in the fake's own data folder (never the owner's). Keys are fake and never leave the process.
    """
    import httpx
    from pydantic import SecretStr

    from mde_research_agent.adapters.search.metered import MeteredSearch
    from mde_research_agent.config import ResearchSettings

    svc.settings = ResearchSettings(
        tavily_api_key=SecretStr("tvly-FAKE-NOT-A-KEY"),  # secret-scan: allow
        serper_api_key=SecretStr("serper-FAKE-NOT-A-KEY"),  # secret-scan: allow
    )
    snap = svc.credits.snapshot()["providers"]
    if (
        mode != "empty"
        and snap["tavily"]["baseline"] is None
        and snap["serper"]["baseline"] is None
    ):
        svc.credits.set_baseline("tavily", "used", 962 if mode == "low" else 93, 1000)
        svc.credits.set_baseline("serper", "remaining", 2389)

    def usage(_request: httpx.Request) -> httpx.Response:
        used = svc.credits.snapshot()["providers"]["tavily"]["view"]["used"] or 0
        account = {"current_plan": "Free (test fake)", "plan_usage": used, "plan_limit": 1000}
        return httpx.Response(200, json={"account": account})

    svc.credits_http = httpx.AsyncClient(transport=httpx.MockTransport(usage))
    if svc.search is not None:
        svc.search = MeteredSearch(svc.search, svc.credits, "tavily")


HOSTILE = {
    "label": "<img src=x onerror=alert(1)> Fees",
    "claim": '<script>alert(2)</script> Fees start at 2 percent. <a href="javascript:alert(3)">pay</a>',
    "quote": "<b onmouseover=alert(4)>Fees start at 2 percent</b> javascript:alert(5)",
    "title": "<img src=x onerror=alert(6)> Pricing page",
    "url": "javascript:alert(7)",
}


def _item_variants(base_items: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """``n`` items cloned from real ones: new ids, mixed evidence and impact, a main/appendix split."""
    out = []
    for i in range(n):
        src = base_items[i % len(base_items)]
        it = json.loads(json.dumps(src))
        it["id"] = f"RI-{i + 1:03d}"  # the same ids the norms and the confirm list point at
        it["label"] = f"{src['label']} (#{i + 1})"
        it["claim"] = f"{src['claim']} Variant {i + 1}."
        it["rank"] = i + 1
        it["evidence"] = ("official", "corroborated", "single_source", "unverified")[i % 4]
        it["impact"] = "high" if i % 5 == 0 else "normal"
        flags = [f for f in it.get("flags", []) if f not in ("stale", "needs-confirm")]
        if i % 7 == 0:
            flags.append("stale")
        if i % 11 == 0:
            flags.append("needs-confirm")
        it["flags"] = sorted(set(flags))
        it["store"] = "main" if i < int(n * 0.75) else "appendix"
        it["appendix_reason"] = None if it["store"] == "main" else "over_store_limit"
        out.append(it)
    return out


def make_big_result(
    research: dict[str, Any], evidence: dict[str, Any], n: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A committed result with ``n`` items (and hostile text in the first three), built from a real committed one."""
    res = json.loads(json.dumps(research))
    ev = json.loads(json.dumps(evidence))
    items = _item_variants(res["items"], n)
    atoms_by_item: dict[str, list[dict[str, Any]]] = {}
    for a in ev["atoms"]:
        if a.get("item_id") and a.get("status") == "verified":
            atoms_by_item.setdefault(a["item_id"], []).append(a)
    old_ids = [it["id"] for it in research["items"]]
    new_atoms = []
    bad_page = {
        "id": "pg_HOSTILE0001",
        "url": HOSTILE["url"],
        "final_url": HOSTILE["url"],
        "domain": "evil.example",
        "source_class": "blog",
        "title": HOSTILE["title"],
        "published": "2026-01-01",
        "retrieved_on": "2026-11-11",
        "text_source": "fetch",
        "source_language": "en",
        "content_hash": "0" * 64,
        "text": "",
    }
    ev["pages"].append(bad_page)
    for i, it in enumerate(items):
        base_id = old_ids[i % len(old_ids)]
        src_atoms = atoms_by_item.get(base_id, [])
        if i < 3:
            it["label"], it["claim"] = HOSTILE["label"], HOSTILE["claim"]
            it["use_note"] = "<img src=x onerror=alert(8)>"
            it["sources"] = [
                {
                    "id": "S-900",
                    "domain": "evil.example",
                    "source_class": "blog",
                    "url": HOSTILE["url"],
                    "retrieved_on": "2026-11-11",
                    "published_on": "2026-01-01",
                },
                *it["sources"][:1],
            ]
            it["data"] = {
                "name": HOSTILE["label"],
                "notes": [HOSTILE["claim"]],
                "url": HOSTILE["url"],
            }
        for a in src_atoms[:1]:
            na = json.loads(json.dumps(a))
            na["id"] = f"atm_BIG{i + 1:06d}"
            na["item_id"] = it["id"]
            if i < 3:
                na["quote"] = HOSTILE["quote"]
                na["page_id"] = bad_page["id"]
            new_atoms.append(na)
    ev["atoms"] = [a for a in ev["atoms"] if a.get("item_id") not in old_ids] + new_atoms
    res["items"] = items
    res["profile"] = {
        **res["profile"],
        "appendix_items": sum(1 for i in items if i["store"] == "appendix"),
    }
    return res, ev


async def seed_runs(svc: Any, n_items: int) -> dict[str, str]:
    """Commit extra runs through the store API: a result with ``n_items`` items and a partial one."""
    world = getattr(svc, "_fake_world", None)
    saved = world.jitter if world is not None else None
    if world is not None:
        world.jitter = None
    entry = next(e for e in svc.inputs.scan().valid if e.name.startswith("quick-yoga"))
    row, _ = await svc.start_from_input("demo", entry.id)
    await svc.wait(row.run_id, 120)
    if world is not None:
        world.jitter = saved
    base_row = await svc.store.get_run("demo", row.run_id)
    research = await svc.store.get_result(row.run_id)
    evidence = await svc.store.get_evidence(row.run_id)
    made: dict[str, str] = {"base": row.run_id}

    async def commit(
        tag: str, res: dict[str, Any], ev: dict[str, Any], status: str, reason: str | None
    ) -> None:
        rid = svc.ids.new("rrn")
        meta = {
            **(base_row.meta or {}),
            "input_name": f"seeded-{tag}.json",
            "idea": f"Seeded {tag} result",
        }
        new = base_row.model_copy(
            update={
                "run_id": rid,
                "status": status,
                "result_status": status,
                "reason": reason,
                "meta": meta,
            }
        )
        await svc.store.create_run(new)
        res["status"] = status
        await svc.store.save_result(
            rid, res, {**ev, "run_id": rid}, base_row.content_hash or "0" * 64
        )
        made[tag] = rid

    big, big_ev = make_big_result(research, evidence, n_items)
    await commit("big", big, big_ev, "complete", None)
    part = json.loads(json.dumps(research))
    part["gaps"] = [
        {
            "target_id": "T-09",
            "reason": "budget_stop",
            "label": "Key screens of yoga studio apps",
            "queries": [],
        },
        {
            "target_id": "T-10",
            "reason": "not_run",
            "label": "Realistic Booking data",
            "queries": [],
        },
        {
            "target_id": "T-02",
            "reason": "provider_down",
            "label": "Providers for messages",
            "queries": ["sms providers"],
        },
        {
            "target_id": "T-04",
            "reason": "user_cancelled",
            "label": "Rules and limits: pricing",
            "queries": [],
        },
        *part["gaps"][:2],
    ]
    await commit(
        "partial",
        part,
        evidence,
        "partial",
        "The budget limit was reached, so two topics were not finished.",
    )
    return made


class _Core:
    """What the preflight page reads from the core settings. The test fake shows 'ready' so the screens can be driven;
    the gateway behind it is the offline double, so nothing here can reach a real model or the real web."""

    llm_mode = "live"
    forbid_live = False

    def model_for_route(self, route: str) -> str:
        return f"fake-model-{route}"


def make_app(args: argparse.Namespace, root: Path) -> Any:
    from fastapi.routing import APIRoute

    from mde_core.settings import load_settings
    from mde_research_agent.api import create_app
    from mde_research_agent.config import ResearchSettings

    svc = build(args, root)
    core: Any = load_settings(llm_mode="mock") if args.not_ready else _Core()
    cfg = (
        ResearchSettings(tavily_api_key=None, serper_api_key=None)
        if args.not_ready
        else ResearchSettings()
    )
    app = create_app(svc, settings=cfg, core=core, ui_path=UI, sweep_seconds=3600)

    async def fake() -> dict[str, Any]:
        return {"fake": True, "note": "TEST FAKE: offline doubles, no real research"}

    # Before the static mount, which would otherwise answer every path.
    app.router.routes.insert(0, APIRoute("/api/fake", fake, methods=["GET"]))
    app.state.fake_ns = SimpleNamespace(root=root)
    n_seed = int(getattr(args, "seed", 0) or 0)
    if n_seed:
        from contextlib import asynccontextmanager

        original = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(a: Any):  # type: ignore[no-untyped-def]
            async with original(a):
                if not getattr(a.state, "seeded", None):
                    try:
                        a.state.seeded = await seed_runs(a.state.service, n_seed)
                        print("seeded runs:", a.state.seeded, flush=True)
                    except (
                        Exception
                    ) as exc:  # the fake still serves; the seed is reported as missing
                        a.state.seeded = {"error": repr(exc)[:300]}
                        print("SEED FAILED:", repr(exc)[:300], flush=True)
                yield

        app.router.lifespan_context = lifespan

        async def seeded() -> dict[str, Any]:
            return dict(getattr(app.state, "seeded", {}) or {})

        app.router.routes.insert(0, APIRoute("/api/fake/seeded", seeded, methods=["GET"]))
    return app


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--port", type=int, default=8140)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--empty", action="store_true")
    p.add_argument("--missing-folder", action="store_true")
    p.add_argument("--not-ready", action="store_true")
    p.add_argument("--slow", action="store_true")
    p.add_argument("--delay", type=float, default=0.6)
    p.add_argument("--max-concurrent", type=int, default=1)
    p.add_argument("--total", default="5.00")
    p.add_argument("--tripwire", default="4.50")
    p.add_argument("--spent", type=float, default=0.0)
    p.add_argument("--credits", choices=["seeded", "low", "empty"], default="seeded")
    p.add_argument("--data-dir", default="")
    p.add_argument(
        "--seed",
        type=int,
        default=0,
        help="also commit a result with this many items (try 600), a partial one, and show their ids at /api/fake/seeded",
    )
    args = p.parse_args()
    if args.host not in ("127.0.0.1", "localhost"):
        raise SystemExit("The test fake only listens on localhost.")
    root = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="research-fake-"))
    root.mkdir(parents=True, exist_ok=True)
    app = make_app(args, root)
    import uvicorn

    print(f"TEST FAKE on http://{args.host}:{args.port}/  (data in {root})", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
