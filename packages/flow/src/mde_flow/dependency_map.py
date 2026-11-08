"""Change handling (spec 3.6) as data: which sections a change rebuilds, which research jobs
re-run, and whether a post-handoff change is *light* or *heavy*.

The matrix lives in :data:`DEPENDENCY_RULES` (first match wins). Nothing here knows an app type,
a company or a domain pack: the heavy-change vocabulary and the screen limit come from
``Policy.gates.post_handoff`` (``heavy_if``, ``max_new_screens_light``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast, get_args

from mde_contracts.calls import ChangeOp
from mde_contracts.common import Frozen
from mde_contracts.engines import SpecSection
from mde_contracts.gates import PlanPhase
from mde_contracts.policy import HeavyChange, PostHandoffPolicy
from mde_contracts.research import ResearchJobId, ResearchNote, ResearchSuggestion

SECTION_ORDER: Final[tuple[SpecSection, ...]] = get_args(SpecSection)
_SECTION_BY_NAME: Final[dict[str, SpecSection]] = {s: s for s in SECTION_ORDER}
_JOB_ORDER: Final[tuple[ResearchJobId, ...]] = get_args(ResearchJobId)


@dataclass(frozen=True, slots=True)
class DependencyRule:
    """One row of the change matrix (spec 3.6)."""

    pattern: str  # an exact slot id, or a prefix ending in ".*"
    sections: tuple[SpecSection, ...]
    research_jobs: tuple[ResearchJobId, ...] = ()
    categories: tuple[HeavyChange, ...] = ()  # what ``heavy_if`` can match
    restart_discovery: bool = False  # the run goes back to Discover first
    note: str = ""

    def matches(self, slot_id: str) -> bool:
        if self.pattern.endswith(".*"):
            return slot_id.startswith(self.pattern[:-1])
        return slot_id == self.pattern


_FEATURE_SECTIONS: Final[tuple[SpecSection, ...]] = ("screens", "navigation", "data", "api")
_PAY_SECTIONS: Final[tuple[SpecSection, ...]] = ("integrations", "screens", "api")

#: Spec 3.6 table, one entry per row, plus the slots the table does not name (explicit, not
#: implicit: every slot id in ``mde_contracts.slots`` resolves to a rule).
DEPENDENCY_RULES: Final[tuple[DependencyRule, ...]] = (
    DependencyRule(
        "scope.apps", ("all",), ("R1", "R2", "R4"), ("scope",), True, "everything; back to Discover"
    ),
    DependencyRule(
        "idea.archetype",
        ("all",),
        ("R1", "R2", "R4"),
        ("scope",),
        True,
        "everything; back to Discover",
    ),
    DependencyRule(
        "regions", ("all",), ("R1", "R2", "R4"), ("scope",), True, "everything; back to Discover"
    ),
    DependencyRule("scope.features", _FEATURE_SECTIONS, (), ("scope",), note="Note is reused"),
    DependencyRule("idea.summary", ("all",), (), ("scope",), note="Note is reused"),
    DependencyRule("design.*", ("design",), (), ("design",), note="design tokens only"),
    DependencyRule(
        "brand.*", ("design",), (), ("design",), note="design tokens; R3 if the brand site changed"
    ),
    DependencyRule("auth.method", ("roles", "navigation", "screens"), (), ("auth",)),
    DependencyRule("auth.sso", ("roles", "navigation", "screens"), (), ("auth",)),
    DependencyRule("users.roles", ("roles", "navigation", "screens"), (), ("auth", "roles")),
    DependencyRule("pay.*", _PAY_SECTIONS, ("R5",), ("pay",), note="R5 for the changed provider"),
    DependencyRule(
        "integrations.list",
        _PAY_SECTIONS,
        ("R5",),
        ("integrations",),
        note="R5 for the changed provider",
    ),
    DependencyRule("data.source", ("data", "api", "integrations"), (), ("data", "integrations")),
    DependencyRule("data.classification", ("data", "api"), (), ("data",)),
    DependencyRule("compliance.flags", ("all",), ("R4",), ("scope",)),
    DependencyRule("behavior.*", ("behavior", "screens")),
    DependencyRule("locale", ("behavior", "screens")),
    DependencyRule("a11y", ("behavior", "screens")),
    DependencyRule("release.channel", ("behavior",)),
    DependencyRule("approvers", (), note="approver roles live in policy, not in the spec"),
)

_FEATURES_RULE: Final[DependencyRule] = next(
    r for r in DEPENDENCY_RULES if r.pattern == "scope.features"
)
_FALLBACK = DependencyRule("*", ("all",), (), ("scope",), note="unknown slot: rebuild everything")


def rule_for_slot(slot_id: str) -> DependencyRule | None:
    for rule in DEPENDENCY_RULES:
        if rule.matches(slot_id):
            return rule
    return None


#: Heavy category of the screen-level ops (add / remove / patch a screen, set its copy). It sits next
#: beside ``HeavyChange`` because the contract's literal does not list it yet (requested contract
#: change); ``heavy_if`` can name it as soon as it does. Until then the screen limit
#: (``max_new_screens_light``, which also counts removed, patched and re-worded screens) and the
#: ``scope`` category of a removal are what keep these ops from being a loophole.
SCREENS_CATEGORY: Final = "screens"
ChangeCategory = HeavyChange | Literal["screens"]
_CATEGORY_ORDER: Final[tuple[str, ...]] = (*get_args(HeavyChange), SCREENS_CATEGORY)


class ChangePlan(Frozen):
    """What a batch of change ops means for the run that applies them."""

    sections: list[SpecSection]
    rerun_jobs: list[ResearchJobId]
    categories: list[ChangeCategory]
    restart_discovery: bool = False  # back to Discover first (app type, scope.apps, region)
    unknown: bool = False  # at least one op could not be classified: treat as heavy
    new_screens_requested: int = 0
    screens_touched: int = 0  # add / remove / patch / re-word ops: every screen-level change
    touched_slots: list[str] = []
    start_phase: PlanPhase = "build"

    @property
    def needs_rebuild(self) -> bool:
        return bool(self.sections) or bool(self.rerun_jobs) or self.restart_discovery


def _sorted_sections(found: set[SpecSection]) -> list[SpecSection]:
    if "all" in found:
        return ["all"]
    return [s for s in SECTION_ORDER if s in found]


def _looks_like_url(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower().startswith(("http://", "https://", "www."))


def _suggestion(note: ResearchNote | None, ref: str | None) -> ResearchSuggestion | None:
    if note is None or ref is None:
        return None
    return next((s for s in note.suggestions if s.id == ref), None)


def _research_item_effect(
    suggestion: ResearchSuggestion | None,
) -> tuple[tuple[SpecSection, ...], tuple[HeavyChange, ...], bool]:
    """(sections, categories, unknown) for toggling one research addition."""
    if suggestion is None:
        return ("all",), ("scope",), True
    applies = suggestion.applies_to
    if applies is not None:
        named = _SECTION_BY_NAME.get(applies)
        if named is not None:
            return (named,), (), False
        rule = rule_for_slot(applies)
        if rule is not None:
            return rule.sections, rule.categories, False
    by_kind: dict[str, tuple[tuple[SpecSection, ...], tuple[HeavyChange, ...]]] = {
        "feature": (_FEATURE_SECTIONS, ("scope",)),
        "pattern": (("screens",), ()),
        "compliance": (("behavior", "screens"), ()),
        "integration": (_PAY_SECTIONS, ("integrations",)),
        "brand": (("design",), ("design",)),
    }
    sections, cats = by_kind.get(suggestion.kind, (("all",), ("scope",)))
    return sections, cats, False


def plan_change(
    ops: Sequence[ChangeOp], *, note: ResearchNote | None = None, research_stale: bool = False
) -> ChangePlan:
    """Resolve change operations to the sections to rebuild and the research jobs to re-run."""
    sections: set[SpecSection] = set()
    jobs: set[ResearchJobId] = set()
    cats: set[str] = set()
    restart = False
    unknown = False
    new_screens = 0
    screens_touched = 0
    slots: list[str] = []

    def add_rule(rule: DependencyRule, op: ChangeOp) -> None:
        nonlocal restart
        sections.update(rule.sections)
        cats.update(rule.categories)
        restart = restart or rule.restart_discovery
        for job in rule.research_jobs:
            jobs.add(job)
        brand_link = _looks_like_url(op.value_text) or any(
            _looks_like_url(v) for v in op.value_list or []
        )
        if rule.pattern == "brand.*" and op.slot_id == "brand.assets" and brand_link:
            jobs.add("R3")  # the brand site changed

    for op in ops:
        slot = op.slot_id
        if op.kind in {"set_slot", "clear_slot", "other"} and slot is not None:
            slots.append(slot)
            rule = rule_for_slot(slot)
            if rule is None:
                unknown = True
                add_rule(_FALLBACK, op)
            else:
                add_rule(rule, op)
        elif op.kind == "toggle_feature":
            add_rule(_FEATURES_RULE, op)
        elif op.kind == "add_screen":
            new_screens += 1
            screens_touched += 1
            cats.add(SCREENS_CATEGORY)
            sections.update(("screens", "navigation"))
        elif op.kind == "remove_screen":
            screens_touched += 1
            cats.update((SCREENS_CATEGORY, "scope"))  # taking a screen away narrows the scope
            sections.update(("screens", "navigation"))
        elif op.kind in {"patch_screen", "set_copy"}:
            screens_touched += 1
            cats.add(SCREENS_CATEGORY)
            sections.add("screens")
        elif op.kind == "toggle_research_item":
            secs, op_cats, unk = _research_item_effect(_suggestion(note, op.target_id))
            sections.update(secs)
            cats.update(op_cats)
            unknown = unknown or unk
        else:  # "other" without a slot, or set/clear without a slot id
            unknown = True
            add_rule(_FALLBACK, op)

    rerun: list[ResearchJobId] = [j for j in _JOB_ORDER if j in jobs]
    start_phase: PlanPhase = "research" if (rerun or note is None or research_stale) else "build"
    return ChangePlan(
        sections=_sorted_sections(sections),
        rerun_jobs=rerun,
        categories=[cast("ChangeCategory", c) for c in _CATEGORY_ORDER if c in cats],
        restart_discovery=restart,
        unknown=unknown,
        new_screens_requested=new_screens,
        screens_touched=screens_touched,
        touched_slots=slots,
        start_phase=start_phase,
    )


Classification = Literal["light", "heavy"]


class PostHandoffVerdict(Frozen):
    kind: Classification
    reasons: list[str] = []


def classify_post_handoff(
    plan: ChangePlan,
    *,
    policy: PostHandoffPolicy,
    plan_roles: Sequence[str],
    new_screens: int,
    repaired: bool = False,
    research_ran: bool = False,
    derived: bool = False,
) -> PostHandoffVerdict:
    """Light or heavy (spec 3.6).

    A light change hands off on the requester's own request, which is only honest when the
    requester **is** Gate 2: ``plan_roles == ["requester"]``. When anyone else must approve the
    plan (one other role, or several), every post-handoff change goes back to Gate 2, whatever it is.

    Otherwise heavy when any of: the run must go back to Discover; an op could not be classified;
    a changed category is listed in ``policy.heavy_if`` (``screens`` stands for any screen-level
    op); more screens were added, removed, patched or re-worded than ``max_new_screens_light``
    (so ``0`` makes every screen change heavy); research had to re-run or the builder had to repair
    something (rule 4: only what the user asked for is its own approval, anything the engine added
    or repaired needs the tap); the change was *derived* (from an upload or an engine inference)
    instead of asked for.
    """
    reasons: list[str] = []
    if list(plan_roles) != ["requester"]:
        reasons.append("approvers_required")
    if plan.restart_discovery:
        reasons.append("discovery_restart")
    if plan.unknown:
        reasons.append("unclassified_change")
    reasons.extend(f"category:{c}" for c in dict.fromkeys(plan.categories) if c in policy.heavy_if)
    screens = max(new_screens, plan.screens_touched)
    if screens > policy.max_new_screens_light:
        reasons.append(f"screens:{screens}>{policy.max_new_screens_light}")
    if derived:
        reasons.append("derived_change")
    if plan.rerun_jobs or research_ran:
        reasons.append("research_rerun")
    if repaired:
        reasons.append("engine_repair")
    return PostHandoffVerdict(kind="heavy" if reasons else "light", reasons=reasons)
