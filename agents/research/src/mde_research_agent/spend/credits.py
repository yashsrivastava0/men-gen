"""``provider-credits.json``: the global ledger of search-provider credits (schema ``research.credits.v1``).

Credits are a second budget next to USD (D-158): a provider counts credits, not dollars, and a free allowance can run
out while the USD budget still has room. This ledger is ADVISORY: it feeds a warning in the console and a note in the
preflight, and it never blocks a run.

It is **global**: one file in the data folder, keyed by provider name plus a one-way key fingerprint (the first 12 hex
characters of the SHA-256 of the key; the key itself is never stored or logged). It is NOT keyed by run, tenant,
project or user, and it lives OUTSIDE the budget periods, so ``Restart budget`` never touches it. Money-free: integer
credits only.

The shape::

    {"schema": "research.credits.v1",
     "providers": {"tavily": {
        "key_fingerprint": "ab12cd34ef56" | null,
        "baseline": {"kind": "used" | "remaining", "amount": 93, "limit": 1000 | null, "as_of": "<UTC iso>"} | null,
        "counted_since_baseline": 0,        # credits this app booked after the baseline was entered
        "counted_since_reading": 0,         # credits this app booked after the provider's last reading
        "counted_outside_runs": 0,          # booked by a ping or a bake-off (no run)
        "last_counted_at": "<UTC iso>" | null,
        "last_reading": {"used", "limit", "plan", "read_at", "source", "scope"} | null,
        "runs": [{"run_id", "credits", "at"}],   # the last 50 runs, newest last
        "previous_keys": [...],             # retired fingerprints (a key change starts a fresh account)
        "key_changed_at": "<UTC iso>" | null}}}

How a figure is derived (``derive_view``): the NEWEST anchor wins, a provider reading or the owner's baseline. With a
reading and nothing counted since, the provider's own number is shown (``source: "provider"``). Otherwise the anchor
plus what this app counted since is shown (``source: "counted"``). A baseline of kind ``used`` (Tavily: "93 of 1,000")
gives ``used = amount + counted``; one of kind ``remaining`` (Serper: "2,389 left") gives
``remaining = amount - counted``. ``unverified_endpoint`` is reserved for a figure read from an endpoint the provider
does not document; nothing produces it today.

Writes are atomic (temp file, fsync, ``os.replace``) under a thread lock AND a cross-process file lock (``fcntl.flock``
on POSIX; elsewhere the thread lock is all there is). Every mutation re-reads the file under the lock, so the app and
the ``credits set`` command can write at the same time without losing a count. A failed write never breaks a search:
``write_failed`` is set and the in-memory figures stay.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mde_core.clock import Clock, SystemClock

try:  # POSIX only; elsewhere the thread lock is the only lock
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - not POSIX
    _fcntl = None  # type: ignore[assignment]

SCHEMA = "research.credits.v1"
FILE_NAME = "provider-credits.json"
PROVIDERS = ("tavily", "serper")
LABELS = {"tavily": "Tavily", "serper": "Serper"}
KINDS = ("used", "remaining")
SOURCES = ("provider", "counted", "unverified_endpoint")
KEEP_RUNS = 50
KEEP_PREVIOUS_KEYS = 5
AMBER_SHARE = 0.80  # amber at 80% of the allowance used
RED_SHARE = 0.95  # red at 95%
LOW_RUNS = 3  # "low remaining" = fewer credits left than this many high-estimate runs
FINGERPRINT_LEN = 12


# --- the run that is booking credits ------------------------------------------------------------------------------------
# A run is one asyncio task, and a task works on a COPY of its context, so setting this inside the run's task never
# leaks to another run. Outside a run (a ping, a bake-off) it is None and the credits are counted "outside runs".
_RUN: ContextVar[str | None] = ContextVar("research_credit_run", default=None)


def current_run_id() -> str | None:
    """The run whose searches are being booked right now, or None."""
    return _RUN.get()


def set_current_run(run_id: str | None) -> Token[str | None]:
    return _RUN.set(run_id)


def key_fingerprint(key: str) -> str:
    """The one-way fingerprint of an API key: the first 12 hex characters of its SHA-256. The key is never kept."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:FINGERPRINT_LEN]


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _need_count(value: object, what: str) -> int:
    if not _is_count(value):
        raise ValueError(f"{what} must be a whole number, 0 or more")
    return int(value)  # type: ignore[arg-type]


def _blank(fingerprint: str | None) -> dict[str, Any]:
    return {
        "key_fingerprint": fingerprint,
        "baseline": None,
        "counted_since_baseline": 0,
        "counted_since_reading": 0,
        "counted_outside_runs": 0,
        "last_counted_at": None,
        "last_reading": None,
        "runs": [],
        "previous_keys": [],
        "key_changed_at": None,
    }


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "providers": {}}


