"""Save a finished Spec run under ``docs/`` so the owner can read what the engine generated (read-only on the run,
no model, no key). The same shape as Compass and Research keep: the deliverable, a readable README, a numbers file and
an index of every saved folder.

    uv run python scripts/spec_export_outputs.py <run-dir> --out docs/spec-agent/outputs/<dated-folder>
        [--label "<input id>"] [--mode live|mock|auto] [--allow-skip] [--no-index]

It copies a run into ``<out>/<run_id>/``:

* ``pack/``: ONLY the files ``pack/MANIFEST.json`` lists, plus ``MANIFEST.json``, ``index.json`` and
  ``build/build-plan.json``. The run store never deletes a stale file after a Verify re-assemble, so the tree is
  never copied whole; each listed file is checked against its hash in the manifest first.
* ``evidence/*.json``, ``graph.json``, ``run.json``, ``report.json`` (the numbers of ``spec_run_report.report``),
  ``summary.json`` (a small numbers file) and a plain-words ``README.md``.
* NOT ``checkpoints/``, ``events.jsonl`` or ``input.json.gz``.

Safety check, before anything is written to the destination: every file is scanned with the patterns the repo's own
guards use (``tests/security/test_single_live_switch.py`` forbidden phrases and ``tests/security/test_repo_hygiene.py``
key-like patterns through ``mde_core.redact``), imported, never copied. A file that hits is not copied (its bytes are
never altered, the pack hashes would break) and is recorded in ``EXPORT-NOTES.md``; the exit code is 1 unless
``--allow-skip``. A ``secret-scan: allow`` marker is NOT honoured here: a run is model output, not a fixture.

``refresh_index`` rewrites ``<out parent>/README.md``: one row per exported run, newest first (deterministic).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from mde_core.redact import SECRET_PATTERNS, normalize_for_scan

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
OUTPUTS_REL = Path("docs") / "spec-agent" / "outputs"
SUMMARY_SCHEMA = "spec.export.summary.v1"
NOTES_FILE = "EXPORT-NOTES.md"
INDEX_FILE = "README.md"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
STILL_GOING = {
    "queued",
    "running",
    "paused_g15",
}  # a run in one of these has nothing final to save yet
MODES = ("auto", "mock", "live")

STATUS_WORDS = {
    "approved": "approved at Gate 2: this is the pack that would be handed to the front-end engine",
    "awaiting_g2": "finished and waiting at Gate 2: nobody has approved this pack yet",
    "partial": "stopped early: some files below may be missing or thin",
    "failed": "failed: nothing usable came out",
}
REASON_WORDS = {
    "budget_cap": "the money cap was reached",
    "budget_tripwire": "the money tripwire fired before the cap",
    "stop": "someone pressed stop",
    "rate_limit": "the model provider limited the rate",
    "no_credit": "the model provider account was out of credit",
    "crash": "the server restarted or crashed",
    "plan_invalid": "the plan the model wrote could not be used",
}
PACK_PARTS = (
    ("INDEX.md", "the map of the whole pack: what each folder holds and what to read first"),
    ("AGENTS.md", "the short instructions the front-end engine reads before anything else"),
    ("screens/", "one file per screen: what is on it, what each button does, each state, the copy"),
    ("modules/", "one file per module: the screens that belong together and their stories"),
    ("contract/", "the contract: operations (API), resources, events, who may do what, components"),
    ("data-model/", "the data: entities, fields, statuses, who sees what"),
    (
        "global/",
        "app-wide files: roles, navigation, routes, rules, glossary, design direction, copy",
    ),
    ("apps/", "one short file per app in the program"),
    ("build/build-plan.json", "the build tasks, in waves, with what each task reads"),
    ("prefix.bundle.md", "the shared reading every build task starts with"),
    ("MANIFEST.json", "the list of every file here with its hash and size in tokens"),
    ("index.json", "the same map as INDEX.md, for programs"),
)


class ExportError(Exception):
    """The export cannot go ahead (bad run folder, mode mismatch, a changed pack, an unscannable file)."""


@dataclass(frozen=True)
class Skip:
    path: str
    reason: str


@dataclass
class ExportResult:
    run_id: str
    dest: Path
    mode: str
    status: str | None
    files_copied: int = 0
    skipped: list[Skip] = field(default_factory=list)
    refused: bool = False  # strict mode with a hit: nothing but EXPORT-NOTES.md was written
    summary: dict[str, Any] = field(default_factory=dict)
    index: Path | None = None


# --- the repo's own guards, imported not copied -----------------------------------------------------------------------


@dataclass(frozen=True)
class Guard:
    forbidden: re.Pattern[str]
    text_suffixes: frozenset[str]
    secret_patterns: tuple[tuple[str, re.Pattern[str]], ...]
    binary_suffixes: frozenset[str]


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ExportError(f"the safety check cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_guard(root: Path = ROOT) -> Guard:
    """The patterns of ``tests/security`` (the guard tests stay the single source). Missing: no export at all."""
    security = root / "tests" / "security"
    try:
        live = _load_module("_spec_export_guard_single", security / "test_single_live_switch.py")
        hygiene = _load_module("_spec_export_guard_repo", security / "test_repo_hygiene.py")
    except (OSError, ImportError, SyntaxError) as exc:
        raise ExportError(
            f"the safety check needs the repo's guard tests in {security}: {exc.__class__.__name__}: {exc}"
        ) from exc
    high = set(hygiene.HIGH_SIGNAL)
    return Guard(
        forbidden=live.FORBIDDEN,
        text_suffixes=frozenset(live.TEXT_SUFFIXES),
        secret_patterns=tuple((n, p) for n, p in SECRET_PATTERNS if n in high),
        binary_suffixes=frozenset(hygiene.BINARY_SUFFIXES),
    )


_MAX_LINES_NAMED = 5


def scan_file(guard: Guard, rel: str, data: bytes) -> list[str]:
    """Why ``data`` (to be saved as ``rel``) may not enter the repo; empty when it may. Never echoes a match."""
    suffix = PurePosixPath(rel).suffix
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if suffix.lower() in guard.binary_suffixes:
            return []  # the repo guards skip these too
        return ["not UTF-8 text, so it cannot be scanned"]
    if suffix.lower() in guard.binary_suffixes:
        return []
    forbidden_lines: list[int] = []
    secret_lines: dict[str, list[int]] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if suffix in guard.text_suffixes and guard.forbidden.search(line):
            forbidden_lines.append(number)
        folded = normalize_for_scan(line)
        for name, pattern in guard.secret_patterns:
            if pattern.search(folded):
                secret_lines.setdefault(name, []).append(number)
    reasons: list[str] = []
    if forbidden_lines:
        reasons.append("a phrase the repo guard forbids (D-140) on " + _lines(forbidden_lines))
    for name, numbers in secret_lines.items():
        reasons.append(f"key-like text ({name}) on {_lines(numbers)}")
    return reasons


def _lines(numbers: list[int]) -> str:
    shown = ", ".join(str(n) for n in numbers[:_MAX_LINES_NAMED])
    more = f" and {len(numbers) - _MAX_LINES_NAMED} more" if len(numbers) > _MAX_LINES_NAMED else ""
    return f"line {shown}{more}" if len(numbers) == 1 else f"lines {shown}{more}"


# --- reading the run ---------------------------------------------------------------------------------------------------


def _load_script(name: str) -> ModuleType:
    return _load_module(f"_spec_export_{name}", SCRIPTS / f"{name}.py")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportError(f"{path.name} cannot be read: {exc}") from exc


def _safe_rel(rel: str) -> str:
    pure = PurePosixPath(rel)
    if not rel or "\\" in rel or pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ExportError(f"the manifest names an unsafe path: {rel!r}")
    return pure.as_posix()


def _read_inside(base: Path, rel: str) -> bytes:
    path = base / rel
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExportError(f"pack file is missing: {rel}") from exc
    if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
        raise ExportError(f"pack file is not a plain file inside the pack: {rel}")
    return resolved.read_bytes()


def resolve_mode(rec: dict[str, Any], requested: str = "auto") -> str:
    """``mock`` or ``live`` from run.json's ``offline``; an explicit request must agree with it."""
    if requested not in MODES:
        raise ExportError(f"mode must be one of {', '.join(MODES)}")
    offline = rec.get("offline")
    detected = "mock" if offline is True else "live" if offline is False else None
    if requested == "auto":
        if detected is None:
            raise ExportError(
                "run.json does not say whether the run was offline: pass --mode mock|live"
            )
        return detected
    if detected is not None and detected != requested:
        raise ExportError(f"run.json says this run was {detected.upper()}, not {requested.upper()}")
    return requested


