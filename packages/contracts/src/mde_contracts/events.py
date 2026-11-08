"""Events between the UI and the engine (spec section 11).

Inbound events carry a client-generated ``event_id`` (idempotency). Outbound events are appended
to ``session_event`` with a per-session ``seq`` for SSE replay (``Last-Event-ID`` = seq).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from mde_contracts.brief import Brief
from mde_contracts.cards import CardInstance
from mde_contracts.common import Frozen, GateKind
from mde_contracts.gates import ApprovalDecision, PlanPhase, SessionState
from mde_contracts.plan import PlanView

# --------------------------------------------------------------------------- inbound


# Inbound events come from untrusted clients: every free-text field has a hard length limit, so an
# oversize body is rejected at the boundary before any scanner, prompt or database sees it.
MAX_USER_MESSAGE_CHARS = 50_000
MAX_UPLOAD_TEXT_CHARS = 200_000
MAX_FILENAME_CHARS = 255
MAX_COMMENT_CHARS = 5_000
MAX_OTHER_TEXT_CHARS = 5_000
MAX_TARGET_CHARS = 200
MAX_ID_CHARS = 200
MAX_OPTION_IDS = 100
MAX_DATA_REF_CHARS = 512
MAX_MEDIA_TYPE_CHARS = 200

# Id-like inbound fields are not free text, so they are held to the shape an id has: a character
# set with no spaces, quotes or control characters, and a length. (A key pasted into one of them
# still matches a conservative id pattern, so the flow also screens these fields for secrets before
# anything is stored: ``mde_flow.screening``.) The patterns are linear-time regular expressions.
IdStr = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,200}$", max_length=MAX_ID_CHARS)
]
EventIdStr = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", max_length=128)
]
UserIdStr = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$", max_length=128)
]
RoleNameStr = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", max_length=64)
]
# the hash a gate decision names: a lowercase SHA-256 hex digest, nothing else
HashStr = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", max_length=64)]
# an opaque reference to a stored file: bounded here; its character set (and the "never the file
# itself" check for a ``data:`` URI) is the API's, ``mde_context_api.inbound``, which answers in words
DataRefStr = Annotated[str, StringConstraints(max_length=MAX_DATA_REF_CHARS)]
# type/subtype with up to a few simple parameters (``text/plain; charset=utf-8``); empty is allowed
# (a browser reports ``""`` for a file type it does not know: the API answers that with its friendly
# "I can read plain text, Markdown and images" message, not a field error)
MediaTypeStr = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^(?:[A-Za-z0-9][A-Za-z0-9.+_-]{0,62}/[A-Za-z0-9][A-Za-z0-9.+_-]{0,95}"
            r"(?: ?; ?[A-Za-z0-9_-]{1,32}=(?:[A-Za-z0-9._+-]{1,64}|\"[A-Za-z0-9 ._+-]{0,64}\")){0,4})?$"
        ),
        max_length=MAX_MEDIA_TYPE_CHARS,
    ),
]


class _Inbound(Frozen):
    event_id: EventIdStr  # evt_...; a repeated id is ignored
    at: datetime | None = None


class UserMessage(_Inbound):
    type: Literal["user_message"] = "user_message"
    text: str = Field(max_length=MAX_USER_MESSAGE_CHARS)


class OptionSelected(_Inbound):
    type: Literal["option_selected"] = "option_selected"
    card_instance_id: IdStr
    option_ids: list[IdStr] = Field(  # one for choice cards, any for multi
        default=[], max_length=MAX_OPTION_IDS
    )
    other_text: str | None = Field(  # "Something else" / text / confirm-with-edit
        default=None, max_length=MAX_OTHER_TEXT_CHARS
    )
    you_decide: bool = False  # the "You decide" link
    skipped: bool = False


class UploadAdded(_Inbound):
    type: Literal["upload_added"] = "upload_added"
    upload_id: IdStr
    filename: str = Field(max_length=MAX_FILENAME_CHARS)
    media_type: MediaTypeStr  # text/plain, text/markdown, image/png, image/jpeg, ...
    size_bytes: int
    text: str | None = Field(  # extracted text for text/markdown (untrusted)
        default=None, max_length=MAX_UPLOAD_TEXT_CHARS
    )
    data_ref: DataRefStr | None = None  # opaque reference for images


class GateDecision(_Inbound):
    type: Literal["gate_decision"] = "gate_decision"
    gate: GateKind
    decision: ApprovalDecision
    version: int = Field(ge=0, le=2_147_483_647)  # 0 is a (stale) version no gate ever shows
    hash: HashStr
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_CHARS)
    approver_id: UserIdStr | None = None  # defaults to the authenticated principal
    role: RoleNameStr | None = None


UiActionName = Literal[
    "stop",
    "undo",
    "restart",
    "pause",
    "resume",
    "finish_with_defaults",
    "toggle_assumption",
    "toggle_research_item",
    "toggle_feature",
    "edit_row",
    "archive",
    "idle",  # the client reports silence on a pending card: nudge once, then assume
]


class UiAction(_Inbound):
    type: Literal["ui_action"] = "ui_action"
    action: UiActionName
    target: IdStr | None = Field(  # slot id, feature id, research ref id, row key
        default=None, max_length=MAX_TARGET_CHARS
    )
    value: bool | None = None
    selected_screen_id: IdStr | None = None


InboundEvent = Annotated[
    UserMessage | OptionSelected | UploadAdded | GateDecision | UiAction,
    Field(discriminator="type"),
]

# --------------------------------------------------------------------------- outbound


class _Outbound(Frozen):
    event_id: str  # evt_...
    session_id: str
    seq: int = 0  # assigned on append; strictly increasing per session
    at: datetime | None = None
    in_reply_to: str | None = None  # inbound event id


class AssistantMessage(_Outbound):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    language: str = "en"
    chips: list[str] = []  # suggested short replies (starter / idea chips)
    canned: bool = False  # True when no LLM was used


class CardEvent(_Outbound):
    type: Literal["card"] = "card"
    card: CardInstance


class ProgressEvent(_Outbound):
    type: Literal["progress"] = "progress"
    phase: PlanPhase
    step: str  # "plan_research" | "search" | "extract" | "synthesize" | "baseline" | "validate" ...
    label: str  # plain-language ("Looking at similar apps")
    done: int = 0
    total: int = 0


class BriefPage(Frozen):
    kind: Literal["brief"] = "brief"
    brief: Brief


class PlanPage(Frozen):
    kind: Literal["plan"] = "plan"
    plan: PlanView


class GateEvent(_Outbound):
    type: Literal["gate"] = "gate"
    gate: GateKind
    version: int
    hash: str
    page: Annotated[BriefPage | PlanPage, Field(discriminator="kind")]
    buttons: list[str]  # ["Looks right", "Change something"] / ["Send for approval", ...]
    required_roles: list[str] = []
    approved_roles: list[str] = []
    diff_lines: list[str] = []  # present when a gate re-opens after a change
    single_button: bool = False  # re-opened with a diff: one button


class PlanReady(_Outbound):
    type: Literal["plan_ready"] = "plan_ready"
    version: int
    hash: str


class SpecReady(_Outbound):
    """Emitted ONLY after Gate 2 approval of the current hash and a successful git commit."""

    type: Literal["spec_ready"] = "spec_ready"
    app_id: str
    version: int
    hash: str
    git_sha: str
    path: str  # repo-relative path of APP_SPEC.md
    repo_dir: str


class StateChanged(_Outbound):
    type: Literal["state_changed"] = "state_changed"
    from_state: SessionState
    to_state: SessionState
    plan_phase: PlanPhase | None = None


class Notice(_Outbound):
    """Friendly, non-fatal message ('taking longer than usual'); never a raw error."""

    type: Literal["notice"] = "notice"
    code: str
    text: str
    level: Literal["info", "warn"] = "info"


OutboundEvent = Annotated[
    AssistantMessage
    | CardEvent
    | ProgressEvent
    | GateEvent
    | PlanReady
    | SpecReady
    | StateChanged
    | Notice,
    Field(discriminator="type"),
]

INBOUND_TYPES: tuple[str, ...] = (
    "user_message",
    "option_selected",
    "upload_added",
    "gate_decision",
    "ui_action",
)
OUTBOUND_TYPES: tuple[str, ...] = (
    "assistant_message",
    "card",
    "progress",
    "gate",
    "plan_ready",
    "spec_ready",
    "state_changed",
    "notice",
)
