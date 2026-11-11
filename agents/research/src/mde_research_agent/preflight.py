"""Preflight: plain checks of whether the app can do a live run, each with what to do about it.

Booleans and names only: no key, no value, no path outside the repository is ever shown. Every check is
``{id, label, ok, detail, fix}``; ``ok`` False with a ``fix`` text is what the Preflight pill explains.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mde_core.money import micros_to_usd
from mde_core.settings import Settings
from mde_research_agent.config import (
    ResearchSettings,
    data_dir,
    inputs_dir,
    keys_present,
    ui_dir,
)
from mde_research_agent.service import ResearchService

MODEL_ROUTES = ("fast", "standard", "deep")


def _check(cid: str, label: str, ok: bool, detail: str, fix: str = "") -> dict[str, Any]:
    return {"id": cid, "label": label, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def run_preflight(
    service: ResearchService, core: Settings, cfg: ResearchSettings
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    keys = keys_present(cfg)
    for provider, env in (("tavily", "TAVILY_API_KEY"), ("serper", "SERPER_API_KEY")):
        checks.append(
            _check(
                f"key:{provider}",
                f"Search key: {provider.title()}",
                keys[provider],
                "present" if keys[provider] else "missing",
                f"Set {env} in the app's environment (at least one search key is needed).",
            )
        )
    checks.append(
        _check(
            "search",
            "A search provider is ready",
            service.search is not None,
            service.search.name if service.search else "none",
            "Set TAVILY_API_KEY or SERPER_API_KEY.",
        )
    )
    folder = inputs_dir(cfg)
    scan = service.inputs.scan() if service.inputs else None
    n_valid = len(scan.valid) if scan else 0
    checks.append(
        _check(
            "inputs",
            "Inputs folder",
            bool(scan and scan.folder_exists and n_valid > 0),
            f"{n_valid} usable file(s) found"
            if scan and scan.folder_exists
            else "folder not found",
            f"Put approved Compass exports (.json) in {folder}.",
        )
    )
    data = data_dir(cfg)
    ok_write = _writable(data)
    checks.append(
        _check(
            "data",
            "Data folder is writable",
            ok_write,
            "yes" if ok_write else "no",
            f"Check the permissions of {data}.",
        )
    )
    checks.append(_check("store", "Store is ready", True, cfg.research_store))
    b = service.budget_status()
    if b.get("enabled"):
        state = b["state"]
        checks.append(
            _check(
                "budget",
                "Budget",
                state == "ok",
                f"USD {micros_to_usd(b['spent_micros'])} of {micros_to_usd(b['total_micros'])} used",
                "The budget tripwire was reached: restart the budget on the Budget page to continue."
                if state == "tripwire"
                else "The budget is used up: restart the budget on the Budget page.",
            )
        )
    ui = ui_dir(cfg)
    ui_ok = (ui / "index.html").is_file()
    checks.append(
        _check(
            "ui",
            "Front end files present",
            ui_ok,
            "yes" if ui_ok else "missing",
            f"Expected {ui}/index.html.",
        )
    )
    routes = {r: core.model_for_route(r) for r in MODEL_ROUTES}
    checks.append(
        _check(
            "routes",
            "Model routes configured",
            all(routes.values()),
            ", ".join(f"{k}={v}" for k, v in routes.items()),
        )
    )
    ready = all(c["ok"] for c in checks if c["id"] != "key:tavily" and c["id"] != "key:serper")
    return {
        "ready": ready and service.ready,
        "checks": checks,
        "model": core.model_for_route("fast"),
        "checker_vendor": service.checker_vendor,
        "time": datetime.now(UTC).isoformat(),
    }
