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
from mde_spec_core.graph import Prov

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
    """Messages for a record's provenance strings (closed kinds, real ids, no unverified research)."""
    errors: list[str] = []
    if not prov:
        return (
            [f"{where}: provenance is empty; say where it comes from or write assumed:<reason>"]
            if need
            else []
        )
    for raw in prov:
        if raw in allowed.echoed:
            continue  # the draft's own provenance, echoed
        parsed = parse_prov(raw)
        if parsed is None or parsed[0] in CODE_KINDS:
            errors.append(
                f"{where}: provenance {raw!r} must be kind:ref with kind one of {list(vocab.provenance_kinds())}"
            )
            continue
        kind, ref = parsed
        if kind in ("user", "compass") and ref not in allowed.refs:
            errors.append(
                f"{where}: provenance {raw!r} cites {ref!r}, which is not an id of this call"
            )
        elif kind == "research":
            if ref not in allowed.research:
                errors.append(
                    f"{where}: provenance {raw!r} cites a research item that is not in this call"
                )
            elif allowed.labels.get(ref) == "unverified":
                errors.append(f"{where}: research item {ref} is unverified and is never used")
        elif kind == "policy" and allowed.policy and ref not in allowed.policy:
            errors.append(f"{where}: {ref} is not a rule of the policy sheet")
        elif kind == "assumed" and not ref:
            errors.append(f"{where}: assumed provenance needs a short reason after the colon")
    return errors


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
