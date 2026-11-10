"""Budget guard (brief 7.9): the USD 6.00 rule. A bug in this file is severe.

``BudgetGuard.reserve`` runs inside the ledger's critical section, so the check
``spent + reserved + worst_case <= effective_cap`` (global AND every scoped cap) and the append of
the reserve line are one atomic step across processes.

Money here is always integer micro-dollars.

Hard limits that no configuration or caller can lift (a bug or a hostile caller must not be able to
spend past USD 6.00):

* ``HARD_CAP_USD`` clamps ``LLM_BUDGET_USD`` (a warning is logged); the effective cap is at most
  ``HARD_EFFECTIVE_CAP_USD`` (USD 5.70) whatever the budget file says.
* ``reserve`` refuses a scope whose kind is not one of ``session``, ``research``, ``eval``, ``test``
  (or a kind added through ``budget.yaml``): an unknown kind used to get no scope cap at all.
* A reservation that is not closed is never "released" by the passage of time: see ``ledger.py``.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from importlib import resources
from pathlib import Path
from typing import Literal, cast

import yaml

from mde_contracts.gateway import BudgetStatus, CallMode, Usage
from mde_core.clock import Clock
from mde_core.errors import BudgetExceeded, ConfigError, UnknownModelPrice
from mde_core.ids import new_ulid
from mde_core.logging import get_logger
from mde_core.money import micros_to_usd, usd_to_micros
from mde_llm_gateway.ledger import Ledger, LedgerEvent, LedgerSnapshot
from mde_llm_gateway.pricing import Pricing

MUST_RUN_PREFIX = "must-run"
Tripwire = Literal["ok", "warn_50", "must_run_only_80", "refuse_95"]

HARD_CAP_USD = Decimal("6.00")  # the project's total live-spend ceiling (CLAUDE.md rule 3)
HARD_EFFECTIVE_CAP_USD = Decimal("5.70")  # never plan to reach the cap: 95% of the hard cap
HARD_CAP_MICROS = usd_to_micros(HARD_CAP_USD)
HARD_EFFECTIVE_CAP_MICROS = usd_to_micros(HARD_EFFECTIVE_CAP_USD)

_DEFAULT_SCOPE_CAPS_USD = {"session": "1.00", "research": "0.30", "eval": "1.50", "test": "0.30"}
DEFAULT_SCOPE_KINDS: frozenset[str] = frozenset(_DEFAULT_SCOPE_CAPS_USD)

log = get_logger("mde.llm_gateway.budget")


@dataclass(frozen=True)
class BudgetConfig:
    """Caps and tripwires. ``cap_micros`` is ``LLM_BUDGET_USD``, clamped to ``HARD_CAP_USD``."""

    cap_micros: int
    effective_fraction: Decimal = Decimal("0.95")
    scope_caps_micros: Mapping[str, int] = field(
        default_factory=lambda: {k: usd_to_micros(v) for k, v in _DEFAULT_SCOPE_CAPS_USD.items()}
    )
    scope_overrides_micros: Mapping[str, int] = field(default_factory=lambda: {})
    warn_fraction: Decimal = Decimal("0.50")
    must_run_fraction: Decimal = Decimal("0.80")
    refuse_fraction: Decimal = Decimal("0.95")
    reservation_ttl_seconds: int = 600
    # Scope kinds ``reserve`` accepts (the part of a scope before ":"). Extend through budget.yaml.
    scope_kinds: frozenset[str] = DEFAULT_SCOPE_KINDS

    def __post_init__(self) -> None:
        if self.cap_micros <= 0:
            raise ConfigError("LLM_BUDGET_USD must be positive")
        if self.cap_micros > HARD_CAP_MICROS:
            log.warning(
                "budget_cap_clamped",
                requested_micros=self.cap_micros,
                clamped_micros=HARD_CAP_MICROS,
                hard_cap_usd=str(HARD_CAP_USD),
            )
            object.__setattr__(self, "cap_micros", HARD_CAP_MICROS)
        if not (Decimal(0) < self.effective_fraction <= Decimal(1)):
            raise ConfigError("effective cap fraction must be in (0, 1]")
        if not (self.warn_fraction <= self.must_run_fraction <= self.refuse_fraction <= 1):
            raise ConfigError("tripwires must satisfy warn <= must_run <= refuse <= 1")

    @property
    def effective_cap_micros(self) -> int:
        """95% of the cap by default (USD 5.70 of 6.00), rounded down; never above USD 5.70."""
        fraction = int(
            (Decimal(self.cap_micros) * self.effective_fraction).to_integral_value(ROUND_FLOOR)
        )
        return min(fraction, HARD_EFFECTIVE_CAP_MICROS)

    def check_scopes(self, scopes: Iterable[str]) -> None:
        """Refuse a scope whose kind is unknown (it would get no scope cap at all)."""
        for scope in scopes:
            kind = scope.split(":", 1)[0]
            if kind not in self.scope_kinds:
                raise BudgetExceeded(
                    f"call refused: unknown budget scope kind {kind!r}; "
                    f"allowed kinds are {sorted(self.scope_kinds)}",
                    details={"scope": scope, "reason": "unknown_scope_kind"},
                )

    def scope_cap(self, scope: str) -> int | None:
        """Cap for one scope: exact override, else the default for its kind, else none."""
        if scope in self.scope_overrides_micros:
            return self.scope_overrides_micros[scope]
        kind = scope.split(":", 1)[0]
        return self.scope_caps_micros.get(kind)

    def tripwire(self, committed_micros: int) -> Tripwire:
        ratio = Decimal(committed_micros) / Decimal(self.cap_micros)
        if ratio >= self.refuse_fraction:
            return "refuse_95"
        if ratio >= self.must_run_fraction:
            return "must_run_only_80"
        if ratio >= self.warn_fraction:
            return "warn_50"
        return "ok"

    @classmethod
    def from_dict(cls, data: Mapping[str, object], *, cap_usd: Decimal | str) -> BudgetConfig:
        tw = cast("Mapping[str, object]", data.get("tripwires", {}))
        caps = cast("Mapping[str, object]", data.get("scope_caps_usd", _DEFAULT_SCOPE_CAPS_USD))
        over = cast("Mapping[str, object]", data.get("scope_overrides_usd", {}))
        extra_kinds = cast("list[object]", data.get("scope_kinds", []))
        return cls(
            cap_micros=usd_to_micros(cap_usd),
            effective_fraction=Decimal(str(data.get("effective_cap_fraction", "0.95"))),
            scope_caps_micros={k: usd_to_micros(str(v)) for k, v in caps.items()},
            scope_kinds=DEFAULT_SCOPE_KINDS
            | frozenset(caps)
            | frozenset(str(k) for k in extra_kinds)
            | frozenset(k.split(":", 1)[0] for k in over),
            scope_overrides_micros={k: usd_to_micros(str(v)) for k, v in over.items()},
            warn_fraction=Decimal(str(tw.get("warn", "0.50"))),
            must_run_fraction=Decimal(str(tw.get("must_run_only", "0.80"))),
            refuse_fraction=Decimal(str(tw.get("refuse", "0.95"))),
            reservation_ttl_seconds=int(cast("int", data.get("reservation_ttl_seconds", 600))),
        )

    @classmethod
    def load(cls, path: str | Path | None = None, *, cap_usd: Decimal | str) -> BudgetConfig:
        """Load ``budget.yaml`` (package default, or ``path``) with ``cap_usd`` as the hard cap."""
        if path is None:
            text = resources.files("mde_llm_gateway").joinpath("budget.yaml").read_text("utf-8")
        else:
            text = Path(path).read_text("utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ConfigError("budget.yaml must be a mapping")
        return cls.from_dict(cast("Mapping[str, object]", data), cap_usd=cap_usd)


def usage_is_invalid(usage: Usage) -> bool:
    """True when any count is negative (a provider bug or tampering: the cost cannot be trusted)."""
    return (
        min(
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.output_tokens,
            usage.reasoning_tokens,
            usage.tool_calls,
        )
        < 0
    )


class TokenEstimator:
    """Conservative input-token estimate: ``chars / chars_per_token``, calibrated from real usage.

    Starts at 3.0 chars per token (real English text is about 4; Hindi and code are lower).
    Calibration only ever makes the estimate MORE conservative: the effective ratio is the smallest
    observed ratio over the last ``window`` real calls, with a 10% safety margin, never above the
    3.0 default and never below 1.5.
    """

    DEFAULT = 3.0
    FLOOR = 1.5
    OVERHEAD_TOKENS = 64  # message framing, tool and format wrappers

    def __init__(self, *, window: int = 50) -> None:
        self._ratios: deque[float] = deque(maxlen=window)

    @property
    def chars_per_token(self) -> float:
        if not self._ratios:
            return self.DEFAULT
        return max(self.FLOOR, min(self.DEFAULT, min(self._ratios) * 0.9))

    def estimate(self, chars: int) -> int:
        if chars < 0:
            raise ValueError("chars must be >= 0")
        return math.ceil(chars / self.chars_per_token) + self.OVERHEAD_TOKENS

    def observe(self, chars: int, input_tokens: int) -> None:
        if chars > 0 and input_tokens > self.OVERHEAD_TOKENS:
            self._ratios.append(chars / (input_tokens - self.OVERHEAD_TOKENS))


@dataclass
class Reservation:
    """A held slice of the budget. Close it exactly once with ``commit`` or ``release``."""

    reservation_id: str
    scopes: tuple[str, ...]
    worst_case_micros: int
    call_id: str
    spec_id: str
    model: str
    mode: CallMode
    purpose: str
    created_at: datetime
    closed: bool = False


@dataclass(frozen=True)
class ScopeUsage:
    scope: str
    spent_micros: int
    reserved_micros: int
    cap_micros: int | None


class BudgetGuard:
    """Reserve before every provider call; commit the real cost afterwards.

    ``bills`` says whether this guard backs real spending. A non-billing guard (mock, replay,
    scripted: in-memory ledger) always commits cost 0 but still enforces the caps on worst case, so
    "budget cap reached" behaviour is testable offline.
    """

    def __init__(
        self,
        config: BudgetConfig,
        pricing: Pricing,
        ledger: Ledger,
        *,
        bills: bool,
        clock: Clock | None = None,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.config = config
        self.pricing = pricing
        self.ledger = ledger
        self.bills = bills
        self.clock: Clock = clock or ledger.clock
        self.estimator = estimator or TokenEstimator()
        self._warned = False

    # ------------------------------------------------------------------ estimation
    def estimate_worst_case(
        self, model: str, *, prompt_chars: int, max_output_tokens: int, max_tool_calls: int = 0
    ) -> int:
        """Worst-case micros for one provider call. Raises ``UnknownModelPrice`` for unknown models."""
        return self.pricing.worst_case_micros(
            model,
            input_tokens=self.estimator.estimate(prompt_chars),
            max_output_tokens=max_output_tokens,
            max_tool_calls=max_tool_calls,
        )

    # ------------------------------------------------------------------ reserve / commit / release
    def reserve(
        self,
        scopes: Iterable[str],
        worst_case_micros: int,
        *,
        purpose: str = "",
        call_id: str = "",
        spec_id: str = "",
        model: str = "",
        mode: CallMode = "live",
    ) -> Reservation:
        """Hold ``worst_case_micros`` or refuse with :class:`BudgetExceeded`.

        Refuses when ``spent + reserved + worst_case > effective_cap`` globally or in ANY scope, when
        the 95% tripwire is hit, when a scope kind is unknown, or (at 80%) when the purpose does not
        start with ``must-run``. ``must-run`` is a privilege of trusted in-process code: the HTTP API
        strips it from caller-supplied purposes.
        """
        unique = tuple(dict.fromkeys(scopes))
        if not unique:
            raise ValueError("reserve() needs at least one scope")
        if worst_case_micros < 0:
            raise ValueError("worst_case_micros must be >= 0")
        self.config.check_scopes(unique)
        rid = f"rsv_{new_ulid()}"
        now = self.clock.now()
        event = LedgerEvent(
            event="reserve",
            ts=now,
            reservation_id=rid,
            scopes=list(unique),
            call_id=call_id or None,
            spec_id=spec_id or None,
            model=model or None,
            mode=mode,
            worst_case_micros=worst_case_micros,
        )

        def check(snap: LedgerSnapshot) -> None:
            self._check(snap, unique, worst_case_micros, purpose)

        self.ledger.reserve(event, check)
        return Reservation(
            reservation_id=rid,
            scopes=unique,
            worst_case_micros=worst_case_micros,
            call_id=call_id,
            spec_id=spec_id,
            model=model,
            mode=mode,
            purpose=purpose,
            created_at=now,
        )

    def _check(
        self, snap: LedgerSnapshot, scopes: tuple[str, ...], worst: int, purpose: str
    ) -> None:
        cfg = self.config
        committed = snap.spent_micros + snap.reserved_micros
        tripwire = cfg.tripwire(committed)
        base: dict[str, object] = {
            "spent_micros": snap.spent_micros,
            "reserved_micros": snap.reserved_micros,
            "worst_case_micros": worst,
            "effective_cap_micros": cfg.effective_cap_micros,
        }
        if tripwire == "refuse_95":
            raise BudgetExceeded(
                "budget tripwire: 95% of the cap is spent or reserved; everything is refused",
                details={**base, "tripwire": tripwire},
            )
        if tripwire == "must_run_only_80" and not purpose.startswith(MUST_RUN_PREFIX):
            raise BudgetExceeded(
                "budget tripwire: 80% of the cap is spent or reserved; only 'must-run' calls allowed",
                details={**base, "tripwire": tripwire, "purpose": purpose},
            )
        if tripwire == "warn_50" and not self._warned:
            self._warned = True
            log.warning(
                "budget_tripwire_50",
                spent_micros=snap.spent_micros,
                reserved_micros=snap.reserved_micros,
                cap_micros=cfg.cap_micros,
            )
        if committed + worst > cfg.effective_cap_micros:
            raise BudgetExceeded(
                "call refused: it could push the budget over the effective cap "
                f"({micros_to_usd(cfg.effective_cap_micros)} USD)",
                details={**base, "scope": "global"},
            )
        for scope in scopes:
            cap = cfg.scope_cap(scope)
            if cap is None:
                continue
            used = snap.spent_by_scope.get(scope, 0) + snap.reserved_by_scope.get(scope, 0)
            if used + worst > cap:
                raise BudgetExceeded(
                    f"call refused: scope {scope!r} cap of {micros_to_usd(cap)} USD would be exceeded",
                    details={
                        **base,
                        "scope": scope,
                        "scope_cap_micros": cap,
                        "scope_used_micros": used,
                    },
                )

    def commit(self, reservation: Reservation, usage: Usage) -> Usage:
        """Record the real cost and close the reservation. Idempotent (a second call is a no-op).

        Returns ``usage`` with ``cost_micros`` set to what was recorded. A non-billing guard records
        0. If the real cost exceeds the reservation it is recorded anyway (money was spent) and an
        error is logged. Usage that cannot be priced (a negative count, an unknown model) is never
        trusted: the reservation's WORST CASE is booked instead, with the token counts zeroed.
        """
        if reservation.closed:
            return usage.model_copy(update={"cost_micros": 0})
        cost = 0
        recorded = usage
        if self.bills:
            try:
                if usage_is_invalid(usage):
                    raise ValueError("usage counts must be >= 0")
                cost = self.pricing.usage_cost_micros(
                    reservation.model,
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens,
                    tool_calls=usage.tool_calls,
                )
            except (ValueError, UnknownModelPrice) as exc:
                cost = reservation.worst_case_micros
                recorded = Usage()
                log.error(
                    "budget_usage_unpriceable_booked_worst_case",
                    reservation_id=reservation.reservation_id,
                    reason=type(exc).__name__,
                    booked_micros=cost,
                    spec_id=reservation.spec_id,
                )
        recorded = recorded.model_copy(update={"cost_micros": cost})
        self._commit_cost(reservation, recorded, cost)
        return recorded

    def commit_cost(
        self, reservation: Reservation, cost_micros: int, usage: Usage | None = None
    ) -> Usage:
        """Commit an explicit cost (for example the worst case when a response carried no usage)."""
        if reservation.closed:
            return (usage or Usage()).model_copy(update={"cost_micros": 0})
        cost = cost_micros if self.bills else 0
        recorded = (usage or Usage()).model_copy(update={"cost_micros": cost})
        self._commit_cost(reservation, recorded, cost)
        return recorded

    def _commit_cost(self, reservation: Reservation, usage: Usage, cost: int) -> None:
        if cost < 0:
            raise ValueError("cost must be >= 0")
        if cost > reservation.worst_case_micros:
            log.error(
                "budget_reservation_overrun",
                reservation_id=reservation.reservation_id,
                cost_micros=cost,
                worst_case_micros=reservation.worst_case_micros,
                spec_id=reservation.spec_id,
            )
        try:
            self.ledger.append(
                LedgerEvent(
                    event="commit",
                    ts=self.clock.now(),
                    reservation_id=reservation.reservation_id,
                    scopes=list(reservation.scopes),
                    call_id=reservation.call_id or None,
                    spec_id=reservation.spec_id or None,
                    model=reservation.model or None,
                    mode=reservation.mode,
                    usage=usage,
                    cost_micros=cost,
                )
            )
        except Exception:
            # Real money may have been spent that the ledger did not record. Say so loudly (the
            # operator reconciles from this line and the dashboard); the exception then stops the
            # call, and with the ledger unwritable every later reserve fails too: fail closed.
            log.critical(
                "budget_commit_failed",
                reservation_id=reservation.reservation_id,
                cost_micros=cost,
                call_id=reservation.call_id,
                spec_id=reservation.spec_id,
                scopes=list(reservation.scopes),
            )
            raise
        reservation.closed = True

    def release(self, reservation: Reservation) -> None:
        """Close the reservation without spending (idempotent)."""
        if reservation.closed:
            return
        self.ledger.append(
            LedgerEvent(
                event="release",
                ts=self.clock.now(),
                reservation_id=reservation.reservation_id,
                scopes=list(reservation.scopes),
                call_id=reservation.call_id or None,
                spec_id=reservation.spec_id or None,
                model=reservation.model or None,
                mode=reservation.mode,
            )
        )
        reservation.closed = True

    # ------------------------------------------------------------------ reporting
    def status(self) -> BudgetStatus:
        snap = self.ledger.snapshot()
        return BudgetStatus(
            cap_micros=self.config.cap_micros,
            effective_cap_micros=self.config.effective_cap_micros,
            spent_micros=snap.spent_micros,
            reserved_micros=snap.reserved_micros,
            by_scope=dict(snap.spent_by_scope),
            tripwire=self.config.tripwire(snap.spent_micros + snap.reserved_micros),
            live_calls=snap.live_calls,
        )

    def usage_for_scope(self, scope: str) -> ScopeUsage:
        snap = self.ledger.snapshot()
        return ScopeUsage(
            scope=scope,
            spent_micros=snap.spent_by_scope.get(scope, 0),
            reserved_micros=snap.reserved_by_scope.get(scope, 0),
            cap_micros=self.config.scope_cap(scope),
        )
