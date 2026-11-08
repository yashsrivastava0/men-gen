"""Plain-language strings the flow controller itself writes (never an engine's replies).

Everything the flow says to the user is in this one module so it can be localised or tuned
without touching control flow. Codes (the ``Notice.code`` values) are stable API; text is not.
"""

from __future__ import annotations

from typing import Final

# --- gate buttons (events.GateEvent.buttons) -------------------------------------------------
BTN_APPROVE: Final = "Looks right"
BTN_APPROVE_ENTERPRISE: Final = "Send for approval"
BTN_CHANGE: Final = "Change something"

# --- notice codes -----------------------------------------------------------------------------
N_STALE_GATE: Final = "stale_gate"  # approval for an old version or hash; the gate was re-shown
N_GATE_CLOSED: Final = "gate_not_open"
N_NOT_AN_APPROVAL: Final = "not_an_approval"
N_SUBMITTED: Final = "submitted_for_approval"
N_WAITING_APPROVERS: Final = "waiting_for_approvers"
N_QUEUED: Final = "message_queued"
N_RUN_STOPPING: Final = "run_still_stopping"
N_PAUSED: Final = "session_paused"
N_ARCHIVED: Final = "session_archived"
N_NOTHING_TO_STOP: Final = "nothing_to_stop"
N_CANNOT_PAUSE: Final = "cannot_pause_while_planning"
N_WAIT_FOR_PLAN: Final = "wait_for_plan"
N_RESEARCH_UNAVAILABLE: Final = "research_unavailable"
N_BUILD_FAILED: Final = "build_failed"
N_NO_CHANGE: Final = "no_change_needed"
N_NOT_UNDERSTOOD: Final = "change_not_understood"
N_HANDOFF_NOTIFY_FAILED: Final = "frontend_notify_failed"
N_UNSUPPORTED_ACTION: Final = "action_not_available"
N_LIGHT_APPLIED: Final = "change_applied"
N_HEAVY_NEEDS_REVIEW: Final = "change_needs_review"
N_RUN_CANCELLED: Final = "run_cancelled"
N_BUDGET_REACHED: Final = "budget_reached"
N_IN_PROGRESS: Final = "message_in_progress"
N_INPUT_CLEANED: Final = "secret_redacted"
N_ANSWER_NEEDED: Final = "answer_needed"  # a reply was not the answer to the builder's question
N_CONTENT_FLAGGED: Final = "content_flagged"  # research ignored instructions found in a page
N_BUDGET_CAP: Final = "budget_cap_reached"  # planning paused before it started or between phases
N_SLOT_CONFLICT: Final = "slot_conflict_resolved"  # two stakeholders disagreed; precedence decided
N_SLOT_DISPUTED: Final = "slot_disputed"  # precedence could not decide; the decision owner must
N_SLOT_SETTLED: Final = "slot_dispute_settled"  # the decision owner chose
N_DISPUTE_BLOCKS: Final = "dispute_blocks_approval"  # a gate cannot complete with a disputed slot

