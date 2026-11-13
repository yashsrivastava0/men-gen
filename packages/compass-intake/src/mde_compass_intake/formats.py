"""Compass export formats: the ONE place that knows what an export file looks like.

Copied from Research's ``intake/formats.py`` (D-175) and extended for Spec. Neither the Spec agent nor ``reader.py``
imports Compass or trusts one fixed layout. This adapter reads whatever the export is (format 1, format 2, a Brief-only
trim, a future format with extra keys) and hands ``reader.py`` ONE internal shape, :class:`Normalised`. ``reader.py`` is
format-free: it turns a Brief into a ``SpecInput`` and asks this module for everything that depends on the envelope.

What it does, in plain words:

* finds the Brief and the export's own seal (``export.approved_baseline`` and the Brief's ``_hash``) and says how far
  the seal could be checked (``input_binding``). A missing baseline or a changed Brief is NEVER a refusal;
* takes the values from ``consumer_views.research`` when it is there, and builds the same values from the Brief when it
  is not (a trimmed or older export): idea, languages (locale codes parsed tolerantly even from a sentence, with the
  source they came from), geography, audience, glossary, declined items;
* reads what Spec needs besides: the structure block (size band, role matrix, navigation, issues), the tier, the
  readiness block and the interview text (to verify a quote against what the person said);
* tolerates every optional block. Unknown keys are ignored. Drift (an unknown format version, a size band that does
  not fit the tier) is a string in ``drift_flags``, never an exception;
* refuses only what is genuinely unusable: no ``brief`` mapping at all, or a Brief with no idea, no facts and no
  research requests.

To support a new Compass version: change THIS file only (one adapter change), add one trimmed fixture under
``tests/fixtures/formats/`` and one row to ``EXPECTED`` in ``tests/test_formats.py``. See
``docs/research-agent/compass-contract.md`` and ``packages/compass-intake/README.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mde_compass_intake.countries import code_for_name
from mde_compass_intake.countries import languages as known_languages
from mde_compass_intake.models import (
    Failure,
    FocusTopic,
    Idea,
    InputBinding,
    LanguageInfo,
    Tier,
)
from mde_compass_intake.tolerant import m, rows, s, slot, slot_value, strs, to_int
from mde_core.errors import MdeError

ZERO_TOKEN_TIMEOUT_MS = 45_000
KNOWN_FORMATS = (1, 2)
TIERS = ("quick", "standard", "thorough")
SIZE_BANDS = ("small", "medium", "large")
# the size band a tier stands for when the export does not state one (the user's own size statement, doc 15 section 4.1)
BAND_OF_TIER = {"quick": "small", "standard": "medium", "thorough": "large"}
# older exports name the depth instead of the interview budget
TIER_OF_DEPTH = {
    "quick": "quick",
    "standard": "standard",
    "large": "thorough",
    "thorough": "thorough",
}


class ExportRefused(MdeError):
    """The export cannot be researched. ``code`` is machine-readable, ``message`` is plain words."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GeoStated:
    """The geography as the Brief states it (strings only; ``reader.py`` decides what is certain)."""

    city: str = ""
    region: str = ""
    country: str = ""
    tier: Tier = "default"
    confidence: float | None = None
    slot_present: bool = False


@dataclass(frozen=True)
class Normalised:
    """An export in the one shape the reader needs. Every field is always present (empty when the export has none)."""

    brief: Mapping[str, Any]
    brief_hash: str
    brief_version: int
    binding: InputBinding
    compass_format: int | None
    session_id: str
    program_id: str
    idea: Idea
    geo: GeoStated
    locale_codes: tuple[str, ...]
    search_languages: tuple[str, ...]
    audience: tuple[str, ...]
    glossary: tuple[str, ...]
    declined: tuple[str, ...]
    focus: tuple[FocusTopic, ...]
    degraded: bool
    failing_probes: tuple[str, ...]
    fallback_count: int
    failures: tuple[Failure, ...]
    structure_source: str
    flags: tuple[str, ...] = field(default=())
    # --- what Spec needs on top of what Research reads -----------------------------------------------------
    locale_source: str = "none"  # view | experience | meta | search_hints | none
    tier: str = "standard"  # quick | standard | thorough
    tier_source: str = "default"  # interview_budget | depth | default
    size_band: str = "medium"  # small | medium | large
    size_band_source: str = "tier"  # structure | view | tier
    structure_band: str = ""  # the band only when the structure block stated it
    structure_block: Mapping[str, Any] = field(
        default_factory=dict
    )  # role_matrix, navigation, issues, counts
    readiness: Mapping[str, str] = field(default_factory=dict)  # section -> pass | gaps
    interview_text: str = (
        ""  # folded text of the interview, to verify a quote against what the person said
    )
    drift_flags: tuple[str, ...] = field(default=())


