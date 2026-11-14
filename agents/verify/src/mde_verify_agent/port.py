"""The Verify implementation of the VerifyPort (doc 19 section 3). Spec loads `build_verify_port` through the entry
point group `mde.spec.verify`; it never imports this package.

Verify only CHECKS. Pure code does what code can (validators, structure checks, the confirm ranking, the canary's
missing-id reading); three single, schema-validated, tool-free model calls add what code cannot judge. It returns
findings and structured patches; Spec applies them (single writer) and re-verifies touched nodes by hash.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph
from mde_spec_core.verify_port import (
    AcceptanceCover,
    BriefView,
    CallOutcome,
    CanaryResult,
    CriticVendor,
    Defect,
    FinalInput,
    ModelCall,
    PortCallSpec,
    RoundLedger,
    StructureInput,
    VerifyCallInput,
    VerifyPort,
    VerifyReport,
    merge_canaries,
    node_hash,
)
from mde_verify_agent import checks, digest
from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut
from mde_verify_agent.calls.canary.spec import SPEC as CANARY_SPEC
from mde_verify_agent.calls.final_critic.schema import FinalIn, FinalOut
from mde_verify_agent.calls.final_critic.spec import SPEC as FINAL_SPEC
from mde_verify_agent.calls.structure_critic.schema import StructureIn, StructureOut
from mde_verify_agent.calls.structure_critic.spec import SPEC as STRUCTURE_SPEC
from mde_verify_agent.evidence import evidence_json, manifest_fields
from mde_verify_agent.repair import plan_patches

PHASE_FREEZE = "freeze"
PHASE_FINAL = "final"
MAX_RESEARCH_LINES = 40


def _brief_block(brief: BriefView, research: Iterable[str]) -> str:
    text = digest.brief_text(brief)
    lines = [f"Research: {ln}" for ln in list(research)[:MAX_RESEARCH_LINES]]
    return "\n".join([text, *lines]).strip() or "(no Brief text was given)"


def _model_defects(findings: Iterable[Any]) -> list[Defect]:
    out: list[Defect] = []
    for f in findings:
        out.append(
            Defect(
                id=f"M-{f.kind}:{f.node}:{checks.slug(f.reason)}",
                cls=f.cls,
                node=f.node,
                field=f.field,
                change=f.change,
                reason=f.reason,
                severity=f.severity,
                source="model",
                code=f"M-{f.kind}",
            )
        )
    return out


def _coverage(wanted: list[str], judged: list[Any]) -> list[AcceptanceCover]:
    """Each listed criterion once, in list order: the reader's judgement where it gave one (its wording mapped back to
    the list by code), else 'not judged' (never silently satisfiable)."""
    by: dict[str, Any] = {}
    for c in judged:
        m = digest.match_criterion(c.criterion, wanted)
        if m is not None:
            by.setdefault(m, c)
    return [
        AcceptanceCover(criterion=w, satisfiable=by[w].satisfiable, how=by[w].how)
        if w in by
        else AcceptanceCover(criterion=w, satisfiable=False, how="not judged by the reader")
        for w in wanted
    ]


class VerifyAgent:
    """Implements `VerifyPort`. One instance per run; it keeps the round ledger (Spec may pass its own in the inputs,
    for example one restored from a checkpoint) and the running cost of its calls."""

    def __init__(
        self, call: ModelCall, cfg: SpecConfig, critic_vendor: CriticVendor = "same"
    ) -> None:
        self._call = call
        self._cfg = cfg
        self._critic_vendor: CriticVendor = critic_vendor
        self.ledger = RoundLedger.from_config(cfg)
        self.cost_micros = 0
        self.flagged_calls: list[str] = []

    # ------------------------------------------------------------------ identity

    @property
    def critic_vendor(self) -> CriticVendor:
        return self._critic_vendor

    def manifest_fields(self) -> dict[str, Any]:
        return manifest_fields(self._critic_vendor)

    def evidence_json(self, report: VerifyReport, canaries: Iterable[CanaryResult] = ()) -> str:
        return evidence_json(report, canaries)

    # ------------------------------------------------------------------ the one way to a model

    async def _ask(
        self, spec: PortCallSpec, inp: VerifyCallInput, *, node_key: str, step: str
    ) -> CallOutcome:
        out = await self._call(spec, inp, node_ids=(f"VERIFY:{node_key}",), step=step)
        self.cost_micros += int(out.cost_micros)
        if out.used_fallback or out.flagged:
            self.flagged_calls.append(spec.id)
        return out

    def _use(self, ledger: RoundLedger | None) -> RoundLedger:
        if ledger is not None:
            self.ledger = ledger
        return self.ledger

    def _report(self, ledger: RoundLedger) -> VerifyReport:
        return VerifyReport(critic_vendor=self._critic_vendor, rounds_used=ledger.rounds_used)

    # ------------------------------------------------------------------ the structure critic (Freeze)

    async def structure_critic(self, inp: StructureInput) -> VerifyReport:
        ledger = self._use(inp.ledger)
        g = inp.graph
        code = checks.structure_code_defects(g, inp.brief.locked_facts)
        if inp.check is not None:
            code = checks.dedupe(
                [*code, *checks.code_findings(g, inp.check, only=checks.STRUCTURE_CODES)],
                prefer=("S-XAPP",),
            )
        report = self._report(ledger)
        model: list[Defect] = []
        before = self.cost_micros
        if ledger.open_round(PHASE_FREEZE):
            sin = StructureIn(
                language=g.meta.source_language,
                brief=_brief_block(inp.brief, inp.research_notes),
                facts=digest.chunk_lines(digest.fact_lines(inp.brief)),
                digest=digest.chunk_lines(digest.structure_digest(g)),
                code_findings=digest.chunk_lines(digest.defect_lines(code)),
            )
            out = await self._ask(
                STRUCTURE_SPEC, sin, node_key="structure_critic", step="verify.structure"
            )
            res: StructureOut = out.output
            model = _model_defects(res.findings)
            report.summary = res.summary
            if out.used_fallback or out.flagged:
                report.notes.append(
                    "the model half of the structure critic fell back; code checks only"
                )
        else:
            report.notes.append("no LLM round left at the Freeze; code checks only")
        return self._finish(report, ledger, PHASE_FREEZE, g, [*code, *model], before)

    # ------------------------------------------------------------------ the canary (per task card)

    async def canary(
        self, task_card: Mapping[str, Any], reads: Mapping[str, str], *, run_index: int = 0
    ) -> CanaryResult:
        """Reads EXACTLY the card's reads (a file the card does not list is refused; one it lists but Spec could not
        supply is simply missing, which is what the canary is there to notice). Not code, not a round: a check."""
        paths = checks.card_read_paths(task_card)
        extra = sorted(set(reads) - set(paths))
        if extra:
            raise ValueError(f"a canary reads exactly the card's reads; not listed: {extra}")
        have = [p for p in paths if p in reads]
        task_id = str(task_card["id"])
        cin = CanaryIn(
            task_id=task_id,
            title=str(task_card.get("title", task_id)),
            criteria=digest.criteria_from_reads(reads),
            read_paths=have,
            reads=[f"=== {p} ===\n{reads[p]}" for p in have],
        )
        out = await self._ask(
            CANARY_SPEC,
            cin,
            node_key=f"canary:{task_id}:{run_index}",
            step=f"verify.canary.{task_id}",
        )
        res: CanaryOut = out.output
        return CanaryResult(
            task_id=task_id,
            outline=list(res.outline),
            blocking_questions=list(res.blocking_questions),
            assumptions=list(res.assumptions),
            acceptance_coverage=_coverage(cin.criteria, res.acceptance_coverage),
            run_index=run_index,
            used_fallback=out.used_fallback,
            flagged=out.flagged or out.used_fallback,
        )

    @staticmethod
    def merge_canaries(results: list[CanaryResult]) -> CanaryResult:
        """BG-15, kept here so Spec has one call: union of questions, the stricter run judges, every run recorded."""
        return merge_canaries(results)

    # ------------------------------------------------------------------ the final critic

    async def final_critic(self, inp: FinalInput) -> VerifyReport:
        ledger = self._use(inp.ledger)
        g = inp.graph
        confirm = (
            inp.confirm
            if inp.confirm is not None
            else list(getattr(inp.check, "confirm", []) or [])
        )
        code = checks.dedupe(
            [
                *checks.code_findings(g, inp.check),
                *checks.structure_code_defects(g, inp.brief.locked_facts),
                *checks.confirm_rank_defects(confirm),
            ],
            prefer=("S-XAPP",),
        )
        report = self._report(ledger)
        model: list[Defect] = []
        before = self.cost_micros
        if ledger.open_round(PHASE_FINAL):
            fin = FinalIn(
                language=g.meta.source_language,
                brief=_brief_block(inp.brief, inp.research_notes),
                facts=digest.chunk_lines(digest.fact_lines(inp.brief)),
                digest=digest.chunk_lines(digest.structure_digest(g)),
                coverage=digest.chunk_lines(digest.coverage_lines(inp.check)),
                freedom=digest.chunk_lines(digest.freedom_lines(inp.check)),
                confirm=digest.chunk_lines(digest.confirm_lines(confirm)),
                differs=digest.chunk_lines(inp.differs_from_brief),
                code_findings=digest.chunk_lines(digest.defect_lines(code)),
            )
            out = await self._ask(FINAL_SPEC, fin, node_key="final_critic", step="verify.final")
            res: FinalOut = out.output
            model = _model_defects(res.findings)
            confirm_ids = [str(c["id"]) for c in confirm]
            for m in res.confirm_moves:
                item = digest.confirm_item_id(m.item, confirm_ids)
                if item is None:
                    continue
                m = m.model_copy(update={"item": item})
                model.append(
                    Defect(
                        id=f"M-confirm:{m.item}:{m.to_rank}",
                        cls="A",
                        node=m.item,
                        field="confirm_rank",
                        change=f"move to rank {m.to_rank}",
                        reason=m.reason,
                        severity="warning",
                        source="model",
                        code="M-confirm",
                    )
                )
            report.differs_from_brief = list(res.differs_from_brief)
            report.summary = res.summary
            if out.used_fallback or out.flagged:
                report.notes.append(
                    "the model half of the final critic fell back; code checks only"
                )
        else:
            report.notes.append("no LLM round left for the final pass; code checks only")
            report.differs_from_brief = list(inp.differs_from_brief)
        return self._finish(report, ledger, PHASE_FINAL, g, [*code, *model], before)

    # ------------------------------------------------------------------ re-verify by hash (code only)

    def reverify(
        self, node_ids: Iterable[str], graph: SpecGraph, hashes: Mapping[str, str]
    ) -> VerifyReport:
        """Touched nodes only, BY HASH: a node whose hash equals the one recorded at its last verification is skipped.
        Code only (no model, no round). Findings on the changed nodes come back; class A fixes as patches; a blocker
        of class B/C on a node that already used its Verify repair leaves it `degraded`."""
        wanted = sorted(set(node_ids))
        current = {n: node_hash(graph, n) for n in wanted}
        changed = [n for n in wanted if hashes.get(n) != current[n]]
        report = self._report(self.ledger)
        report.hashes = {n: current[n] for n in changed}
        skipped = len(wanted) - len(changed)
        report.notes.append(
            f"re-verified {len(changed)} node(s) by hash, {skipped} unchanged and skipped"
        )
        if not changed:
            return report
        scope = set(changed)
        found = [
            d
            for d in checks.dedupe(
                [*checks.structure_code_defects(graph), *checks.code_findings(graph)],
                prefer=("S-XAPP",),
            )
            if d.node in scope
        ]
        report.findings = checks.sort_defects(found)
        report.patches = [
            checks.patch_for(d) for d in report.findings if d.cls == "A" and d.severity != "info"
        ]
        report.degraded_nodes = sorted(
            {
                d.node
                for d in report.findings
                if d.cls in ("B", "C")
                and d.severity == "blocker"
                and not self.ledger.node_can_repair(d.node)
                and graph.find(d.node) is not None
            }
        )
        return report

    # ------------------------------------------------------------------ shared tail

    def _finish(
        self,
        report: VerifyReport,
        ledger: RoundLedger,
        phase: str,
        graph: SpecGraph,
        defects: list[Defect],
        cost_before: int,
    ) -> VerifyReport:
        findings = checks.dedupe(defects, prefer=("S-XAPP",))
        patches, degraded, notes = plan_patches(findings, ledger, phase, graph)
        report.findings = findings
        report.patches = patches
        report.degraded_nodes = degraded
        report.notes += notes
        report.rounds_used = ledger.rounds_used
        report.cost_micros = self.cost_micros - cost_before
        report.flagged_calls = sorted(set(self.flagged_calls))
        return report


def build_verify_port(
    call: ModelCall, cfg: SpecConfig, critic_vendor: CriticVendor = "same"
) -> VerifyPort:
    """The entry point target (`mde.spec.verify`): Spec calls this with its injected `ModelCall` and the vendor
    honesty value the gateway computed; it never learns a vendor or model name here."""
    return VerifyAgent(call, cfg, critic_vendor)


__all__ = ["VerifyAgent", "build_verify_port"]