# --- text -------------------------------------------------------------------------------------
STALE_GATE: Final = (
    "That page was out of date, so I did not count your answer. Here is the latest version."
)
GATE_CLOSED: Final = "There is nothing waiting for approval right now."
NOT_AN_APPROVAL: Final = "I did not read that as an approval, so here is the page again."
SUBMITTED: Final = "Sent for approval to: {roles}. The build starts when everyone has approved."
WAITING_APPROVERS: Final = "Waiting for: {roles}."
QUEUED: Final = "I'll apply this as soon as the plan is ready."
IN_PROGRESS: Final = "I am still working on that message. The answer will appear here in a moment."
RUN_STOPPING: Final = "The previous run is still stopping. Please try again in a moment."
PAUSED: Final = "This conversation is paused. Tap Resume to continue where you left off."
ARCHIVED: Final = "This draft is saved and closed. You can start a new app any time."
NOTHING_TO_STOP: Final = "Nothing is running right now, so there is nothing to stop."
CANNOT_PAUSE: Final = (
    "The plan is being prepared. Press Stop to cancel it, or wait for it to finish."
)
WAIT_FOR_PLAN: Final = (
    "Hang on, the plan is still being prepared. I'll look at that when it is ready."
)
RESEARCH_UNAVAILABLE: Final = "I could not finish the research this time, so I'll use sensible defaults and say so in the plan."
BUILD_FAILED: Final = (
    "That took longer than expected and did not finish. You can try again from where you were."
)
NO_CHANGE: Final = "Noted. That does not change anything in the plan."
NOT_UNDERSTOOD: Final = (
    "I was not sure what to change. Tell me which part you would like different."
)
HANDOFF_NOTIFY_FAILED: Final = "Your spec is saved. The builder will pick it up shortly."
UNSUPPORTED_ACTION: Final = "That is not available right now."
INPUT_CLEANED: Final = (
    "That looked like a password or key, so I removed it before saving anything. "
    "Please do not paste secrets here."
)
STARTING_PLAN: Final = "Great. I'm researching similar apps and then building your plan."
RESUMING_PLAN: Final = "Thanks. I'm continuing with your plan."
STOPPED: Final = "Stopped. You are back where you were."
RESTARTED: Final = "Okay, starting fresh. Your earlier draft is saved."
CHANGE_APPLIED: Final = "Done. I applied your change to the app (version {version})."
HEAVY_NEEDS_REVIEW: Final = "That is a bigger change, so please look over the updated plan."
GATE_CHANGE_PROMPT: Final = "Tap the part you would like to change, or tell me what to change."
APPLYING_QUEUED: Final = "Applying the notes you sent while I was working."
BRIEF_CHANGED: Final = "I updated the page. Please check it again."
PLAN_CHANGED: Final = "I updated the plan. Please check it again."
APPROVAL_VOIDED: Final = "Your earlier approval no longer applies to this new version."
BUDGET_REACHED: Final = (
    "The budget for this app has been reached, so I paused before building. "
    "Someone who can approve it needs to raise the limit."
)
RUN_CANCELLED_REDISCOVER: Final = "That changes the kind of app, so let's go back to that question."
ANSWER_NEEDED: Final = (
    "I still need your answer to the question above before I can carry on with your plan."
)
ANSWER_OFFER_DEFAULT: Final = (
    'Not sure which to pick? Tap "Use the default" and I will choose a sensible option '
    "and carry on."
)
CHIP_USE_DEFAULT: Final = "Use the default"
CONTENT_FLAGGED: Final = (
    "I ignored some instructions I found in that content. I treated it as plain information "
    "and did not follow it."
)
BUDGET_PAUSED_GUIDED: Final = (
    "I paused here because the budget for this app is used up. "
    "Tap Resume when it has been topped up."
)
BUDGET_APPROVERS: Final = " Waiting for: {roles}."
BUDGET_STILL_PAUSED: Final = "The budget is still used up, so I stayed paused."
BUDGET_CHANGE_DROPPED: Final = (
    " I did not apply your last change yet; please ask for it again after resuming."
)
SLOT_CONFLICT_NEW: Final = (
    "{slot} had two different answers: {old_value} from {old_roles} and {new_value} from "
    "{new_roles}. The policy puts {new_roles} first, so I used {new_value}. "
    "The other answer stays on record."
)
SLOT_CONFLICT_OLD: Final = (
    "{slot} had two different answers: {old_value} from {old_roles} and {new_value} from "
    "{new_roles}. The policy puts {old_roles} first, so I kept {old_value}. "
    "The other answer stays on record."
)
SLOT_DISPUTED: Final = (
    "{slot} has two different answers: {old_value} from {old_roles} and {new_value} from "
    "{new_roles}. I did not overwrite either. The person who owns this app needs to choose: "
    "change that part on the page, or tell me which one to use."
)
SLOT_STILL_DISPUTED: Final = (
    "{slot} is still waiting for the person who owns this app to choose. "
    "I noted the answer {new_value} from {new_roles} too."
)
SLOT_SETTLED: Final = "Thanks, I used your choice for {slot}."
DISPUTE_BLOCKS: Final = (
    "I cannot count the approvals yet: {slots} still has two different answers. "
    "The person who owns this app needs to choose first."
)

# --- progress labels --------------------------------------------------------------------------
PROGRESS_RESEARCH: Final = "Looking at similar apps"
PROGRESS_BUILD: Final = "Putting your plan together"

# --- diffs ------------------------------------------------------------------------------------
NO_VISIBLE_CHANGE: Final = "Nothing you can see changed, but this is a new version."
BLOCK_LABELS: Final[dict[str, str]] = {
    "APP": "app details",
    "DESIGN": "the look",
    "NAV": "navigation",
    "BEHAVIOR": "behavior (offline, notifications, language)",
    "DATA": "data rules",
    "API": "service notes",
    "ROLES": "who can do what",
}
