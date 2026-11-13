"""Discover what the Spec Bench can run on: Compass exports and Research runs (doc 19 4.3).

Both discoveries read files only and never raise on a bad file: a file that cannot be used is listed with a plain
reason. Research is read as plain JSON (``runs/<run_id>/{run.json, research.json}``); no Research code is imported.

What Research records, and how a Compass export is matched to it: ``run.json`` holds ``export_sha256`` (the canonical
JSON hash of the PARSED export, ``mde_core.hashing.hash_json``, not the hash of the file's bytes), ``program_id`` (the
Brief id), ``brief_hash`` and ``brief_version``. ``CompassEntry.export_hash`` is the same canonical hash, so equal hashes
mean the same export.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from mde_compass_intake import SpecInput, intake
from mde_core.hashing import hash_json

Profile = Literal["compact", "standard", "deep"]
MAX_FILE_BYTES = 8 * 1024 * 1024  # the size limit Research puts on an export
USABLE_STATUSES = ("complete", "partial", "skipped")  # a finished run that left a result behind
SYNTH_PREFIX = "synth:"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadinessView(_Model):
    ready: bool
    missing: tuple[str, ...] = ()


class CompassEntry(_Model):
    id: str  # the file name without ``.json``: stable across runs
    path: str
    sha256: str  # of the file's bytes
    export_hash: (
        str  # canonical hash of the parsed export: what Research records as ``export_sha256``
    )
    format: (
        int | None
    )  # the Compass format the reader recognised (1, 2, a trimmed one reads as the closest)
    tier: str  # quick | standard | thorough: the interview budget the person chose
    size_band: str  # small | medium | large
    size_band_source: str  # tier | structure
    profile: Profile  # the derived detail profile
    profile_source: Literal["derive", "fallback"]
    readiness: ReadinessView
    warnings: tuple[str, ...] = ()
    drift_flags: tuple[str, ...] = ()
    brief_id: str = ""  # the program id (what Research calls ``program_id``)
    brief_hash: str = ""
    brief_version: int = 0
    features: int = 0  # in-scope features
    screens: int = 0  # screens in Compass's structure proposal
    weight: int = 0  # features + screens: "smallest" means smallest weight
    fixture: bool = (
        False  # a hand-made file (its name starts with ``synthetic-``), not a real interview
    )
    idea: str = ""


class SkippedFile(_Model):
    path: str
    reason: str  # plain words


class CompassDiscovery(_Model):
    entries: tuple[CompassEntry, ...] = ()
    skipped: tuple[SkippedFile, ...] = ()


class ResearchEntry(_Model):
    id: str  # the run id (the folder name)
    path: str  # the run folder
    status: str  # complete | partial | failed, or what run.json says (skipped, interrupted, running, ...)
    input_hash: str = ""  # run.json ``export_sha256``
    brief_id: str = ""  # run.json ``program_id``
    brief_hash: str = ""
    brief_version: int = 0
    created_at: str = ""
    has_research: bool = False  # research.json exists next to run.json
    research_path: str = ""  # the file to hand to Spec ("" when it does not exist)
    usable: bool = False  # a finished run (complete, partial, skipped) whose research.json exists
    note: str = ""  # why the entry is odd (missing run.json, unreadable file)


# --- Research --------------------------------------------------------------------------------------------------------


def default_research_runs_dir() -> Path:
    """Where Research keeps its runs: ``$RESEARCH_DATA_DIR/runs`` or ``agents/research/.runtime/runs``."""
    env = os.environ.get("RESEARCH_DATA_DIR", "").strip()
    if env:
        return Path(env) / "runs"
    from mde_spec_agent.config import repo_root

    return repo_root() / "agents" / "research" / ".runtime" / "runs"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _str(v: object) -> str:
    return v if isinstance(v, str) else ""


def _int(v: object) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _research_entry(folder: Path) -> ResearchEntry | None:
    run = _read_json(folder / "run.json")
    research_file = folder / "research.json"
    has_research = research_file.is_file()
    result = _read_json(research_file) if has_research else None
    if run is None and result is None:
        return None  # not a run folder, or nothing readable in it
    note = ""
    if run is None:
        note = "run.json is missing or unreadable; read from research.json only"
        run = {}
    elif has_research and result is None:
        note = "research.json exists but cannot be read"
    result = result or {}
    status = _str(run.get("status")) or _str(result.get("status")) or "unknown"
    readable_result = has_research and bool(result)
    return ResearchEntry(
        id=_str(run.get("run_id")) or folder.name,
        path=str(folder),
        status=status,
        input_hash=_str(run.get("export_sha256")),
        brief_id=_str(run.get("program_id")) or _str(result.get("program_id")),
        brief_hash=_str(run.get("brief_hash")) or _str(result.get("brief_hash")),
        brief_version=_int(run.get("brief_version")),
        created_at=_str(run.get("created_at")),
        has_research=has_research,
        research_path=str(research_file) if has_research else "",
        usable=status in USABLE_STATUSES and readable_result,
        note=note,
    )


def discover_research(runs_dir: Path | str | None = None) -> list[ResearchEntry]:
    """Every Research run folder under ``runs_dir``, oldest first. A missing folder or file is not an error."""
    root = Path(runs_dir) if runs_dir is not None else default_research_runs_dir()
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    entries = [e for f in folders if (e := _research_entry(f)) is not None]
    return sorted(entries, key=lambda e: (parse_time(e.created_at), e.id))


def parse_time(value: str) -> datetime:
    """A run's ``created_at`` as an aware UTC time; an unreadable or missing one sorts as the oldest."""
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


