"""The deterministic planner: ``ResearchInput`` + data files -> a :class:`Plan` of targets.

Research is organised by what Spec must decide. For each of the eight jobs the data files list target
templates; each template is expanded over what the Brief actually contains (roles, journeys, entities, needs,
rule topics, ...), merged with the trusted Compass requests and the model's optional suggestions, passed
through the worthiness gate, ranked and trimmed to the load class's ceiling. The result needs no model call:
a model may reword and add (RSCH-1), but this baseline is what runs when it fails.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from mde_research_agent.catalog.loader import JOB_NAMES
from mde_research_agent.catalog.models import Catalog, JobSpec, TargetTemplate
from mde_research_agent.core.text import same_thing, stem_set, tokens
from mde_research_agent.domain.input import Request, ResearchInput
from mde_research_agent.domain.plan import Dropped, Links, Place, Plan, Target
from mde_research_agent.plan.candidates import (
    Candidate,
    candidates_for,
    short_phrase,
    trusted_requests,
)
from mde_research_agent.plan.place import build_place, scoped_place
from mde_research_agent.plan.proposal import PlanProposal, ProposedTarget
from mde_research_agent.plan.routing import (
    category_stems,
    job_of_request,
    names_same_thing,
    overlap,
    read_request,
    specific_entity,
)
from mde_research_agent.plan.signals import compute_signals, light_mode, trust_decision

RECENCY_MONTHS = 24
_FORMAT_FIELD = re.compile(r"\{(\w+)\}")


def category_phrase(inp: ResearchInput, proposal: PlanProposal | None = None) -> str:
    """A short noun phrase for the kind of app, in the user's own words."""
    raw = inp.idea.category.strip()
    if (
        "_" in raw
    ):  # a family code ("delivery_logistics"), not the user's words: prefer a clean synonym
        synonym = next((s for s in inp.hints.synonyms if s.isascii() and len(s.split()) <= 4), "")
        raw = synonym or raw.replace("_", " ")
    if (not raw or "_" in inp.idea.category) and proposal and proposal.category_phrase:
        raw = proposal.category_phrase
    if not raw and proposal and proposal.category_phrase:
        raw = proposal.category_phrase
    if not raw:
        names = [f.name for f in inp.features if f.duplicate_of is None][:2]
        raw = " and ".join(names) if names else short_phrase(inp.idea.summary, 5)
    return short_phrase(raw, 6) or raw


def category_noun(category: str) -> str:
    """The category without a trailing "app" or "apps" ("dental clinic app" -> "dental clinic"), for labels."""
    words = category.split()
    while len(words) > 1 and words[-1].casefold() in ("app", "apps", "application", "applications"):
        words.pop()
    return " ".join(words)


SIGNAL_CONDITIONS = frozenset(
    {
        "payments",
        "messaging",
        "maps_location",
        "tracking",
        "identity_kyc",
        "government_documents",
        "accounting",
        "scheduling",
        "two_sided",
        "media_upload",
        "health_data",
        "minors",
        "multi_region",
        "many_roles",
        "sensitive_data",
    }
)


def _conditions_hold(
    tpl: TargetTemplate, inp: ResearchInput, active: frozenset[str] = frozenset()
) -> bool:
    for cond in tpl.when:
        if cond in active:
            continue
        if cond == "non_latin_locale" and not inp.locales.non_latin:
            return False
        if cond == "has_integrations" and not inp.integrations:
            return False
        if cond == "multi_app" and sum(1 for a in inp.apps if a.is_real) < 2:
            return False
        if cond == "has_sensitive_data" and not inp.sensitive_flags:
            return False
        if cond in SIGNAL_CONDITIONS or cond == "trust":
            return False  # a signal (or the trust decision) that is not active
    return True


def _fmt(text: str, values: dict[str, str]) -> str:
    return " ".join(_FORMAT_FIELD.sub(lambda m: values.get(m.group(1), ""), text).split())


