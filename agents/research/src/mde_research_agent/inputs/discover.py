"""Find, check and identify the inputs folder's export files. Read-only: the app never writes to that folder.

* ``*.json`` to depth 2 on every ``scan()``, cached by ``(path, size, mtime)``, so a drop-in file appears at once and
  an unchanged 4 MB file is parsed only once.
* Identity is the first 12 hex characters of the SHA-256 of the file bytes. The API maps an id to a path HERE; no
  path ever comes from a client. Symlinks that leave the folder are refused, non-UTF-8 files and files over 25 MB
  are skipped with a reason, and the same content under two names is listed once.
* A valid input is a Compass format-2 export whose three hashes agree (RV-13); anything else is listed under
  ``skipped`` with a plain reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.inputs.preview import build_preview
from mde_research_agent.intake import ExportRefused, read_export

MAX_BYTES = 25 * 1024 * 1024
MAX_DEPTH = 2

REASON_TEXT = {
    "not_json": "This file is not JSON.",
    "not_a_compass_export": "This JSON is not a Compass export.",
    "format_not_supported": "Only Compass export format 2 can be researched.",
    "hash_mismatch": "The Brief changed after it was approved (the hashes do not match).",
    "not_approved": "The Brief was never approved (no approved baseline).",
    "too_large": "This file is over 25 MB.",
    "unreadable": "This file could not be read (it may not be UTF-8 text).",
    "unsafe_path": "This file is a link that leaves the inputs folder.",
}
CODE_TO_REASON = {
    "not_an_export": "not_a_compass_export",
    "missing_research_view": "not_a_compass_export",
    "not_format_2": "format_not_supported",
    "not_approved": "not_approved",
    "hash_mismatch": "hash_mismatch",
}


@dataclass(frozen=True)
class SkippedFile:
    name: str
    reason: str
    text: str
    size: int = 0


@dataclass(frozen=True)
class InputEntry:
    id: str
    name: str  # relative to the inputs folder; display only, never accepted back from a client
    path: Path
    size: int
    mtime_ns: int
    modified: str
    preview: dict[str, Any]
    also_named: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "modified": self.modified,
            "short_hash": self.id,
            "also_named": list(self.also_named),
            "preview": self.preview,
        }


@dataclass
class InputsScan:
    folder: str
    folder_exists: bool
    valid: list[InputEntry] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)


_SIZE_ORDER = {"tiny": 0, "small": 1, "medium": 2, "big": 3}


class InputsStore:
    def __init__(
        self, folder: Path | str, catalog: Catalog, estimates: Any, *, uploads: Path | None = None
    ) -> None:
        self.folder = Path(folder)
        self.uploads = Path(uploads) if uploads else None
        self.catalog = catalog
        self.estimates = estimates
        self._cache: dict[tuple[str, int, int], tuple[InputEntry | None, SkippedFile | None]] = {}
        self._by_id: dict[str, InputEntry] = {}

    # --- scanning ----------------------------------------------------------------------------------------
    def _candidates(self, root: Path, *, display_root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        found: list[Path] = []
        for p in sorted(root.rglob("*.json")):
            try:
                depth = len(p.relative_to(root).parts)
            except ValueError:
                continue
            if depth <= MAX_DEPTH:
                found.append(p)
        return found

    def scan(self) -> InputsScan:
        result = InputsScan(folder=str(self.folder), folder_exists=self.folder.is_dir())
        roots: list[tuple[Path, str]] = [(self.folder, "")]
        if self.uploads is not None and self.uploads.is_dir():
            roots.append((self.uploads, "uploads/"))
        seen: dict[str, InputEntry] = {}
        by_id: dict[str, InputEntry] = {}
        live_keys: set[tuple[str, int, int]] = set()
        for root, prefix in roots:
            real_root = root.resolve()
            for path in self._candidates(root, display_root=root):
                name = prefix + path.relative_to(root).as_posix()
                try:
                    real = path.resolve()
                    if not real.is_relative_to(real_root):
                        result.skipped.append(
                            SkippedFile(name, "unsafe_path", REASON_TEXT["unsafe_path"])
                        )
                        continue
                    st = real.stat()
                except OSError:
                    result.skipped.append(
                        SkippedFile(name, "unreadable", REASON_TEXT["unreadable"])
                    )
                    continue
                key = (str(real), st.st_size, st.st_mtime_ns)
                live_keys.add(key)
                if key not in self._cache:
                    self._cache[key] = self._classify(real, name, st.st_size, st.st_mtime_ns)
                entry, skipped = self._cache[key]
                if skipped is not None:
                    result.skipped.append(
                        SkippedFile(name, skipped.reason, skipped.text, skipped.size)
                    )
                    continue
                assert entry is not None
                if entry.id in seen:
                    first = seen[entry.id]
                    seen[entry.id] = InputEntry(
                        **{**first.__dict__, "also_named": (*first.also_named, name)}
                    )
                    by_id[entry.id] = seen[entry.id]
                    continue
                shown = InputEntry(**{**entry.__dict__, "name": name})
                seen[entry.id] = shown
                by_id[entry.id] = shown
        for stale in set(self._cache) - live_keys:
            del self._cache[stale]
        self._by_id = by_id
        result.valid = sorted(
            by_id.values(),
            key=lambda e: (
                _SIZE_ORDER.get(str(e.preview.get("size_class", "")), 9),
                e.preview.get("load", {}).get("score", 0),
                e.name,
            ),
        )
        return result

    def _classify(
        self, real: Path, name: str, size: int, mtime_ns: int
    ) -> tuple[InputEntry | None, SkippedFile | None]:
        def skip(reason: str) -> tuple[None, SkippedFile]:
            return None, SkippedFile(name, reason, REASON_TEXT[reason], size)

        if size > MAX_BYTES:
            return skip("too_large")
        try:
            raw = real.read_bytes()
        except OSError:
            return skip("unreadable")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return skip("unreadable")
        try:
            data = json.loads(text)
        except ValueError:
            return skip("not_json")
        if not isinstance(data, dict):
            return skip("not_a_compass_export")
        try:
            inp = read_export(data, self.catalog)
        except ExportRefused as exc:
            return skip(CODE_TO_REASON.get(exc.code, "not_a_compass_export"))
        except Exception:  # an odd file must be a reason, never a crash
            return skip("not_a_compass_export")
        digest = hashlib.sha256(raw).hexdigest()[:12]
        from datetime import UTC, datetime

        modified = datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC).isoformat()
        preview = build_preview(data, inp, self.catalog, self.estimates)
        return InputEntry(digest, name, real, size, mtime_ns, modified, preview), None

    # --- lookups (the client never supplies a path) -----------------------------------------------------------
    def entry(self, input_id: str) -> InputEntry | None:
        if input_id not in self._by_id:
            self.scan()
        return self._by_id.get(input_id)

    def load_export(self, input_id: str) -> tuple[InputEntry, dict[str, Any], bytes] | None:
        """``(entry, export, raw bytes)`` read fresh from disk, or None. The bytes go into the run's folder."""
        e = self.entry(input_id)
        if e is None:
            return None
        try:
            raw = e.path.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(raw).hexdigest()[:12] != e.id:
            self._cache.clear()  # changed since it was listed: the caller refreshes the list
            return None
        data = json.loads(raw.decode("utf-8"))
        return e, data, raw
