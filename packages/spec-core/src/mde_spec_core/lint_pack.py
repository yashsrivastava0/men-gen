"""The pack linter: guarantees G-1..G-8 checked on a pack FOLDER, without the graph (doc 18 2.2).

`lint_pack_dir(path)` reads the files as they sit on disk and recomputes everything it can: manifest hashes and token
sizes, byte stability, the prefix bundle, every task's read total, build-order words, copy-key namespaces, id
resolution inside each screen file, tiers, the self-describing manifest, secrets and component-library names.
Anyone can run it (`python -m mde_spec_core check <pack-dir>`). Zero model cost.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .canon import canonical_json, normalise_text, sha256_text
from .config import SpecConfig, default_config
from .graph import SpecGraph
from .packform import WHOLE_FILE
from .render import TokenCounter
from .simulate import block_bodies, task_tokens
from .validators.base import Finding, VocabView
from .validators.content import _VOLATILE
from .validators.policy import _SECRETS

# Files the manifest cannot list (it holds their hashes or is written after it); evidence/ is not for the front end.
UNLISTED_OK = ("MANIFEST.json", "index.json", "build/build-plan.json", "graph.json")
_NO_BUILD_ORDER_IN = ("AGENTS.md", "MANIFEST.json", "index.json")

# G-4: words that order the build. Wizard steps and GIVEN-WHEN-THEN never match these phrases.
_BUILD_ORDER = re.compile(
    r"\bbuild (?:this )?(?:after|before|next|last)\b|\bfirst build\b|\bthen build\b|"
    r"\bbuild order\b|\bbefore (?:you )?build(?:ing)?\b|\bonly after (?:the )?[\w ]{0,30}is built\b|"
    r"\bwaves?\b",
    re.I,
)
_LIST = r"\[([^\]]*)\]"
_BIND = re.compile(r"bind: " + _LIST)
_EFFECT = re.compile(r"effect: " + _LIST)
_LEADS = re.compile(r"leads_to: " + _LIST)
_USE_BLOCK = re.compile(r"use_block: (BLK-S\d+@\w+)")
_COPY_KEYS = re.compile(r"^copy_keys: " + _LIST, re.M)
_SCR = re.compile(r"\bSCR-S?\d+\b")
_TIER_WORDS = ("Locked", "Default", "Open", "assumed")


def _f(
    code: str, sev: str, where: str, why: str, fix: str, detail: str = "", repair: str = "C"
) -> Finding:
    return Finding(code, sev, where, why, fix, repair, detail)  # type: ignore[arg-type]


# ---------------------------------------------------------------- reading and writing a pack folder


def read_pack_dir(path: str | Path) -> dict[str, str]:
    """Every file under `path` (dotfiles skipped) as exact text, keyed by its posix path."""
    root = Path(path)
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        out[rel.as_posix()] = p.read_bytes().decode("utf-8", errors="replace")
    return out


def write_pack_dir(files: Any, dest: str | Path) -> Path:
    """Write a pack (a `Pack`, or a path -> text map) to a folder byte for byte (LF kept)."""
    items: Mapping[str, str] = files.files if hasattr(files, "files") else files
    root = Path(dest)
    for rel, text in items.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def pack_from_files(files: dict[str, str]) -> Any:
    """A `Pack` rebuilt from files (so validators and the simulator can run on a folder)."""
    from .pack import Pack

    manifest = json.loads(files["MANIFEST.json"])
    plan = json.loads(files.get("build/build-plan.json", "{}") or "{}")
    index = json.loads(files["index.json"]) if "index.json" in files else {}
    needs = json.loads(files["global/needs.json"]) if "global/needs.json" in files else []
    tasks = plan.get("tasks", [])
    return Pack(
        files=dict(files),
        manifest=manifest,
        index=index,
        tasks=tasks,
        budget_report={
            "total_tokens": sum(
                i["tokens"] for p, i in manifest["files"].items() if p != WHOLE_FILE
            ),
            "tasks": len(tasks),
            "task_budget": manifest["task_read_budget"],
        },
        content_hash=manifest.get("content_hash", ""),
        package_hash=manifest.get("package_hash", ""),
        blocks=block_bodies(files),
        needs=needs,
        pilots={},
    )


# ---------------------------------------------------------------- the checks


def _jload(files: dict[str, str], path: str, out: list[Finding]) -> Any:
    if path not in files:
        return None
    try:
        return json.loads(files[path])
    except json.JSONDecodeError as exc:
        out.append(
            _f(
                "G-8",
                "blocker",
                path,
                f"is not valid JSON ({exc.msg})",
                "regenerate the file",
                "BAD_JSON",
            )
        )
        return None


def lint_pack_files(
    files: dict[str, str], config: SpecConfig | None = None, graph: SpecGraph | None = None
) -> list[Finding]:
    cfg = config or default_config()
    out: list[Finding] = []
    manifest = _jload(files, "MANIFEST.json", out)
    plan = _jload(files, "build/build-plan.json", out)
    if manifest is None:
        out.append(
            _f(
                "G-8",
                "blocker",
                "MANIFEST.json",
                "the pack has no readable manifest, so nothing in it can be checked",
                "write the manifest (assemble does)",
                "NO_MANIFEST",
            )
        )
        return out
    counter = TokenCounter(float(manifest.get("tokenizer_chars_per_token") or 3.0))
    _g1(files, manifest, plan, counter, graph, out)
    _bytes_and_volatile(files, out)
    _g8(files, manifest, out)
    _g3_prefix(files, plan, out)
    _tasks(files, manifest, plan, counter, cfg, out)
    _caps(files, manifest, counter, cfg, out)
    _g4(files, out)
    _g5(files, manifest, out)
    _g6(files, out)
    _g7(files, out)
    _secrets_and_names(files, out)
    return out


def lint_pack_dir(
    path: str | Path, config: SpecConfig | None = None, graph: SpecGraph | None = None
) -> list[Finding]:
    """The whole linter on a pack folder: G-1..G-8, secrets, library names."""
    return lint_pack_files(read_pack_dir(path), config, graph)


def _g1(
    files: dict[str, str],
    manifest: dict[str, Any],
    plan: Any,
    counter: TokenCounter,
    graph: SpecGraph | None,
    out: list[Finding],
) -> None:
    listed: dict[str, Any] = manifest.get("files", {})
    for p, info in sorted(listed.items()):
        if p not in files:
            out.append(
                _f(
                    "G-8",
                    "blocker",
                    p,
                    "is listed in the manifest but not in the pack",
                    "restore or relist it",
                    "LISTED_MISSING",
                )
            )
            continue
        if sha256_text(files[p]) != info.get("sha256"):
            out.append(
                _f(
                    "G-1",
                    "blocker",
                    p,
                    "the bytes do not match the manifest hash: the file changed after it was packaged",
                    "re-assemble the pack; an approved version is never edited by hand",
                    "MANIFEST_HASH_MISMATCH",
                )
            )
        elif counter.count(files[p]) != info.get("tokens"):
            out.append(
                _f(
                    "G-8",
                    "blocker",
                    p,
                    f"the manifest says {info.get('tokens')} tokens but the file is {counter.count(files[p])}",
                    "re-assemble the pack",
                    "MANIFEST_SIZE_STALE",
                )
            )
    for p in sorted(files):
        if p in listed or p in UNLISTED_OK or p.startswith("evidence/"):
            continue
        out.append(
            _f(
                "G-8",
                "warning",
                p,
                "is in the pack but the manifest does not list it",
                "list it with its size",
                "UNLISTED_FILE",
            )
        )
    if plan and "package_hash" in manifest:
        src = canonical_json({p: i["sha256"] for p, i in sorted(listed.items())}) + canonical_json(
            [{k: v for k, v in t.items() if k != "id"} for t in plan.get("tasks", [])]
        )
        if sha256_text(src) != manifest["package_hash"]:
            out.append(
                _f(
                    "G-1",
                    "blocker",
                    "MANIFEST.json",
                    "the package hash does not recompute from the file hashes and the task cards",
                    "re-assemble the pack",
                    "PACKAGE_HASH_STALE",
                )
            )
    if graph is not None and manifest.get("content_hash") != graph.content_hash():
        out.append(
            _f(
                "G-1",
                "blocker",
                "MANIFEST.json",
                "the content hash is not the hash of the graph beside the pack",
                "re-assemble from the approved graph",
                "CONTENT_HASH_MISMATCH",
            )
        )


def _bytes_and_volatile(files: dict[str, str], out: list[Finding]) -> None:
    for p, t in sorted(files.items()):
        if t != normalise_text(t):
            out.append(
                _f(
                    "V-34",
                    "blocker",
                    p,
                    "line endings, trailing spaces or the final newline are not normalised",
                    "write LF endings, no trailing spaces and exactly one final newline",
                    "NOT_NORMALISED",
                    "A",
                )
            )
        if p.endswith(".md") and p != WHOLE_FILE and (m := _VOLATILE.search(t)):
            out.append(
                _f(
                    "V-34",
                    "blocker",
                    p,
                    "volatile data (a hash, timestamp or version) sits inside a body",
                    "versions and hashes live only in MANIFEST.json and index.json",
                    "VOLATILE_IN_BODY",
                    "A",
                )
            )
            del m


def _g8(files: dict[str, str], manifest: dict[str, Any], out: list[Finding]) -> None:
    if "AGENTS.md" not in files:
        out.append(
            _f(
                "G-8",
                "blocker",
                "AGENTS.md",
                "the pack has no AGENTS.md: a reader cannot know how to start",
                "add it",
                "NO_AGENTS",
            )
        )
    else:
        a = files["AGENTS.md"]
        if len(a.splitlines()) > 80:
            out.append(
                _f(
                    "G-8",
                    "warning",
                    "AGENTS.md",
                    f"is {len(a.splitlines())} lines: it should fit one page",
                    "shorten it",
                    "AGENTS_LONG",
                )
            )
        for word in ("prefix.bundle.md",):
            if word not in a:
                out.append(
                    _f(
                        "G-8",
                        "blocker",
                        "AGENTS.md",
                        f"does not tell the reader about {word}",
                        "say how to start in one page",
                        "AGENTS_NO_START",
                    )
                )
    if "INDEX.md" not in files:
        out.append(
            _f("G-8", "blocker", "INDEX.md", "the pack has no INDEX.md", "add it", "NO_INDEX")
        )
    if manifest.get("schema") != "spec.manifest.v1":
        out.append(
            _f(
                "G-8",
                "blocker",
                "MANIFEST.json",
                f"schema is {manifest.get('schema')!r}, not spec.manifest.v1",
                "regenerate the manifest",
                "MANIFEST_SCHEMA",
            )
        )
    for key in (
        "content_hash",
        "package_hash",
        "task_read_budget",
        "tokenizer_chars_per_token",
        "files",
    ):
        if key not in manifest:
            out.append(
                _f(
                    "G-8",
                    "blocker",
                    "MANIFEST.json",
                    f"has no '{key}': the manifest is not self-describing",
                    "regenerate the manifest",
                    "MANIFEST_FIELD",
                )
            )
    for p, info in sorted(manifest.get("files", {}).items()):
        if not isinstance(info, dict) or "tokens" not in info or "sha256" not in info:
            out.append(
                _f(
                    "G-8",
                    "blocker",
                    p,
                    "its manifest entry has no token size or hash",
                    "regenerate the manifest",
                    "MANIFEST_ENTRY",
                )
            )


def _g3_prefix(files: dict[str, str], plan: Any, out: list[Finding]) -> None:
    if not plan:
        out.append(
            _f(
                "G-3",
                "blocker",
                "build/build-plan.json",
                "the pack has no build plan, so the prefix cannot be checked",
                "assemble writes it",
                "NO_PLAN",
            )
        )
        return
    names = plan.get("prefix_files", [])
    gone = [n for n in names if n not in files]
    if gone:
        out.append(
            _f(
                "G-3",
                "blocker",
                "build/build-plan.json",
                f"prefix_files names missing files: {', '.join(gone)}",
                "fix the plan",
                "PREFIX_FILE_MISSING",
            )
        )
        return
    want = normalise_text("\n\n".join(f"<!-- {p} -->\n{files[p]}" for p in names))
    if "prefix.bundle.md" not in files:
        out.append(
            _f(
                "G-3",
                "blocker",
                "prefix.bundle.md",
                "is missing",
                "assemble writes it",
                "NO_PREFIX",
            )
        )
    elif files["prefix.bundle.md"] != want:
        out.append(
            _f(
                "G-3",
                "blocker",
                "prefix.bundle.md",
                "is not the concatenation of the files named in prefix_files, in that order",
                "rebuild the bundle from its files",
                "PREFIX_NOT_CONCAT",
            )
        )
    for t in plan.get("tasks", []):
        r = t.get("reads", {})
        if r.get("prefix") != "prefix.bundle.md":
            out.append(
                _f(
                    "G-3",
                    "blocker",
                    t["id"],
                    "does not read prefix.bundle.md as its one prefix",
                    "every task reads the same prefix",
                    "TASK_PREFIX",
                )
            )
        extra = [p for p in r.get("global", []) if p in names]
        if extra:
            out.append(
                _f(
                    "G-3",
                    "blocker",
                    t["id"],
                    f"also reads {', '.join(extra)}, which are already in the prefix",
                    "read the prefix once",
                    "TASK_PREFIX_DUP",
                )
            )


def _tasks(
    files: dict[str, str],
    manifest: dict[str, Any],
    plan: Any,
    counter: TokenCounter,
    cfg: SpecConfig,
    out: list[Finding],
) -> None:
    if not plan or "prefix.bundle.md" not in files:
        return
    from .pack import Pack

    view = Pack(files, manifest, {}, plan.get("tasks", []), {}, "", "", {}, [], {})
    budget = int(manifest.get("task_read_budget", cfg.budgets.task_read_budget))
    for t in plan.get("tasks", []):
        for p in [*t["reads"].get("global", []), *t["reads"].get("screens", [])] + (
            [t["reads"]["module"]] if t["reads"].get("module") else []
        ):
            if p not in files:
                out.append(
                    _f(
                        "G-6",
                        "blocker",
                        t["id"],
                        f"reads {p}, which is not in the pack",
                        "fix the task card",
                        "TASK_READS_MISSING",
                    )
                )
        tt = task_tokens(view, t, counter, cfg)
        if tt["total"] > budget:
            out.append(
                _f(
                    "V-26",
                    "blocker",
                    t["id"],
                    f"TASK_OVER_BUDGET: the task reads {tt['total']} tokens against {budget}",
                    "split the task at a module boundary",
                    "TASK_OVER_BUDGET",
                    "A",
                )
            )
        stored = t.get("read_tokens", {}).get("total")
        if stored is not None and stored != tt["total"]:
            out.append(
                _f(
                    "V-26",
                    "blocker",
                    t["id"],
                    f"the task card says {stored} tokens but its files add up to {tt['total']}",
                    "re-assemble the pack",
                    "TASK_TOTAL_STALE",
                    "A",
                )
            )


def _caps(
    files: dict[str, str],
    manifest: dict[str, Any],
    counter: TokenCounter,
    cfg: SpecConfig,
    out: list[Finding],
) -> None:
    b = cfg.budgets
    for p, text in sorted(files.items()):
        n = counter.count(text)
        cap: int | None = None
        sev = "blocker"
        if p == "INDEX.md":
            cap = b.index_cap
        elif p == "prefix.bundle.md":
            cap = b.prefix_cap
        elif p.startswith("data-model/"):
            cap = b.global_shard_cap
        elif p.startswith("modules/"):
            cap = b.module_cap
        elif (
            p.startswith("screens/")
            and p.endswith(".md")
            and ".could." not in p
            and ".detail." not in p
        ):
            cap = b.slice_cap
        elif p.startswith("contract/"):
            cap = b.contract_resource_cap
        elif p == "build/build-plan.json":
            cap, sev = b.build_plan_cap, "warning"
        if cap is not None and n > cap:
            out.append(
                _f(
                    "V-26",
                    sev,
                    p,
                    f"FILE_OVER_CAP: {n} tokens, cap {cap}",
                    "split or shard the file",
                    "FILE_OVER_CAP",
                    "A",
                )
            )


def _g4(files: dict[str, str], out: list[Finding]) -> None:
    for p, t in sorted(files.items()):
        if (
            not p.endswith(".md")
            or p in _NO_BUILD_ORDER_IN
            or p.startswith(("build/", "evidence/"))
        ):
            continue
        m = _BUILD_ORDER.search(t)
        if m:
            out.append(
                _f(
                    "G-4",
                    "blocker",
                    p,
                    f"build-order wording ('{m.group(0)}'): a body must not say what to build before what",
                    "state entries and exits; order lives only in the build plan",
                    "BUILD_ORDER_WORDS",
                )
            )


def _split(items: str) -> list[str]:
    return [x.strip().strip("\"'") for x in items.split(",") if x.strip()]


def _copy_keys(text: str) -> list[str]:
    m = _COPY_KEYS.search(text)
    return _split(m.group(1)) if m else []


def _g5(files: dict[str, str], manifest: dict[str, Any], out: list[Finding]) -> None:
    global_keys: set[str] = set()
    for p, t in files.items():
        if p.startswith("global/copy/") and p.endswith(".json"):
            try:
                global_keys |= set(json.loads(t))
            except (json.JSONDecodeError, TypeError):
                out.append(
                    _f(
                        "G-5",
                        "blocker",
                        p,
                        "is not a JSON map of keys to text",
                        "regenerate the copy file",
                        "BAD_COPY",
                    )
                )
    screens = {p: t for p, t in files.items() if re.fullmatch(r"screens/SCR-S?\d+\.md", p)}
    for p, t in sorted(screens.items()):
        sid = p.removeprefix("screens/").removesuffix(".md")
        for key in _copy_keys(t):
            if key.startswith(f"{sid}.") or key.startswith("lib."):
                continue
            other = key.split(".", 1)[0]
            if _SCR.fullmatch(other) and other != sid:
                out.append(
                    _f(
                        "G-5",
                        "blocker",
                        p,
                        f"copy key {key} belongs to {other}: two screens share one owned key",
                        "keys are namespaced by their own screen id",
                        "COPY_KEY_OTHER_OWNER",
                    )
                )
            elif key not in global_keys:
                out.append(
                    _f(
                        "G-5",
                        "blocker",
                        p,
                        f"copy key {key} is not namespaced by {sid} and is not a global shared string",
                        "namespace it by the screen id (minted by code) or move it to global copy",
                        "COPY_KEY_NOT_NAMESPACED",
                    )
                )


def _closure_index(text: str) -> tuple[str, str]:
    head, sep, tail = text.partition("\n## closure")
    return head, tail if sep else ""


def _g6(files: dict[str, str], out: list[Finding]) -> None:
    prefix = files.get("prefix.bundle.md", "")
    info_ids = 0
    for p, t in sorted(files.items()):
        if not re.fullmatch(r"screens/SCR-S?\d+\.md", p):
            continue
        head, closure = _closure_index(t)
        lines = closure.splitlines()
        ent_lines = {ln.split(" ", 1)[0]: ln for ln in lines if ln.startswith("ENT-")}
        defined_ops = {ln.split(" ", 1)[0] for ln in lines if ln.startswith("OP-")}
        unresolved = {ln.split(" ", 1)[0] for ln in lines if ln.endswith("(unresolved)")}
        routes = {
            ln.split(" ", 2)[1].rstrip(":")
            for ln in lines
            if ln.startswith("route SCR-") and not ln.endswith("(unresolved)")
        }
        blocks = {ln.split(" ", 2)[1].rstrip(":") for ln in lines if ln.startswith("block BLK-")}

        def miss(kind: str, ref: str, why: str = "", where: str = p) -> None:
            if ref in prefix:
                return
            out.append(
                _f(
                    "G-6",
                    "blocker",
                    where,
                    f"{kind} {ref} is used here but not defined inside the file or the prefix{why}",
                    "put it in the screen's closure (a generated part: a Spec bug)",
                    "ID_UNRESOLVED",
                )
            )

        for m in _BIND.finditer(head):
            for b in _split(m.group(1)):
                if b.startswith("OP-"):
                    if b not in defined_ops or b in unresolved:
                        miss("operation", b)
                elif "." in b:
                    eid, fld = b.split(".", 1)
                    line = ent_lines.get(eid)
                    if line is None or eid in unresolved:
                        miss("entity", eid)
                    elif not re.search(rf"(?:: |; ){re.escape(fld)} \(", line):
                        miss("field", b, " (the entity line does not list the field)")
        for m in _EFFECT.finditer(head):
            for step in _split(m.group(1)):
                verb, _, target = step.partition(" ")
                if verb == "call" and target.startswith("OP-"):
                    if target not in defined_ops or target in unresolved:
                        miss("operation", target)
                elif verb == "go" and target.startswith("SCR-") and target not in routes:
                    miss("route target", target)
        for m in _LEADS.finditer(head):
            for target in _split(m.group(1)):
                if target.startswith("SCR-") and target not in routes:
                    miss("route target", target)
        for m in _USE_BLOCK.finditer(head):
            if f"{m.group(1)}" not in blocks:
                miss("block", m.group(1))
        info_ids += len(re.findall(r"reached_from: " + _LIST, head))
    if info_ids:
        out.append(
            _f(
                "G-6",
                "info",
                "screens",
                f"{info_ids} screens carry reached_from entries: informational ids that may point outside the slice",
                "none; they are marked informational",
                "INFO_IDS",
                "A",
            )
        )


def _g7(files: dict[str, str], out: list[Finding]) -> None:
    for p, t in sorted(files.items()):
        if not re.fullmatch(r"screens/SCR-S?\d+\.md", p):
            continue
        for key in ("tiers:", "provenance:"):
            if not re.search(rf"^{re.escape(key)}", t, re.M):
                out.append(
                    _f(
                        "G-7",
                        "blocker",
                        p,
                        f"the card has no '{key[:-1]}' line: its facts are not marked",
                        "mark Locked, Default, Open or assumed on every fact",
                        "NO_TIERS",
                    )
                )
    a = files.get("AGENTS.md", "")
    if a:
        miss = [w for w in _TIER_WORDS if w not in a]
        if miss:
            out.append(
                _f(
                    "G-7",
                    "blocker",
                    "AGENTS.md",
                    f"does not explain the tiers: {', '.join(miss)} missing",
                    "say what Locked, Default, Open and assumed mean",
                    "TIERS_UNEXPLAINED",
                )
            )


def _name_regex(names: set[str]) -> re.Pattern[str]:
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_-])(?:{alt})(?![A-Za-z0-9_-])", re.I)


def _secrets_and_names(files: dict[str, str], out: list[Finding]) -> None:
    rx = _name_regex(VocabView().banned_library_names)
    for p, t in sorted(files.items()):
        for s in _SECRETS:
            if s.search(t):
                out.append(
                    _f(
                        "V-20",
                        "blocker",
                        p,
                        "SECRET_IN_SPEC: text looks like a secret or key",
                        "remove it and rotate the secret if it was real",
                        "SECRET_IN_SPEC",
                        "C",
                    )
                )
                break
        if p in ("MANIFEST.json", "index.json") or p.startswith("evidence/"):
            continue
        m = rx.search(t)
        if m:
            out.append(
                _f(
                    "LINT-NAMES",
                    "blocker",
                    p,
                    f"names a component library or tool ('{m.group(0)}'): a pack describes needs, never products",
                    "describe the need; the front end picks what fulfils it",
                    "LIBRARY_NAME",
                    "A",
                )
            )
