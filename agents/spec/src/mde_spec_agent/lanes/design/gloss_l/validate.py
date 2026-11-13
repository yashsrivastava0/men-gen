"""Local checks of GLOSS-L's output: exactly one non-empty entry per (item, language), nothing unknown."""

from __future__ import annotations

from mde_spec_agent.lanes.design.gloss_l.schema import GlossIn, GlossOut


def validate(inp: GlossIn, out: GlossOut) -> list[str]:
    errors: list[str] = []
    langs = [inp.source_language, *inp.languages]
    ids = {i.id for i in inp.items}
    seen: set[tuple[str, str]] = set()
    for n, e in enumerate(out.entries):
        where = f"entries[{n}]"
        if e.id not in ids:
            errors.append(f"{where}: id {e.id!r} is not one of the items")
            continue
        if e.language not in langs:
            errors.append(f"{where}: language {e.language!r} is not one of {langs}")
            continue
        if not e.text.strip():
            errors.append(f"{where}: text must not be empty")
        key = (e.id, e.language)
        if key in seen:
            errors.append(f"{where}: {e.id} in {e.language} appears twice")
        seen.add(key)
    missing = [(i, lg) for i in sorted(ids) for lg in langs if (i, lg) not in seen]
    if missing:
        shown = ", ".join(f"{i}/{lg}" for i, lg in missing[:12])
        errors.append(
            f"missing {len(missing)} entries, for example {shown}: write one per item and language"
        )
    return errors
