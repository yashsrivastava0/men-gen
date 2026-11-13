"""The VerifyPort: the one meeting point of Spec and Verify (doc 19 section 3, AD-33).

Spec and Verify never import each other. This module (foundation layer: models, a protocol, a loader, pure helpers)
is all they share:

* Spec hands Verify ONE injected async callable, `ModelCall`. Spec's own runner stands behind it: it reserves the USD
  cap, reconciles, enforces the one-re-ask rule and returns the deterministic fallback flagged. Verify has no other
  way to reach a model, so the money cap cannot be bypassed from here.
* Verify only CHECKS. It returns findings and structured patches `{node, field, change, reason, cls}`; Spec is the
  single writer and applies them. Verify never writes into the graph or the pack.
* Spec finds the implementation through the Python entry point group `mde.spec.verify` (`load_verify_port`).

The pure helpers that BOTH sides need (the round ledger, the node hash, the canary merge, the vendor honesty rule)
live here so that neither side re-implements them.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from mde_contracts.gateway import CallSpec
from mde_spec_core.canon import sha256_json
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph

ENTRY_POINT_GROUP = "mde.spec.verify"
entry_point_name = ENTRY_POINT_GROUP  # the name Spec and the docs use for the group

RepairCls = Literal["A", "B", "C"]
Severity = Literal["blocker", "warning", "info"]
CriticVendor = Literal["same", "different"]
Source = Literal["code", "model"]

# ------------------------------------------------------------------------------------- the model call


@dataclass(frozen=True)
class PortCallSpec:
    """A call as Verify describes it. Same shape as Spec's own call spec, so Spec's runner takes it as it is.

    `spec` is the gateway `CallSpec` (id, route, templates, mock handler, untrusted inputs); `kind` groups re-asks;
    `fallback` is the deterministic function the runner uses (flagged) when the model side fails; `validators` are
    local checks `(input, output) -> error messages`.
    """

    spec: CallSpec[Any, Any]
    kind: str
    timeout_s: float
    fallback: Callable[[Any], Any]
    validators: tuple[Callable[[Any, Any], list[str]], ...] = ()

    @property
    def id(self) -> str:
        return self.spec.id


class VerifyCallInput(BaseModel):
    """Base of every Verify call input. `reask` carries the validator messages of ONE targeted re-ask, word for word."""

    model_config = ConfigDict(extra="forbid")
    reask: list[str] = []


@dataclass
class CallOutcome:
    """What a model call returns to Verify. `flagged` = the output is a fallback or a failed re-ask: say so."""

    output: Any
    used_fallback: bool = False
    flagged: bool = False
    cost_micros: int = 0


class ModelCall(Protocol):
    """Injected by Spec. `node_ids` names the units of the one-re-ask rule (Verify passes `VERIFY:<call>` ids of
    its own, never graph nodes, so a Verify re-ask never spends a node's ladder re-ask); `step` labels the event."""

    async def __call__(
        self, spec: PortCallSpec, inp: Any, *, node_ids: Sequence[str] = (), step: str = ""
    ) -> CallOutcome: ...


# ------------------------------------------------------------------------------------- findings and patches


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Defect(_M):
    """One thing Verify found. `cls`: A code fix (Spec applies, at most 3 passes), B lane call (Spec re-writes the
    node; a round), C needs a decision (routed to Product; inside the same limit)."""

    id: str
    cls: RepairCls
    node: str
    field: str = ""
    change: str
    reason: str
    severity: Severity = "warning"
    source: Source = "code"
    code: str = ""  # the validator or structure-check code that produced it (V-04, S-XAPP, ...)


class Patch(_M):
    """A structured patch Spec applies. Verify never applies one."""

    node: str
    field: str = ""
    change: str
    reason: str
    cls: RepairCls


class AcceptanceCover(_M):
    criterion: str
    satisfiable: bool
    how: str = ""


class CanaryResult(_M):
    """What the weakest consumer says about ONE task card (it reads exactly the card's reads; not code)."""

    task_id: str
    outline: list[str] = Field(default_factory=list)
    blocking_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    acceptance_coverage: list[AcceptanceCover] = Field(default_factory=list)
    run_index: int = 0
    used_fallback: bool = False
    flagged: bool = False
    disagreement: bool = False  # BG-15: two runs of this card disagreed
    stricter_run: int | None = None
    runs: list[CanaryResult] = Field(
        default_factory=list
    )  # BG-15: every run is recorded, none is dropped

    def unsatisfied(self) -> list[str]:
        return [c.criterion for c in self.acceptance_coverage if not c.satisfiable]

    def strictness(self) -> tuple[int, int]:
        return (len(self.unsatisfied()), len(self.blocking_questions))


class VerifyReport(_M):
    findings: list[Defect] = Field(default_factory=list)
    patches: list[Patch] = Field(default_factory=list)
    rounds_used: int = (
        0  # LLM rounds consumed so far across the Freeze and the final pass (cap: config)
    )
    critic_vendor: CriticVendor = "same"
    canaries: list[CanaryResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    degraded_nodes: list[str] = Field(default_factory=list)  # no round left for them: listed at G2
    flagged_calls: list[str] = Field(default_factory=list)  # call ids that used the fallback
    cost_micros: int = 0
    hashes: dict[str, str] = Field(
        default_factory=dict
    )  # node id -> hash at verification (reverify)
    differs_from_brief: list[str] = Field(
        default_factory=list
    )  # the final critic's plain differences
    summary: str = ""  # one plain sentence per critic pass, for the G2 data

    @property
    def blockers(self) -> list[Defect]:
        return [d for d in self.findings if d.severity == "blocker"]


# ------------------------------------------------------------------------------------- inputs


class LockedFact(_M):
    """A Locked fact of the approved Brief and where it must live in the graph.

    `value` is the Brief's own text or number; `node` and `field` say which graph node carries it. A Locked fact that
    is missing, no longer Locked, or whose value changed is a defect (never silently repaired by Verify).
    """

    id: str
    node: str
    field: str = ""
    value: str = ""
    text: str = ""


class BriefView(_M):
    """What Verify sees of the approved Brief. Quotes and text are the user's words: DATA, never instructions."""

    summary: str = ""
    quotes: list[str] = Field(default_factory=list)
    locked_facts: list[LockedFact] = Field(default_factory=list)


class RoundLedger:
    """Which Verify rounds were spent (doc 19 3.3). Survives in the checkpoint (`to_dict`).

    * At most `max_rounds` LLM rounds across the Freeze and the final pass; a round is opened per phase.
    * A node may take ONE Verify-driven repair (independent of the ladder's re-ask), then it is `degraded`.
    * Class A fixes are applied by Spec in at most `class_a_max_passes` passes; an unconverged fix becomes class B.
    """

    def __init__(self, max_rounds: int = 2, class_a_max_passes: int = 3) -> None:
        self.max_rounds = max_rounds
        self.class_a_max_passes = class_a_max_passes
        self.phases: list[str] = []
        self.node_rounds: dict[str, int] = {}
        self.class_a_passes = 0

    @classmethod
    def from_config(cls, cfg: SpecConfig) -> RoundLedger:
        return cls(cfg.verify.max_llm_rounds, cfg.repair.class_a_max_passes)

    @property
    def rounds_used(self) -> int:
        return len(self.phases)

    def can_open_round(self, phase: str) -> bool:
        return phase not in self.phases and len(self.phases) < self.max_rounds

    def open_round(self, phase: str) -> bool:
        """Open the round of `phase`: ONE model critic call per phase (the Freeze, the final pass). False when this
        phase already had its round or the limit is reached; a re-check after Spec applied patches is `reverify`
        (code, by hash), never a second model round."""
        if not self.can_open_round(phase):
            return False
        self.phases.append(phase)
        return True

    def node_can_repair(self, node_id: str) -> bool:
        return self.node_rounds.get(node_id, 0) < 1

    def charge_node(self, node_id: str) -> None:
        self.node_rounds[node_id] = self.node_rounds.get(node_id, 0) + 1

    def next_class_a_pass(self) -> bool:
        """Spec calls this before each pass of class A fixes; False after the last allowed pass."""
        if self.class_a_passes >= self.class_a_max_passes:
            return False
        self.class_a_passes += 1
        return True

    def reclassify_unconverged(self, patches: Iterable[Patch]) -> list[Patch]:
        """Class A patches that did not converge in time become class B (a lane call)."""
        return [p.model_copy(update={"cls": "B"}) if p.cls == "A" else p for p in patches]

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_rounds": self.max_rounds,
            "class_a_max_passes": self.class_a_max_passes,
            "phases": list(self.phases),
            "node_rounds": dict(sorted(self.node_rounds.items())),
            "class_a_passes": self.class_a_passes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RoundLedger:
        led = cls(int(data.get("max_rounds", 2)), int(data.get("class_a_max_passes", 3)))
        led.phases = [str(p) for p in data.get("phases", [])]
        led.node_rounds = {str(k): int(v) for k, v in dict(data.get("node_rounds", {})).items()}
        led.class_a_passes = int(data.get("class_a_passes", 0))
        return led


@dataclass
class StructureInput:
    """At the Freeze: the Brief against the frozen structure. `check` is Spec's `CheckReport` when it has one."""

    graph: SpecGraph
    brief: BriefView = field(default_factory=BriefView)
    check: Any = None
    ledger: RoundLedger | None = None
    research_notes: list[str] = field(default_factory=list)  # untrusted text, as lines


@dataclass
class FinalInput:
    """The final pass: the spec against the Brief, with the coverage matrix and the freedom checks (in `check`)."""

    graph: SpecGraph
    brief: BriefView = field(default_factory=BriefView)
    check: Any = None  # CheckReport: findings, coverage matrix, ranked confirm list
    confirm: list[dict[str, Any]] | None = None  # defaults to check.confirm
    differs_from_brief: list[str] = field(
        default_factory=list
    )  # merged, split, added, phased (Spec's own list)
    ledger: RoundLedger | None = None
    research_notes: list[str] = field(default_factory=list)


@runtime_checkable
class VerifyPort(Protocol):
    """What Spec calls. Every method only CHECKS; the returned patches are Spec's to apply."""

    @property
    def critic_vendor(self) -> CriticVendor: ...

    async def structure_critic(self, inp: StructureInput) -> VerifyReport: ...

    async def canary(
        self, task_card: Mapping[str, Any], reads: Mapping[str, str], *, run_index: int = 0
    ) -> CanaryResult: ...

    async def final_critic(self, inp: FinalInput) -> VerifyReport: ...

    def reverify(
        self, node_ids: Iterable[str], graph: SpecGraph, hashes: Mapping[str, str]
    ) -> VerifyReport: ...


# ------------------------------------------------------------------------------------- pure helpers


def node_hash(graph: SpecGraph, node_id: str) -> str:
    """Hash of one node's content (empty string when the id is not a node). What re-verification compares."""
    node = graph.find(node_id)
    if node is None:
        return ""
    return sha256_json(node.model_dump(mode="json"))


def _norm_question(text: str) -> str:
    return " ".join(text.casefold().split())


def merge_canaries(results: Sequence[CanaryResult]) -> CanaryResult:
    """BG-15: two (or more) runs of ONE task card. All runs are recorded; the blocking questions are the UNION; the
    card is judged by the STRICTER run (more unsatisfiable criteria, then more blocking questions; ties: the first)."""
    if not results:
        raise ValueError("merge_canaries needs at least one result")
    if len(results) == 1:
        return results[0]
    task_ids = {r.task_id for r in results}
    if len(task_ids) != 1:
        raise ValueError(f"merge_canaries needs one task card, got {sorted(task_ids)}")
    stricter = max(range(len(results)), key=lambda i: (results[i].strictness(), -i))
    judge = results[stricter]

    def union(items: Iterable[Iterable[str]]) -> list[str]:
        seen: dict[str, str] = {}
        for group in items:
            for text in group:
                seen.setdefault(_norm_question(text), text)
        return list(seen.values())

    # a criterion is satisfiable only if EVERY run says so; `how` comes from a run that said no, else the judge
    criteria: dict[str, AcceptanceCover] = {}
    for r in results:
        for c in r.acceptance_coverage:
            cur = criteria.get(c.criterion)
            if cur is None or (cur.satisfiable and not c.satisfiable):
                criteria[c.criterion] = c
    signatures = {
        (tuple(sorted(map(_norm_question, r.blocking_questions))), tuple(sorted(r.unsatisfied())))
        for r in results
    }
    return CanaryResult(
        task_id=judge.task_id,
        outline=list(judge.outline),
        blocking_questions=union(r.blocking_questions for r in results),
        assumptions=union(r.assumptions for r in results),
        acceptance_coverage=list(criteria.values()),
        run_index=judge.run_index,
        used_fallback=any(r.used_fallback for r in results),
        flagged=any(r.flagged for r in results),
        disagreement=len(signatures) > 1,
        stricter_run=judge.run_index,
        runs=[r.model_copy(update={"runs": []}) for r in results],
    )


def critic_vendor_for(call_routes: Mapping[str, str]) -> CriticVendor:
    """Honest independence. `call_routes` maps the role ("writer", "critic") to an OPAQUE vendor id that the GATEWAY
    computed (Spec passes it in; Verify never inspects a vendor or model name). 'different' only when both roles are
    known and their ids differ; anything else is 'same', so independence is never claimed falsely.

    A bigger or dearer route is NOT assumed to be a better critic (D-172): this function never looks at size or price.
    """
    writer = call_routes.get("writer")
    critic = call_routes.get("critic")
    if writer and critic and writer != critic:
        return "different"
    return "same"


def verify_enabled(cfg: SpecConfig, profile: str) -> bool:
    """Compact never; standard only when configured; deep on (config.verify.profiles)."""
    return profile != "compact" and profile in cfg.verify.profiles


def canary_cards(tasks: Sequence[Mapping[str, Any]], profile: str, cfg: SpecConfig) -> list[str]:
    """Task ids that get a canary. Deep (share 1.0): every card; below 1.0 a deterministic sample by task hash;
    compact (share 0.0): none. Foundation cards (wave W0) read no screens and are skipped."""
    share = float(cfg.verify.canary_share.get(profile, 0.0))
    out: list[str] = []
    for t in tasks:
        if t.get("wave") == "W0":
            continue
        if share >= 1.0:
            out.append(str(t["id"]))
        elif share > 0.0:
            bucket = int(sha256_json(str(t["id"]))[:8], 16) / 0xFFFFFFFF
            if bucket < share:
                out.append(str(t["id"]))
    return out


Builder = Callable[..., VerifyPort]


def load_verify_port(
    call: ModelCall,
    cfg: SpecConfig,
    *,
    profile: str,
    critic_vendor: CriticVendor = "same",
    entry_points: Callable[[], Iterable[Any]] | None = None,
) -> VerifyPort | None:
    """Find the Verify implementation by the entry point group `mde.spec.verify` and build it.

    None when the profile has no Verify (compact never; standard configurable; deep on) or when no implementation is
    installed: Spec then checks itself in code only and the pack says `checked_by: [self_check]`. A broken
    implementation raises (it is installed but failing; never hidden). `entry_points` is a test seam.
    """
    if not verify_enabled(cfg, profile):
        return None
    eps = (
        list(entry_points())
        if entry_points is not None
        else list(importlib_metadata.entry_points(group=ENTRY_POINT_GROUP))
    )
    for ep in sorted(eps, key=lambda e: str(e.name)):
        builder: Builder = ep.load()
        return builder(call, cfg, critic_vendor)
    return None


__all__ = [
    "ENTRY_POINT_GROUP",
    "AcceptanceCover",
    "BriefView",
    "CallOutcome",
    "CanaryResult",
    "CriticVendor",
    "Defect",
    "FinalInput",
    "LockedFact",
    "ModelCall",
    "Patch",
    "PortCallSpec",
    "RoundLedger",
    "StructureInput",
    "VerifyCallInput",
    "VerifyPort",
    "VerifyReport",
    "canary_cards",
    "critic_vendor_for",
    "entry_point_name",
    "load_verify_port",
    "merge_canaries",
    "node_hash",
    "verify_enabled",
]
