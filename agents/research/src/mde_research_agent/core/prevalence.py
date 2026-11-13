"""How common is a choice, and how recent is the evidence? Computed by CODE from items, never by a model.

``seen_in`` is the number of distinct sources (competitors, or independent domains) that show a choice and
``of`` how many were checked. Fewer than ``min_sources`` checked means ``limited_evidence`` (we cannot say
"common" from two pages). Otherwise the share decides: ``core`` (most), ``common``, ``advanced`` (present
but rare), and ``emerging`` when the choice is rising, recent and seen in only a few sources. Thresholds are
data (``data/prevalence.yaml``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from mde_research_agent.catalog.models import PrevalenceConfig
from mde_research_agent.domain.research import Level, Prevalence, Recency, Trend


def age_months(published: date | None, as_of: date) -> int | None:
    """Whole months between ``published`` and ``as_of`` (never negative; ``None`` for an unknown date)."""
    if published is None:
        return None
    months = (as_of.year - published.year) * 12 + (as_of.month - published.month)
    if as_of.day < published.day:
        months -= 1
    return max(0, months)


def level_for(
    seen_in: int, of: int, *, newest_age_months: int | None, rising: bool, cfg: PrevalenceConfig
) -> Level:
    if of < cfg.min_sources:
        return "limited_evidence"
    if (
        rising
        and seen_in <= cfg.emerging_max_seen
        and newest_age_months is not None
        and newest_age_months <= cfg.emerging_max_age_months
    ):
        return "emerging"
    share = seen_in / of if of else 0.0
    if share >= cfg.core_share:
        return "core"
    if share >= cfg.common_share:
        return "common"
    return "advanced"


def make_prevalence(
    seen_in: int, of: int, *, newest_age_months: int | None, rising: bool, cfg: PrevalenceConfig
) -> Prevalence:
    seen = min(seen_in, of)
    return Prevalence(
        seen_in=seen,
        of=of,
        level=level_for(seen, of, newest_age_months=newest_age_months, rising=rising, cfg=cfg),
    )


def is_stale(age: int | None, *, job: str, is_fee: bool, cfg: PrevalenceConfig) -> bool:
    """Older than the limit for this kind of fact (an unknown date is never 'stale', only unknown)."""
    if age is None:
        return False
    limit = (
        cfg.stale_months.fees
        if is_fee
        else (
            cfg.stale_months.recency_jobs if job in cfg.recency_jobs else cfg.stale_months.default
        )
    )
    return age > limit


def is_fee_text(text: str, cfg: PrevalenceConfig) -> bool:
    low = text.casefold()
    return any(w in low for w in cfg.fee_words)


def recency_of(dates: Iterable[date | None], as_of: date, trend: Trend = "unknown") -> Recency:
    known = [d for d in dates if d is not None]
    newest = max(known) if known else None
    return Recency(newest_source=newest, age_months=age_months(newest, as_of), trend=trend)