def gather_files(run_dir: Path, rec: dict[str, Any]) -> dict[str, bytes]:
    """The run's files to save, keyed by their path under ``<out>/<run_id>/``. Only what the manifest lists."""
    pack = run_dir / "pack"
    manifest_path = pack / "MANIFEST.json"
    if not manifest_path.is_file():
        raise ExportError(
            f"there is no pack to save: {manifest_path} is missing (status: {rec.get('status')})"
        )
    manifest = _json(manifest_path)
    listed = manifest.get("files")
    if not isinstance(listed, dict) or not listed:
        raise ExportError("pack/MANIFEST.json lists no files")
    out: dict[str, bytes] = {}
    for rel in sorted(listed):
        safe = _safe_rel(rel)
        data = _read_inside(pack, safe)
        want = (listed[rel] or {}).get("sha256")
        if want and hashlib.sha256(data).hexdigest() != want:
            raise ExportError(
                f"pack/{safe} does not match its hash in MANIFEST.json (changed after assembly)"
            )
        out[f"pack/{safe}"] = data
    out["pack/MANIFEST.json"] = manifest_path.read_bytes()
    for extra in ("index.json", "build/build-plan.json"):
        if (pack / extra).is_file():
            out[f"pack/{extra}"] = _read_inside(pack, extra)
    evidence = run_dir / "evidence"
    if evidence.is_dir():
        for path in sorted(evidence.glob("*.json")):
            out[f"evidence/{path.name}"] = _read_inside(evidence, path.name)
    for name in ("graph.json", "run.json"):
        if (run_dir / name).is_file():
            out[name] = _read_inside(run_dir, name)
    return out


