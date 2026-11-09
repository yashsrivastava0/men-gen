"""Assemble the engine's ``Ports`` from the call folders (ADR 0014).

Each ``calls/<name>/`` folder exports ``SPEC`` (a ``CompassCallSpec``) in ``spec.py`` and one adapter class
(name ending in ``Adapter``) in ``adapter.py`` taking ``(runner, catalog)``. A call folder that does not exist
yet is served by a deterministic placeholder port (only the intake-side calls today).
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from mde_compass.calls.base import CallRunner, CompassCallSpec
from mde_compass.catalog import Catalog
from mde_compass.engine.fallback_ports import (
    NoAmbiguityScanner,
    NoChunkReader,
    NoClaimMerger,
    UnavailableBriefCompiler,
)
from mde_compass.engine.ports import Ports

# call folder -> Ports attribute
PORT_BY_CALL: dict[str, str] = {
    "understand_turn": "understander",
    "write_card": "card_writer",
    "situate": "situator",
    "compile_text": "compiler",
    "seed_research": "seeder",
    "probe_handoff": "prober",
    "answer": "answerer",
    "interpret_change": "change_interpreter",
    "make_variants": "variant_maker",
    "read_chunk": "chunk_reader",
    "merge_claims": "claim_merger",
    "scan_ambiguity": "ambiguity_scanner",
    # the AI-planned interview (build prompt 02)
    "plan_questions": "question_planner",
    "audit_questions": "question_auditor",
    "update_summary": "summary_updater",
    "compile_brief": "brief_compiler",
    "rephrase_question": "question_rephraser",
    "decide_answers": "answer_decider",
}
PLACEHOLDERS: dict[str, Any] = {
    "chunk_reader": NoChunkReader,
    "claim_merger": NoClaimMerger,
    "ambiguity_scanner": NoAmbiguityScanner,
    # remove an entry when its call folder (spec + adapter) lands
    "brief_compiler": UnavailableBriefCompiler,
}


def _module(name: str, leaf: str) -> Any | None:
    try:
        return importlib.import_module(f"mde_compass.calls.{name}.{leaf}")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith(f"mde_compass.calls.{name}"):
            return None
        raise


def all_specs() -> list[CompassCallSpec[Any, Any]]:
    """Every call spec that exists (served to the gateway through ``StaticRegistry``)."""
    specs: list[CompassCallSpec[Any, Any]] = []
    for name in PORT_BY_CALL:
        mod = _module(name, "spec")
        if mod is not None and hasattr(mod, "SPEC"):
            specs.append(mod.SPEC)
    return specs


def missing_calls() -> list[str]:
    return [name for name in PORT_BY_CALL if _module(name, "spec") is None]


def build_ports(runner: CallRunner, catalog: Catalog) -> Ports:
    chosen: dict[str, Any] = {}
    for name, attr in PORT_BY_CALL.items():
        mod = _module(name, "adapter")
        cls: Any = None
        if mod is not None:
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if obj.__module__ == mod.__name__ and obj.__name__.endswith("Adapter"):
                    cls = obj
                    break
        if cls is not None:
            chosen[attr] = cls(runner, catalog)
        elif attr in PLACEHOLDERS:
            chosen[attr] = PLACEHOLDERS[attr]()
        else:
            raise RuntimeError(f"call folder {name!r} has no adapter and no placeholder")
    return Ports(**chosen)
