"""V-12, V-15, V-18 (superseded by V-26), V-21, V-26, V-34, V-35, V-42, V-43: tokens, fixtures, bytes, budgets, behaviours,
COULD discipline, asset and motion needs."""

from __future__ import annotations

import re

from .base import Context, Finding, validator

_VOLATILE = re.compile(
    r"\b[0-9a-f]{64}\b|\b20\d\d-\d\d-\d\dT\d\d:\d\d|\bgenerated at\b|\bversion: \d", re.I
)


@validator(
    "V-12", "Tokens: references resolve, contrast met in light and dark, no raw values in screens"
)
def v12(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    need = ctx.config.checks.wcag_body_text_ratio
    for did in g.directions:
        tk = g.tokens.get(did)
        if not tk:
            out.append(
                Finding(
                    "V-12",
                    "blocker",
                    did,
                    "direction has no tokens",
                    "tokens are built by code from the seed",
                    "A",
                )
            )
            continue
        names = set(tk.get("names", []))
        for ref in tk.get("refs", []):
            if ref not in names:
                out.append(
                    Finding(
                        "V-12",
                        "blocker",
                        did,
                        f"token reference {ref} does not resolve",
                        "fix the token tree",
                        "A",
                    )
                )
        for pair in tk.get("contrast", []):
            if pair.get("body", True) and float(pair["ratio"]) < need:
                out.append(
                    Finding(
                        "V-12",
                        "blocker",
                        did,
                        f"contrast {pair['fg']} on {pair['bg']} ({pair.get('scheme', 'light')}) is {pair['ratio']}, needs {need}",
                        "derive an accessible text role (code)",
                        "A",
                    )
                )
        for scheme in ("light", "dark"):
            if scheme not in {p.get("scheme", "light") for p in tk.get("contrast", [])}:
                out.append(
                    Finding(
                        "V-12",
                        "warning",
                        did,
                        f"no contrast pairs checked for {scheme}",
                        "check both schemes",
                        "A",
                    )
                )
    for s in g.screens.values():
        for _where, text in ((s.id, s.purpose),):
            if re.search(r"#[0-9a-fA-F]{6}\b", text):
                out.append(
                    Finding(
                        "V-12",
                        "blocker",
                        s.id,
                        "raw colour value in a screen",
                        "use token names only",
                        "B",
                    )
                )
    return out


@validator(
    "V-15", "Fixtures satisfy the entity schema; synthetic markers present; image briefs not URLs"
)
def v15(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    for h in g.hero.values():
        e = g.entities.get(h.entity)
        if e is None:
            continue
        keys = {f.key for f in e.fields}
        for k in h.content:
            if k not in keys:
                out.append(
                    Finding(
                        "V-15",
                        "blocker",
                        h.id,
                        f"content key {k} is not a field of {e.id}",
                        "fill only fields of the entity",
                        "B",
                    )
                )
        if h.image_brief and re.search(r"https?://", h.image_brief):
            out.append(
                Finding(
                    "V-15",
                    "blocker",
                    h.id,
                    "image brief holds a URL",
                    "briefs only: subject, count, avoid list",
                    "A",
                )
            )
        if not h.synthetic:
            out.append(
                Finding("V-15", "blocker", h.id, "fixture not marked synthetic", "mark it", "A")
            )
    return out


@validator("V-18", "Budgets (superseded by V-26)", superseded_by="V-26")
def v18(ctx: Context) -> list[Finding]:
    return []


@validator("V-21", "Determinism: the same graph renders the same bytes")
def v21(ctx: Context) -> list[Finding]:
    if ctx.pack is None:
        return []
    from mde_spec_core.pack import PackOptions, assemble

    again = assemble(
        ctx.graph,
        PackOptions(
            config=ctx.config,
            chars_per_token=ctx.pack.manifest["tokenizer_chars_per_token"],
            task_read_budget=ctx.pack.manifest["task_read_budget"],
            consumer_id=ctx.pack.manifest["consumer"],
        ),
    )
    if again.files.keys() != ctx.pack.files.keys():
        return [
            Finding(
                "V-21",
                "blocker",
                "pack",
                "a rebuild gives a different file set",
                "find the unstable ordering (Spec bug)",
                "A",
            )
        ]
    diff = [
        p
        for p in again.files
        if again.files[p] != ctx.pack.files[p]
        and p not in ("MANIFEST.json", "index.json", "build/build-plan.json")
    ]
    return [
        Finding(
            "V-21",
            "blocker",
            p,
            "bytes differ across two renders",
            "find the volatile field (Spec bug)",
            "A",
        )
        for p in diff
    ]


@validator(
    "V-26", "Budgets: every file and shard within its cap; every task within task_read_budget"
)
def v26(ctx: Context) -> list[Finding]:
    p, out = ctx.pack, []
    if p is None:
        return []
    b = ctx.config.budgets
    tok = {path: i["tokens"] for path, i in p.manifest["files"].items()}
    caps = (
        ("INDEX.md", b.index_cap),
        ("prefix.bundle.md", b.prefix_cap),
    )
    for path, cap in caps:
        if tok.get(path, 0) > cap:
            out.append(
                Finding(
                    "V-26",
                    "blocker",
                    path,
                    f"{tok[path]} tokens, cap {cap}",
                    "shard the global files (the packager re-shards first)",
                    "A",
                )
            )
    for path, n in tok.items():
        if path.startswith("data-model/") and n > b.global_shard_cap:
            out.append(
                Finding(
                    "V-26",
                    "blocker",
                    path,
                    f"shard {n} tokens, cap {b.global_shard_cap}",
                    "split the entity cluster",
                    "A",
                )
            )
        if path.startswith("modules/") and n > b.module_cap:
            out.append(
                Finding(
                    "V-26",
                    "blocker",
                    path,
                    f"module file {n} tokens, cap {b.module_cap}",
                    "split the module",
                    "A",
                )
            )
        if (
            path.startswith("screens/")
            and path.endswith(".md")
            and ".could." not in path
            and ".detail." not in path
            and n > b.slice_cap
        ):
            out.append(
                Finding(
                    "V-26",
                    "blocker",
                    path,
                    f"slice {n} tokens, cap {b.slice_cap}",
                    "move detail to .detail.md or split the screen",
                    "A",
                )
            )
        if path.startswith("contract/") and n > b.contract_resource_cap:
            out.append(
                Finding(
                    "V-26",
                    "blocker",
                    path,
                    f"contract resource {n} tokens, cap {b.contract_resource_cap}",
                    "split the resource",
                    "A",
                )
            )
    for t in p.tasks:
        if t["read_tokens"]["total"] > p.manifest["task_read_budget"]:
            out.append(
                Finding(
                    "V-26",
                    "blocker",
                    t["id"],
                    f"task reads {t['read_tokens']['total']} tokens against {p.manifest['task_read_budget']}",
                    "TASK_OVER_BUDGET: split the task at a module boundary",
                    "A",
                )
            )
    return out


@validator(
    "V-34",
    "Byte stability: no volatile data in bodies; the prefix bundle and the content hash recompute identically",
)
def v34(ctx: Context) -> list[Finding]:
    p, out = ctx.pack, []
    if p is None:
        return []
    for path, text in p.files.items():
        if path in ("MANIFEST.json", "index.json", "build/build-plan.json"):
            continue
        if "\r" in text or not text.endswith("\n"):
            out.append(
                Finding(
                    "V-34",
                    "blocker",
                    path,
                    "line endings or final newline not normalised",
                    "normalise (R-code)",
                    "A",
                )
            )
        # the one-file form repeats files that are each scanned on their own (a contract file may say `version: 0`)
        if path.endswith(".md") and path != "APP_SPEC.md" and _VOLATILE.search(text):
            out.append(
                Finding(
                    "V-34",
                    "blocker",
                    path,
                    "volatile data (hash, timestamp or version) inside a body",
                    "versions and hashes live only in MANIFEST.json and index.json",
                    "A",
                )
            )
    if ctx.graph.content_hash() != p.content_hash:
        out.append(
            Finding("V-34", "blocker", "pack", "content hash does not recompute", "Spec bug", "A")
        )
    return out


@validator(
    "V-35",
    "Global behaviour completeness: every applicable category present for the profile; provider-side flows have interruption and pending timeout",
)
def v35(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    have = {b.category for b in g.behaviors.values()}
    for cat in ctx.vocab.behavior_categories.get(ctx.profile, []):
        if cat not in have:
            out.append(
                Finding(
                    "V-35",
                    "blocker",
                    "behaviors",
                    f"category {cat} missing",
                    "write it (BEHAV) or instantiate the library default",
                    "B",
                )
            )
    for i in g.integrations.values():
        if i.provider_side_flow:
            if not i.interruption:
                out.append(
                    Finding(
                        "V-35",
                        "blocker",
                        i.id,
                        "provider-side flow without an interrupted-flow rule",
                        "required for any provider-side flow",
                        "B",
                    )
                )
            if not i.pending_timeout:
                out.append(
                    Finding(
                        "V-35",
                        "blocker",
                        i.id,
                        "provider-side flow without a pending-timeout rule",
                        "required for any provider-side flow",
                        "B",
                    )
                )
    return out


@validator(
    "V-42",
    "Opportunity discipline: COULD is Open only, within caps, each with a basis; essential motion has an alternative",
)
def v42(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    cfg = ctx.config.profiles
    per_app: dict[str, int] = {}
    for s in g.screens.values():
        if len(s.could) > cfg.could_cap_per_screen:
            out.append(
                Finding(
                    "V-42",
                    "blocker",
                    s.id,
                    f"{len(s.could)} COULD entries (cap {cfg.could_cap_per_screen})",
                    "trim by criticality",
                    "B",
                )
            )
        per_app[s.app_id] = per_app.get(s.app_id, 0) + len(s.could)
        for c in s.could:
            if not c.basis.strip():
                out.append(
                    Finding(
                        "V-42",
                        "blocker",
                        f"{s.id}.{c.id}",
                        "COULD without a basis",
                        "an idea with no basis is dropped",
                        "B",
                    )
                )
            if c.kind == "alternative":
                out.append(
                    Finding(
                        "V-42",
                        "warning",
                        f"{s.id}.{c.id}",
                        "an alternative that adds scope is a G2 suggestion, not a COULD line",
                        "move it to suggestions",
                        "B",
                    )
                )
            if c.motion and c.motion in ctx.vocab.essential_motion and not c.reduced_motion_alt:
                out.append(
                    Finding(
                        "V-42",
                        "blocker",
                        f"{s.id}.{c.id}",
                        "essential motion without a non-motion alternative",
                        "state the alternative",
                        "B",
                    )
                )
            m = re.search(r"(FLW-\w+) step (\d+)", c.basis)
            if m and (f := g.flows.get(m.group(1))):
                stg = next((x for x in f.stages if x.n == int(m.group(2))), None)
                if stg is not None and stg.emotion_assumed and not c.assumed:
                    out.append(
                        Finding(
                            "V-42",
                            "blocker",
                            f"{s.id}.{c.id}",
                            "basis is an assumed emotion but the entry is not marked assumed",
                            "an assumed basis stays assumed",
                            "A",
                        )
                    )
    for app, n in per_app.items():
        if n > cfg.could_cap_per_app[ctx.profile]:
            out.append(
                Finding(
                    "V-42",
                    "blocker",
                    app,
                    f"{n} COULD entries in the app (cap {cfg.could_cap_per_app[ctx.profile]})",
                    "trim by criticality",
                    "B",
                )
            )
    return out


@validator(
    "V-43",
    "Asset and motion needs complete: kind, purpose, brief, source policy, fallback and alt-text rule",
)
def v43(ctx: Context) -> list[Finding]:
    out = []
    for a in ctx.graph.assets.values():
        if ctx.vocab.asset_kinds and a.kind not in ctx.vocab.asset_kinds:
            out.append(
                Finding(
                    "V-43", "blocker", a.id, f"unknown asset kind {a.kind}", "use a known kind", "B"
                )
            )
        if not a.subject or a.count < 1:
            out.append(
                Finding(
                    "V-43",
                    "blocker",
                    a.id,
                    "brief lacks subject or count",
                    "state subject, count and avoid list",
                    "B",
                )
            )
        if not a.fallback or (
            a.kind in ("photo", "illustration", "avatar", "brand_mark") and not a.alt_text_rule
        ):
            out.append(
                Finding(
                    "V-43", "blocker", a.id, "missing fallback or alt-text rule", "add them", "B"
                )
            )
    return out
