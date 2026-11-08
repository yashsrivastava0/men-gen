"""The working object of one turn: request, run context, mutable state copy, collected events."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from mde_contracts.calls import ChangeOp, Intent, TurnLite
from mde_contracts.common import Mode
from mde_contracts.engines import DiscoveryRequest, DiscoveryState, RunContext
from mde_contracts.events import OutboundEvent
from mde_contracts.policy import Policy
from mde_discovery.policy_guard import Violation
from mde_discovery.slots import SlotStore

MAX_RECENT_TURNS = 8
PROMPT_TURNS = 4


@dataclass
class Turn:
    req: DiscoveryRequest
    ctx: RunContext
    state: DiscoveryState
    store: SlotStore
    ids: Callable[[str], str]
    now: Callable[[], datetime]
    in_reply_to: str | None = None
    events: list[OutboundEvent] = field(default_factory=lambda: [])
    llm_calls: int = 0
    intent: Intent | None = None
    approval_confident: bool = False
    change_ops: list[ChangeOp] = field(default_factory=lambda: [])
    stop_requested: bool = False
    pause_requested: bool = False
    start_over_requested: bool = False
    spoken: list[str] = field(default_factory=lambda: [])
    fallback_used: bool = False
    user_text: str | None = None
    policy_hits: list[Violation] = field(default_factory=lambda: [])  # company rules the text broke

    @property
    def mode(self) -> Mode:
        return self.req.mode

    @property
    def policy(self) -> Policy:
        return self.req.policy

    @property
    def lang(self) -> str:
        return self.state.language

    def set(self, **changes: object) -> None:
        """Replace fields of the (immutable) discovery state."""
        self.state = self.state.model_copy(update=changes)

    def prompt_turns(self) -> list[TurnLite]:
        return list(self.state.recent_turns[-PROMPT_TURNS:])