# --- Compass ---------------------------------------------------------------------------------------------------------

_PROFILE_ORDER: tuple[Profile, ...] = ("compact", "standard", "deep")
_TIER_PROFILE: dict[str, Profile] = {"quick": "compact", "standard": "standard", "thorough": "deep"}


def _fallback_profile(si: SpecInput) -> Profile:
    """Used only while ``mde_spec_agent.derive.profile`` does not exist: the tier picks the profile, a large size band
    lifts a standard one to deep, a small band lowers a standard one to compact."""
    base = _TIER_PROFILE.get(si.tier, "standard")
    if base == "standard" and si.size_band == "large":
        return "deep"
    if base == "standard" and si.size_band == "small":
        return "compact"
    return base


def detail_profile_of(si: SpecInput) -> tuple[Profile, Literal["derive", "fallback"]]:
    """The derived detail profile: ``derive.profile.detail_profile`` when it exists and answers, else the local rule."""
    try:
        from mde_spec_agent.derive.profile import detail_profile  # type: ignore[import-not-found]

        got = detail_profile(si)
        got = getattr(got, "value", got)
    except Exception:  # absent module or a signature we do not know: the local rule answers
        got = None
    if isinstance(got, str) and got in _PROFILE_ORDER:
        return got, "derive"  # type: ignore[return-value]
    return _fallback_profile(si), "fallback"


def _skip_reason(code: str | None) -> str:
    if code == "not_an_export":
        return "not a Compass export: it has no Brief in it"
    return "the Brief has no idea statement and no features to build from"


def _classify(path: Path, raw: bytes, data: dict[str, Any]) -> tuple[CompassEntry | None, str]:
    got = intake(data)
    if got.spec_input is None:
        return None, _skip_reason(got.refused)
    si = got.spec_input
    profile, source = detail_profile_of(si)
    features = len(si.in_scope_features())
    screens = len(si.structure.screens)
    return (
        CompassEntry(
            id=path.stem,
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            export_hash=hash_json(data),
            format=si.compass_format,
            tier=si.tier,
            size_band=si.size_band,
            size_band_source=si.size_band_source,
            profile=profile,
            profile_source=source,
            readiness=ReadinessView(ready=got.readiness.ready, missing=got.readiness.missing),
            warnings=got.readiness.warnings,
            drift_flags=si.drift_flags,
            brief_id=si.program_id,
            brief_hash=si.brief_hash,
            brief_version=si.brief_version,
            features=features,
            screens=screens,
            weight=features + screens,
            fixture=path.stem.startswith("synthetic-"),
            idea=si.idea.summary,
        ),
        "",
    )


