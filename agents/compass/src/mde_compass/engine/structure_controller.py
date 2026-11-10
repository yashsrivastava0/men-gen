"""The structure of the app inside a turn (ADR 0017): the thin layer between ``Service`` and ``engine/structure_draft``.

* ``prepare``: decides the size band of the session (tier, raised by a document's verified claims, or the person's own
  choice) and records it hash-free in ``meta.interview``. Cheap and deterministic; it runs before anything is drafted.
* ``compute``: snapshots what the draft needs from the turn (a deep copy of the Brief, the active facts, the project
  card), runs ``compute_structure`` and returns its result. It never writes to the turn, so it can run in the same gather
  as research seeding and the ambiguity scan (``_seed`` rebuilds ``turn.brief`` meanwhile).
* ``apply``: after the gather, writes a result into the turn: the four sections, the ids the draft allocated, the
  digests, the audit counts, the assumptions for what is still wrong, the notices. A failed draft never breaks the turn.
* ``run``: compute and apply in sequence (the review-time re-run, usually a digest no-op).

A session that was ever approved never gets a first draft: its Brief changes through the change flow, not by drafting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mde_compass.catalog.size_bands import BandId, band_for_tier, get_band
from mde_compass.domain.brief import AssumptionRecord, Brief
from mde_compass.domain.common import Provenance
from mde_compass.engine import assumptions as assumptions_mod
from mde_compass.engine import structure_actions as actions
from mde_compass.engine import structure_checks as checks
from mde_compass.engine import structure_derive as derive
from mde_compass.engine import structure_draft as draft
from mde_compass.engine.brief_builder import target_of
from mde_compass.engine.compile_plan import project_card
from mde_compass.engine.context_pack import full_ledger
from mde_core.errors import NotFound
from mde_core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from mde_compass.engine.service import Service, _Turn

log = get_logger(__name__)

BASIS = "structure"
MAX_ASSUMPTIONS = (
    8  # what is still wrong is listed for the person, most important first, never more than this
)
STRUCTURE_PREFIXES = ("MOD", "SCR", "FLW", "ENT")
BAND_TIER = {"small": "Quick", "medium": "Standard", "large": "Thorough"}


class StructureController:
    def __init__(self, svc: Service) -> None:
        self.svc = svc

    # ------------------------------------------------------------------------------------------- guards
    @property
    def enabled(self) -> bool:
        return bool(self.svc.config.interview.structure_enabled)

    def active(self, turn: _Turn, *, explicit: bool = False) -> bool:
        """Whether a draft may run this turn: the feature is on, the planner engine runs the interview, there is
        something to plan, and the session was never approved (an approved Brief changes through the change flow).
        An ``explicit`` request of the person (redraft a module, plan another size) is that change flow, so it may run
        on a session that was approved."""
        if not self.enabled or not self.svc.interview.active or self.svc.ports is None:
            return False
        if turn.session.approved_version is not None and not explicit:
            return False
        return any(f.scope_status == "in" for f in turn.brief.scope)

    def unreviewed(self, turn: _Turn) -> int:
        """Modules the person has not accepted yet, when the band asks for a hard review (large apps); else 0."""
        if not self.enabled or not turn.brief.modules:
            return 0
        band, _ = self.band(turn)
        return actions.unreviewed_modules(turn.brief) if get_band(band).review == "hard" else 0

    # ---------------------------------------------------------------------------------------------- band
    def _document_claims(self, turn: _Turn) -> int:
        return sum(
            1 for f in turn.memory.active_facts() if f.origin == "document" and f.supports_locked()
        )

    def tier(self, turn: _Turn) -> str:
        return str(self.svc.interview.budget_name(turn))

    def prepare(self, turn: _Turn) -> None:
        """Record the band of this session in ``meta.interview`` (outside the hash). The person's own choice (source
        ``user``) is kept; otherwise it is the tier's, raised by what a document brought."""
        if not self.enabled:
            return
        block = self.svc.interview.block(turn)
        if block.size_source == "user" and block.size_band in ("small", "medium", "large"):
            return
        band, source = derive.effective_band(self.tier(turn), claims=self._document_claims(turn))
        if (block.size_band, block.size_source) != (band, source):
            turn.brief.meta.interview = block.model_copy(
                update={"size_band": band, "size_source": source}
            )

    def band(self, turn: _Turn) -> tuple[BandId, str]:
        """The band of this session, read-only (it never writes to the Brief, so a view or a blocker check can call it):
        the one ``prepare`` recorded, else the one the tier and any document call for."""
        block = turn.brief.meta.interview
        if block is not None and block.size_band in ("small", "medium", "large"):
            return get_band(block.size_band).id, block.size_source or "tier"
        return derive.effective_band(self.tier(turn), claims=self._document_claims(turn))

    def suggest(self, turn: _Turn) -> None:
        """At idea time: when the idea clearly asks for a bigger app than the tier plans for, say so ONCE with a one-tap
        button for the tier that fits. It never switches the tier."""
        if not self.enabled or not self.svc.interview.active:
            return
        block = self.svc.interview.block(turn)
        if block.size_suggested:
            return
        tier_band = band_for_tier(self.tier(turn)).id
        bigger = derive.suggest_band(turn.brief, tier_band)
        if bigger is None:
            return
        target = get_band(bigger)
        turn.brief.meta.interview = block.model_copy(update={"size_suggested": bigger})
        tiers = {
            "quick": "budget_quick",
            "standard": "budget_standard",
            "thorough": "budget_thorough",
        }
        t = self.svc._t
        turn.out.append(
            _notice(
                "size_suggestion",
                t(
                    turn,
                    "notice.size_suggestion",
                    tier=t(turn, f"size.{tier_band}"),
                    suggested=t(turn, f"size.{bigger}"),
                ),
                command=tiers[target.tier],
                label=t(turn, "notice.size_suggestion_action", suggested=t(turn, f"size.{bigger}")),
            )
        )

    # ------------------------------------------------------------------------------------------ compute
    def _input(
        self,
        turn: _Turn,
        *,
        only: tuple[str, ...] | None = None,
        instruction: str = "",
        force: bool = False,
    ) -> draft.StructureInput:
        cfg = self.svc.config
        facts = list(turn.memory.active_facts())
        band, _ = self.band(turn)
        budget_chars = cfg.context.fact_digest * cfg.context.chars_per_token
        digest = full_ledger(facts)
        return draft.StructureInput(
            brief=turn.brief.model_copy(deep=True),
            facts=facts,
            language=turn.lang,
            band=get_band(band),
            project_card=project_card(turn.slots, self.svc.catalog),
            org=self._org(turn.brief),
            fact_digest=digest[:budget_chars],
            now=self.svc.clock.now(),
            instruction=instruction,
            only_modules=only,
            force=force,
        )

    def _org(self, brief: Brief) -> str:
        """The company's own answers (``org.*`` slots: identity, approvers, regions, ...) as lines of text."""
        lines: list[str] = []
        for slot in self.svc.catalog.slots.values():
            if slot.group != "org":
                continue
            target = target_of(slot)
            if target.kind != "dict":
                continue
            item = getattr(brief, target.section).get(target.key)
            if item is not None and item.has_value():
                lines.append(f"{slot.id.split('.', 1)[1]}: {item.value}")
        return "\n".join(lines)[:2000]

    async def compute(
        self,
        turn: _Turn,
        *,
        only: tuple[str, ...] | None = None,
        instruction: str = "",
        force: bool = False,
        explicit: bool = False,
    ) -> draft.StructureResult | None:
        """The draft for this turn, or None when none may run. Never raises: a failed draft leaves the Brief as it was."""
        if not self.active(turn, explicit=explicit):
            return None
        assert self.svc.ports is not None
        try:
            inp = self._input(turn, only=only, instruction=instruction, force=force)
            ctx = self.svc._ctx(turn)
            return await draft.compute_structure(inp, self.svc.ports, ctx)
        except Exception as exc:  # a structure failure must never break the interview
            log.warning("structure_draft_failed", error=type(exc).__name__)
            return None

    # -------------------------------------------------------------------------------------------- apply
    def apply(self, turn: _Turn, result: draft.StructureResult | None) -> None:
        """Write a result into the turn. Sections and ids are written in place (the Brief object may have been replaced
        while the draft ran, so this always works on the current one); the call is a no-op for a result that changed
        nothing."""
        if result is None:
            return
        brief = turn.brief
        work = result.work
        for section in draft.KEYS_OF_STRUCTURE:
            setattr(brief, section, list(getattr(work, section)))
        for prefix in STRUCTURE_PREFIXES:
            top = max(brief.ids.counters.get(prefix, 0), work.ids.counters.get(prefix, 0))
            if top:
                brief.ids.counters[prefix] = top
        for tomb in work.ids.tombstones:
            brief.ids.tombstone(tomb)
        digests = brief.meta.compile_digests
        for key in [k for k in digests if k.startswith("structure:")]:
            if key not in result.digests:
                del digests[key]
        digests.update(result.digests)

        block = self.svc.interview.block(turn)
        counts = derive.counts(brief)
        errors = checks.errors(result.issues)
        brief.meta.interview = block.model_copy(
            update={
                "fallback_count": block.fallback_count + result.fallbacks,
                "structure_modules": counts.modules,
                "structure_screens": counts.screens,
                "structure_flows": counts.flows,
                "structure_entities": counts.entities,
                "structure_issues": len(errors),
            }
        )
        turn.brief = assumptions_mod.merge_derived(brief, self._assumptions(turn, result), (BASIS,))
        self._notices(turn, result, counts)

    def _assumptions(self, turn: _Turn, result: draft.StructureResult) -> list[AssumptionRecord]:
        """What is still wrong after the repair pass, listed for the person (never blocking, never an invented fact):
        errors first, then the realism findings the model could not fix. Matched by claim text, so a re-run that finds
        the same problem keeps the person's decision."""
        rows: list[AssumptionRecord] = []
        order = sorted(
            result.issues, key=lambda i: (i.severity != "error", i.code != "rule_unmet", i.code)
        )
        for issue in order:
            if issue.fix == "none" and issue.severity != "error":
                continue  # size and tidiness warnings are reported in the view, not as assumptions
            if issue.code in ("screen_near_duplicate", "screen_unreachable"):
                continue
            rows.append(
                AssumptionRecord(
                    id="ASM-000",
                    claim=issue.detail or issue.code,
                    reason=self.svc._t(turn, "brief.structure.issue_reason"),
                    basis=BASIS,
                    impact="low",  # shown for the person to look at; it never blocks approval
                    theme="Features",
                    slot_id=None,
                    decision="proposed",
                    what_changes=issue.code,
                    tier="Default",
                    provenance=[Provenance(type="model_knowledge", ref=f"structure:{issue.code}")],
                    confidence=0.5,
                    status="assumed",
                )
            )
            if len(rows) >= MAX_ASSUMPTIONS:
                break
        return rows

    def _notices(self, turn: _Turn, result: draft.StructureResult, counts: derive.Counts) -> None:
        t = self.svc._t
        band, _ = self.band(turn)
        if result.fallbacks:
            turn.notice(
                "structure_floor",
                t(turn, "notice.structure_floor", n=str(result.fallbacks)),
                warn=True,
            )
        verdict = derive.size_verdict(turn.brief, get_band(band))
        if verdict == "over":
            bigger = "large" if band == "medium" else "medium"
            turn.notice(
                "structure_over_band",
                t(
                    turn,
                    "notice.structure_over_band",
                    n=str(counts.screens),
                    band=t(turn, f"size.{band}"),
                    bigger=t(turn, f"size.{bigger}"),
                ),
            )
        if result.drafted and derive.has_structure(turn.brief):
            turn.notice(
                "structure_ready",
                t(
                    turn,
                    "notice.structure_ready",
                    modules=str(counts.modules),
                    screens=str(counts.screens),
                    flows=str(counts.flows),
                    entities=str(counts.entities),
                ),
            )

    # ------------------------------------------------------------------------------------- the person
    async def redraft(self, turn: _Turn, ev: Any) -> None:
        """``regenerate`` (one module, or every module that is not pinned, with the person's own words) and ``set_band``
        (plan the app for another size, from the same answers). Pinned records stay. The result is recorded as ONE change
        set from a diff, so undo puts the old plan back."""
        t = self.svc._t
        if not self.enabled or not self.svc.interview.active:
            turn.notice("structure_unavailable", t(turn, "change.nothing_changed"), warn=True)
            return
        only: tuple[str, ...] | None = None
        force = False
        what = ""
        if ev.action == "set_band":
            if ev.band is None:
                raise NotFound("a band is needed")
            block = self.svc.interview.block(turn)
            turn.brief.meta.interview = block.model_copy(
                update={"size_band": ev.band, "size_source": "user"}
            )
            force = True
            what = t(turn, f"size.{ev.band}")
            turn.notice("structure_band_set", t(turn, "notice.structure_band_set", band=what))
        elif ev.target:
            module = next((m for m in turn.brief.modules if m.id == ev.target), None)
            if module is None:
                raise NotFound(f"modules.{ev.target}: no such record")
            only, what = (module.id,), module.name
        else:
            force, what = True, t(turn, "section.structure")
        before = turn.brief.model_copy(deep=True)
        result = await self.compute(
            turn, only=only, instruction=ev.text, force=force, explicit=True
        )
        if result is None or not result.changed:
            turn.notice("structure_nothing_to_do", t(turn, "change.nothing_changed"))
            return
        self.apply(turn, result)
        ops, inverse = actions.diff_ops(before, turn.brief)
        if not ops:
            return
        from mde_compass.engine.patch import PatchResult

        res = PatchResult(brief=turn.brief, ops=ops, inverse_ops=inverse)
        summary = t(turn, "notice.structure_redrafted", what=what)
        await self.svc._finish_change(
            turn, res, ops, source="structure", reason=ev.text[:200], what=summary
        )

    # ------------------------------------------------------------------------------------------- run
    async def run(self, turn: _Turn, **kwargs: Any) -> None:
        """Compute and apply in sequence: the review-time pass (a digest no-op unless an input changed)."""
        self.prepare(turn)
        self.apply(turn, await self.compute(turn, **kwargs))


def _notice(code: str, text: str, *, command: str = "", label: str = "") -> Any:
    from mde_compass.domain.events import Notice

    return Notice(code=code, text=text, command=command, command_label=label)


__all__ = ["StructureController"]
