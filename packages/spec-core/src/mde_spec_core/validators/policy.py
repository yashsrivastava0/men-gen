"""V-16, V-17, V-20, V-23, V-33, V-36, V-45: tiers, evidence, secrets, allowed things, policy provenance, research drift."""

from __future__ import annotations

import re

from mde_spec_core.graph import Envelope

from .base import Context, Finding, validator

_POLICY = re.compile(r"^policy:[A-Za-z0-9_.\-]+@\d{4}-\d{2}-\d{2}$")
_SECRETS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"),
]
_PHONE = re.compile(r"(?<![\d•*])\+?\d[\d \-]{9,14}\d(?![\d•*])")
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[A-Za-z]{2,}")
_NUM_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s?(%|percent|rs|inr|usd|€|\$|₹|days?|hours?|minutes?|km|kg)", re.I
)

_EVIDENCE_KINDS = {"user", "compass", "policy"}
_RESEARCH_LABELS_OK = {"official", "corroborated"}


def _get(item: object, key: str, default: str = "") -> str:
    """A research item may be a dict (the file) or an object (the reader): read either."""
    if isinstance(item, dict):
        return str(item.get(key, default))
    return str(getattr(item, key, default))


def _prov_ref(n: Envelope) -> str:
    return ",".join(p.ref for p in n.provenance)


@validator(
    "V-16", "Every feature has a capability tier; tier B flagged; tier C only as needs-engineering"
)
def v16(ctx: Context) -> list[Finding]:
    out = []
    for f in ctx.graph.features.values():
        if f.capability and f.capability_tier is None:
            out.append(
                Finding(
                    "V-16",
                    "blocker",
                    f.id,
                    f"capability {f.capability} has no tier",
                    "read the capability sheet",
                    "B",
                )
            )
        if (
            f.capability
            and ctx.vocab.capability_ids
            and f.capability not in ctx.vocab.capability_ids
        ):
            out.append(
                Finding(
                    "V-16",
                    "blocker",
                    f.id,
                    f"capability {f.capability} is not in the capability sheet",
                    "use a sheet id",
                    "B",
                )
            )
        if f.capability_tier == "C" and f.status == "in":
            out.append(
                Finding(
                    "V-16",
                    "blocker",
                    f.id,
                    "tier C (not supportable) feature is in the release",
                    "say so plainly at G1.5 and G2; do not turn C into scope",
                    "C",
                )
            )
        if f.capability_tier == "B":
            out.append(
                Finding(
                    "V-16",
                    "info",
                    f.id,
                    "tier B: needs a development build or native module",
                    "flag at G2",
                    "C",
                )
            )
    for s in ctx.graph.screens.values():
        for c in s.could:
            if c.tier not in ("A", "B", "C"):
                out.append(
                    Finding(
                        "V-16",
                        "blocker",
                        f"{s.id}.{c.id}",
                        "COULD entry without a tier",
                        "record its tier",
                        "B",
                    )
                )
    return out


@validator(
    "V-17",
    "Tier rules: Locked has evidence; research and assumptions are not Locked; deviations recorded",
    gate=1,
)
def v17(ctx: Context) -> list[Finding]:
    out = []
    for n in ctx.graph.all_nodes():
        if n.tier != "locked":
            continue
        kinds = {p.kind for p in n.provenance}
        if not kinds & _EVIDENCE_KINDS:
            out.append(
                Finding(
                    "V-17",
                    "blocker",
                    n.id,
                    "Locked without user, document or policy evidence",
                    "downgrade to Default (logged R-code); never repair into a Locked fact",
                    "A",
                )
            )
        if kinds <= {"research", "assumed", "model"} or n.assumed:
            out.append(
                Finding(
                    "V-17",
                    "blocker",
                    n.id,
                    "research or an assumption cannot be Locked",
                    "downgrade to Default or Open",
                    "A",
                )
            )
    return out


