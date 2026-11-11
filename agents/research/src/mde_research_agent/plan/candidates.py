"""Candidates for the ``foreach`` expansion of target templates (roles, flows, entities, needs, rule topics, ...).

Each function reads the ``ResearchInput`` and returns the names a template should be expanded over, with the
Brief ids they link to, a priority bonus and extra terms for passage selection. Structure (modules, entities,
flows) is a HINT: names only, never counts. Trust rules apply: duplicates removed, own brand never a target,
open questions and assumptions never read, ``later`` features still researched (it only lowers the rank).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.text import same_thing, stem_set, tokens
from mde_research_agent.domain.input import Request, ResearchInput
from mde_research_agent.domain.plan import Links

PRIORITY_RANK = {"must": 0, "nice": 1, "later": 2}
MAX_PHRASE_WORDS = 4
_EDGE_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "by",
        "with",
        "or",
        "at",
        "as",
        "is",
        "are",
        "be",
    ]
)


@dataclass
class Candidate:
    name: str
    links: Links = field(default_factory=Links)
    flags: tuple[str, ...] = ()
    bonus: int = 0
    terms: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    label_override: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def short_phrase(text: str, n: int = MAX_PHRASE_WORDS) -> str:
    """The first ``n`` words of ``text`` that are not stop words, in their original spelling."""
    stop = {"a", "an", "the", "of", "for", "to", "and", "in", "on", "by", "with", "or"}
    words = [
        w for w in text.replace(",", " ").replace("/", " ").split() if w.casefold() not in stop
    ]
    return " ".join(words[:n])


def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    for c in cands:
        for o in out:
            if same_thing(c.name, o.name):
                o.links = _merge_links(o.links, c.links)
                o.terms = tuple(dict.fromkeys(o.terms + c.terms))
                o.bonus = max(o.bonus, c.bonus)
                break
        else:
            out.append(c)
    return out


def _merge_links(a: Links, b: Links) -> Links:
    data = {f: tuple(dict.fromkeys(getattr(a, f) + getattr(b, f))) for f in Links.model_fields}
    return Links(**data)


def trusted_requests(inp: ResearchInput) -> list[Request]:
    """Requests Research may use as seeds: model-written, not thin, not template text."""
    return [r for r in inp.requests if r.origin != "template" and not r.thin]


def _bonus(cat: Catalog, key: str) -> int:
    return cat.plan.priority_bonus.get(key, 0)


def reference_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    return [
        Candidate(
            r.label,
            flags=("assumption_derived",) if r.tier != "locked" else (),
            bonus=_bonus(cat, "locked") if r.tier == "locked" else 0,
        )
        for r in inp.references
        if not r.own_brand
    ]


def role_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    cands = [
        Candidate(
            r.name,
            links=Links(roles=(r.id,)),
            bonus=_bonus(cat, "locked") if r.tier == "locked" else 0,
            terms=tuple(tokens(r.goal))[:6],
            extra={"goal": r.goal},
        )
        for r in inp.roles
        if r.duplicate_of is None and not r.is_app_name and r.name
    ]
    cands.sort(key=lambda c: (-(c.bonus), 0 if c.extra.get("goal") else 1))
    return _dedupe(cands)


def flow_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    """Journeys: the user's features first (must before nice before later), then Brief flow names (a hint)."""
    feats = [f for f in inp.features if f.duplicate_of is None and f.scope_status != "out"]
    feats.sort(key=lambda f: (PRIORITY_RANK.get(f.priority, 1), f.id))
    cands = [
        Candidate(
            f.name,
            links=Links(features=(f.id,)),
            bonus=_bonus(cat, f.priority) + (_bonus(cat, "locked") if f.tier == "locked" else 0),
            terms=tuple(tokens(f.intent))[:6],
        )
        for f in feats
    ]
    cands += [
        Candidate(fl.name, links=Links(flows=(fl.id,), roles=((fl.role,) if fl.role else ())))
        for fl in inp.flows
        if fl.name
    ]
    return _dedupe(cands)


def entity_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    generic = {" ".join(tokens(g)) for g in cat.plan.generic_entities}
    cands = [
        Candidate(
            e.name,
            links=Links(entities=(e.id,)),
            bonus=_bonus(cat, "generic_entity") if " ".join(tokens(e.name)) in generic else 0,
        )
        for e in inp.entities
        if e.name
    ]
    cands.sort(key=lambda c: (c.bonus < 0, c.name.casefold()))
    return _dedupe(cands)


def module_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    generic = {" ".join(tokens(g)) for g in cat.plan.generic_modules}
    cands = [
        Candidate(
            m.name,
            links=Links(modules=(m.id,)),
            bonus=_bonus(cat, "generic_module") if " ".join(tokens(m.name)) in generic else 0,
        )
        for m in inp.modules
        if m.name
    ]
    cands.sort(key=lambda c: (c.bonus < 0, c.name.casefold()))
    return _dedupe(cands)


def _corpus(inp: ResearchInput) -> list[tuple[str, Links, bool]]:
    """Texts that can reveal an integration need, with the ids they belong to and whether they are request text."""
    items: list[tuple[str, Links, bool]] = []
    for f in inp.features:
        if f.duplicate_of is None:
            items.append((f"{f.name} {f.intent}", Links(features=(f.id,)), False))
    for i in inp.integrations:
        items.append((i.name, Links(integrations=(i.name,)), False))
    for fact in inp.facts:
        if not fact.is_template:
            items.append((fact.statement, Links(), False))
    items += [(g, Links(), False) for g in inp.glossary]
    return items


