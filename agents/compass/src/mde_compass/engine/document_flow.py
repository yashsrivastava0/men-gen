"""Document claims to Brief writes (pure; the service applies them). See ``document_map.yaml``.

Evidence rule: a written claim carries ``document`` provenance with ``ref = "SRC-001 <pointer>"`` and the
verbatim quote, so it may be Locked; a claim that needs confirming, conflicts, or is an open issue is NEVER
written: it becomes a question (open issues) or part of one summary question (needs confirming).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import JsonValue

from mde_compass.domain.claims import Claim
from mde_compass.domain.common import Provenance


@lru_cache(maxsize=1)
def _map() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((Path(__file__).parent / "document_map.yaml").read_text(encoding="utf-8"))


@dataclass
class DocumentWrite:
    slot_id: str
    value: JsonValue
    provenance: Provenance
    confidence: float
    claim_ids: list[str] = field(default_factory=list)


@dataclass
class DocumentPlan:
    writes: list[DocumentWrite] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)  # "TBD" items: become questions
    needs_confirming: int = 0
    conflicts: int = 0


def _short(text: str, max_words: int) -> str:
    words = text.strip().split()
    return " ".join(words[:max_words]).rstrip(" ,;:.")


def _glossary(text: str) -> dict[str, JsonValue] | None:
    for sep in (" means ", " is defined as ", ": ", " = "):
        if sep in text:
            term = text.split(sep, 1)[0].strip().strip("\"'")
            if 0 < len(term.split()) <= 4:
                return {"term": term, "synonyms": [], "avoid": []}
    return None


def plan_from_claims(
    claims: list[Claim], existing: dict[str, JsonValue] | None = None
) -> DocumentPlan:
    """``existing`` maps slot id to its current value so list slots are extended, not overwritten."""
    plan = DocumentPlan()
    existing = existing or {}
    per_slot: dict[str, DocumentWrite] = {}
    for claim in claims:
        st = claim.statement
        if st.type == "open_issue":
            plan.open_issues.append(st.text.strip())
            continue
        if claim.status == "conflicting":
            plan.conflicts += 1
            continue
        if claim.status != "used":
            plan.needs_confirming += 1
            continue
        rule = _map().get(st.type)
        if rule is None:
            continue
        slot_id, shape, limit = rule["slot"], rule["shape"], int(rule["max_words"])
        prov = Provenance(
            type="document",
            ref=f"{claim.source_id} {st.pointer}".strip(),
            quote=st.quote[:300],
        )
        item: JsonValue
        if shape == "named_item":
            item = {"name": _short(st.text, limit), "intent": st.text.strip()[:200]}
            if slot_id == "roles.list":
                item = {"name": _short(st.text, limit)}
        elif shape == "glossary":
            gloss = _glossary(st.text)
            if gloss is None:
                continue
            item = gloss
        else:
            item = _short(st.text, limit)
        if not item or (isinstance(item, dict) and not item.get("name") and not item.get("term")):
            continue
        write = per_slot.get(slot_id)
        if write is None:
            current = existing.get(slot_id)
            if shape == "single_text":
                base: JsonValue = current if isinstance(current, str) and current else None
                if base is not None:
                    continue  # never overwrite what the user already said (chat beats documents)
                write = DocumentWrite(slot_id, item, prov, st.confidence, [claim.id])
            else:
                base_list = list(current) if isinstance(current, list) else []
                write = DocumentWrite(slot_id, base_list, prov, st.confidence, [])
            per_slot[slot_id] = write
        if shape == "single_text":
            continue  # the first statement wins
        assert isinstance(write.value, list)
        key = lambda v: str(v.get("name") or v.get("term") if isinstance(v, dict) else v).lower()  # noqa: E731
        if key(item) not in {key(v) for v in write.value}:
            write.value.append(item)
            write.claim_ids.append(claim.id)
    plan.writes = [w for w in per_slot.values() if w.claim_ids]
    return plan