@validator("V-20", "No secrets or personal data in the spec or fixtures")
def v20(ctx: Context) -> list[Finding]:
    texts: list[tuple[str, str]] = [("graph", ctx.graph.canonical_text())]
    if ctx.pack is not None:
        texts += [
            (p, t) for p, t in ctx.pack.files.items() if p not in ("MANIFEST.json", "index.json")
        ]
    out = []
    deny_digits = ctx.config.checks.digit_run_deny_length
    digit_run = re.compile(rf"(?<![\w.])\d{{{deny_digits},}}(?![\w.])")
    for where, text in texts:
        for rx in _SECRETS:
            if rx.search(text):
                out.append(
                    Finding(
                        "V-20",
                        "blocker",
                        where,
                        "looks like a secret or key",
                        "remove it; keys never enter a pack",
                        "A",
                    )
                )
                break
        for m in _EMAIL.finditer(text):
            if not m.group(0).lower().endswith("@example.com") and not m.group(0).lower().endswith(
                ".example.com"
            ):
                out.append(
                    Finding(
                        "V-20",
                        "blocker",
                        where,
                        "an email outside the reserved example.com domain",
                        "use demo@example.com",
                        "A",
                    )
                )
                break
        if _PHONE.search(text) and digit_run.search(text) is None:
            out.append(
                Finding(
                    "V-20",
                    "warning",
                    where,
                    "an unmasked phone-like number",
                    "phone numbers appear only masked",
                    "A",
                )
            )
        elif digit_run.search(text):
            out.append(
                Finding(
                    "V-20",
                    "blocker",
                    where,
                    f"a digit run of {deny_digits} or more (ID, account or phone number)",
                    "ID numbers appear only masked",
                    "A",
                )
            )
    # an ID-like value that passes a real validator fails (python-stdnum is for rejecting, never generating)
    return out


@validator(
    "V-23", "Only allowed capabilities; no library, component, tool, package or version is named"
)
def v23(ctx: Context) -> list[Finding]:
    out = []
    text = ctx.graph.canonical_text().lower()
    for name in sorted(ctx.vocab.banned_library_names):
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text):
            out.append(
                Finding(
                    "V-23",
                    "blocker",
                    "graph",
                    f"names a library or tool: {name}",
                    "Spec states needs, never a library",
                    "B",
                )
            )
    for s in ctx.graph.screens.values():
        for c in s.could:
            if c.motion and ctx.vocab.motion_intents and c.motion not in ctx.vocab.motion_intents:
                out.append(
                    Finding(
                        "V-23",
                        "blocker",
                        f"{s.id}.{c.id}",
                        f"motion {c.motion} is not a motion intent",
                        "use a known intent",
                        "B",
                    )
                )
    return out


@validator("V-33", "Policy provenance: a policy-Locked item cites a sheet id and date", gate=1)
def v33(ctx: Context) -> list[Finding]:
    out = []
    for n in ctx.graph.all_nodes():
        for p in n.provenance:
            if p.kind == "policy":
                if not _POLICY.match(p.ref):
                    out.append(
                        Finding(
                            "V-33",
                            "blocker",
                            n.id,
                            f"policy provenance {p.ref!r} is not policy:<id>@<date>",
                            "cite the sheet entry and its date",
                            "B",
                        )
                    )
                elif (
                    ctx.vocab.policy_ids
                    and p.ref[len("policy:") :].split("@")[0] not in ctx.vocab.policy_ids
                ):
                    out.append(
                        Finding(
                            "V-33",
                            "blocker",
                            n.id,
                            f"{p.ref} is not a rule in the policy sheet",
                            "only rules in the sheet are policy-Locked",
                            "B",
                        )
                    )
        if (
            n.tier == "locked"
            and any(p.kind == "policy" for p in n.provenance)
            and not any(_POLICY.match(p.ref) for p in n.provenance if p.kind == "policy")
        ):
            out.append(
                Finding(
                    "V-33",
                    "blocker",
                    n.id,
                    "policy-Locked without a sheet citation",
                    "cite policy:<id>@<date>",
                    "B",
                )
            )
    return out


