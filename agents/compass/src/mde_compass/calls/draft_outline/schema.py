"""Wire models of ``cmp.draft_outline`` (ADR 0017): the modules, entity names and journeys of the app.

The models live in ``engine/structure_wire`` (the engine's ports speak them and the three structure calls share them);
this module is where the call folder names what it owns.
"""

from __future__ import annotations

from mde_compass.engine.structure_wire import (
    EntityOut,
    JourneyOut,
    ModuleOut,
    OutlineInput,
    OutlineOutput,
)

__all__ = ["EntityOut", "JourneyOut", "ModuleOut", "OutlineInput", "OutlineOutput"]