# --- binding -------------------------------------------------------------------------------------------


def _computed_hash(brief: Mapping[str, Any]) -> str:
    body = {k: v for k, v in brief.items() if not str(k).startswith("_")}
    try:
        blob = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = repr(sorted(map(str, body)))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _binding(
    export: Mapping[str, Any], brief: Mapping[str, Any]
) -> tuple[str, int, InputBinding, list[str]]:
    """``(brief_hash, version, input_binding, flags)``. The seal is the approved baseline hash, the Brief's own
    ``_hash`` and the export's ``brief_hash``: when all that are present agree, the Brief is ``verified``."""
    baseline = m(export.get("approved_baseline"))
    approved = s(baseline.get("hash"))
    own = s(brief.get("_hash"))
    declared = s(export.get("brief_hash"))
    flags: list[str] = []
    brief_hash = own or declared or approved
    if not brief_hash:
        brief_hash = _computed_hash(brief)
        flags.append("brief_hash_computed")
    if not approved:
        binding: InputBinding = "unverified_no_baseline"
    elif approved == own and (not declared or declared == approved):
        binding = "verified"
    else:
        binding = "unverified_hash_mismatch"
    version = to_int(
        baseline.get("version") or export.get("brief_version") or brief.get("version"), 0
    )
    return brief_hash, version, binding, flags


# --- locales -------------------------------------------------------------------------------------------

_CODE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z]{4})?(?:[-_](?:[A-Za-z]{2}|\d{3}))?")
_WRITTEN_CODE = re.compile(r"\b([A-Za-z]{2,3})[-_]([A-Z]{2}|[A-Z][a-z]{3})\b")
_BARE_SPLIT = re.compile(r"[\s,;/&+]+")
_JOINERS = frozenset({"and", "y", "et", "e", "und", "or", "ou", "o"})


def _tidy_code(code: str) -> str:
    """``pt_pt`` -> ``pt-PT``, ``hi-latn`` -> ``hi-Latn``: language lowercase, script Title, region UPPER."""
    parts = re.split(r"[-_]", code.strip())
    out = [parts[0].lower()]
    for part in parts[1:]:
        out.append(part.title() if len(part) == 4 else part.upper())
    return "-".join(out)


def _primary_known(code: str, languages: Mapping[str, LanguageInfo]) -> bool:
    return code.split("-")[0].lower() in languages


def _prose_locales(text: str, languages: Mapping[str, LanguageInfo]) -> list[str]:
    """Locale codes in a sentence: written codes (``pt-PT``), language names (``Marathi``), or a bare list of codes
    (``en, hi and mr``). A lone two-letter word in a sentence is never read as a code (``in``, ``no``, ``or``)."""
    found: list[tuple[int, str]] = []
    written: set[str] = set()
    for mt in _WRITTEN_CODE.finditer(text):
        code = _tidy_code(f"{mt.group(1)}-{mt.group(2)}")
        if _primary_known(code, languages):
            found.append((mt.start(), code))
            written.add(code.split("-")[0])
    for code, info in languages.items():
        if code in written:  # "Portuguese (pt-PT)": the written code is the more exact of the two
            continue
        for mt in re.finditer(rf"(?<![\w-]){re.escape(info.name)}(?![\w-])", text, re.IGNORECASE):
            found.append((mt.start(), code))
    if not found:
        pieces = [p for p in _BARE_SPLIT.split(text.strip(" .")) if p and p.lower() not in _JOINERS]
        if pieces and all(_CODE.fullmatch(p) and _primary_known(p, languages) for p in pieces):
            found = [(i, _tidy_code(p)) for i, p in enumerate(pieces)]
    found.sort(key=lambda t: t[0])
    return list(dict.fromkeys(code for _, code in found))


