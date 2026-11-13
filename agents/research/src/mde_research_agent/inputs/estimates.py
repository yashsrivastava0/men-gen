"""The cost and time estimate shown before a run (``data/estimates.yaml``), calibrated from measured runs.

Always labelled "estimate, not measured" until the spend record holds a real run of the same size class; then the
middle value is the median of the measured runs (the range stays). The same holds for the expected provider credits
(``credits``: Tavily and Serper, integers): the measured median of what each provider billed, from the entry's
``meta.credits``.
"""

from __future__ import annotations

import statistics
from functools import lru_cache
from typing import Any

import yaml

from mde_research_agent.catalog.loader import DATA_DIR


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return yaml.safe_load((DATA_DIR / "estimates.yaml").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


class Estimates:
    def __init__(self) -> None:
        self.data = _data()

    def size_class(self, load_class: str, *, light: bool) -> str:
        if light:
            return "tiny"
        return str(self.data["class_of_load"].get(load_class, "medium"))

    def label(self, size_class: str) -> str:
        return str(self.data["labels"].get(size_class, size_class.title()))

    def for_class(
        self, size_class: str, spend_entries: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        base = self.data["classes"][size_class]
        usd = dict(base["usd_micros"])
        measured: list[int] = []
        for e in spend_entries or []:
            meta = e.get("meta") or {}
            if (
                e.get("kind") == "run"
                and e.get("status") in ("complete", "partial")
                and meta.get("size_class") == size_class
                and meta.get(
                    "credits"
                )  # a run of the current code (credits are booked per run); older ones are not measurements
                and int(e.get("total_micros", 0)) > 0
            ):
                measured.append(int(e["total_micros"]))
        if measured:
            usd["mid"] = int(statistics.median(measured))
            usd["low"] = min(usd["low"], min(measured))
            usd["high"] = max(usd["high"], max(measured))
        credits, credit_runs = self._credits(base, size_class, spend_entries)
        return {
            "size_class": size_class,
            "searches": dict(base["searches"]),
            "pages": dict(base["pages"]),
            "minutes": dict(base["minutes"]),
            "usd_micros": usd,
            "credits": credits,
            "credits_measured_runs": credit_runs,
            "basis": "measured" if measured else "estimate",
            "measured_runs": len(measured),
            "note": (
                f"from {len(measured)} measured run(s) of this size"
                if measured
                else "estimate, not measured"
            ),
        }

    @staticmethod
    def _credits(
        base: dict[str, Any], size_class: str, spend_entries: list[dict[str, Any]] | None
    ) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
        """Expected credits per provider ``{low, mid, high}``; the measured median replaces ``mid`` (range kept)."""
        credits = {
            p: {k: int(v) for k, v in c.items()} for p, c in (base.get("credits") or {}).items()
        }
        runs: dict[str, int] = dict.fromkeys(credits, 0)
        for provider, values in credits.items():
            measured: list[int] = []
            for e in spend_entries or []:
                meta = e.get("meta") or {}
                booked = (meta.get("credits") or {}).get(provider)
                if (
                    e.get("kind") == "run"
                    and e.get("status") in ("complete", "partial")
                    and meta.get("size_class") == size_class
                    and isinstance(booked, int)
                    and not isinstance(booked, bool)
                    and booked > 0
                ):
                    measured.append(booked)
            if measured:
                values["mid"] = int(statistics.median(measured))
                values["low"] = min(values["low"], min(measured))
                values["high"] = max(values["high"], max(measured))
                runs[provider] = len(measured)
        return credits, runs
