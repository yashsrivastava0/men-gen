"""The input-honesty notes on the G2 page (banners, flags, risk lines) and the G1.5 page (structure, size, capabilities).

A note reaches the page from two places and is shown once: the run stored it when it started (``banners`` and ``flags`` of
``evidence/self-check.json``, ``kind: input_note``), and the derivation re-derives it from the run's recorded inputs
(``derived.notes``), so a run stored before a note existed still shows it. The store-rule sheet's age is judged here, against
the date the caller passes (the injected clock's day): a view never reads the wall clock.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mde_spec_agent.derive.honesty import Note, policy_sheet_stale_note

INPUT_NOTE = "input_note"


def note_flag(n: Note) -> dict[str, Any]:
    """The flag row of a note, the shape ``ctx.flags`` and ``evidence/self-check.json`` carry."""
    return {
        "kind": INPUT_NOTE,
        "code": n.code,
        "why": n.text,
        "fix": n.fix,
        "severity": n.severity,
        "where": "",
        "banner": n.banner,
        "risk": n.risk,
    }


def note_banner(n: Note, step: str = "intake") -> dict[str, Any]:
    return {"code": n.code, "message": n.text, "step": step}


def _dict(x: Any) -> dict[str, Any]:
    if isinstance(x, dict):
        return x
    if hasattr(x, "as_dict"):
        return x.as_dict()
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    return dict(x)


def merge_notes(
    banners: list[Any],
    flags: list[Any],
    derived: Any,
    *,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """``(banners, flags, extra risk lines)`` with every honesty note present once.

    Stored banners and flags come first; a note the derivation knows and the stored lists lack (an older run, a view called
    without a run) is added. A note flagged ``risk`` that is not a banner becomes a risk line."""
    out_banners = [_dict(b) for b in banners]
    out_flags = [_dict(f) for f in flags]
    have: dict[str, dict[str, Any]] = {
        str(f.get("code")): f for f in out_flags if f.get("kind") == INPUT_NOTE
    }
    notes: list[Note] = list(getattr(getattr(derived, "notes", None), "notes", ()) or ())
    stale = policy_sheet_stale_note(today)
    if stale is not None:
        notes.append(stale)
    for n in notes:
        if n.code not in have:
            row = note_flag(n)
            out_flags.append(row)
            have[n.code] = row
    banner_codes = {str(b.get("code")) for b in out_banners}
    for code, row in have.items():
        if row.get("banner") and code not in banner_codes:
            out_banners.append({"code": code, "message": str(row.get("why", "")), "step": "intake"})
            banner_codes.add(code)
    risks = [
        {"text": str(row.get("why", "")), "severity": str(row.get("severity", "warning"))}
        for row in have.values()
        if row.get("risk") and not row.get("banner")
    ]
    return out_banners, out_flags, risks
