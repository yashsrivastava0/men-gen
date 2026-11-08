"""Export JSON Schemas for every contract model and (if importable) the context API OpenAPI doc.

Output: docs/api/schemas/<name>.json and docs/api/openapi.json. Deterministic (sorted keys).
Usage: python scripts/export_schemas.py [--check]   (--check fails if files would change)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "api" / "schemas"


def dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    from mde_contracts.registry import exported_models

    files: dict[Path, str] = {}
    for name, model in sorted(exported_models().items()):
        files[OUT / f"{name}.json"] = dump(model.model_json_schema())
    try:
        from mde_context_api.main import create_app  # type: ignore[import-not-found]

        files[ROOT / "docs" / "api" / "openapi.json"] = dump(create_app().openapi())
    except Exception as exc:
        print(f"openapi skipped: {type(exc).__name__}")

    stale = 0
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = {p.name for p in files if p.parent == OUT}
    for old in OUT.glob("*.json"):
        if old.name not in wanted:
            stale += 1
            if not check:
                old.unlink()
    for path, text in files.items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            stale += 1
            if not check:
                path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{len(files)} schema files; {'would change' if check else 'updated'}: {stale}")
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
