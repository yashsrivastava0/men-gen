"""Mock handler of PROD-INTEG: the completed draft, plus the providers of the research dossier list whose capability
matches the need (never a name the list does not hold). A need without a match keeps its provider empty."""

from __future__ import annotations

from .baseline import complete
from .schema import IntegIn, IntegOut


def handle(inp: IntegIn) -> IntegOut:
    out = complete(inp)
    named: dict[str, list[str]] = {}
    for line in (
        inp.provider_names
    ):  # "id|name|need" rows of the closed list; only dossier ids are used here
        parts = line.split("|")
        if len(parts) == 3 and parts[0] not in ("", "-"):
            named.setdefault(parts[2].casefold(), []).append(parts[0])
    for r in out.integrations:
        if not r.provider_ids:
            r.provider_ids = sorted(set(named.get(r.need_key.casefold(), [])))[:3]
    return out
