"""Verify extracted items against the stored page: quote check, limits, forbidden patterns, payload validity.

Pure functions. A candidate is a wire atom (common fields + the kind's fields). ``check_candidate`` returns the
verified, normalised item (an :class:`AtomRecord` without ids) or a reason it was rejected. Nothing downstream
trusts the model: the quote must be a substring of the stored page text after normalisation (RV-02), strings are
screened for URLs and instructions, lengths are enforced, and the payload must fit the kind (RV-01).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel

from mde_research_agent.core.dates import parse_loose_date
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.quotes import verify_quote
from mde_research_agent.core.safety import forbidden_in_value
from mde_research_agent.domain.atoms import COMMON_FIELDS, payload_from_wire, wire_atom_model
from mde_research_agent.engine.state import PageRecord

MAX_QUOTE_WORDS = 25
MAX_CLAIM_WORDS = 40
MAX_LABEL_WORDS = 8


@dataclass(frozen=True)
class Candidate:
    kind: str
    norm_key: str
    label: str
    claim: str
    quote: str
    impact: str
    published_on: date | None
    data: dict[str, Any]


@dataclass(frozen=True)
class Rejected:
    kind: str
    norm_key: str
    reason: str  # quote_not_found | forbidden_pattern | invalid | empty


def _clip(text: str, n: int) -> str:
    words = text.split()
    return " ".join(words[:n])


def candidates_from_output(output: BaseModel, page: PageRecord) -> list[Candidate | Rejected]:
    """Flatten an extraction output (one array per kind) into candidates; a malformed item is ``Rejected``."""
    out: list[Candidate | Rejected] = []
    for kind, atoms in output.model_dump().items():
        wire = wire_atom_model(kind)
        for raw in atoms:
            try:
                atom = wire.model_validate(raw)
                data = payload_from_wire(kind, atom)
            except ValueError:
                out.append(Rejected(kind, str(raw.get("norm_key", "")), "invalid"))
                continue
            key = clean_key(atom.norm_key)
            if not key or not atom.quote.strip() or not atom.claim.strip():
                out.append(Rejected(kind, key, "empty"))
                continue
            common = {f: getattr(atom, f) for f in COMMON_FIELDS}
            if forbidden_in_value(
                [common["claim"], common["label"], common["quote"], common["norm_key"], data]
            ):
                out.append(Rejected(kind, key, "forbidden_pattern"))
                continue
            out.append(
                Candidate(
                    kind=kind,
                    norm_key=key,
                    label=_clip(atom.label, MAX_LABEL_WORDS),
                    claim=_clip(atom.claim, MAX_CLAIM_WORDS),
                    quote=_clip(atom.quote, MAX_QUOTE_WORDS),
                    impact=atom.impact,
                    published_on=parse_loose_date(atom.published_on) or page.published,
                    data=data,
                )
            )
    return out


def check_candidate(c: Candidate, page: PageRecord) -> Rejected | None:
    """None when the candidate's quote is really on the page; else why not."""
    res = verify_quote(page.norm, c.quote)
    if not res.ok:
        return Rejected(c.kind, c.norm_key, "quote_not_found")
    return None
