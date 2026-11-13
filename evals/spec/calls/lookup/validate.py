from __future__ import annotations

from evals.spec.calls.lookup.schema import LookupIn, LookupOut


def validate(inp: LookupIn, out: LookupOut) -> list[str]:
    names = {line.partition(":")[0].strip() for line in inp.catalogue}
    wanted = [line.partition(":")[0].strip() for line in inp.items]
    errors: list[str] = []
    got = [m.item for m in out.matches]
    for w in wanted:
        if w not in got:
            errors.append(f"no answer for need {w}")
    for g in sorted(set(got) - set(wanted)):
        errors.append(f"answer for an unknown need {g}")
    for m in out.matches:
        if len(m.candidates) > 3:
            errors.append(f"{m.item}: more than three candidates")
        for c in m.candidates:
            if c not in names:
                errors.append(f"{m.item}: {c!r} is not a component of the catalogue")
        if m.gap and m.candidates:
            errors.append(f"{m.item}: a gap has no candidates")
        if not m.gap and not m.candidates and m.gap_note:
            errors.append(f"{m.item}: gap_note without gap")
    return errors
