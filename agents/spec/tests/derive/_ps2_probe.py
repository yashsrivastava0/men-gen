"""Run by test_ps2_real_exports in a subprocess under a different locale and hash seed: prints the pack hashes as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mde_compass_intake import read_export
from mde_spec_agent.derive import derive_graph
from mde_spec_core.pack import PackOptions, assemble

root = Path(sys.argv[1])
out: dict[str, dict[str, str]] = {}
for name in sys.argv[2:]:
    si = read_export(json.loads((root / f"{name}.json").read_text(encoding="utf-8")))
    g = derive_graph(si).graph
    pack = assemble(g)
    out[name] = {
        "content": pack.content_hash,
        "package": pack.package_hash,
        "files": __import__("hashlib")
        .sha256(json.dumps(pack.files, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        .hexdigest(),
    }
    other = assemble(
        g, PackOptions(chars_per_token=2.4, task_read_budget=40000, consumer_id="second")
    )
    out[name]["second_package"] = other.package_hash
print(json.dumps(out, sort_keys=True))