def parse_locales(value: Any, languages: Mapping[str, LanguageInfo]) -> tuple[list[str], bool]:
    """``(codes, parsed_from_text)`` for a locale value that may be a list of codes (the usual case), one string or a
    sentence. A well-formed code is kept as written (any language, known or not); everything else is read as prose."""
    items = (
        [value]
        if isinstance(value, str)
        else list(value)
        if isinstance(value, (list, tuple))
        else []
    )
    codes: list[str] = []
    from_text = False
    for item in items:
        text = s(item)
        if not text:
            continue
        if _CODE.fullmatch(text):
            codes.append(_tidy_code(text))
        else:
            parsed = _prose_locales(text, languages)
            from_text = from_text or bool(parsed)
            codes += parsed
    return list(dict.fromkeys(codes)), from_text


# --- geography -----------------------------------------------------------------------------------------


def _geo_from_text(text: str) -> dict[str, str]:
    """A geography stated as one string ("Pune, India"): known countries are the country, the first other piece is
    the city, the rest is the region."""
    pieces = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
    countries = [p for p in pieces if code_for_name(p)]
    others = [p for p in pieces if p not in countries]
    return {
        "country": ", ".join(countries),
        "city": others[0] if others else "",
        "region": ", ".join(others[1:]),
    }


def _geo(brief: Mapping[str, Any], view: Mapping[str, Any]) -> GeoStated:
    context = m(brief.get("context"))
    slot_raw = context.get("geo")
    value, tier, conf = slot(slot_raw)
    raw: Mapping[str, Any]
    if isinstance(value, Mapping):
        raw = value
    elif isinstance(value, str) and value.strip():
        raw = _geo_from_text(value)
    else:
        raw = m(view.get("geography"))
    return GeoStated(
        city=s(raw.get("city")),
        region=s(raw.get("region")),
        country=s(raw.get("country")),
        tier=tier,
        confidence=conf,
        slot_present=slot_raw is not None,
    )


# --- the research view, or its equivalent from the Brief -----------------------------------------------


def _idea(brief: Mapping[str, Any], view: Mapping[str, Any]) -> Idea:
    from_view = m(view.get("idea"))
    from_brief = m(brief.get("idea"))

    def pick(key: str) -> str:
        return s(from_view.get(key)) or s(slot_value(from_brief.get(key)))

    summary = (
        s(from_view.get("summary"))
        or s(brief.get("summary"))
        or s(slot_value(from_brief.get("summary")))
    )
    return Idea(
        summary=summary,
        category=pick("category"),
        subcategory=pick("subcategory"),
        business_model=pick("business_model"),
    )


def _locales(
    brief: Mapping[str, Any], view: Mapping[str, Any], languages: Mapping[str, LanguageInfo]
) -> tuple[tuple[str, ...], tuple[str, ...], bool, str]:
    """``(locale codes, search languages, parsed_from_text, source)``. Order of trust: the research view, the Brief's
    experience locales, the Brief's meta locales, the Brief's search-hint languages."""
    lang = m(view.get("languages"))
    experience = m(brief.get("experience"))
    meta = m(brief.get("meta"))
    sources = (
        lang.get("locales"),
        slot_value(experience.get("locales")),
        meta.get("locales"),
        m(brief.get("search_hints")).get("languages"),
    )
    names = ("view", "experience", "meta", "search_hints")
    codes: list[str] = []
    from_text = False
    found = "none"
    for name, source in zip(names, sources, strict=True):
        codes, from_text = parse_locales(source, languages)
        if codes:
            found = name
            break
    hints, _ = parse_locales(lang.get("search_languages"), languages)
    if not hints:
        hints, _ = parse_locales(m(brief.get("search_hints")).get("languages"), languages)
    return tuple(codes), tuple(hints or codes), from_text, found