# --- the numbers file and the README -----------------------------------------------------------------------------------


def _usd(micros: int) -> str:
    return f"{(Decimal(micros) / Decimal(1_000_000)).quantize(Decimal('0.0001'))}"


def build_summary(
    rec: dict[str, Any], rep: dict[str, Any], *, mode: str, label: str, skipped: list[Skip]
) -> dict[str, Any]:
    by_kind = {
        kind: {
            "calls": k["calls"],
            "first_try_pass": k["first_try_pass"],
            "repair_rate": k["repair_rate"],
            "repaired": k["repaired"],
            "fallback": k["fallback"],
        }
        for kind, k in sorted(rep["by_kind"].items())
    }
    cost_micros = sum(int(k.get("cost_micros", 0)) for k in rep["by_kind"].values())
    return {
        "schema": SUMMARY_SCHEMA,
        "run_id": rec.get("id") or rep.get("run_id"),
        "input": label,
        "compass_id": rec.get("compass_id"),
        "synth": rec.get("synth"),
        "program_id": rec.get("program_id"),
        "created_at": rec.get("created_at"),
        "updated_at": rec.get("updated_at"),
        "profile": rep.get("profile"),
        "status": rep.get("status"),
        "partial_reason": rep.get("partial_reason"),
        "offline": rec.get("offline"),
        "mode": mode,
        "cost_micros": cost_micros,
        "cost_usd": _usd(cost_micros),
        "run_spend_micros": rec.get("spend_micros"),
        "calls": rep["calls_total"],
        "by_kind": by_kind,
        "pack": rep["pack"],
        "tasks": rep["tasks"],
        "validators": rep["validators"],
        "banners": rep["banners"],
        "not_copied": [{"path": s.path, "reason": s.reason} for s in skipped],
    }


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def build_readme(summary: dict[str, Any], *, dest: Path, root: Path, parts: set[str]) -> str:
    mode = str(summary["mode"])
    live = mode == "live"
    pack = summary["pack"]
    tasks = summary["tasks"]
    val = summary["validators"]
    kinds = summary["by_kind"]
    calls = int(summary["calls"])
    clean = round(sum(k["first_try_pass"] * k["calls"] for k in kinds.values()))
    repaired = sum(k["repaired"] for k in kinds.values())
    fallback = sum(k["fallback"] for k in kinds.values())
    status = str(summary["status"])
    status_text = STATUS_WORDS.get(status, status)
    if summary.get("partial_reason"):
        reason = REASON_WORDS.get(summary["partial_reason"], summary["partial_reason"])
        status_text += f" (reason: {reason})"
    lines = [
        f"# Spec output: {summary['input']} ({mode.upper()})",
        "",
        "This folder is one saved Spec run, copied here so you can read what the engine generates without starting "
        "the app. Spec turns an approved Compass Brief (and a Research note, when there is one) into a folder of "
        "plain files, the Spec Pack. The pack tells the front-end engine what the app is and does. It never says "
        "how the app should look in code.",
        "",
    ]
    if live:
        lines += [
            f"**LIVE.** A real model wrote the text in this pack. The run made {calls} model calls and cost "
            f"USD {summary['cost_usd']}.",
            "",
        ]
    else:
        lines += [
            "**MOCK.** The text in this pack is placeholder text written by code. No model was called and nothing "
            "was spent. Read it for the shape of what Spec makes (the files, the ids, the links between them, the "
            "checks), not for the quality of the writing.",
            "",
        ]
    lines += [
        "| | |",
        "| --- | --- |",
        f"| Input | {summary['input']} |",
        f"| Run id | `{summary['run_id']}` |",
        f"| Mode | {mode.upper()} |",
        f"| Status | {status_text} |",
        f"| Size profile | {summary['profile']} |",
        f"| Model calls | {calls} ({clean} clean first try, {repaired} repaired, {fallback} fell back to the "
        "safe default) |",
        f"| Cost | USD {summary['cost_usd']} |",
        f"| The pack | {pack['files']} files, about {pack['tokens']:,} tokens, content hash `{pack['content_hash']}` |",
        f"| Checked by | {', '.join(pack['checked_by'] or []) or 'nothing recorded'} |",
        f"| Build tasks | {tasks['count']} (the biggest reads {tasks['max_read_tokens']:,} tokens; the budget is "
        f"{(tasks['budget'] or 0):,}) |",
        f"| Started | {summary['created_at']} |",
        "",
        "## Open these first",
        "",
        "1. `pack/INDEX.md`: the map of the whole pack. Start here.",
        "2. `pack/screens/`: one file per screen, what is on it and what each button does.",
        "3. `pack/contract/`: the operations, resources, events and who may do what.",
        "4. `evidence/self-check.json`: what the engine checked on its own work and what it flagged.",
        "5. `pack/build/build-plan.json`: the build tasks the front-end engine would follow, in waves.",
        "",
        "## What the checks said",
        "",
    ]
    ok = val.get("ok")
    sev = val.get("by_severity") or {}
    sev_text = ", ".join(f"{n} {k}" for k, n in sorted(sev.items())) or "none recorded"
    lines.append(
        f"The engine's own validators: {'passed' if ok else 'did NOT pass' if ok is False else 'not recorded'} "
        f"({sev_text})."
    )
    if val.get("by_validator"):
        lines.append(
            "Which validators spoke: "
            + ", ".join(f"{k} ({n})" for k, n in sorted(val["by_validator"].items()))
            + "."
        )
    for banner in summary["banners"]:
        lines.append(f"Banner shown to the user: {banner.get('message') or banner.get('code')}")
    lines += ["", "## What is in this folder", "", "| Path | What it is |", "| --- | --- |"]
    for name, what in PACK_PARTS:
        if name.split("/")[0] in parts:
            lines.append(f"| `pack/{name}` | {what} |")
    lines += [
        "| `evidence/` | the engine's self-check, the Verify result when Verify ran, the Gate 2 summary |",
        "| `graph.json` | the full reference graph the pack was written from (every id and link) |",
        "| `run.json` | the run record: steps done, gates, flags, estimate |",
        "| `report.json` | calls, repairs and cost by kind of call (`scripts/spec_run_report.py`) |",
        "| `summary.json` | the same numbers in one small file |",
    ]
    if summary["not_copied"]:
        lines.append(f"| `{NOTES_FILE}` | files that were not copied, and why |")
    lines += [
        "",
        "Left out on purpose: the run's checkpoints, its event log and its input file. They stay in the run "
        "folder under `agents/spec/.runtime/runs/`, which is not in git.",
        "",
    ]
    if summary["not_copied"]:
        lines += [
            f"**Not complete.** {len(summary['not_copied'])} file(s) were not copied because they failed the "
            f"safety check. See `{NOTES_FILE}`. The check below will not pass on an incomplete pack.",
            "",
        ]
    check_path = _display(dest / "pack", root)
    lines += [
        "## Check it yourself",
        "",
        "```",
        f"uv run python -m mde_spec_core check {check_path}",
        "```",
        "",
        "That runs the pack linter and the spec check (it finds `graph.json` beside the pack). It costs nothing and "
        "calls no model. It prints `RESULT: OK` when nothing blocks.",
        "",
        "The saving script is `scripts/spec_export_outputs.py` (or `python -m mde_spec_agent bench export`). "
        "Everything here is a copy: the pack's bytes are exactly the bytes the engine wrote.",
        "",
    ]
    return "\n".join(lines)


