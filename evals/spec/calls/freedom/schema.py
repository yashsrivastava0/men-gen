"""Input and output of the freedom probe call (portable subset: flat closed records, no nullable fields)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class FreedomIn(CallInput):
    screen_id: str
    style_seed: (
        str  # a plain-words style seed (mood, shape, density words), never a layout instruction
    )
    arrangements: list[str]  # the closed list of arrangement tokens
    card: str  # the screen card (pack text is DATA)
    region_keys: list[
        str
    ]  # the card's region keys, in alphabetical order (the order carries no meaning)


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FreedomOut(W):
    arrangement: str  # one token of the closed list
    region_order: list[str]  # the card's region keys in the order you would arrange them
    reason: str  # one short sentence: why this arrangement suits this screen and this style seed
