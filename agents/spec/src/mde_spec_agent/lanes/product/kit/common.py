"""Small shared pieces: the schema base class, provenance strings, tiers, number and form-word checks, text helpers.

Output schemas stay in the portable subset (flat closed records in arrays, no optional or nullable fields), so
provenance travels as plain strings `"kind:ref"` (`"user:FTR-001"`, `"research:RI-12"`, `"assumed:no source states it"`,
`"policy:<id>@<date>"`). Code turns them into the graph's `Prov` list and decides the tier: a model never writes Locked.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput
from mde_spec_core import libraries
from mde_spec_core.canon import canonical_json
from mde_spec_core.graph import Prov, SpecGraph

from . import vocab


class W(BaseModel):
    """Base of every output record: closed (an unknown field is an error)."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------------ provenance
_POLICY = re.compile(r"^policy:[A-Za-z0-9_.\-]+@\d{4}-\d{2}-\d{2}$")
EVIDENCE_KINDS = ("user", "compass", "policy")
# Kinds the code's own draft may carry (a module the derivation minted is `code:MOD-S001`). They are read and kept when the
# answer echoes the draft's own string, and refused when the model writes one itself.
CODE_KINDS = ("code", "model")


def parse_prov(text: str) -> tuple[str, str] | None:
    """`"kind:ref"` -> (kind, ref); a whole policy reference is kind policy with the string as ref."""
    s = text.strip()
    if _POLICY.match(s):
        return ("policy", s)
    kind, sep, ref = s.partition(":")
    if not sep or kind not in (*vocab.provenance_kinds(), *CODE_KINDS):
        return None
    return (kind, ref.strip())


class ProvIn(CallInput):
    """Input fields every call carries so a validator can judge provenance without the graph."""

    ref_ids: list[
        str
    ] = []  # ids a `user:` or `compass:` provenance may cite (features, roles, entities, facts ...)
    research_ids: list[str] = []  # research item ids a `research:` provenance may cite
    research_labels: list[str] = []  # "RI-12=official": the evidence class of each (code-made)
    policy_refs: list[str] = []  # "policy:<id>@<date>" strings that exist in the store-policy sheet


@dataclass(frozen=True)
class ProvAllowed:
    refs: frozenset[str]
    research: frozenset[str]
    labels: Mapping[str, str]
    policy: frozenset[str]
    echoed: frozenset[str] = (
        frozenset()
    )  # provenance strings of the draft itself (code-made, kept word for word)

    @classmethod
    def of(cls, inp: ProvIn) -> ProvAllowed:
        labels = dict(x.split("=", 1) for x in inp.research_labels if "=" in x)
        draft = getattr(inp, "baseline", None)
        echoed = frozenset(_draft_prov(draft.model_dump())) if draft is not None else frozenset()
        return cls(
            frozenset(inp.ref_ids),
            frozenset(inp.research_ids),
            labels,
            frozenset(inp.policy_refs),
            echoed,
        )