def build_notes(skipped: list[Skip], *, refused: bool) -> str:
    lines = [
        "# Export notes",
        "",
        (
            "This export was refused. No file was copied by this attempt, and anything else in this folder is "
            "from an earlier export."
            if refused
            else "Some files were not copied. The rest of the export is complete."
        ),
        "Nothing was changed to make a file pass: a changed file would break the pack's hashes.",
        "",
    ]
    lines += [f"- not copied: {s.path}, {s.reason}" for s in sorted(skipped, key=lambda s: s.path)]
    return "\n".join(lines) + "\n"


# --- writing -----------------------------------------------------------------------------------------------------------


def _json_text(obj: Any) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _is_export(folder: Path) -> bool:
    """Only an empty folder, an earlier export, or a refusal note may be replaced."""
    if not folder.exists():
        return True
    if not folder.is_dir():
        return False
    names = {p.name for p in folder.iterdir()}
    if not names or names == {NOTES_FILE}:
        return True
    summary = folder / "summary.json"
    try:
        return bool(
            summary.is_file()
            and json.loads(summary.read_text("utf-8")).get("schema") == SUMMARY_SCHEMA
        )
    except (OSError, ValueError, AttributeError):
        return False


def default_out(rec: dict[str, Any], mode: str, root: Path = ROOT) -> Path:
    day = str(rec.get("created_at") or "")[:10] or "undated"
    return root / OUTPUTS_REL / f"{mode}-{day}"


