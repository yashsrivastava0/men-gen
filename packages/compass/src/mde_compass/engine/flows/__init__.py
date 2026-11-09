"""Targeted interview flows (priorities, references, voice read-back, many apps, locked edits, back-edge).

Pure functions live here (data in, data out); ``hooks.FlowHooks`` is the thin mixin that applies them to a turn.
Cue phrases and numbers are data in ``flows.yaml``; every user-facing text comes from the catalog strings.
"""
