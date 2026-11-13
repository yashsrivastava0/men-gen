"""Mock handler of PROD-JOURNEYS: the completed draft. Emotions are Spec's reading by position and stake (assumed: no
fact is cited); variants for deep apps are copied from the flow they vary, with the variant's own goal wording."""

from __future__ import annotations

from .baseline import complete
from .schema import JourneysIn, JourneysOut


def handle(inp: JourneysIn) -> JourneysOut:
    return complete(inp)