def _draft_prov(obj: object) -> list[str]:
    """Every provenance string in a draft (the `prov` lists of its records)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "prov" and isinstance(v, list):
                out += [x for x in v if isinstance(x, str)]
            else:
                out += _draft_prov(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _draft_prov(v)
    return out


def check_prov(
    where: str, prov: Sequence[str], allowed: ProvAllowed, *, need: bool = True
) -> list[str]:
    """Provenance is never a reason to re-ask: whatever a model cites that this call did not offer is repaired by code
    (`repair_prov`, logged), so there is nothing to report. Kept so every validator names where provenance is judged."""
    return []


_ID_IN_TEXT = re.compile(r"[A-Z]{1,6}-S?\d{1,6}")


def _own_provenance(graph: SpecGraph | None, ident: str) -> list[str]:
    node = graph.find(ident) if graph is not None else None
    if node is None:
        return []
    return [f"{p.kind}:{p.ref}" for p in node.provenance if p.ref]


def repair_prov(
    strings: Sequence[str],
    allowed: ProvAllowed,
    graph: SpecGraph | None,
    node_id: str,
) -> tuple[list[str], list[dict[str, str]]]:
    """Provenance strings the call really offered, and R-code rows for every change (never silent, never new evidence).

    * a string that is the draft's own, or that cites an id the call offered, stays;
    * a citation of a node that EXISTS in the graph but was not offered (a draft rule, flow, story ... or a malformed
      `brief-FLW-001`) is rewritten to what that node itself rests on (its own provenance), or to `assumed:` when it has none;
    * anything else (an unknown id, an unknown kind, research not shown, an unverified item, a rule not in the policy
      sheet) is dropped; a node left with nothing is marked assumed.
    """
    out: list[str] = []
    rows: list[dict[str, str]] = []

    def row(kind: str, before: str, after: str, what: str) -> None:
        rows.append(
            {"kind": kind, "node_id": node_id, "what": what, "before": before, "after": after}
        )

    for raw in strings:
        if raw in allowed.echoed:
            out.append(raw)
            continue
        parsed = parse_prov(raw)
        ok = False
        if parsed is not None and parsed[0] not in CODE_KINDS:
            kind, ref = parsed
            if kind in ("user", "compass"):
                ok = ref in allowed.refs
            elif kind == "research":
                ok = ref in allowed.research and allowed.labels.get(ref) != "unverified"
            elif kind == "policy":
                ok = not allowed.policy or ref in allowed.policy
            else:
                ok = kind in ("library", "assumed") and bool(ref)
        if ok:
            out.append(raw)
            continue
        named = [
            i
            for i in dict.fromkeys(_ID_IN_TEXT.findall(raw))
            if graph is not None and graph.find(i) is not None
        ]
        inherited = [x for i in named for x in _own_provenance(graph, i)]
        if named:
            new = inherited or [f"assumed:derived from {named[0]}"]
            out.extend(new)
            row(
                "exact_synonym",
                raw[:120],
                "; ".join(new)[:200],
                f"provenance {raw!r} names {named[0]}, which this call did not offer as a source: rewritten to what that node rests on",
            )
        else:
            row(
                "drop_unknown_optional",
                raw[:120],
                "",
                f"provenance {raw!r} is not a source this call offered: dropped (the node is assumed unless another source remains)",
            )
    if not out:
        out.append("assumed:no source the call offered")
    out = list(dict.fromkeys(out))
    return out, rows


def prov_nodes(
    prov: Sequence[str],
    quote_of: Callable[[str], str | None] | Mapping[str, str | None] | None = None,
) -> list[Prov]:
    out: list[Prov] = []
    for raw in prov:
        parsed = parse_prov(raw)
        if parsed is None:
            continue
        kind, ref = parsed
        quote = None
        if kind in ("user", "compass"):
            if ref.startswith("FCT-"):
                quote = ref
            elif callable(quote_of):
                quote = quote_of(ref)
            else:
                quote = (quote_of or {}).get(ref)
        out.append(Prov(kind=kind, ref=ref[:160], quote_id=quote))  # type: ignore[arg-type]
    return out


def merge_prov(old: Iterable[Prov], new: Iterable[Prov]) -> list[Prov]:
    seen: set[tuple[str, str]] = set()
    out: list[Prov] = []
    for p in [*old, *new]:
        k = (p.kind, p.ref)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def tier_and_assumed(prov: Sequence[Prov], labels: Mapping[str, str]) -> tuple[str, bool]:
    """A model never writes Locked. Evidence of a user, Compass or policy makes Default; research that is official or
    corroborated makes Default; a single source makes Open; nothing but assumption makes Default and assumed."""
    kinds = {p.kind for p in prov}
    if kinds & set(EVIDENCE_KINDS):
        return "default", False
    research = [labels.get(p.ref, "unverified") for p in prov if p.kind == "research"]
    if any(x in ("official", "corroborated") for x in research):
        return "default", False
    if research:
        return "open", True
    return "default", True


# ------------------------------------------------------------------------------------------------ text checks
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def number_tokens(text: str) -> set[str]:
    return {m.group(0).replace(",", ".") for m in _NUM.finditer(text)}


def invented_numbers(text: str, source_tokens: Collection[str]) -> list[str]:
    """Numbers in `text` that no source states (never invent a number)."""
    return sorted(n for n in number_tokens(text) if n not in source_tokens)


def new_form_words(text: str, source_text: str) -> list[str]:
    """Layout, widget, colour or motion words the model introduced (words already in the source are the user's own)."""
    low = source_text.lower()
    found = {f.word.lower() for f in libraries.form_word_findings(text)}
    return sorted(w for w in found if w not in low)


def snake(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or fallback


def clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def lines_blob(lines: Iterable[str]) -> str:
    return "\n".join(lines)


@dataclass
class Errors:
    """A tiny collector with a cap, so one bad answer never produces a wall of messages."""

    items: list[str] = field(default_factory=list)
    cap: int = 20

    def add(self, msg: str) -> None:
        if len(self.items) < self.cap:
            self.items.append(msg)

    def extend(self, msgs: Iterable[str]) -> None:
        for m in msgs:
            self.add(m)


def source_numbers(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out |= number_tokens(t)
    return out


def blob_of(obj: BaseModel) -> str:
    return canonical_json(obj.model_dump(mode="json"))


# ------------------------------------------------------------------------------------------------ V-36 repair
RESEARCH_OK = ("official", "corroborated")


def research_safe(
    prov: Sequence[Prov],
    labels: Mapping[str, str],
    *,
    claims_field: bool,
    tier: str = "default",
) -> tuple[list[Prov], list[str], list[dict[str, str]]]:
    """Research provenance that the checks (V-36) can accept, made mechanically.

    A node type with a `claims_used` list keeps `research:<item>` and records the item there, unless the item is not
    official or corroborated and the node is a Default (a single source is Open): then the citation is downgraded. A node
    type without `claims_used` cannot record the item, so the citation becomes `assumed:` with the item named in its
    reason. Returns the provenance, the claims, and R-code rows (what was changed, never silent)."""
    out: list[Prov] = []
    claims: list[str] = []
    rows: list[dict[str, str]] = []
    for p in prov:
        if p.kind != "research":
            out.append(p)
            continue
        label = labels.get(p.ref, "unverified")
        keep = (
            claims_field and label != "unverified" and (tier != "default" or label in RESEARCH_OK)
        )
        if keep:
            out.append(p)
            claims.append(p.ref)
            continue
        out.append(Prov(kind="assumed", ref=f"informed by research item {p.ref} ({label})"[:160]))
        rows.append(
            {
                "kind": "drop_unknown_optional",
                "node_id": "",
                "what": f"research citation {p.ref} cannot be recorded on this node (V-36): kept as assumed",
                "before": f"research:{p.ref}",
                "after": "assumed",
            }
        )
    return out, sorted(set(claims)), rows


def safe_prov(
    strings: Sequence[str],
    quote_of: Callable[[str], str | None] | Mapping[str, str | None] | None,
    labels: Mapping[str, str],
    node_id: str,
    *,
    claims_field: bool,
    tier: str = "default",
    allowed: ProvAllowed | None = None,
    graph: SpecGraph | None = None,
) -> tuple[list[Prov], list[str], list[dict[str, str]]]:
    """The provenance a node can carry: `repair_prov` (ids the call did not offer), `prov_nodes`, then `research_safe`
    (V-36). Returns the provenance, the claims and the R-code rows of every change."""
    rows: list[dict[str, str]] = []
    if allowed is not None:
        strings, prows = repair_prov(strings, allowed, graph, node_id)
        rows += prows
    prov, claims, rrows = research_safe(
        prov_nodes(strings, quote_of), labels, claims_field=claims_field, tier=tier
    )
    return prov, claims, [*rows, *({**r, "node_id": node_id} for r in rrows)]


# ------------------------------------------------------------------------------------------------ text scrub
_CLAUSE = re.compile(r"(?<=[.;,:!?])\s+")


def scrub(text: str, source: str, nums: Collection[str]) -> tuple[str, list[str]]:
    """The text without the clauses that carry a layout or widget word the source does not have, or a number no source
    states. A warning-grade lint is never a reason to re-ask: the offending clause is cut (and reported), the rest stays.
    Returns the text ("" when nothing is left) and the clauses removed."""
    if not text.strip() or not (new_form_words(text, source) or invented_numbers(text, nums)):
        return text, []
    kept: list[str] = []
    removed: list[str] = []
    for part in _CLAUSE.split(text.strip()):
        bad = new_form_words(part, source) or invented_numbers(part, nums)
        (removed if bad else kept).append(part)
    out = " ".join(kept).strip().rstrip(",;:").strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out, removed


def scrub_row(
    node_id: str, field: str, before: str, after: str, removed: Sequence[str]
) -> dict[str, str]:
    return {
        "kind": "drop_unknown_optional",
        "node_id": node_id,
        "what": f"{field}: a clause with a layout word or a number no source states was cut ({'; '.join(removed)[:120]})",
        "before": before[:160],
        "after": after[:160],
    }