@validator(
    "V-36",
    "Research drift: research-derived values cite items; no unverified item; Default cites official or corroborated",
    gate=1,
)
def v36(ctx: Context) -> list[Finding]:
    rv, out = ctx.research, []
    if rv is None:
        return [
            Finding(
                "V-36",
                "info",
                "research",
                "no research view: every research-derived claim must be absent or assumed",
                "libraries fill, marked assumed",
                "C",
            )
        ]
    labels = {_get(it, "id"): _get(it, "evidence", "unverified") for it in getattr(rv, "items", [])}
    for n in ctx.graph.all_nodes():
        claims = getattr(n, "claims_used", [])
        research_prov = [p for p in n.provenance if p.kind == "research"]
        if research_prov and not claims:
            out.append(
                Finding(
                    "V-36",
                    "blocker",
                    n.id,
                    "research-derived but claims_used is empty",
                    "record the item ids",
                    "B",
                )
            )
        for c in claims:
            lab = labels.get(c)
            if lab is None:
                out.append(
                    Finding(
                        "V-36",
                        "blocker",
                        n.id,
                        f"claims_used cites {c}, which is not in the research file",
                        "cite a real item",
                        "B",
                    )
                )
            elif lab == "unverified":
                out.append(
                    Finding(
                        "V-36",
                        "blocker",
                        n.id,
                        f"cites unverified item {c}",
                        "unverified items are never used",
                        "B",
                    )
                )
            elif n.tier == "default" and lab not in _RESEARCH_LABELS_OK:
                out.append(
                    Finding(
                        "V-36",
                        "blocker",
                        n.id,
                        f"a Default cites {lab} item {c}",
                        "single_source is Open or a confirm item",
                        "B",
                    )
                )
    return out


def _numbers(text: str) -> set[tuple[str, str]]:
    return {(m.group(1).replace(",", "."), m.group(2).lower()) for m in _NUM_UNIT.finditer(text)}


@validator(
    "V-45",
    "Grounding (narrow): numerals with units occur in the cited quote; names from closed lists; same-subject conflicts",
    gate=1,
)
def v45(ctx: Context) -> list[Finding]:
    sev = "blocker" if ctx.config.checks.v45_downgrade_enabled else "warning"
    out = []
    for r in ctx.graph.rules.values():
        if r.tier == "locked" or any(p.kind == "research" for p in r.provenance):
            quote = " ".join(ctx.quotes.get(p.ref, "") for p in r.provenance)
            if r.value and quote:
                if (r.value.replace(",", "."), (r.unit or "").lower()) not in _numbers(
                    quote
                ) and r.value not in quote:
                    out.append(
                        Finding(
                            "V-45",
                            sev,
                            r.id,
                            f"{r.value} {r.unit or ''} does not occur in its cited quote",
                            "remove the claim or cite the quote that has it (never repaired into a Locked fact)",
                            "B",
                        )
                    )
            elif r.value and not quote and r.tier == "locked":
                out.append(
                    Finding(
                        "V-45",
                        "info",
                        r.id,
                        "number cannot be grounded: no quote text available",
                        "listed as unchecked for G2",
                        "C",
                    )
                )
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for r in ctx.graph.rules.values():
        if r.value is None:
            continue
        k = (r.subject.casefold(), r.condition.casefold())
        if k in seen and seen[k][1] != r.value:
            out.append(
                Finding(
                    "V-45",
                    "warning",
                    r.id,
                    f"same subject and condition as {seen[k][0]} with a different value",
                    "resolve the conflict (confirm item)",
                    "C",
                )
            )
        seen[k] = (r.id, r.value)
    if ctx.brand_names:
        for i in ctx.graph.integrations.values():
            for pid in i.provider_ids:
                if pid not in ctx.brand_names:
                    out.append(
                        Finding(
                            "V-45",
                            sev,
                            i.id,
                            f"provider {pid} is not in the closed list",
                            "provider names come from the dossier list or the Brief's own words",
                            "B",
                        )
                    )
    return out
