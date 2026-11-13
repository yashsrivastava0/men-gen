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


def _credit_checks(service: ResearchService, keys: dict[str, bool]) -> list[dict[str, Any]]:
    """Information about provider credits, one line per provider whose key is set.

    ``ok`` is ALWAYS true: credits only inform (they never make the app "not ready" and never stop a run). When a
    provider is running low the line carries a plain ``note`` the front end can show.
    """
    out: list[dict[str, Any]] = []
    try:
        status = service.credits_status()
    except Exception:  # information only: never let it break the preflight
        return out
    for p in status.get("providers", []):
        if not keys.get(p["provider"]):
            continue
        used, limit, left = p["used"], p["limit"], p["remaining"]
        if used is not None and limit:
            detail = f"{used:,} of {limit:,} credits used"
        elif left is not None:
            detail = f"{left:,} credits left"
        else:
            detail = "no count yet"
        check = _check(f"credits:{p['provider']}", f"Search credits: {p['label']}", True, detail)
        check["note"] = p["note"] if p["level"] in ("amber", "red") else ""
        check["level"] = p["level"]
        out.append(check)
    return out


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
    # What the model routes still need (a vendor key, a price) is decided by the gateway when the app is composed
    # and kept in ``service.missing``; the search-key line is already shown by the checks above.
    model_missing = [m for m in service.missing if "SERPER_API_KEY" not in m]
    checks.append(
        _check(
            "models",
            "Models are priced and their keys are set",
            not model_missing,
            "yes" if not model_missing else "missing: " + "; ".join(model_missing),
            "Set what is named in the app's environment (a key of the vendor a route uses, or MODEL_ROUTES).",
        )
    )
    checks.extend(_credit_checks(service, keys))
    # Credits are information only: whatever they say, they are not part of "ready".
    ready = all(
        c["ok"]
        for c in checks
        if c["id"] not in ("key:tavily", "key:serper") and not c["id"].startswith("credits:")
    )
    return {
        "ready": ready and service.ready,
        "checks": checks,
        "model": core.model_for_route("fast"),
        "checker_vendor": service.checker_vendor,
        "time": datetime.now(UTC).isoformat(),
    }
