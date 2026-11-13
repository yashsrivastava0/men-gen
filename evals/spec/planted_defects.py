"""PS-7, the planted-defect test of the self-check (doc 16 section 5, doc 18 section 3.4, doc 19 section 7).

22 structural defects (`mde_spec_core.planted.STRUCTURAL`) and 8 meaning defects (real planters in `planters.py`) are
planted one by one in a copy of a clean pack and run against three arms:
  (a) the self-check: the validators V-01 to V-45 (code, no model);
  (b) a PROOF-ONLY critic arm: the Verify final critic through its port, given the check report and the Brief;
  (c) Verify-for-deep: structure critic, canary and final critic.
Structural kinds are reported against the bars of doc 16 (90 percent overall, 80 percent per kind, false positives at
most 5 percent, at most 2 LLM rounds); the meaning kinds are reported SEPARATELY and have no bar. A kind is "caught" by a
critic arm when the arm reports a finding the clean pack does not produce. Offline the critic is the mock critic: it
catches what its mock rules catch, which proves the harness and nothing about the live rate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.spec.common import (
    Check,
    RunnerCall,
    Tally,
    checks_pass,
    compare,
    thresholds,
    verdict_for,
    write_json,
)
from evals.spec.packs import card_reads
from evals.spec.planters import (
    OFFICIAL_ITEM,
    PACK_VISIBLE,
    NotApplicable,
    Note,
    brief_for,
    prepare_baseline,
    real_meaning_defects,
    research_notes,
)
from mde_spec_agent.calls.base import CallRunner
from mde_spec_core import planted
from mde_spec_core.check import spec_check
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.graph import SpecGraph
from mde_spec_core.pack import Pack, assemble
from mde_spec_core.planted import Defect, Plant
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context
from mde_spec_core.verify_port import CriticVendor, FinalInput, StructureInput
from mde_verify_agent.port import VerifyAgent

ARMS = ("self_check", "critic", "verify")
ARM_NAMES = {
    "self_check": "self-check (validators, code)",
    "critic": "proof-only critic arm (Verify final critic)",
    "verify": "Verify for deep (structure critic + canary + final critic)",
}


class _Research:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items


@dataclass
class Case:
    defect: Defect
    graph: SpecGraph | None = None
    pack: Pack | None = None
    ctx_kwargs: dict[str, Any] = field(default_factory=dict)
    note: Note | None = None
    applicable: bool = True
    reason: str = ""
    packager_refused: bool = False


def plant(base: SpecGraph, d: Defect) -> Case:
    """Plant one defect in a copy (the same preparation as `planted.run_planted`, which the self-check arm reuses)."""
    g = base.model_copy(deep=True)
    pack = assemble(g)
    pl = Plant(g, pack=pack, pack_files=dict(pack.files))
    try:
        d.apply(pl)
    except NotApplicable as exc:
        return Case(d, applicable=False, reason=str(exc))
    except (ValueError, KeyError, IndexError, StopIteration, AttributeError, TypeError) as exc:
        return Case(
            d,
            applicable=False,
            reason=f"the planter could not change this pack: {type(exc).__name__}: {exc}",
        )
    kw = dict(pl.ctx_kwargs)
    note = kw.pop("_note", None)
    try:
        if d.id in ("volatile_data_in_body", "task_over_budget"):
            if d.id == "volatile_data_in_body":
                pack.files = pl.pack_files or pack.files
            pack2 = pack
        else:
            pack2 = assemble(g)
    except (
        Exception
    ):  # the assembler itself refuses an invalid plant: counted as caught by the packager
        return Case(d, g, None, kw, note, packager_refused=True)
    return Case(d, g, pack2, kw, note)


def _findings(g: SpecGraph, pack: Pack | None, kw: dict[str, Any]) -> set[tuple[str, str, str]]:
    """(code, where, severity) of every blocker and warning the validators report."""
    if pack is None:
        return {("PACKAGER", "", "blocker")}
    ctx = make_context(
        g,
        vocab=planted.vocab_view_for(g),
        pack=pack,
        research=_Research(kw["research_items"])
        if kw.get("research_items") is not None
        else _Research([OFFICIAL_ITEM]),
        brand_names=kw.get("brand_names", set()),
    )
    try:
        return {
            (f.code, f.where, f.severity)
            for f in run_all(ctx)
            if f.severity in ("blocker", "warning")
        }
    except Exception:
        return {("PACKAGER", "", "blocker")}


def _check_report(g: SpecGraph, pack: Pack, kw: dict[str, Any]) -> Any:
    items: list[dict[str, Any]] = kw.get("research_items") or [OFFICIAL_ITEM]
    extra = {k: v for k, v in kw.items() if k != "research_items"}
    return spec_check(g, pack, research_view=_Research(list(items)), **extra)


# ---------------------------------------------------------------------------------------------------- pure core


def catch_stats(rows: Sequence[dict[str, Any]], arm: str) -> dict[str, Any]:
    """Overall and per-kind catch rate over the APPLICABLE plants of one arm."""
    app = [r for r in rows if r["applicable"] and arm in r]
    caught = [r for r in app if r[arm]["caught"]]
    return {
        "planted": len(app),
        "caught": len(caught),
        "overall": len(caught) / len(app) if app else None,
        "per_kind": {r["defect"]: bool(r[arm]["caught"]) for r in app},
        "min_per_kind": (min((1.0 if r[arm]["caught"] else 0.0) for r in app) if app else None),
        "missed": sorted(r["defect"] for r in app if not r[arm]["caught"]),
    }


# ---------------------------------------------------------------------------------------------------- the report


@dataclass
class PlantedReport:
    pack: str
    profile: str
    rows: list[dict[str, Any]]
    arms: tuple[str, ...]
    false_positives: dict[str, dict[str, Any]]
    rounds_used_max: int
    critic_vendor: str
    tally: Tally
    thresholds: dict[str, float]
    max_rounds: int
    canary_scope: str
    clean_pack: dict[str, Any] = field(default_factory=dict)

    def _rows(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["kind"] == kind]

    def stats(self, kind: str, arm: str) -> dict[str, Any]:
        return catch_stats(self._rows(kind), arm)

    def arm_checks(self, arm: str) -> list[Check]:
        t = self.thresholds
        s = self.stats("structural", arm)
        fp = self.false_positives.get(arm, {}).get("rate")
        out = [
            compare(f"{arm}: structural catch overall", s["overall"], ">=", t["planted_catch_min"]),
            compare(
                f"{arm}: structural catch, worst kind",
                s["min_per_kind"],
                ">=",
                t["planted_catch_per_kind_min"],
            ),
            compare(
                f"{arm}: false positives on the unmodified pack", fp, "<=", t["false_positive_max"]
            ),
        ]
        if arm != "self_check":
            out.append(
                compare(
                    f"{arm}: LLM rounds used (max)", self.rounds_used_max, "<=", self.max_rounds
                )
            )
        return out

    def arm_would_pass(self, arm: str) -> bool | None:
        return checks_pass(self.arm_checks(arm))

    def arm_verdict(self, arm: str) -> str:
        w = self.arm_would_pass(arm)
        if arm == "self_check":  # code, deterministic: a measurement even offline
            return "not_measured" if w is None else ("pass" if w else "fail")
        return verdict_for(w, live=self.tally.live, runs=1)

    def verdict(self) -> str:
        vs = [self.arm_verdict(a) for a in self.arms]
        for v in ("not_measured", "single_run", "fail"):
            if v in vs:
                return v
        return "pass"

    def as_dict(self) -> dict[str, Any]:
        arms: dict[str, Any] = {}
        for a in self.arms:
            arms[a] = {
                "name": ARM_NAMES[a],
                "structural": self.stats("structural", a),
                "meaning": {**self.stats("meaning", a), "bar": "none (code cannot judge meaning)"},
                "false_positives": self.false_positives.get(a, {}),
                "checks": [c.as_dict() for c in self.arm_checks(a)],
                "verdict": self.arm_verdict(a),
            }
        return {
            "gate": "PS-7",
            "pack": self.pack,
            "profile": self.profile,
            "structural_kinds": len(self._rows("structural")),
            "meaning_kinds": len(self._rows("meaning")),
            "clean_pack": self.clean_pack,
            "not_applicable": [
                {"defect": r["defect"], "reason": r["reason"]}
                for r in self.rows
                if not r["applicable"]
            ],
            "arms": arms,
            "rounds_used_max": self.rounds_used_max,
            "critic_vendor": self.critic_vendor,
            "canary_scope": self.canary_scope,
            "verdict": self.verdict(),
            "single_run": True,
            "money": self.tally.as_dict(),
            "rows": self.rows,
            "note": (
                "Each kind is planted once (a rate over kinds). The self-check arm is code and a real measurement. "
                + (
                    "The critic arms ran live: single run."
                    if self.tally.live
                    else "The critic arms are the MOCK critic: they catch what its mock rules catch (a Locked fact "
                    "that is missing or changed, coverage gaps, role and entity conflicts); this proves the harness, not the live rate."
                )
            ),
        }


# ---------------------------------------------------------------------------------------------------- runner


def _ids(report: Any) -> set[str]:
    return {d.id for d in report.findings if d.severity != "info"}


def critic_view(g: SpecGraph, brief: Any) -> str:
    """What the structure and final critics read of the graph (Verify's digest) plus the Brief: if a plant leaves this text
    unchanged, no critic could ever see it (a finding about the harness, reported per kind)."""
    from mde_verify_agent import digest

    return "\n".join([*digest.structure_digest(g), *digest.fact_lines(brief)])


async def _critic(
    agent: VerifyAgent, g: SpecGraph, check: Any, brief: Any, notes: list[str]
) -> Any:
    return await agent.final_critic(
        FinalInput(graph=g, brief=brief, check=check, research_notes=notes)
    )


async def _verify_deep(
    agent: VerifyAgent,
    g: SpecGraph,
    pack: Pack,
    clean_pack: Pack | None,
    check: Any,
    brief: Any,
    notes: list[str],
    scope: str,
) -> tuple[set[str], set[str], int, list[Any]]:
    """Structure critic, canaries, final critic. Returns (finding ids, canary question ids, rounds used, reports)."""
    r1 = await agent.structure_critic(
        StructureInput(graph=g, brief=brief, check=check, research_notes=notes)
    )
    ids = _ids(r1)
    questions: set[str] = set()
    clean_reads = {
        t["id"]: card_reads_of(clean_pack, t) for t in (clean_pack.tasks if clean_pack else [])
    }
    pp = ProofPackShim(pack)
    for task in pack.tasks:
        if task.get("wave") == "W0":
            continue
        reads = card_reads(pp, task)
        if scope == "changed" and clean_reads.get(task["id"]) == reads:
            continue
        res = await agent.canary(task, reads)
        questions |= {f"{task['id']}:{q}" for q in res.blocking_questions}
    r2 = await _critic(agent, g, check, brief, notes)
    ids |= _ids(r2)
    return ids, questions, r2.rounds_used, [r1, r2]


class ProofPackShim:
    """Just enough of a ProofPack for `card_reads` on a bare Pack."""

    def __init__(self, pack: Pack) -> None:
        self.files = pack.files


def card_reads_of(pack: Pack | None, task: dict[str, Any]) -> dict[str, str]:
    return card_reads(ProofPackShim(pack), task) if pack else {}


async def run_planted_defects(
    runner: CallRunner,
    base_graph: SpecGraph,
    *,
    name: str = "pack",
    arms: Sequence[str] = ARMS,
    structural: Sequence[Defect] | None = None,
    meaning: Sequence[Defect] | None = None,
    critic_vendor: CriticVendor = "same",
    canary_scope: str = "changed",
    prepare: bool = True,
    cfg: SpecConfig | None = None,
) -> PlantedReport:
    """Plant every defect and run the chosen arms. `canary_scope` 'changed' runs the canary only on task cards whose
    reads differ from the clean pack's (cheaper live); 'all' runs every card (what Verify-for-deep does in delivery)."""
    cfg = cfg or default_config()
    start = len(runner.calls)
    clean = prepare_baseline(base_graph) if prepare else base_graph.model_copy(deep=True)
    clean_pack = assemble(clean)
    vocab = planted.vocab_view_for(clean)
    notes = research_notes([OFFICIAL_ITEM])
    brief = brief_for(clean)
    clean_kw: dict[str, Any] = {}
    clean_set = _findings(clean, clean_pack, clean_kw)
    clean_codes = {c for c, _w, _s in clean_set}
    clean_check = _check_report(clean, clean_pack, clean_kw) if set(arms) - {"self_check"} else None

    def agent() -> VerifyAgent:
        return VerifyAgent(RunnerCall(runner), cfg, critic_vendor)

    clean_critic_ids: set[str] = set()
    clean_verify_ids: set[str] = set()
    clean_verify_q: set[str] = set()
    fp: dict[str, dict[str, Any]] = {}
    n_scr = max(1, len(clean.screens))

    def screen_hits(items: set[str]) -> list[str]:
        return sorted(
            i
            for i in items
            if any(f":{sid}:" in i or i.endswith(f":{sid}") for sid in clean.screens)
        )

    if "self_check" in arms:
        flagged = sorted(
            {w.split(".", 1)[0] for _c, w, _s in clean_set if w.split(".", 1)[0] in clean.screens}
        )
        fp["self_check"] = {
            "rate": len(flagged) / n_scr,
            "screens_flagged": flagged,
            "clean_findings": len(clean_set),
        }
    if "critic" in arms:
        rep = await _critic(agent(), clean, clean_check, brief, notes)
        clean_critic_ids = _ids(rep)
        hit = sorted(
            {d.node for d in rep.findings if d.severity != "info" and d.node in clean.screens}
        )
        fp["critic"] = {
            "rate": len(hit) / n_scr,
            "screens_flagged": hit,
            "clean_findings": len(clean_critic_ids),
        }
    if "verify" in arms:
        a = agent()
        clean_verify_ids, clean_verify_q, _r, reps = await _verify_deep(
            a, clean, clean_pack, None, clean_check, brief, notes, "all"
        )
        hit = sorted(
            {
                d.node
                for rp in reps
                for d in rp.findings
                if d.severity != "info" and d.node in clean.screens
            }
        )
        fp["verify"] = {
            "rate": len(hit) / n_scr,
            "screens_flagged": hit,
            "clean_findings": len(clean_verify_ids),
            "canary_questions": len(clean_verify_q),
        }

    defects = [
        *(structural if structural is not None else planted.STRUCTURAL),
        *(meaning if meaning is not None else real_meaning_defects()),
    ]
    rows: list[dict[str, Any]] = []
    rounds_max = 0
    for d in defects:
        case = plant(clean, d)
        row: dict[str, Any] = {
            "defect": d.id,
            "kind": d.kind,
            "expected_code": d.expected,
            "applicable": case.applicable,
            "reason": case.reason,
            "pack_visible": PACK_VISIBLE.get(d.id, True),
            "touched": case.note.touched if case.note else [],
            "detail": case.note.detail if case.note else "",
        }
        rows.append(row)
        if not case.applicable:
            continue
        if case.graph is not None:
            row["critic_input_differs"] = critic_view(case.graph, brief) != critic_view(
                clean, brief
            )
        if "self_check" in arms:
            codes: set[tuple[str, str, str]] = (
                _findings(case.graph, case.pack, case.ctx_kwargs)
                if case.graph
                else {("PACKAGER", "", "blocker")}
            )
            new = sorted(f"{c} {w}".strip() for c, w, _s in codes - clean_set)
            if d.kind == "structural":  # the lead's own runner decides structural kinds
                pr = planted.run_planted(clean, vocab, defects=[d])[0]
                masked = d.expected in clean_codes and not any(
                    c == d.expected for c, _w, _s in codes - clean_set
                )
                caught = bool(pr.caught and not masked)
            elif d.expected:
                got = (
                    d.expected in {c for c, _w, _s in codes}
                    or case.packager_refused
                    or ("PACKAGER" in {c for c, _w, _s in codes})
                )
                masked = d.expected in clean_codes and not any(
                    c == d.expected for c, _w, _s in codes - clean_set
                )
                caught = bool(got and not masked)
            else:
                caught, masked = bool(new), False
            row["self_check"] = {
                "caught": caught,
                "new_findings": new[:10],
                "masked_by_clean_pack": masked,
            }
        if case.packager_refused or case.pack is None or case.graph is None:
            for a in ("critic", "verify"):
                if a in arms:
                    row[a] = {"caught": True, "new_findings": ["the packager refused the pack"]}
            continue
        check = (
            _check_report(case.graph, case.pack, case.ctx_kwargs)
            if (set(arms) - {"self_check"})
            else None
        )
        extra_notes = notes + research_notes(
            case.ctx_kwargs.get("research_items", [])
            if case.ctx_kwargs.get("research_items") not in (None, [OFFICIAL_ITEM])
            else []
        )
        if "critic" in arms:
            ag = agent()
            rep = await _critic(ag, case.graph, check, brief, extra_notes)
            new_ids = sorted(_ids(rep) - clean_critic_ids)
            model_only = any(f.source == "model" and f.id in set(new_ids) for f in rep.findings)
            rounds_max = max(rounds_max, rep.rounds_used)
            row["critic"] = {
                "caught": bool(new_ids),
                "new_findings": new_ids[:10],
                "by_model_half": model_only,
            }
        if "verify" in arms:
            ag = agent()
            ids, qs, rounds, _reps = await _verify_deep(
                ag, case.graph, case.pack, clean_pack, check, brief, extra_notes, canary_scope
            )
            new_ids = sorted(ids - clean_verify_ids)
            new_q = sorted(qs - clean_verify_q)
            rounds_max = max(rounds_max, rounds)
            row["verify"] = {
                "caught": bool(new_ids or new_q),
                "new_findings": new_ids[:10],
                "new_canary_questions": new_q[:5],
            }
    return PlantedReport(
        pack=name,
        profile=clean.meta.profile,
        rows=rows,
        arms=tuple(arms),
        false_positives=fp,
        rounds_used_max=rounds_max,
        critic_vendor=critic_vendor,
        tally=Tally.since(runner, start),
        thresholds=thresholds(cfg),
        max_rounds=cfg.verify.max_llm_rounds,
        canary_scope=canary_scope,
        clean_pack={
            "screens": len(clean.screens),
            "findings": len(clean_set),
            "codes": sorted(clean_codes),
            "masked_kinds": sorted(
                r["defect"] for r in rows if r.get("self_check", {}).get("masked_by_clean_pack")
            ),
            "note": "A plant whose validator already reports on the unmodified pack, with nothing new, counts as missed (conservative).",
        },
    )


def write_report(report: PlantedReport, out_dir: Path) -> Path:
    return write_json(out_dir / "planted-defects.json", report.as_dict())