def need_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    """Integration needs (payments, messaging, maps, ...) revealed by the Brief, most evidence first.

    Evidence is the user's own words (features, named integrations, facts, glossary); a trusted request is linked to a
    need only when the request itself is about services, so one broad request is not attached to every need.
    """
    corpus = [(stem_set(text), links) for text, links, _ in _corpus(inp)]
    service_words = {
        w for word in cat.plan.request_jobs.get("services", ()) for w in stem_set(word)
    }
    requests = [(stem_set(f"{r.topic} {r.question}"), r) for r in trusted_requests(inp)]
    scored: list[tuple[int, int, Candidate]] = []
    for order, (key, need) in enumerate(cat.needs.needs.items()):
        words = {w for word in need.words for w in stem_set(word)}
        hits = 0
        links = Links()
        for stems, ln in corpus:
            if stems & words:
                hits += 1
                links = _merge_links(links, ln)
        rids = tuple(r.id for stems, r in requests if stems & words and stems & service_words)
        if hits or rids:
            links = _merge_links(links, Links(requests=rids))
            bonus = min(5, hits) + (3 if rids else 0)
            scored.append(
                (
                    -(hits + 2 * len(rids)),
                    order,
                    Candidate(
                        need.label,
                        links=links,
                        bonus=bonus,
                        extra={"need_key": key},
                        terms=tuple(need.words[:6]),
                    ),
                )
            )
    scored.sort(key=lambda t: (t[0], t[1]))
    out = [c for _, _, c in scored]
    for i in (
        inp.integrations
    ):  # an integration the person named that matched no need keeps its own topic
        mine = " ".join(tokens(i.name))
        if not any(
            mine in " ".join(tokens(c.name)) or c.extra.get("need_key", "") in mine for c in out
        ):
            out.append(
                Candidate(
                    i.name,
                    links=Links(integrations=(i.name,)),
                    bonus=6,
                    extra={"need_key": "_".join(tokens(i.name))},
                )
            )
    return out


def rule_topic_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    """Topics that carry rules, numbers or legal-adjacent statements: fact topic tags plus trusted request topics."""
    generic = {" ".join(tokens(g)) for g in cat.plan.generic_topics}
    kinds = set(cat.plan.rule_fact_kinds)
    groups: dict[str, list[str]] = {}
    compliance: set[str] = set()
    for f in inp.facts:
        if f.is_template or f.kind not in kinds:
            continue
        tags = [t.strip() for raw in f.topics for t in raw.split(",") if t.strip()]
        tag = next((t for t in tags if " ".join(tokens(t)) not in generic), None)
        if tag is None:
            continue
        groups.setdefault(tag.casefold(), []).append(f.statement)
        if f.kind == "compliance":
            compliance.add(tag.casefold())
    cands = [
        Candidate(
            tag,
            bonus=min(10, 2 * len(stmts)) + (5 if tag in compliance else 0),
            terms=tuple(dict.fromkeys(w for s in stmts[:3] for w in tokens(s) if len(w) > 4))[:8],
        )
        for tag, stmts in groups.items()
    ]
    job_words = {w for word in cat.plan.request_jobs.get("data", ()) for w in stem_set(word)}
    for r in trusted_requests(inp):
        if stem_set(f"{r.topic} {r.question}") & job_words:
            cands.append(
                Candidate(
                    short_phrase(r.entity or r.topic, 5) or r.topic,
                    links=Links(requests=(r.id,)),
                    bonus=_bonus(cat, "request_high" if r.priority == "high" else "request_medium"),
                    anchors=(short_phrase(r.topic, 5),),
                    label_override=r.shown_to_user_as or None,
                )
            )
    cands.sort(key=lambda c: (-c.bonus, c.name.casefold()))
    return _dedupe(cands)


def format_topic_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    """Unusual formats and IDs named in the Brief (a number, a registration, a licence ...): never trivia."""
    words = {w for word in cat.needs.format_words for w in stem_set(word)}
    out: list[Candidate] = []
    for f in inp.facts:
        if f.is_template or f.kind not in {"number", "entity", "rule", "compliance", "context"}:
            continue
        toks = tokens(f.statement)
        for i, tok in enumerate(toks):
            if stem_set(tok) & words and len(tok) > 2:
                window = toks[max(0, i - 1) : i + 2]
                while window and window[0] in _EDGE_STOP:
                    window = window[1:]
                while window and window[-1] in _EDGE_STOP:
                    window = window[:-1]
                phrase = " ".join(window)
                if len(phrase) < 4:
                    continue
                out.append(Candidate(phrase, bonus=5 if f.kind in {"number", "entity"} else 0))
                break
    out.sort(key=lambda c: (-c.bonus, c.name))
    return _dedupe(out)


def platform_candidates(cat: Catalog) -> list[Candidate]:
    return [Candidate(p.name, extra={"domain": p.domain}) for p in cat.plan.platforms]


def script_locale_candidates(inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    out = []
    for code in inp.locales.non_latin:
        info = cat.languages.get(code.split("-")[0])
        if info:
            out.append(
                Candidate(info.name, extra={"script": info.script, "code": code.split("-")[0]})
            )
    return _dedupe(out)


def candidates_for(foreach: str, inp: ResearchInput, cat: Catalog) -> list[Candidate]:
    match foreach:
        case "reference":
            return reference_candidates(inp, cat)
        case "role":
            return role_candidates(inp, cat)
        case "flow":
            return flow_candidates(inp, cat)
        case "entity":
            return entity_candidates(inp, cat)
        case "module":
            return module_candidates(inp, cat)
        case "need":
            return need_candidates(inp, cat)
        case "rule_topic":
            return rule_topic_candidates(inp, cat)
        case "format_topic":
            return format_topic_candidates(inp, cat)
        case "platform":
            return platform_candidates(cat)
        case "script_locale":
            return script_locale_candidates(inp, cat)
    return []
