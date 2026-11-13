"""Deterministic fallback of G2-SUMMARY: NO paragraph (FN-29). The page shows the template summary from code; a paragraph is
omitted, never invented."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out


def fallback(inp: G2In) -> G2Out:
    return G2Out(paragraph="")