def _iso(value: datetime | str | None, default: datetime) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    if isinstance(value, str) and value:
        return value
    return default.isoformat()


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class CreditLedger:
    """The global credit ledger. Thread-safe and safe across processes; one instance per file is enough."""

    def __init__(
        self, path: Path | str, *, clock: Clock | None = None, keep_runs: int = KEEP_RUNS
    ) -> None:
        self.path = Path(path)
        self._clock = clock or SystemClock()
        self._keep_runs = max(1, keep_runs)
        self._lock = threading.RLock()
        self.write_failed = False
        self._data: dict[str, Any] = _empty()
        with self._lock:
            self._load()

    # --- persistence ------------------------------------------------------------------------------------------------
    def _load(self) -> None:
        """Read the file (the newest truth). A missing file keeps what is in memory; a damaged one is set aside."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            loaded = json.loads(text)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict) and loaded.get("schema") == SCHEMA:
            providers = loaded.get("providers")
            loaded["providers"] = providers if isinstance(providers, dict) else {}
            self._data = loaded
            return
        with contextlib.suppress(
            OSError
        ):  # unreadable or foreign: keep it for a human, start again
            os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
        self._data = _empty()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            self.write_failed = False
        except OSError:
            self.write_failed = True

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Thread lock, then the cross-process file lock, then a fresh read: a read-modify-write that cannot lose."""
        with self._lock:
            fd: int | None = None
            if _fcntl is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(f"{self.path}.lock", os.O_CREAT | os.O_RDWR, 0o600)
                    _fcntl.flock(fd, _fcntl.LOCK_EX)
                except OSError:  # no lock file possible: the thread lock still holds
                    if fd is not None:
                        os.close(fd)
                    fd = None
            try:
                self._load()
                yield
            finally:
                if fd is not None and _fcntl is not None:
                    with contextlib.suppress(OSError):
                        _fcntl.flock(fd, _fcntl.LOCK_UN)
                    with contextlib.suppress(OSError):
                        os.close(fd)

    # --- the records ------------------------------------------------------------------------------------------------
    def _record(self, provider: str, fingerprint: str | None) -> dict[str, Any]:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider!r}")
        providers: dict[str, Any] = self._data["providers"]
        rec = providers.get(provider)
        if not isinstance(rec, dict):
            rec = _blank(fingerprint)
            providers[provider] = rec
            return rec
        for key, default in _blank(None).items():
            rec.setdefault(key, default)
        if not fingerprint or rec["key_fingerprint"] == fingerprint:
            return rec
        if (
            rec["key_fingerprint"] is None
        ):  # a baseline entered before any key was seen: it belongs to this key
            rec["key_fingerprint"] = fingerprint
            return rec
        # A different key is a different account with its own allowance: retire the old figures, start fresh.
        fresh = _blank(fingerprint)
        fresh["runs"] = rec.get("runs", [])
        fresh["key_changed_at"] = self._clock.now().isoformat()
        fresh["previous_keys"] = [
            *rec.get("previous_keys", []),
            {
                "key_fingerprint": rec["key_fingerprint"],
                "retired_at": fresh["key_changed_at"],
                "baseline": rec.get("baseline"),
                "counted_since_baseline": rec.get("counted_since_baseline", 0),
            },
        ][-KEEP_PREVIOUS_KEYS:]
        providers[provider] = fresh
        return fresh

    # --- writing ----------------------------------------------------------------------------------------------------
    def set_baseline(
        self,
        provider: str,
        kind: str,
        amount: int,
        limit: int | None = None,
        *,
        as_of: datetime | str | None = None,
        fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """The owner's reading of the balance: ``used`` (Tavily "93 of 1,000") or ``remaining`` (Serper "2,389 left").

        Counting starts again from this moment. Returns the new derived view of the provider.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        amount = _need_count(amount, "amount")
        if limit is not None:
            limit = _need_count(limit, "limit")
            if kind == "used" and amount > limit:
                raise ValueError("the used amount is more than the limit")
            if kind == "remaining" and amount > limit:
                raise ValueError("the remaining amount is more than the limit")
        with self._locked():
            rec = self._record(provider, fingerprint)
            rec["baseline"] = {
                "kind": kind,
                "amount": amount,
                "limit": limit,
                "as_of": _iso(as_of, self._clock.now()),
            }
            rec["counted_since_baseline"] = 0
            rec["counted_since_reading"] = 0
            self._save()
            return derive_view(rec)

    def add_credits(
        self, provider: str, n: int, run_id: str | None = None, *, fingerprint: str | None = None
    ) -> None:
        """Book ``n`` credits a provider billed for one call, to the run that made it (None: outside runs)."""
        n = _need_count(n, "credits")
        if n == 0:
            return
        with self._locked():
            now = self._clock.now().isoformat()
            rec = self._record(provider, fingerprint)
            rec["counted_since_baseline"] += n
            rec["counted_since_reading"] += n
            rec["last_counted_at"] = now
            if run_id:
                runs: list[dict[str, Any]] = rec["runs"]
                row = next((r for r in reversed(runs) if r.get("run_id") == run_id), None)
                if row is None:
                    runs.append({"run_id": run_id, "credits": n, "at": now})
                else:
                    row["credits"] = int(row.get("credits", 0)) + n
                    row["at"] = now
                del runs[: -self._keep_runs]
            else:
                rec["counted_outside_runs"] += n
            self._save()

    def record_reading(
        self, provider: str, reading: Mapping[str, Any], *, fingerprint: str | None = None
    ) -> dict[str, Any]:
        """Store what the provider itself said: ``{used, limit, plan, read_at, source, scope}``.

        ``used`` is required (a whole number). Counting since the reading starts again from 0.
        """
        used = _need_count(reading.get("used"), "used")
        limit_raw = reading.get("limit")
        limit = _need_count(limit_raw, "limit") if limit_raw is not None else None
        source = str(reading.get("source") or "provider")
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        plan = reading.get("plan")
        with self._locked():
            rec = self._record(provider, fingerprint)
            rec["last_reading"] = {
                "used": used,
                "limit": limit,
                "plan": str(plan)[:60] if isinstance(plan, str) and plan else None,
                "read_at": _iso(reading.get("read_at"), self._clock.now()),
                "source": source,
                "scope": reading.get("scope")
                if reading.get("scope") in ("account", "key")
                else None,
            }
            rec["counted_since_reading"] = 0
            self._save()
            return derive_view(rec)

    # --- reading ----------------------------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """The whole ledger (a copy) with the derived ``view`` of every known provider added."""
        with self._lock:
            self._load()
            data = json.loads(json.dumps(self._data))
        out: dict[str, Any] = {"schema": SCHEMA, "providers": {}, "write_failed": self.write_failed}
        for name in PROVIDERS:
            rec = data["providers"].get(name)
            rec = rec if isinstance(rec, dict) else _blank(None)
            for key, default in _blank(None).items():
                rec.setdefault(key, default)
            rec["view"] = derive_view(rec)
            out["providers"][name] = rec
        return out

    def run_credits(self, run_id: str) -> dict[str, int]:
        """What one run booked, per provider (only providers that booked something)."""
        snap = self.snapshot()
        out: dict[str, int] = {}
        for name, rec in snap["providers"].items():
            total = sum(int(r.get("credits", 0)) for r in rec["runs"] if r.get("run_id") == run_id)
            if total:
                out[name] = total
        return out


# --- derived figures (pure) ---------------------------------------------------------------------------------------------
def derive_view(rec: Mapping[str, Any]) -> dict[str, Any]:
    """``{used, limit, remaining, source, as_of, read_at, plan}`` of one provider record. See the module docstring."""
    baseline = rec.get("baseline") if isinstance(rec.get("baseline"), dict) else None
    reading = rec.get("last_reading") if isinstance(rec.get("last_reading"), dict) else None
    counted_b = int(rec.get("counted_since_baseline") or 0)
    counted_r = int(rec.get("counted_since_reading") or 0)
    view: dict[str, Any] = {
        "used": None,
        "limit": None,
        "remaining": None,
        "source": "counted",
        "as_of": None,
        "read_at": reading.get("read_at") if reading else None,
        "plan": reading.get("plan") if reading else None,
        "counted_since_anchor": counted_b,
    }
    t_reading = _parse(reading.get("read_at")) if reading else None
    t_base = _parse(baseline.get("as_of")) if baseline else None
    use_reading = reading is not None and _is_count(reading.get("used"))
    if use_reading and baseline is not None and t_reading is not None and t_base is not None:
        use_reading = t_reading >= t_base  # the newest anchor wins; a tie goes to the provider
    if use_reading and reading is not None:
        view["used"] = int(reading["used"]) + counted_r
        view["limit"] = reading.get("limit") if _is_count(reading.get("limit")) else None
        if view["limit"] is None and baseline is not None:
            view["limit"] = baseline.get("limit")
        if counted_r > 0:
            view["source"] = "counted"  # the provider's figure plus what this app booked after it
        elif reading.get("source") == "unverified_endpoint":
            view["source"] = "unverified_endpoint"
        else:
            view["source"] = "provider"
        view["as_of"] = reading.get("read_at")
        view["counted_since_anchor"] = counted_r
    elif baseline is not None:
        view["as_of"] = baseline.get("as_of")
        view["limit"] = baseline.get("limit")
        amount = int(baseline["amount"])
        if baseline.get("kind") == "used":
            view["used"] = amount + counted_b
        elif view["limit"] is not None:
            view["used"] = max(0, int(view["limit"]) - amount) + counted_b
        else:
            view["remaining"] = max(0, amount - counted_b)
    elif counted_b:
        view["used"] = counted_b  # no balance was ever entered: only what this app has counted
        view["as_of"] = rec.get("last_counted_at")
    if view["remaining"] is None and view["used"] is not None and view["limit"] is not None:
        view["remaining"] = max(0, int(view["limit"]) - int(view["used"]))
    return view


def level_of(view: Mapping[str, Any], need_high: int | None = None) -> str:
    """``ok`` | ``amber`` | ``red`` | ``unknown``.

    Red: 95% of the allowance used, or fewer credits left than one run's high estimate. Amber: 80% used, or fewer left
    than three runs' high estimates. ``unknown`` when there is no figure at all.
    """
    used, limit, remaining = view.get("used"), view.get("limit"), view.get("remaining")
    if used is None and remaining is None:
        return "unknown"
    level = "ok"
    if isinstance(used, int) and isinstance(limit, int) and limit > 0:
        share = used / limit
        if share >= RED_SHARE:
            level = "red"
        elif share >= AMBER_SHARE:
            level = "amber"
    if isinstance(remaining, int):
        if remaining <= 0:
            level = "red"
        elif need_high:
            if remaining < need_high:
                level = "red"
            elif remaining < LOW_RUNS * need_high and level == "ok":
                level = "amber"
    return level


def _day(value: object) -> str:
    return value[:10] if isinstance(value, str) else ""


def _note(
    name: str, view: Mapping[str, Any], level: str, need_high: int | None, rec: Mapping[str, Any]
) -> str:
    label = LABELS.get(name, name)
    parts: list[str] = []
    changed = rec.get("key_changed_at")
    if view["used"] is None and view["remaining"] is None:
        parts.append(
            f"No count yet for {label}. Enter what your {label} account shows (below), "
            + (
                "or let the app ask Tavily."
                if name == "tavily"
                else "and the app counts from there."
            )
        )
    elif view["source"] == "provider":
        parts.append(f"{label} itself reported this figure.")
    elif name == "tavily":
        parts.append("Counted by this app: Tavily has not been asked since, or did not answer.")
    else:
        parts.append(
            f"{label} does not report its balance, so the app counts: the figure you entered minus what it has booked since."
        )
    if changed:
        parts.append(
            f"A different {label} key has been in use since {_day(changed)}; enter its balance again."
        )
    left = view.get("remaining")
    if level in ("amber", "red") and isinstance(left, int):
        if need_high and left < need_high:
            parts.append(
                f"Only {left} left; a Small run can need up to {need_high}, so a run may stop early (partial)."
            )
        elif need_high:
            parts.append(f"{left} left; a Small run can need up to {need_high}.")
        else:
            parts.append(f"Only {left} left.")
    return " ".join(parts)


def describe(
    snapshot: Mapping[str, Any],
    *,
    need_high: Mapping[str, int] | None = None,
    keys: Mapping[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """The providers as the console shows them (labels, level, plain note). Credits only; advisory."""
    out: list[dict[str, Any]] = []
    for name in PROVIDERS:
        rec = snapshot["providers"][name]
        view = rec["view"]
        need = (need_high or {}).get(name)
        level = level_of(view, need)
        baseline = rec.get("baseline")
        out.append(
            {
                "provider": name,
                "label": LABELS[name],
                "unit": "credit",
                "used": view["used"],
                "limit": view["limit"],
                "remaining": view["remaining"],
                "level": level,
                "source": view["source"],
                "as_of": view["as_of"],
                "read_at": view["read_at"],
                "plan": view["plan"],
                "note": _note(name, view, level, need, rec),
                "key_present": bool((keys or {}).get(name, True)),
                "key_fingerprint": rec.get("key_fingerprint"),
                "key_changed_at": rec.get("key_changed_at"),
                "can_check": name == "tavily",
                "baseline": dict(baseline) if isinstance(baseline, dict) else None,
            }
        )
    return out


def per_run(snapshot: Mapping[str, Any], limit: int = KEEP_RUNS) -> list[dict[str, Any]]:
    """Credits per run across providers, newest first: ``{run_id, at, credits: {provider: n}, total}``."""
    runs: dict[str, dict[str, Any]] = {}
    for name in PROVIDERS:
        for row in snapshot["providers"][name].get("runs", []):
            rid = str(row.get("run_id"))
            entry = runs.setdefault(
                rid, {"run_id": rid, "at": row.get("at"), "credits": {}, "total": 0}
            )
            entry["credits"][name] = int(row.get("credits", 0))
            entry["total"] += int(row.get("credits", 0))
            if (row.get("at") or "") > (entry["at"] or ""):
                entry["at"] = row.get("at")
    ordered = sorted(runs.values(), key=lambda r: (r["at"] or "", r["run_id"]), reverse=True)
    return ordered[:limit]