def _texts(view_rows: Any, brief_rows: Any, key: str) -> tuple[str, ...]:
    wanted = [s(r.get(key)) for r in rows(view_rows, key)]
    return tuple(wanted or [s(r.get(key)) for r in rows(brief_rows, key)])


# --- trust signals Compass records outside the Brief content -----------------------------------------------


def _failures(data: Mapping[str, Any]) -> tuple[Failure, ...]:
    """Calls that made no tokens, recomputed from ``spend.by_call`` (Compass's own fallback count undercounts)."""
    spend = m(data.get("spend"))
    by_call = spend.get("by_call")
    found: list[Failure] = []
    for call, st in sorted((by_call if isinstance(by_call, Mapping) else {}).items()):
        if not isinstance(st, Mapping) or not st.get("calls"):
            continue
        if not st.get("tokens_in") and not st.get("tokens_out"):
            calls = max(1, to_int(st.get("calls"), 1))
            latency = st.get("latency_ms")
            slow = (
                isinstance(latency, (int, float))
                and float(latency) / calls >= ZERO_TOKEN_TIMEOUT_MS
            )
            found.append(Failure(call=str(call), reason="timeout" if slow else "zero_token"))
    return tuple(found)


def _structure_source(
    data: Mapping[str, Any], brief: Mapping[str, Any], fallback_count: int
) -> str:
    structure = m(data.get("structure"))
    has_structure = bool(structure.get("counts")) or bool(brief.get("modules"))
    if not has_structure:
        return "unknown"
    by_call = m(data.get("spend")).get("by_call")
    drafted = (by_call if isinstance(by_call, Mapping) else {}).get("cmp.draft_module")
    if not isinstance(drafted, Mapping) or not drafted.get("calls"):
        return "fallback"
    return "mixed" if fallback_count >= 5 else "model"


# --- what Spec reads besides what Research reads ----------------------------------------------------------


def fold(text: str) -> str:
    """Text for comparing a quote with what was said: case and whitespace folded."""
    return " ".join(text.casefold().split())


