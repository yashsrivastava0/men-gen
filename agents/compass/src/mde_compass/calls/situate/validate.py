"""Code validators of ``cmp.situate``: hypotheses stay hypotheses, family ids exist, no invented user facts."""

from __future__ import annotations

from mde_compass.calls.situate.schema import SituateInput, SituateOutput
from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.fallback import extract_references
from mde_compass.catalog.loader import Catalog, load_catalog

MAX_STATEMENTS = 40


def structural(inp: SituateInput, out: SituateOutput) -> list[str]:
    """Spec-level checks that justify the deterministic fallback."""
    errors: list[str] = []
    h = out.hypothesis
    if h.label != "hypothesis":
        errors.append("the hypothesis must be labelled hypothesis")
    if not 0.0 <= h.confidence <= 1.0:
        errors.append("confidence out of range")
    if len(inp.statements) > MAX_STATEMENTS:
        errors.append("too many statements")
    return errors


def sanitize(
    inp: SituateInput, out: SituateOutput, catalog: Catalog | None = None
) -> tuple[SituateOutput, list[str]]:
    """Drop unknown family ids; do not let the profile claim what the text does not say. Returns (out, notes)."""
    cat = catalog or load_catalog()
    notes: list[str] = []
    h = out.hypothesis
    families = [f for f in h.likely_families if f in cat.families]
    if len(families) != len(h.likely_families):
        notes.append("dropped unknown family ids")
    features = [
        ft.model_copy(update={"custom": ft.custom or ft.family not in cat.families})
        for ft in h.likely_features
    ]
    h2 = h.model_copy(
        update={
            "label": "hypothesis",
            "likely_families": families,
            "likely_features": features,
            "confidence": min(1.0, max(0.0, h.confidence)),
        }
    )
    text = " ".join([inp.user_text, *(s.text for s in inp.statements)])
    profile = out.profile
    if (
        profile.reference_driven
        and not extract_references(text)
        and not lx.has_phrase(
            text, ["inspired by", "similar to", "like", "jaisa", "jaise", "जैसा", "जैसे"]
        )
    ):
        profile = profile.model_copy(update={"reference_driven": False})
        notes.append("reference_driven without a reference cue")
    if (
        profile.urgency == "just_build"
        and not lx.has_phrase(text, tuple(lx.load_lexicon().intents["finish_with_defaults"]))
        and not lx.has_phrase(text, ["asap", "urgent", "quickly", "jaldi", "जल्दी"])
    ):
        profile = profile.model_copy(update={"urgency": "thorough"})
        notes.append("just_build without the user asking for it")
    return out.model_copy(update={"hypothesis": h2, "profile": profile}), notes
