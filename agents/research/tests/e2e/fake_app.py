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
        kwargs["missing"] = [
            "LLM_MODE=live",
            "ALLOW_LIVE_LLM=1",
            "TAVILY_API_KEY or SERPER_API_KEY",
        ]
    svc = build_app_test_service(root, **kwargs)
    if args.spent and svc.record is not None and not svc.record.data["entries"]:
        from mde_core.money import usd_to_micros

        eid = svc.record.start_entry("smoke", label="Earlier live checks (test fake)")
        svc.record.add_cost(eid, "plan", usd_to_micros(args.spent))
        svc.record.finish_entry(eid, "complete")
    return svc


class _Core:
    """What the preflight page reads from the core settings. The test fake shows 'ready' so the screens can be driven;
    the gateway behind it is the offline double, so nothing here can reach a real model or the real web."""

    llm_mode = "live"
    allow_live_llm = True
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
    app = create_app(svc, settings=ResearchSettings(), core=core, ui_path=UI, sweep_seconds=3600)

    async def fake() -> dict[str, Any]:
        return {"fake": True, "note": "TEST FAKE: offline doubles, no real research"}

    # Before the static mount, which would otherwise answer every path.
    app.router.routes.insert(0, APIRoute("/api/fake", fake, methods=["GET"]))
    app.state.fake_ns = SimpleNamespace(root=root)
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
    p.add_argument("--data-dir", default="")
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