def discover_compass_report(dirs: list[Path] | tuple[Path, ...]) -> CompassDiscovery:
    """Every ``*.json`` directly inside each directory, classified; files that cannot be used are listed with a reason.

    A file whose name was already listed from an earlier directory is skipped (ids stay unique and stable)."""
    entries: list[CompassEntry] = []
    skipped: list[SkippedFile] = []
    seen: dict[str, str] = {}
    for directory in dirs:
        try:
            files = sorted(p for p in Path(directory).glob("*.json") if p.is_file())
        except OSError:
            continue
        for path in files:
            if path.stem in seen:
                skipped.append(
                    SkippedFile(
                        path=str(path),
                        reason=f"same name as {seen[path.stem]}, which is already listed",
                    )
                )
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    skipped.append(SkippedFile(path=str(path), reason="larger than 8 MB"))
                    continue
                raw = path.read_bytes()
            except OSError as exc:
                skipped.append(
                    SkippedFile(path=str(path), reason=f"cannot be read ({exc.__class__.__name__})")
                )
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                skipped.append(SkippedFile(path=str(path), reason="not valid JSON"))
                continue
            if not isinstance(data, dict):
                skipped.append(
                    SkippedFile(path=str(path), reason="JSON, but not an object: no Brief")
                )
                continue
            try:
                entry, reason = _classify(path, raw, data)
            except Exception as exc:  # a hostile file must never crash the listing
                entry, reason = None, f"could not be read as an export ({exc.__class__.__name__})"
            if entry is None:
                skipped.append(SkippedFile(path=str(path), reason=reason))
                continue
            seen[path.stem] = str(path)
            entries.append(entry)
    return CompassDiscovery(entries=tuple(entries), skipped=tuple(skipped))


def discover_compass(dirs: list[Path] | tuple[Path, ...]) -> list[CompassEntry]:
    """The usable exports only (see ``discover_compass_report`` for the skipped files)."""
    return list(discover_compass_report(dirs).entries)


def default_dirs() -> list[Path]:
    from mde_spec_agent.config import default_input_dirs

    return default_input_dirs()


# --- resolving what the owner typed ----------------------------------------------------------------------------------


def synth_name(token: str) -> str | None:
    """``synth:program`` -> ``program``; anything else -> None."""
    return token[len(SYNTH_PREFIX) :] if token.startswith(SYNTH_PREFIX) else None


def resolve_compass(token: str, entries: list[CompassEntry]) -> CompassEntry | Path | str | None:
    """An id (filename stem) -> its entry; an existing file path -> the Path; ``synth:name`` -> the token; else None."""
    if synth_name(token) is not None:
        return token
    for e in entries:
        if e.id == token:
            return e
    p = Path(token).expanduser()
    if p.is_file():
        return p
    return None


def entry_for_path(path: Path, entries: list[CompassEntry]) -> CompassEntry | None:
    """The discovered entry of a file given by path (by resolved path, else None)."""
    want = path.resolve()
    for e in entries:
        if Path(e.path).resolve() == want:
            return e
    return None


def classify_file(path: Path) -> tuple[CompassEntry | None, str]:
    """Classify one file given by path (for ``--input`` outside the default folders)."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        return None, "cannot be read as JSON"
    if not isinstance(data, dict):
        return None, "JSON, but not an object: no Brief"
    return _classify(path, raw, data)


__all__ = [
    "CompassDiscovery",
    "CompassEntry",
    "ReadinessView",
    "ResearchEntry",
    "SkippedFile",
    "classify_file",
    "default_dirs",
    "default_research_runs_dir",
    "detail_profile_of",
    "discover_compass",
    "discover_compass_report",
    "discover_research",
    "entry_for_path",
    "parse_time",
    "resolve_compass",
    "synth_name",
]
