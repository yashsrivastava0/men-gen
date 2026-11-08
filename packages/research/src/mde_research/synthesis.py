"""From C8's draft (or the deterministic fallback) to the validated Research Note.

C8 proposes; code disposes. Every suggestion must cite fact ids that exist, ``verified`` is inherited
from those facts (an unverified suggestion is never included by default), urls may only be urls that
appeared as sources of a fact, ids (``RA-###``) are assigned here, and every free-text field is
cleaned, length-capped and checked for instruction look-alikes before it can reach the note.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from mde_contracts.calls import (
    BrandDraft,
    BriefContext,
    C8Input,
    C8Output,
    IntegrationDraft,
    PointerDraft,
    SuggestionDraft,
)
from mde_contracts.common import Depth
from mde_contracts.research import (
    BrandFinding,
    CompliancePointer,
    IntegrationNote,
    ResearchFact,
    ResearchNote,
    ResearchStatus,
    ResearchSuggestion,
    SourceRef,
    SuggestionKind,
)
from mde_contracts.slots import SLOT_IDS
from mde_research.config import ResearchConfig
from mde_research.crosscheck import source_ref
from mde_research.domains import safe_http_url, url_key
from mde_research.privacy import QueryPrivacy
from mde_research.safety import check_statement, clean_statement

SPEC_SECTIONS = frozenset(
    {"design", "screens", "navigation", "data", "api", "roles", "integrations", "behavior"}
)
FEATURES_SLOT = "scope.features"
MAX_FALLBACK_SUGGESTIONS = 6
MAX_GAPS = 5
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FONT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .\-]{0,38}$")
_TONE = re.compile(r"^[A-Za-z][A-Za-z \-]{0,19}$")
_RA = re.compile(r"^RA-(\d{3})$")
_TITLE_FROM_STATEMENT = 60


@dataclass(frozen=True)
class NoteParts:
    suggestions: list[ResearchSuggestion]
    compliance: list[CompliancePointer]
    brand: BrandFinding | None
    integrations: list[IntegrationNote]
    gaps: list[str]


def _norm_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def normalize_applies_to(value: str | None, features: set[str]) -> str | None:
    """``scope.features:<id>`` for a feature, a spec section or a slot id; anything else is None."""
    if not value:
        return None
    text = value.strip()
    if text in SPEC_SECTIONS or text in SLOT_IDS or text == FEATURES_SLOT:
        return text
    if text.startswith(f"{FEATURES_SLOT}:"):
        feature_id = text.split(":", 1)[1].strip()
        return f"{FEATURES_SLOT}:{feature_id}" if feature_id in features else FEATURES_SLOT
    if text in features:
        return f"{FEATURES_SLOT}:{text}"
    return None


def _feature_of(applies_to: str | None) -> str | None:
    if applies_to and applies_to.startswith(f"{FEATURES_SLOT}:"):
        return applies_to.split(":", 1)[1]
    return None


def _text(raw: str, limit: int, min_chars: int = 3) -> str | None:
    clean = clean_statement(raw, limit)
    if len(clean) < min_chars or check_statement(clean, raw, min_chars=min_chars):
        return None
    return clean


def next_suggestion_number(previous: ResearchNote | None) -> int:
    best = 0
    for suggestion in previous.suggestions if previous else []:
        match = _RA.match(suggestion.id)
        if match:
            best = max(best, int(match.group(1)))
    return best + 1


def build_suggestions(
    drafts: Sequence[SuggestionDraft],
    *,
    facts: Mapping[str, ResearchFact],
    have_features: set[str],
    known_features: set[str],
    cap: int,
    previous: ResearchNote | None,
) -> list[ResearchSuggestion]:
    """Validate, deduplicate, cap and number the suggestions (ids stay stable across reruns)."""
    reuse: dict[tuple[str, str], str] = {}
    for old in previous.suggestions if previous else []:
        reuse[(old.kind, _norm_title(old.title))] = old.id
    used = set(reuse.values())
    counter = next_suggestion_number(previous)
    out: list[ResearchSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for draft in drafts:
        title = _text(draft.title, 60)
        rationale = _text(draft.rationale, 200)
        fact_ids = [i for i in dict.fromkeys(draft.fact_ids) if i in facts]
        if title is None or rationale is None or not fact_ids:
            continue
        applies_to = normalize_applies_to(draft.applies_to, known_features | have_features)
        feature = _feature_of(applies_to)
        if feature is not None and feature in have_features:
            continue  # the Brief already has it
        key = (draft.kind, _norm_title(title))
        if key in seen:
            continue
        seen.add(key)
        verified = all(facts[i].verified for i in fact_ids)
        suggestion_id = reuse.get(key)
        if suggestion_id is None:
            while f"RA-{counter:03d}" in used:
                counter += 1
            if counter > 999:
                break
            suggestion_id = f"RA-{counter:03d}"
            counter += 1
            used.add(suggestion_id)
        out.append(
            ResearchSuggestion(
                id=suggestion_id,
                kind=draft.kind,
                title=title,
                rationale=rationale,
                fact_ids=fact_ids,
                default_included=draft.default_included and verified,
                applies_to=applies_to,
                verified=verified,
            )
        )
        if len(out) >= cap:
            break
    return out


def _known_urls(facts: Sequence[ResearchFact]) -> dict[str, SourceRef]:
    urls: dict[str, SourceRef] = {}
    for fact in facts:
        for source in fact.sources:
            urls.setdefault(url_key(source.url), source)
    return urls


def build_pointers(
    drafts: Sequence[PointerDraft], *, flags: Sequence[str], known: Mapping[str, SourceRef]
) -> list[CompliancePointer]:
    allowed = {f.casefold(): f for f in flags}
    out: list[CompliancePointer] = []
    seen: set[str] = set()
    for draft in drafts:
        flag = allowed.get(draft.flag.strip().casefold())
        source = known.get(url_key(safe_http_url(draft.url) or ""))
        title = _text(draft.title, 100)
        if flag is None or flag in seen or source is None or title is None:
            continue
        seen.add(flag)
        out.append(CompliancePointer(flag=flag, title=title, url=source.url))
    return out


def build_brand(
    draft: BrandDraft | None,
    *,
    brand_facts: Sequence[ResearchFact],
    theme_ids: set[str],
) -> BrandFinding | None:
    if draft is None or not brand_facts:
        return None
    urls = _known_urls(brand_facts)
    source = urls.get(url_key(safe_http_url(draft.source_url) or ""))
    if source is None:
        source = next(iter(urls.values()))
    colors = list(dict.fromkeys(c.upper() for c in draft.colors if _HEX.match(c.strip())))[:6]
    fonts = list(dict.fromkeys(f.strip() for f in draft.fonts if _FONT.match(f.strip())))[:3]
    tone = list(
        dict.fromkeys(t.strip().lower() for t in draft.tone_words if _TONE.match(t.strip()))
    )[:5]
    if not (colors or fonts or tone or draft.logo_available):
        return None
    direction = draft.nearest_direction.strip() if draft.nearest_direction else None
    return BrandFinding(
        source_url=source.url,
        colors=colors,
        fonts=fonts,
        tone_words=tone,
        logo_available=draft.logo_available,
        nearest_direction=direction if direction in theme_ids else None,
    )


def build_integrations(
    drafts: Sequence[IntegrationDraft], *, known: Mapping[str, SourceRef]
) -> list[IntegrationNote]:
    out: list[IntegrationNote] = []
    seen: set[str] = set()
    for draft in drafts:
        provider = _text(draft.provider, 60)
        if provider is None or provider.casefold() in seen:
            continue
        seen.add(provider.casefold())
        caveats = [c for c in (_text(x, 160) for x in draft.caveats) if c][:4]
        source = known.get(url_key(safe_http_url(draft.source_url or "") or ""))
        out.append(
            IntegrationNote(
                provider=provider,
                sdk_available=draft.sdk_available,
                expo_compatible=draft.expo_compatible,
                caveats=caveats,
                source=source,
            )
        )
    return out[:6]


def build_gaps(raw: Sequence[str], privacy: QueryPrivacy) -> list[str]:
    out: list[str] = []
    for item in raw:
        text = _text(item, 140, min_chars=8)
        if text is None:
            continue
        text = privacy.clean_query(text) if privacy.restricted else text
        if text and text not in out:
            out.append(text)
    return out[:MAX_GAPS]


def build_parts(
    draft: C8Output,
    *,
    facts: Sequence[ResearchFact],
    ctx: BriefContext,
    cfg: ResearchConfig,
    privacy: QueryPrivacy,
    known_features: set[str],
    theme_ids: set[str],
    previous: ResearchNote | None,
) -> NoteParts:
    by_id = {f.id: f for f in facts}
    known = _known_urls(facts)
    have = {f.id for f in ctx.features}
    return NoteParts(
        suggestions=build_suggestions(
            draft.suggestions,
            facts=by_id,
            have_features=have,
            known_features=known_features,
            cap=cfg.max_suggestions,
            previous=previous,
        ),
        compliance=build_pointers(draft.compliance, flags=ctx.compliance_flags, known=known),
        brand=build_brand(
            draft.brand, brand_facts=[f for f in facts if f.job == "R3"], theme_ids=theme_ids
        ),
        integrations=build_integrations(draft.integrations, known=known),
        gaps=build_gaps(draft.gaps, privacy),
    )


# ----------------------------------------------------------------------------- fallback

_KIND_FOR_FACT: dict[str, SuggestionKind] = {
    "feature": "feature",
    "pattern": "pattern",
    "integration": "integration",
    "compliance": "compliance",
}


def fallback_draft(inp: C8Input) -> C8Output:
    """Registered fallback ``c8_deterministic_suggestions``: suggestions straight from verified
    facts (never included by default, no model involved), pointers from compliance facts, or
    nothing at all when no fact is verified."""
    suggestions: list[SuggestionDraft] = []
    for fact in inp.facts:
        kind = _KIND_FOR_FACT.get(fact.kind)
        if kind is None or not fact.verified or len(suggestions) >= MAX_FALLBACK_SUGGESTIONS:
            continue
        title = fact.statement
        if len(title) > _TITLE_FROM_STATEMENT:
            title = title[: _TITLE_FROM_STATEMENT - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        suggestions.append(
            SuggestionDraft(
                kind=kind,
                title=title,
                rationale=fact.statement[:200],
                fact_ids=[fact.id],
                default_included=False,
                applies_to=None,
            )
        )
    pointers: list[PointerDraft] = []
    compliance = [f for f in inp.facts if f.kind == "compliance" and f.verified and f.source_urls]
    if compliance:
        url = compliance[0].source_urls[0]
        for flag in inp.brief.compliance_flags:
            if flag == "needs_expert_review":
                continue
            title = flag.replace("_", " ").capitalize()
            pointers.append(PointerDraft(flag=flag, title=title, url=url))
    return C8Output(suggestions=suggestions, compliance=pointers)


# ----------------------------------------------------------------------------- assembly


def assemble_note(
    *,
    brief_hash: str,
    depth: Depth,
    facts: Sequence[ResearchFact],
    parts: NoteParts,
    status: ResearchStatus,
    reason: str | None,
    queries_run: int,
    cost_micros: int,
    created_at: datetime,
    previous: ResearchNote | None,
) -> ResearchNote:
    return ResearchNote(
        version=(previous.version + 1) if previous else 1,
        brief_hash=brief_hash,
        depth=depth,
        status=status,
        unavailable_reason=reason,
        facts=list(facts),
        suggestions=parts.suggestions,
        compliance=parts.compliance,
        brand=parts.brand,
        integrations=parts.integrations,
        gaps=parts.gaps,
        queries_run=queries_run,
        cost_micros=cost_micros,
        created_at=created_at,
    ).with_hash()


def unavailable_note(
    brief_hash: str,
    reason: str,
    depth: Depth,
    *,
    queries_run: int = 0,
    cost_micros: int = 0,
    created_at: datetime,
) -> ResearchNote:
    note = ResearchNote.unavailable(brief_hash, reason, depth)
    return note.model_copy(
        update={"queries_run": queries_run, "cost_micros": cost_micros, "created_at": created_at}
    ).with_hash()


def retrieval_date(now: datetime) -> date:
    return now.date()


def internal_source_ref(source: str, retrieved_at: date) -> SourceRef:
    """A connector document as a source (``internal://<reference>``): never a web url."""
    safe = re.sub(r"[^A-Za-z0-9._/\-]+", "-", source.strip())[:120].strip("-/") or "document"
    return source_ref(f"internal://{safe}", None, retrieved_at).model_copy(
        update={"domain": "internal"}
    )
