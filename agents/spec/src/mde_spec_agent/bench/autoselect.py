"""Auto-select (doc 19 4.3): which Compass export and which Research run to try, and why. A PURE function.

``auto_select`` reads nothing and writes nothing: its inputs are the two lists the discovery already built. The UI, the
CLI and the API all call it. An override is simply another id the caller passes to the run.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.bench.inputs import CompassEntry, ResearchEntry, parse_time
from mde_spec_core.synth import PRESETS

Size = Literal["compact", "standard", "deep", "program"]
SIZES: tuple[Size, ...] = ("compact", "standard", "deep", "program")

NO_RESEARCH = "no Research run: Spec runs with a thin research lane and says so"
SYNTH_NO_RESEARCH = (
    "a synthetic input has no Research run: Spec runs with a thin research lane and says so"
)


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    size: Size
    label: str  # short, plain: what this pair is
    compass_id: str  # an export id (file name without .json) or ``synth:<preset>``
    compass_path: str = ""
    synthetic: bool = False
    profile: str  # the detail profile the run will derive
    research_id: str | None = None
    research_path: str = ""  # the research.json to hand to Spec
    reason: str  # plain words: why this pair
    gaps: tuple[str, ...] = ()  # what is missing or weak, in plain words


# --- matching --------------------------------------------------------------------------------------------------------


def matches(entry: CompassEntry, run: ResearchEntry) -> bool:
    """A run belongs to an export when its recorded input hash equals the export's canonical hash; a run that recorded
    no hash matches by the Brief id and the Brief's content hash (both must be present and equal)."""
    if run.input_hash:
        return run.input_hash == entry.export_hash
    return bool(
        entry.brief_id
        and entry.brief_hash
        and run.brief_id == entry.brief_id
        and run.brief_hash == entry.brief_hash
    )


def _rank(run: ResearchEntry) -> int:
    if run.usable and run.status == "complete":
        return 0
    if run.usable:
        return 1  # partial or skipped, with a result
    return 2  # failed, running, or nothing to read


def ranked_research(entry: CompassEntry, research: list[ResearchEntry]) -> list[ResearchEntry]:
    """Every run that matches the export, best first: complete before partial before unusable, newest first inside each."""
    found = [r for r in research if matches(entry, r)]
    return sorted(found, key=lambda r: (_rank(r), _neg_time(r), r.id))


def _neg_time(run: ResearchEntry) -> float:
    return -parse_time(run.created_at).timestamp()


def best_research(
    entry: CompassEntry, research: list[ResearchEntry]
) -> tuple[ResearchEntry | None, tuple[str, ...]]:
    """The run to use for an export (None when there is no usable one) and the plain-words gaps."""
    ranked = ranked_research(entry, research)
    if not ranked:
        return None, (NO_RESEARCH,)
    best = ranked[0]
    if not best.usable:
        statuses = ", ".join(sorted({r.status for r in ranked}))
        return None, (
            f"Research ran on this export but left nothing usable ({statuses}): "
            "Spec runs with a thin research lane and says so",
        )
    if best.status != "complete":
        return best, (
            f"the best Research run is {best.status}: some topics were not finished, "
            "so the research lane is thinner than a complete run's",
        )
    return best, ()


# --- proposing -------------------------------------------------------------------------------------------------------


def _class_key(e: CompassEntry) -> tuple[bool, int, int, str]:
    # real interviews first, then no drift, then the smallest, then the name (a total order: no ties)
    return (e.fixture, len(e.drift_flags), e.weight, e.id)


def _day(run: ResearchEntry) -> str:
    return run.created_at[:10] if run.created_at else "date unknown"


def _real_proposal(size: Size, e: CompassEntry, research: list[ResearchEntry]) -> Proposal:
    run, gaps = best_research(e, research)
    kind = "a hand-made fixture" if e.fixture else "a real export"
    drift = "no drift" if not e.drift_flags else f"drift: {', '.join(e.drift_flags)}"
    reason = (
        f"The smallest of the {size} class ({kind}, {drift}): tier {e.tier}, {e.features} features, "
        f"{e.screens} screens in Compass's structure."
    )
    if run is not None:
        how = "by the recorded input hash" if run.input_hash else "by the Brief id and hash"
        reason += f" Research: {run.status} run {run.id} from {_day(run)}, matched {how}."
    else:
        reason += " Research: none to use."
    all_gaps = list(gaps)
    if e.drift_flags:
        all_gaps.append(
            f"the export drifted from what the reader expects: {', '.join(e.drift_flags)}"
        )
    return Proposal(
        size=size,
        label=f"{e.id}",
        compass_id=e.id,
        compass_path=e.path,
        profile=e.profile,
        research_id=run.id if run else None,
        research_path=run.research_path if run else "",
        reason=reason,
        gaps=tuple(all_gaps),
    )


def _synthetic_proposals() -> list[Proposal]:
    prog, big = PRESETS["program"], PRESETS["big"]
    return [
        Proposal(
            size="deep",
            label="synthetic 60-screen app",
            compass_id="synth:big",
            synthetic=True,
            profile=big.profile,
            reason=(
                f"A made-up single app with {big.screens_per_app} screens, {big.entities} entities and "
                f"{big.integrations} integrations: the size where the whole pack stops fitting a small reader."
            ),
            gaps=(SYNTH_NO_RESEARCH,),
        ),
        Proposal(
            size="program",
            label="synthetic Uber Eats-class program",
            compass_id="synth:program",
            synthetic=True,
            profile=prog.profile,
            reason=(
                f"A made-up program of {prog.apps} apps with {prog.screens_per_app} screens each "
                f"({prog.apps * prog.screens_per_app} in all), {prog.entities} entities and "
                f"{prog.integrations} integrations: the largest case Spec is built for."
            ),
            gaps=(SYNTH_NO_RESEARCH,),
        ),
    ]


def auto_select(
    compass: list[CompassEntry],
    research: list[ResearchEntry],
    size: str | None = None,
) -> list[Proposal]:
    """The proposed pairs, in the order compact, standard, deep, program. ``size`` keeps one class.

    Only exports that are ready (Compass has an idea and a feature) are proposed. A class with no ready export gets no
    real proposal. Nothing here touches the disk."""
    if size is not None and size not in SIZES:
        raise ValueError(f"size must be one of {', '.join(SIZES)}")
    out: list[Proposal] = []
    for klass in ("compact", "standard", "deep"):
        pool = sorted(
            (e for e in compass if e.readiness.ready and e.profile == klass), key=_class_key
        )
        if pool:
            out.append(_real_proposal(klass, pool[0], research))  # type: ignore[arg-type]
    out.extend(_synthetic_proposals())  # the 60-screen app is deep, the program is its own class
    return [p for p in out if size is None or p.size == size]


def research_matches(entry: CompassEntry, research: list[ResearchEntry]) -> list[ResearchEntry]:
    """For ``GET /api/inputs``: the runs that belong to an export, best first."""
    return ranked_research(entry, research)


__all__ = [
    "NO_RESEARCH",
    "SIZES",
    "SYNTH_NO_RESEARCH",
    "Proposal",
    "Size",
    "auto_select",
    "best_research",
    "matches",
    "ranked_research",
    "research_matches",
]
