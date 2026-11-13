"""Extract SMALL test fixtures from full Compass format-2 exports.

Usage (from the repository root)::

    uv run python agents/research/scripts/extract_fixtures.py
    uv run python agents/research/scripts/extract_fixtures.py --src docs/compass/research-inputs --out agents/research/tests/fixtures/exports

A fixture keeps the SAME key layout as a real export, so the intake code runs unchanged on a full
export and on a fixture, but it drops what Research never reads: histories, provenance quotes,
the structure bulk (screens, flows' steps, entity fields), the interview transcript, brief versions,
change sets and sources. What stays: the approved hash and its section hashes, the ``research``
consumer view (whole), the Brief sections Research needs (ids, tiers, statuses, values, facts with
their kinds, requests, hints) and the spend table. The originals are never edited and no test depends
on their path. Dropping a fixture file into the output folder is all a new example needs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SRC = ROOT / "docs" / "compass" / "research-inputs"
DEFAULT_OUT = ROOT / "agents" / "research" / "tests" / "fixtures" / "exports"

# Brief sections that stay (everything else is dropped).
KEEP_SECTIONS = (
    "idea",
    "context",
    "audience",
    "apps",
    "roles",
    "scope",
    "references",
    "experience",
    "integrations",
    "data",
    "glossary",
    "domain_facts",
    "research_requests",
    "search_hints",
    "declined",
    "ids",
)
# Structure records are reduced to the ids and names Research indexes by.
STRUCTURE_FIELDS = {
    "modules": ("id", "name", "kind", "apps", "roles", "tier", "status"),
    "entities": ("id", "key", "name", "module", "tier", "status"),
    "flows": ("id", "name", "role", "features", "tier", "status"),
}
DROP_META = {"compile_digests"}
QUOTE_LIMIT = 160


def _trim_provenance(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep the kind and the reference of each provenance entry, never the quoted words."""
    return [{"type": str(i.get("type", "")), "ref": str(i.get("ref", ""))} for i in items]


def slim(value: Any) -> Any:
    """Drop histories, reduce provenance to ``{type, ref}``, cut long quotes; recurse."""
    if isinstance(value, list):
        return [slim(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key == "history":
            continue
        if key == "provenance" and isinstance(item, list):
            out[key] = _trim_provenance(item)
        elif key == "quote" and isinstance(item, str):
            out[key] = item[:QUOTE_LIMIT]
        else:
            out[key] = slim(item)
    return out


def slim_export(export: dict[str, Any], source_name: str) -> dict[str, Any]:
    """The small fixture for one full export."""
    brief = export["brief"]
    slim_brief: dict[str, Any] = {
        "version": brief.get("version"),
        "meta": slim({k: v for k, v in brief.get("meta", {}).items() if k not in DROP_META}),
    }
    for name in KEEP_SECTIONS:
        if name in brief:
            slim_brief[name] = slim(brief[name])
    for name, fields in STRUCTURE_FIELDS.items():
        slim_brief[name] = [
            {k: slim(v) for k, v in rec.items() if k in fields} for rec in brief.get(name, [])
        ]
    slim_brief["_hash"] = brief.get("_hash")
    slim_brief["_section_hashes"] = brief.get("_section_hashes")
    baseline = export["export"].get("approved_baseline", {})
    spend = export.get("spend", {})
    return {
        "_fixture": {
            "source": source_name,
            "note": "Slim fixture extracted by agents/research/scripts/extract_fixtures.py. "
            "Same key layout as a Compass format-2 export; histories, provenance quotes and "
            "the structure bulk are dropped.",
        },
        "export": {
            "format": export["export"].get("format"),
            "format_version": export["export"].get("format_version"),
            "brief_version": export["export"].get("brief_version"),
            "brief_hash": export["export"].get("brief_hash"),
            "approved_baseline": {
                "version": baseline.get("version"),
                "hash": baseline.get("hash"),
                "section_hashes": baseline.get("section_hashes"),
            },
        },
        "brief": slim_brief,
        "structure": {
            "band": export.get("structure", {}).get("band"),
            "source": export.get("structure", {}).get("source"),
            "counts": export.get("structure", {}).get("counts"),
            "issues_count": len(export.get("structure", {}).get("issues", [])),
        },
        "consumer_views": {"research": export["consumer_views"]["research"]},
        "spend": {
            "calls": spend.get("calls"),
            "cost_micros": spend.get("cost_micros"),
            "by_call": spend.get("by_call", {}),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # pyright: ignore[reportOptionalMemberAccess]
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    sources = sorted(p for p in args.src.glob("*.json"))
    if not sources:
        sys.stderr.write(f"no *.json exports in {args.src}\n")
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    for path in sources:
        data = json.loads(path.read_text(encoding="utf-8"))
        small = slim_export(data, path.name)
        target = args.out / path.name
        target.write_text(
            json.dumps(small, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sys.stdout.write(
            f"{path.name}: {path.stat().st_size:>9} -> {target.stat().st_size:>7} bytes\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