def _make_target(
    job: JobSpec,
    tpl: TargetTemplate,
    cand: Candidate,
    *,
    place: Place,
    category: str,
    cat: Catalog,
    inp: ResearchInput,
) -> Target:
    name = cand.name
    noun = category_noun(category)
    target_place = scoped_place(place, tpl.place_scope)
    values = {
        "category": category,
        "category_noun": noun,
        "category_apps": f"{noun} apps",
        "place": target_place.text if target_place else "",
        "entity": name,
        "need": name,
        "module": name,
        "role": name,
        "flow": name,
        "topic": name,
        "platform": name,
        "language": name,
    }
    primary = tpl.anchor
    if primary == "category":
        anchors = (category,)
    else:
        anchors = tuple(
            dict.fromkeys(
                [short_phrase(name, 4) or name, *cand.anchors, cand.extra.get("need_key", "")]
            )
        )
        anchors = tuple(a for a in anchors if a)
    slot_terms: list[str] = []
    for slot in tpl.slots:
        slot_terms += list(cat.lanes.kinds[slot.kind].terms)
    terms = tuple(
        dict.fromkeys([*tokens(" ".join(anchors)), *job.intent, *slot_terms, *cand.terms])
    )[: cat.plan.base_terms_max]
    bonus = cand.bonus
    flags = list(cand.flags)
    if place.uncertain and tpl.place_scope != "none":
        flags.append("geo_uncertain")
    domain = cand.extra.get("domain")
    is_entity = tpl.foreach == "reference"
    return Target(
        id="",
        job=job.job,
        template_id=tpl.id,
        spec_need=job.spec_need,
        lane=job.lane,
        also_lanes=job.also_lanes,
        label=_fmt(cand.label_override or tpl.label, values),
        question=_fmt(tpl.question, values),
        decision_unlocked=tpl.decision_unlocked,
        anchors=anchors,
        entity=name if is_entity else None,
        place=target_place,
        intent=job.intent,
        needs=tpl.needs,
        priority=max(0, min(100, tpl.priority + bonus)),
        slots=tuple(
            s.model_copy(update={"min": s.deep_min})
            if s.deep_min and inp.load and inp.load.load_class == "deep"
            else s
            for s in tpl.slots
        ),
        links=cand.links,
        flags=tuple(dict.fromkeys(flags)),
        recency_months=RECENCY_MONTHS if job.recency else None,
        origin="request" if cand.links.requests and not cand.links.features else "template",
        terms=terms,
        domain_restrict=(domain,) if domain else (),
    )


def _expand(
    job: JobSpec,
    tpl: TargetTemplate,
    inp: ResearchInput,
    cat: Catalog,
    place: Place,
    category: str,
    active: frozenset[str] = frozenset(),
) -> list[Target]:
    if not _conditions_hold(tpl, inp, active):
        return []
    if tpl.foreach == "none":
        cands = [Candidate(category)]
    else:
        cands = candidates_for(tpl.foreach, inp, cat)[: tpl.max]
    return [
        _make_target(job, tpl, c, place=place, category=category, cat=cat, inp=inp) for c in cands
    ]


MIN_FALLBACK_OVERLAP = (
    2  # stems a request's topic must share with a topic's label when no job word matched
)


def _target_for_request(
    req: Request, targets: list[Target], inp: ResearchInput, cat: Catalog
) -> int | None:
    """The index of the ONE topic that carries a request, or None when no topic does.

    1. a named reference ("how does <app> handle ...") goes to that app's own topic;
    2. otherwise the request's job is decided by ``routing`` (data words in ``plan.request_jobs``), and inside the job
       the topic whose anchors mean the same thing, then the one whose label shares most words with the request's topic
       line, then the highest-ranked topic of the job (so several requests share the job's main topic);
    3. a request with no job word is given to the topic with the most shared words (at least two), else it is unmapped.
    """
    ignore = category_stems(inp)
    reading = read_request(req, ignore)
    entity = specific_entity(req, ignore)
    topic_stems = stem_set(f"{req.topic} {entity}")
    for i, t in enumerate(targets):
        if not t.entity:
            continue
        named = stem_set(t.entity)
        if (entity and names_same_thing(entity, t.entity)) or (named and named <= topic_stems):
            return i
    job = job_of_request(reading, cat)
    pool = [i for i, t in enumerate(targets) if t.job == job and not t.entity] if job else []
    if not pool:
        scored = [
            (overlap(reading, " ".join([t.label, *t.anchors])), -i, i)
            for i, t in enumerate(targets)
            if not t.entity
        ]
        best = max(scored, default=None)
        return best[2] if best and best[0] >= MIN_FALLBACK_OVERLAP else None
    subject = short_phrase(entity or req.topic, 5)
    same = next(
        (i for i in pool if any(names_same_thing(subject, a) for a in targets[i].anchors)), None
    )
    if same is not None:
        return same

    def words_of(t: Target) -> str:
        """What a topic is about: its label, anchors and the words of its own subject (not its job's shared words)."""
        shared = {w for x in cat.jobs[t.job].intent for w in tokens(x)}
        return " ".join([t.label, *t.anchors, *(w for w in t.terms if w not in shared)])

    best_i = max(
        pool, key=lambda i: (overlap(reading, words_of(targets[i])), targets[i].priority, -i)
    )
    if overlap(reading, words_of(targets[best_i])) > 0:
        return best_i
    # nothing shares a word: only a topic about the app as a whole may carry the request
    whole = [i for i in pool if f"{job}:{targets[i].template_id}" in cat.plan.request_catch_all]
    return max(whole, key=lambda i: (targets[i].priority, -i), default=None)


