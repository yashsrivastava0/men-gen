"""`spec check` at Gate 3: the WHOLE suite on the finished pack, the builder-needs rule and the coverage matrix.

`ok` means no blocker and no missing need. The report is plain data with a deterministic JSON form for
`evidence/self-check.json`; it also carries what the repair ladder did (R-code log, re-asks, flags, banners) so the
Gate 2 page can show it. Code only: no model, no network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .bn import Need, builder_needs, missing_needs
from .bn import summary as bn_summary
from .coverage import CoverageMatrix, coverage_matrix
from .errors_out import counts, error_for_finding, plain_words, sort_findings
from .graph import SpecGraph
from .pack import Pack, PackBreach, PackOptions, assemble
from .validators.base import Finding, blockers, run_all
from .validators.context import make_context

CHECK_SCHEMA = "spec.self-check.v1"

# What the owner is shown first: money, legal, identity (doc 18 3.4). Keyword lists are ranking hints, not content.
_RANK_WORDS: dict[str, tuple[str, ...]] = {
    "money": (
        "money",
        "price",
        "fee",
        "fees",
        "tax",
        "refund",
        "payment",
        "pay",
        "charge",
        "cost",
        "rate",
        "percent",
        "percentage",
        "currency",
        "payout",
        "invoice",
        "discount",
        "deposit",
    ),
    "legal": (
        "legal",
        "law",
        "privacy",
        "consent",
        "terms",
        "compliance",
        "regulation",
        "gdpr",
        "licence",
        "license",
        "retention",
        "age",
        "liability",
        "policy",
        "contract",
    ),
    "identity": (
        "identity",
        "kyc",
        "verification",
        "verify",
        "otp",
        "login",
        "password",
        "account",
        "sign in",
        "sign-in",
        "passport",
        "document",
        "biometric",
        "phone number",
        "email",
    ),
}
_RANK_ORDER = ("money", "legal", "identity")
CONFIRM_TOP = 20


@dataclass
class CheckReport:
    findings: list[Finding]
    needs: list[Need]
    coverage: CoverageMatrix
    ok: bool
    by_severity: dict[str, int]
    by_validator: dict[str, int]
    errors: list[dict[str, Any]]
    pack_error: dict[str, Any] | None = None
    rcode: dict[str, Any] = field(default_factory=dict)
    reasks: list[Any] = field(default_factory=list)
    flags: list[Any] = field(default_factory=list)
    banners: list[Any] = field(default_factory=list)
    confirm: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blockers(self) -> list[Finding]:
        return blockers(self.findings)

    @property
    def missing(self) -> list[Need]:
        return missing_needs(self.needs)

    @property
    def partial(self) -> bool:
        """A missing need ends the run `partial` and never reaches Gate 2."""
        return bool(self.missing)

    def plain_lines(self) -> list[str]:
        lines = [plain_words(f) for f in sort_findings(self.findings) if f.severity != "info"]
        lines += [f"[BLOCKER] {n.id} {n.title}: still missing: {n.why}" for n in self.missing]
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": CHECK_SCHEMA,
            "ok": self.ok,
            "partial": self.partial,
            "counts": {"by_severity": self.by_severity, "by_validator": self.by_validator},
            "errors": self.errors,
            "pack_error": self.pack_error,
            "builder_needs": {
                "summary": bn_summary(self.needs),
                "needs": [n.as_dict() for n in self.needs],
            },
            "coverage": self.coverage.as_dict(),
            "rcode": self.rcode,
            "reasks": [_plain(x) for x in self.reasks],
            "flags": [_plain(x) for x in self.flags],
            "banners": [_plain(x) for x in self.banners],
            "confirm_list": self.confirm,
        }

    def to_json(self) -> str:
        """Deterministic text for evidence/self-check.json (sorted keys, no timestamps, LF, final newline)."""
        return json.dumps(self.as_dict(), sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def _plain(x: Any) -> Any:
    if hasattr(x, "as_row"):
        return x.as_row()
    if hasattr(x, "as_dict"):
        return x.as_dict()
    if hasattr(x, "__dataclass_fields__"):
        return {k: getattr(x, k) for k in sorted(x.__dataclass_fields__)}
    return x


def _rcode_summary(log: Any) -> dict[str, Any]:
    if log is None:
        return {"changes": 0, "counts": {}, "rows": []}
    return {"changes": len(log), "counts": log.counts(), "rows": log.rows()}


def spec_check(
    graph: SpecGraph,
    pack: Pack | None = None,
    *,
    rcode_log: Any = None,
    reasks: Iterable[Any] = (),
    flags: Iterable[Any] = (),
    banners: Iterable[Any] = (),
    options: PackOptions | None = None,
    research_view: Any = None,
    **ctx_kwargs: Any,
) -> CheckReport:
    """The whole suite V-01..V-45 at gate 3 on `pack` (assembled here when not given), plus the builder-needs rule
    and the coverage matrix. `ctx_kwargs` go to the validator Context (research, quotes, brand_names, waivers...)."""
    pack_error: dict[str, Any] | None = None
    findings: list[Finding] = []
    if pack is None:
        try:
            pack = assemble(graph, options)
        except PackBreach as exc:
            pack_error = exc.error
            findings.append(
                Finding(
                    "V-26",
                    "blocker",
                    exc.error["where"],
                    f"{exc.error['code']}: {exc.error['why']}",
                    exc.error["fix"],
                    "A",
                )
            )
    if research_view is not None and "research" not in ctx_kwargs:
        ctx_kwargs["research"] = research_view
    ctx = make_context(graph, pack=pack, **ctx_kwargs)
    findings += run_all(ctx, gate=3)
    needs = builder_needs(graph, pack)
    cov = coverage_matrix(graph)
    for g in cov.gaps:
        findings.append(Finding("COVERAGE", "warning", g.id, g.problem, g.fix, "B", detail=g.kind))
    ordered = sort_findings(findings)
    c = counts(ordered)
    ok = not blockers(ordered) and not missing_needs(needs)
    return CheckReport(
        findings=ordered,
        needs=needs,
        coverage=cov,
        ok=ok,
        by_severity=c["by_severity"],
        by_validator=c["by_validator"],
        errors=[error_for_finding(f) for f in ordered],
        pack_error=pack_error,
        rcode=_rcode_summary(rcode_log),
        reasks=list(reasks),
        flags=list(flags),
        banners=list(banners),
        confirm=confirm_list_ranked(graph, research_view),
    )


# ---------------------------------------------------------------- the ranked confirm list


def _category(text: str) -> str | None:
    low = text.lower()
    for cat in _RANK_ORDER:
        for w in _RANK_WORDS[cat]:
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
                return cat
    return None


def _research_items(rv: Any) -> Sequence[Any]:
    return list(getattr(rv, "items", []) or []) if rv is not None else []


def confirm_list_ranked(
    graph: SpecGraph, research_view: Any = None, top: int = CONFIRM_TOP
) -> list[dict[str, Any]]:
    """What the owner is asked to confirm, money first, then legal, then identity, then the rest; at most `top`.

    Sources: rules flagged `confirm`, `confirm` lists on screens, Locked-looking numbers that rest on research that is
    not official, assumed nodes with a number, and (when a research view is given) its unverified or single-source items."""
    raw: list[dict[str, Any]] = []
    for r in sorted(graph.rules.values(), key=lambda x: x.id):
        if r.confirm or r.assumed:
            text = f"{r.subject}: {r.statement}" + (f" ({r.value} {r.unit})" if r.value else "")
            raw.append(
                {
                    "id": r.id,
                    "kind": "rule",
                    "text": text,
                    "source": "graph",
                    "number": bool(r.value),
                }
            )
    for s in sorted(graph.screens.values(), key=lambda x: x.id):
        for c in s.confirm:
            raw.append(
                {"id": s.id, "kind": "screen", "text": str(c), "source": "graph", "number": False}
            )
    for f in sorted(graph.features.values(), key=lambda x: x.id):
        if f.capability_tier == "C":
            raw.append(
                {
                    "id": f.id,
                    "kind": "feature",
                    "text": f"{f.name} cannot be built as asked (tier C)",
                    "source": "graph",
                    "number": False,
                }
            )
    for it in _research_items(research_view):
        d = it if isinstance(it, dict) else getattr(it, "__dict__", {})
        ev = d.get("evidence", "unverified")
        if ev in ("unverified", "single_source"):
            text = str(d.get("claim") or d.get("text") or d.get("value") or d.get("id", ""))
            raw.append(
                {
                    "id": str(d.get("id", "")),
                    "kind": "research",
                    "text": f"{text} ({ev})",
                    "source": "research",
                    "number": bool(re.search(r"\d", text)),
                }
            )
    out: list[dict[str, Any]] = []
    for item in raw:
        cat = _category(item["text"])
        rank = _RANK_ORDER.index(cat) if cat else len(_RANK_ORDER)
        out.append(
            {**item, "category": cat or "other", "_rank": (rank, 0 if item["number"] else 1)}
        )
    out.sort(key=lambda x: (x["_rank"], x["id"], x["text"]))
    top_items = out[:top]
    for n, x in enumerate(top_items, start=1):
        x["rank"] = n
        del x["_rank"], x["number"]
    return top_items
