"""One reader for Compass exports and Research outputs (copied from Research, contract tests on both sides: D-175).

Public: ``read_export`` (a Compass export in, a graded ``SpecInput`` out), ``check_readiness`` and ``intake`` (can Spec go
on?), ``read_research`` (a ``research.v5`` file in, a typed ``ResearchView`` out) and ``pack`` (select what applies to a
need card). Imports no agent and no model vendor.
"""

from mde_compass_intake.formats import ExportRefused, Normalised, normalise
from mde_compass_intake.reader import read_export
from mde_compass_intake.readiness import Intake, Readiness, check_readiness, intake
from mde_compass_intake.research_reader import ResearchView, pack, read_research
from mde_compass_intake.spec_input import SpecInput

__all__ = [
    "ExportRefused",
    "Intake",
    "Normalised",
    "Readiness",
    "ResearchView",
    "SpecInput",
    "check_readiness",
    "intake",
    "normalise",
    "pack",
    "read_export",
    "read_research",
]
