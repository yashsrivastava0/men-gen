"""Deterministic content from the locale pools: the fallback of MOCK-HERO and the base of its mock handler. Every value is
synthetic and passes the fixture safety rules (phones masked, emails at example.com, no ID numbers, no URLs, no real brands)."""

from __future__ import annotations

import random
import re

from mde_spec_agent.derive.text import humanize
from mde_spec_agent.lanes.screens.mock_hero.schema import (
    HeroBriefOut,
    HeroEntityIn,
    HeroIn,
    HeroOut,
    HeroValueOut,
    RecIn,
)
from mde_spec_core.mock import (
    LocaleFallbackError,
    Pool,
    format_money,
    mask_phone,
    mock_tables,
    pool_for_language,
)

_AVOID = ["brand marks", "readable text", "real people's faces"]


def pool_of(language: str) -> Pool:
    try:
        return pool_for_language(language)
    except (LocaleFallbackError, KeyError, ValueError):
        return pool_for_language("en")


def _words(key: str) -> set[str]:
    return set(key.lower().split("_"))


def _kind(key: str) -> str | None:
    hints = mock_tables()["text_key_hints"]
    w = _words(key)
    for kind in ("person", "place"):
        if w & set(hints[kind]):
            return kind
    return None


def _slug(name: str, n: int) -> str:
    latin = re.sub(r"[^a-z0-9]+", ".", name.encode("ascii", "ignore").decode().lower()).strip(".")
    return latin if len(latin) >= 3 else f"user.{n}"


def value_for(
    pool: Pool, ent: HeroEntityIn, rec: RecIn, n: int, key: str, ftype: str, allowed: list[str]
) -> str:
    r = random.Random(f"{rec.id}:{key}")  # noqa: S311 - seeded, not security
    w = _words(key)
    if ftype == "text" and w & {"email", "mail"}:
        ftype = "email"
    elif ftype == "text" and w & {"phone", "mobile", "whatsapp"}:
        ftype = "phone"
    if ftype == "phone":
        return mask_phone(f"+919{r.randint(10, 99)}00{r.randint(10, 99)}{r.randint(100, 999)}")
    if ftype == "email":
        return f"{_slug(r.choice(pool.people), n)}@example.com"
    if ftype == "money":
        return format_money(r.randint(40, 4800) * 5, pool)
    if ftype in ("number", "quantity"):
        return str(r.randint(1, 40))
    if ftype == "toggle":
        return r.choice(["true", "false"])
    if ftype in ("choice", "enum"):
        return r.choice(allowed) if allowed else f"option {r.randint(1, 4)}"
    if ftype == "code":
        prefix = re.sub(r"[^A-Z]", "", ent.name.upper())[:4] or "CODE"
        return f"{prefix}{r.randint(10, 99)}"
    if ftype == "location":
        return r.choice(pool.places)
    kind = _kind(key)
    if kind == "person":
        return r.choice(pool.people)
    if kind == "place":
        return r.choice(pool.places)
    if _words(key) & {"name", "title", "label"}:
        return f"{ent.name} {n}"
    state = f" in the {rec.state} state" if rec.state else ""
    return f"{humanize(key)} of {ent.name.lower()} {n}{state}"


def generate(inp: HeroIn) -> HeroOut:
    pool = pool_of(inp.language)
    values: list[HeroValueOut] = []
    briefs: list[HeroBriefOut] = []
    for ent in inp.entities:
        for n, rec in enumerate(ent.records, start=1):
            for f in ent.fields:
                values.append(
                    HeroValueOut(
                        record=rec.id,
                        field=f.key,
                        value=value_for(pool, ent, rec, n, f.key, f.type, f.allowed),
                    )
                )
            if ent.media:
                briefs.append(
                    HeroBriefOut(
                        record=rec.id,
                        subject=f"{ent.name.lower()} {n}, as it would really look",
                        count=1 + (n % 3),
                        avoid=list(_AVOID),
                    )
                )
    return HeroOut(values=values, briefs=briefs)


def brief_text(b: HeroBriefOut) -> str:
    return f"subject: {b.subject}; count: {b.count}; avoid: {', '.join(b.avoid)}"