def _strings(node: Any, out: list[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, str):
        if node.strip():
            out.append(node)
    elif isinstance(node, Mapping):
        for v in node.values():
            _strings(v, out, depth + 1)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _strings(v, out, depth + 1)


def _interview_text(data: Mapping[str, Any]) -> str:
    out: list[str] = []
    _strings(data.get("interview"), out)
    return fold("\n".join(out))


def _tier(meta: Mapping[str, Any]) -> tuple[str, str]:
    """``(tier, source)``: the interview budget the person chose (quick, standard, thorough), else the plan depth."""
    plan = m(meta.get("plan"))
    budget = s(plan.get("interview_budget")).lower()
    if budget in TIERS:
        return budget, "interview_budget"
    depth = TIER_OF_DEPTH.get(s(plan.get("depth")).lower())
    if depth:
        return depth, "depth"
    return "standard", "default"


def _size_band(structure: Mapping[str, Any], view: Mapping[str, Any], tier: str) -> tuple[str, str]:
    """``(band, source)``: the structure's band, else the research view's, else the one the tier stands for."""
    for source, raw in (
        ("structure", structure.get("band")),
        ("structure", m(structure.get("research")).get("size_band")),
        ("view", m(view.get("guidance")).get("size_band")),
    ):
        band = s(raw).lower()
        if band in SIZE_BANDS:
            return band, source
    return BAND_OF_TIER.get(tier, "medium"), "tier"


def _readiness(brief: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(k): s(m(v).get("status"))
        for k, v in m(brief.get("readiness")).items()
        if s(m(v).get("status"))
    }


def _drift(fmt: int | None, tier: str, tier_source: str, band: str, band_source: str) -> list[str]:
    """Loud drift flags of the envelope: an unknown or missing format version, a tier that was guessed, a size band
    two steps away from the tier (a size jump)."""
    flags: list[str] = []
    if fmt is None:
        flags.append("format_version_missing")
    elif fmt not in KNOWN_FORMATS:
        flags.append(f"unknown_format_version:{fmt}")
    if tier_source == "default":
        flags.append("tier_defaulted")
    if band_source == "tier":
        flags.append("size_band_from_tier")
    elif abs(SIZE_BANDS.index(band) - SIZE_BANDS.index(BAND_OF_TIER[tier])) >= 2:
        flags.append(f"size_band_tier_mismatch:{tier}/{band}")
    return flags


# --- the entry point ------------------------------------------------------------------------------------


def normalise(
    data: Mapping[str, Any], languages: Mapping[str, LanguageInfo] | None = None
) -> Normalised:
    """The export in one shape, or :class:`ExportRefused` when there is no usable Brief in it.

    ``languages`` defaults to the catalog in ``data/languages.yaml``."""
    languages = known_languages() if languages is None else languages
    brief_raw = data.get("brief") if isinstance(data, Mapping) else None
    if not isinstance(brief_raw, Mapping):
        raise ExportRefused("not_an_export", "This is not a Compass export (it has no Brief).")
    brief: Mapping[str, Any] = brief_raw
    export = m(data.get("export"))
    views = m(data.get("consumer_views"))
    view_raw = views.get("research")
    view = m(view_raw)
    flags: list[str] = []

    compass_format = to_int(export.get("format_version"), -1)
    fmt = compass_format if compass_format >= 0 else None
    flags.append(f"compass_format_{fmt}" if fmt is not None else "compass_format_unknown")
    if not isinstance(view_raw, Mapping):
        flags.append("research_view_missing")
    if "quality" not in view:
        flags.append("no_quality_block")
    if not any("origin" in r for r in rows(brief.get("research_requests"))):
        flags.append("request_origin_missing")
    flags.append("tiers_read_from_brief")

    idea = _idea(brief, view)
    has_requests = bool(rows(brief.get("research_requests"), "id"))
    has_facts = bool(rows(brief.get("domain_facts"), "id"))
    if not (idea.summary or idea.category or has_requests or has_facts):
        raise ExportRefused(
            "unusable_brief",
            "The Brief has no idea, no facts and no research requests, so there is nothing to research.",
        )

    brief_hash, version, binding, hash_flags = _binding(export, brief)
    flags += hash_flags
    codes, search, from_text, locale_source = _locales(brief, view, languages)
    if from_text:
        flags.append("locales_parsed_from_text")
    meta = m(brief.get("meta"))
    probes_raw = meta.get("probes")
    probes = probes_raw if isinstance(probes_raw, Mapping) else {}
    fallback_count = to_int(m(meta.get("interview")).get("fallback_count"), 0)
    audience_view = m(view.get("audience"))
    audience = strs(audience_view.get("segments")) or strs(
        slot_value(m(brief.get("audience")).get("segments"))
    )
    session_id = s(export.get("session_id")) or s(meta.get("session_id"))
    focus = tuple(
        FocusTopic(topic=s(f.get("topic")), about=strs(f.get("about")))
        for f in rows(m(view.get("guidance")).get("focus"), "topic")
    )
    structure = m(data.get("structure"))
    tier, tier_source = _tier(meta)
    band, band_source = _size_band(structure, view, tier)
    return Normalised(
        brief=brief,
        brief_hash=brief_hash,
        brief_version=version,
        binding=binding,
        compass_format=fmt,
        session_id=session_id,
        program_id=s(meta.get("program_id")),
        idea=idea,
        geo=_geo(brief, view),
        locale_codes=codes,
        search_languages=search,
        audience=audience,
        glossary=_texts(view.get("glossary"), brief.get("glossary"), "term"),
        declined=_texts(view.get("declined"), brief.get("declined"), "what"),
        focus=focus,
        degraded=bool(meta.get("degraded")),
        failing_probes=tuple(sorted(str(k) for k, ok in probes.items() if ok is False)),
        fallback_count=fallback_count,
        failures=_failures(data),
        structure_source=_structure_source(data, brief, fallback_count),
        flags=tuple(flags),
        locale_source=locale_source,
        tier=tier,
        tier_source=tier_source,
        size_band=band,
        size_band_source=band_source,
        structure_band=band if band_source == "structure" else "",
        structure_block=structure,
        readiness=_readiness(brief),
        interview_text=_interview_text(data),
        drift_flags=tuple(_drift(fmt, tier, tier_source, band, band_source)),
    )