def export_run(
    run_dir: Path,
    out: Path | None = None,
    *,
    label: str | None = None,
    mode: str = "auto",
    allow_skip: bool = False,
    root: Path = ROOT,
    guard: Guard | None = None,
) -> ExportResult:
    run_dir = Path(run_dir)
    if not (run_dir / "run.json").is_file():
        raise ExportError(f"{run_dir} is not a Spec run folder (no run.json)")
    rec = _json(run_dir / "run.json")
    run_id = str(rec.get("id") or run_dir.name)
    if not RUN_ID_RE.match(run_id):
        raise ExportError(f"the run id {run_id!r} is not safe to use as a folder name")
    if rec.get("status") in STILL_GOING:
        raise ExportError(f"the run is not finished (status: {rec.get('status')})")
    resolved_mode = resolve_mode(rec, mode)
    guard = guard or load_guard(root)
    out = Path(out) if out is not None else default_out(rec, resolved_mode, root)
    dest = out / run_id

    files = gather_files(run_dir, rec)
    skipped = [
        Skip(rel, why)
        for rel in sorted(files)
        if (why := "; ".join(scan_file(guard, rel, files[rel])))
    ]
    result = ExportResult(run_id, dest, resolved_mode, rec.get("status"), skipped=skipped)
    if skipped and not allow_skip:
        if _is_export(dest):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / NOTES_FILE).write_text(build_notes(skipped, refused=True), encoding="utf-8")
        result.refused = True
        return result
    for skip in skipped:
        del files[skip.path]

    rep = _load_script("spec_run_report").report(run_dir)
    summary = build_summary(
        rec,
        rep,
        mode=resolved_mode,
        label=label or str(rec.get("compass_id") or rec.get("program_id")),
        skipped=skipped,
    )
    generated = {"report.json": _json_text(rep), "summary.json": _json_text(summary)}
    parts = {
        rel.split("/")[1] for rel in files if rel.startswith("pack/")
    }  # top-level names in pack/
    readme = build_readme(summary, dest=dest, root=root, parts=parts)
    generated["README.md"] = readme
    if skipped:
        generated[NOTES_FILE] = build_notes(skipped, refused=False)
    for rel, text in generated.items():  # what this script writes is held to the same rules
        why = scan_file(guard, rel, text.encode("utf-8"))
        if why:
            raise ExportError(
                f"the generated {rel} fails the safety check ({'; '.join(why)}); nothing was written"
            )

    if not _is_export(dest):
        raise ExportError(f"{dest} exists and is not a Spec export: refusing to replace it")
    out.mkdir(parents=True, exist_ok=True)
    stage = out / f".export-{run_id}.tmp"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        for rel, data in files.items():
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for rel, text in generated.items():
            (stage / rel).write_text(text, encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        stage.rename(dest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    result.files_copied = len(files)
    result.summary = summary
    return result


# --- the parent index --------------------------------------------------------------------------------------------------


def refresh_index(outputs_dir: Path) -> Path:
    """Rewrite ``<outputs_dir>/README.md``: one row per saved run, newest first. Deterministic: same folders, same bytes."""
    rows: list[tuple[str, str, str, dict[str, Any]]] = []
    report_only: list[str] = []
    for folder in sorted(
        p for p in outputs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
    ):
        found = False
        for run in sorted(p for p in folder.iterdir() if p.is_dir()):
            try:
                summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(summary, dict) and summary.get("schema") == SUMMARY_SCHEMA:
                found = True
                rows.append((str(summary.get("created_at") or ""), folder.name, run.name, summary))
        if not found:
            report_only.append(folder.name)
    rows.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    lines = [
        "# Spec outputs",
        "",
        "What the Spec engine generated, saved here so it can be read without the app. Each row is one run, newest "
        "first. MOCK means placeholder text written by code (no model, no cost); LIVE means a real model wrote it. "
        "Open the run's README first; its pack opens at `pack/INDEX.md`.",
        "",
        "This page is written by `scripts/spec_export_outputs.py` (also `python -m mde_spec_agent bench export`). "
        "Do not edit it by hand: the next export rewrites it.",
        "",
        "| Run | Mode | Input | Profile | Status | Calls | USD |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, folder, run_id, s in rows:
        lines.append(
            f"| [`{folder}/{run_id}`]({folder}/{run_id}/README.md) | {str(s.get('mode')).upper()} | {s.get('input')} "
            f"| {s.get('profile')} | {s.get('status')} | {s.get('calls')} | {s.get('cost_usd')} |"
        )
    if not rows:
        lines.append("| none yet | | | | | | |")
    if report_only:
        lines += [
            "",
            "Folders with numbers only (no pack): "
            + ", ".join(f"`{name}`" for name in report_only)
            + ".",
        ]
    path = outputs_dir / INDEX_FILE
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- command line ------------------------------------------------------------------------------------------------------


def format_result(result: ExportResult, root: Path = ROOT) -> list[str]:
    if result.refused:
        lines = [
            f"export refused: {len(result.skipped)} file(s) failed the safety check, nothing was copied"
        ]
    else:
        lines = [
            f"exported {result.run_id} to {_display(result.dest, root)}",
            f"  mode: {result.mode.upper()}, status: {result.status}, files copied: {result.files_copied}",
        ]
    lines += [f"  not copied: {s.path}, {s.reason}" for s in result.skipped]
    if result.refused:
        lines.append(
            f"  see {_display(result.dest / NOTES_FILE, root)}; fix the source or use --allow-skip"
        )
    if result.index is not None:
        lines.append(f"  index refreshed: {_display(result.index, root)}")
    return lines


def export_and_report(
    run_dir: Path,
    out: Path | None = None,
    *,
    label: str | None = None,
    mode: str = "auto",
    allow_skip: bool = False,
    refresh: bool = True,
    root: Path = ROOT,
) -> int:
    """Export, print the plain result, return the exit code: 0 done, 1 a file failed the safety check, 2 bad input."""
    try:
        result = export_run(run_dir, out, label=label, mode=mode, allow_skip=allow_skip, root=root)
        if refresh and not result.refused:
            result.index = refresh_index(result.dest.parent.parent)
    except ExportError as exc:
        sys.stderr.write(f"export failed: {exc}\n")
        return 2
    for line in format_result(result, root):
        sys.stdout.write(line + "\n")
    return 1 if result.refused else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Save a finished Spec run under docs/")
    ap.add_argument("run", type=Path, help="a run folder: agents/spec/.runtime/runs/<run_id>")
    ap.add_argument(
        "--out", type=Path, help="default: docs/spec-agent/outputs/<mode>-<date of the run>"
    )
    ap.add_argument(
        "--label", help="the input's name for the README (default: the run's compass id)"
    )
    ap.add_argument("--mode", choices=MODES, default="auto", help="default: read from run.json")
    ap.add_argument(
        "--allow-skip", action="store_true", help="copy the rest when a file fails the safety check"
    )
    ap.add_argument(
        "--no-index", action="store_true", help="do not rewrite the parent folder's README.md"
    )
    a = ap.parse_args(argv)
    return export_and_report(
        a.run, a.out, label=a.label, mode=a.mode, allow_skip=a.allow_skip, refresh=not a.no_index
    )


if __name__ == "__main__":
    sys.exit(main())
