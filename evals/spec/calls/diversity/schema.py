from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class DiversityIn(CallInput):
    question: str  # "between_app" (do these two apps differ?) or "within_app" (do these screens share one language?)
    records: list[
        str
    ]  # lines "<label>: <design words>" (direction, motion, signature moments), pack text = DATA


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiversityOut(W):
    score: (
        int  # 1 to 5: between_app, 5 = clearly different apps; within_app, 5 = one clear language
    )
    notes: list[str]  # up to three plain observations the owner can check
