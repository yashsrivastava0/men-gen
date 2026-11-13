"""JSON Schemas of the two public contracts, generated from the package models.

``research.v5``: the item's ``data`` is validated PER KIND (an ``if kind == X then data matches X's payload`` block per
kind, draft 2020-12), so a consumer (or RV-01) can check ``data`` against the kind it claims. ``research.event.v1``:
the event with its closed list of types. The committed files in ``agents/research/contracts/`` must equal what this
module generates (a test keeps them in sync): regenerate with ``uv run python -m mde_research_agent.schemas --write``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mde_research_agent.domain.atoms import PAYLOADS
from mde_research_agent.domain.events import Event
from mde_research_agent.domain.research import ResearchOutput

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
RESEARCH_FILE = "research.v5.schema.json"
EVENT_FILE = "research.event.v1.schema.json"


def research_v5_schema() -> dict[str, Any]:
    schema = ResearchOutput.model_json_schema(by_alias=True, mode="serialization")
    defs = schema.setdefault("$defs", {})
    clauses: list[dict[str, Any]] = []
    for kind, model in sorted(PAYLOADS.items()):
        payload = model.model_json_schema(mode="serialization")
        for name, sub in payload.pop("$defs", {}).items():
            defs[f"{kind}__{name}"] = _rename_refs(sub, kind)
        defs[f"data__{kind}"] = _rename_refs(payload, kind)
        clauses.append(
            {
                "if": {"properties": {"kind": {"const": kind}}, "required": ["kind"]},
                "then": {"properties": {"data": {"$ref": f"#/$defs/data__{kind}"}}},
            }
        )
    item = defs["Item"]
    item["allOf"] = clauses
    item["properties"]["kind"] = {"type": "string", "enum": sorted(PAYLOADS), "title": "Kind"}
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "research.v5"
    return schema


def _rename_refs(node: Any, kind: str) -> Any:
    if isinstance(node, dict):
        return {
            k: (
                f"#/$defs/{kind}__{v.rsplit('/', 1)[-1]}"
                if k == "$ref" and isinstance(v, str)
                else _rename_refs(v, kind)
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rename_refs(x, kind) for x in node]
    return node


def event_schema() -> dict[str, Any]:
    schema = Event.model_json_schema(by_alias=True, mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "research.event.v1"
    return schema


def dumps(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_all(directory: Path = CONTRACTS_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, schema in ((RESEARCH_FILE, research_v5_schema()), (EVENT_FILE, event_schema())):
        path = directory / name
        path.write_text(dumps(schema), encoding="utf-8")
        out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--write" in args:
        for p in write_all():
            sys.stdout.write(f"wrote {p}\n")
        return 0
    drift = [
        name
        for name, schema in ((RESEARCH_FILE, research_v5_schema()), (EVENT_FILE, event_schema()))
        if not (CONTRACTS_DIR / name).exists()
        or (CONTRACTS_DIR / name).read_text(encoding="utf-8") != dumps(schema)
    ]
    if drift:
        sys.stderr.write(f"schema drift: {drift} (run with --write)\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