def _attach_requests(targets: list[Target], inp: ResearchInput, cat: Catalog) -> list[Target]:
    """Link each trusted request to the ONE topic it informs; add its words and rank (a request is never spread)."""
    out = list(targets)
    for req in trusted_requests(inp):
        if any(req.id in t.links.requests for t in out):
            continue  # a topic built for this request (a rule or provider topic) already carries it
        pick = _target_for_request(req, out, inp, cat)
        if pick is None:
            continue
        t = out[pick]
        bonus = cat.plan.priority_bonus.get(
            "request_high" if req.priority == "high" else "request_medium", 0
        )
        links = Links(**{**t.links.model_dump(), "requests": (*t.links.requests, req.id)})
        extra_terms = tuple(w for w in tokens(" ".join(req.terms[:4])) if len(w) > 2)
        out[pick] = t.model_copy(
            update={
                "links": links,
                "priority": min(100, t.priority + bonus),
                "terms": tuple(dict.fromkeys([*t.terms, *extra_terms]))[: cat.plan.base_terms_max],
            }
        )
    return out


def _from_proposals(
    proposals: Iterable[ProposedTarget], cat: Catalog, place: Place, category: str
) -> tuple[list[Target], list[Dropped]]:
    targets: list[Target] = []
    dropped: list[Dropped] = []
    for p in list(proposals)[: cat.plan.max_extra_targets]:
        job = next((j for j in JOB_NAMES if j == p.job), None)
        if job is None or not p.anchor.strip() or not p.question.strip():
            continue
        if p.trivia or not p.needs:
            dropped.append(Dropped(label=p.anchor, reason="trivia", job=p.job))
            continue
        spec = cat.jobs[job]
        tpl = spec.targets[0]
        slots = tuple(s for t in spec.targets for s in t.slots if s.required)[:2] or tpl.slots
        targets.append(
            Target(
                id="",
                job=job,
                template_id=f"model:{job}",
                spec_need=spec.spec_need,
                lane=spec.lane,
                also_lanes=spec.also_lanes,
                label=short_phrase(p.anchor, 6),
                question=p.question.strip(),
                decision_unlocked=p.decision_unlocked.strip() or spec.spec_need,
                anchors=(short_phrase(p.anchor, 4) or p.anchor,),
                place=scoped_place(place, tpl.place_scope),
                intent=spec.intent,
                needs=tuple(n for n in p.needs if n in ("current", "norm", "entity", "number")),
                priority=max(0, min(90, p.priority)),
                slots=slots,
                recency_months=RECENCY_MONTHS if spec.recency else None,
                origin="model",
                terms=tuple(dict.fromkeys([*tokens(p.anchor), *spec.intent, *p.terms]))[
                    : cat.plan.base_terms_max
                ],
                flags=("assumption_derived",),
            )
        )
    del category
    return targets, dropped


def _gate(
    targets: list[Target], inp: ResearchInput, cat: Catalog
) -> tuple[list[Target], list[Dropped]]:
    """Worthiness gate: drop what the person declined, the own brand alone, and anything without an anchor."""
    generic = {s for w in cat.intent_words.generic_words for s in stem_set(w)}
    declined = [(d, stem_set(d) - generic) for d in inp.declined]
    own = [stem_set(o) for o in inp.own_brand_terms if stem_set(o)]
    keep: list[Target] = []
    dropped: list[Dropped] = []
    for t in targets:
        subject = stem_set(" ".join((*t.anchors, t.label)))
        hit = next((d for d, s in declined if s and s <= subject), None)
        if hit is not None:
            dropped.append(Dropped(label=hit, reason="declined", job=t.job, links=t.links))
            continue
        if (
            t.template_id.startswith("references")
            and own
            and any(s <= stem_set(t.entity or "") for s in own)
        ):
            dropped.append(Dropped(label=t.label, reason="own_brand", job=t.job, links=t.links))
            continue
        if not t.anchors or not t.decision_unlocked or not t.intent:
            dropped.append(
                Dropped(label=t.label, reason="not_worthwhile", job=t.job, links=t.links)
            )
            continue
        keep.append(t)
    return keep, dropped


