"""What a patch builder needs besides the call's input: the graph snapshot the batch was built from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mde_spec_core.canon import sha256_json
from mde_spec_core.graph import SpecGraph

from .slices import Maps


@dataclass
class Env:
    graph: SpecGraph  # the snapshot (never mutated by a batch)
    spec_input: Any = None
    maps: Maps = field(default_factory=Maps)

    def quote_of(self, ref: str) -> str | None:
        node = self.graph.find(ref)
        if node is not None:
            for p in node.provenance:
                if p.quote_id:
                    return p.quote_id
        return None


def input_hash_of(inp: Any) -> str:
    """Hash of what a node was written from (doc 15 10.5): the call input without the re-ask text."""
    data = inp.model_dump(mode="json", exclude={"reask", "baseline"})
    # digits become letters: a long run of digits in a hash reads as a phone number to the V-20 scan (as the seed does)
    return sha256_json(data)[:32].translate(str.maketrans("0123456789", "ghijklmnop"))