def _merge_same(targets: list[Target]) -> list[Target]:
    """Merge targets of one job whose anchors mean the same thing (merge by entity), keeping the best-ranked."""
    out: list[Target] = []
    for t in sorted(
        targets, key=lambda x: (-x.priority, -len(x.links.requests), x.job, x.template_id, x.label)
    ):
        twin = next(
            (
                i
                for i, o in enumerate(out)
                if o.job == t.job
                and o.template_id == t.template_id
                and any(same_thing(a, b) for a in o.anchors for b in t.anchors)
            ),
            None,
        )
        if twin is None:
            out.append(t)
            continue
        o = out[twin]
        links = Links(
            **{
                f: tuple(dict.fromkeys(getattr(o.links, f) + getattr(t.links, f)))
                for f in Links.model_fields
            }
        )
        out[twin] = o.model_copy(
            update={"links": links, "terms": tuple(dict.fromkeys([*o.terms, *t.terms]))}
        )
    return out


def _trim(targets: list[Target], cap: int) -> tuple[list[Target], list[Target]]:
    """Keep the ``cap`` best, but never leave a job with targets without at least one of them."""
    ordered = sorted(
        targets, key=lambda x: (-x.priority, -len(x.links.requests), x.job, x.template_id, x.label)
    )
    if len(ordered) <= cap:
        return ordered, []
    kept = ordered[:cap]
    by_job: dict[str, list[Target]] = defaultdict(list)
    for t in kept:
        by_job[t.job].append(t)
    for job in JOB_NAMES:
        if by_job.get(job) or not any(t.job == job for t in ordered):
            continue
        best = next(t for t in ordered if t.job == job)
        donor = min(
            (t for t in kept if len(by_job[t.job]) > 1),
            key=lambda x: (x.priority, x.label),
            default=None,
        )
        if donor is None:
            break
        kept.remove(donor)
        by_job[donor.job].remove(donor)
        kept.append(best)
        by_job[job].append(best)
    kept.sort(key=lambda x: (-x.priority, -len(x.links.requests), x.job, x.template_id, x.label))
    rest = [t for t in ordered if t not in kept]
    return kept, rest


def build_plan(inp: ResearchInput, cat: Catalog, *, proposal: PlanProposal | None = None) -> Plan:
    """Targets for this Brief, numbered ``T-01``... in rank order; deterministic for the same inputs."""
    place = build_place(inp, cat, proposal)
    category = category_phrase(inp, proposal)
    targets: list[Target] = []
    signals = compute_signals(inp, cat.signals)
    trust_on, _why = trust_decision(inp, signals, cat)
    light = light_mode(inp, signals, cat)
    active = frozenset({*signals, *(("trust",) if trust_on else ())})
    for name in JOB_NAMES:
        job = cat.jobs[name]
        for tpl in job.targets:
            targets += _expand(job, tpl, inp, cat, place, category, active)
    light_dropped: list[Dropped] = []
    if light:  # a tiny app: only the feature norms and the screen inventory (doc 13 4.2)
        keep = set(cat.signals.light.templates)
        light_dropped = [
            Dropped(label=t.label, reason="trivia", job=t.job, links=t.links)
            for t in targets
            if f"{t.job}:{t.template_id}" not in keep
        ]
        targets = [t for t in targets if f"{t.job}:{t.template_id}" in keep]
    targets = _attach_requests(targets, inp, cat)
    extra, dropped = _from_proposals(
        proposal.extra_targets if proposal else (), cat, place, category
    )
    targets += extra
    targets, gate_dropped = _gate(targets, inp, cat)
    dropped += gate_dropped + light_dropped
    targets = _merge_same(targets)
    load_class = inp.load.load_class if inp.load else "standard"
    cap = cat.ceilings.for_class(load_class).soft.targets
    kept, rest = _trim(targets, cap)
    numbered = tuple(t.model_copy(update={"id": f"T-{i:02d}"}) for i, t in enumerate(kept, start=1))
    unrun = tuple(t.model_copy(update={"id": f"U-{i:02d}"}) for i, t in enumerate(rest, start=1))
    plan_source = "rebuilt" if not trusted_requests(inp) else inp.quality.plan_source
    notes: list[str] = []
    if rest:
        notes.append(f"{len(rest)} lower-ranked topics were not run (over the ceiling)")
    if place.inferred:
        notes.append("country inferred by the planner; confirm")
    return Plan(
        targets=numbered,
        dropped=tuple(dropped),
        unrun=unrun,
        place=place,
        category_phrase=category,
        discovery_names=tuple(
            (proposal.discovery_names if proposal else ())[: cat.plan.max_discovery_names]
        ),
        plan_source=plan_source,
        notes=tuple(notes),
        light=light,
        signals=signals,
    )
