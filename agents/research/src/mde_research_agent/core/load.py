"""The research load: how much research THIS idea needs, from the content of the Brief.

``load_score = features + facts of the listed kinds + weights.app * apps + weights.sensitive * sensitive
flags + weights.reference * named references`` (weights, fact kinds and class cuts are data in
``data/load.yaml``). The interview tier (Quick, Standard, Thorough) and Compass's ``size_band``,
``depth``, ``search_budget`` and ``max_requests`` are NOT inputs: this function does not even receive them.
Structure counts (screens, modules) are not used either, because the structure is a hint.
"""

from __future__ import annotations

from mde_research_agent.catalog.models import LoadConfig
from mde_research_agent.domain.input import LoadClass, LoadComponents, LoadInfo


def classify(score: int, config: LoadConfig) -> LoadClass:
    """``focused`` below 25, ``standard`` below 61, ``deep`` otherwise (cuts come from the data file)."""
    for cut in config.classes:
        if cut.below is None or score < cut.below:
            return cut.name
    return config.classes[-1].name


def compute_load(
    *,
    features: int,
    facts_of_kind: int,
    apps: int,
    sensitive_flags: int,
    named_references: int,
    config: LoadConfig,
) -> LoadInfo:
    """The score, its class and the components that made it (all counts already deduplicated)."""
    w = config.weights
    components = LoadComponents(
        features=features * w.feature,
        facts=facts_of_kind * w.fact,
        apps=apps * w.app,
        sensitive=sensitive_flags * w.sensitive,
        references=named_references * w.reference,
    )
    score = (
        components.features
        + components.facts
        + components.apps
        + components.sensitive
        + components.references
    )
    return LoadInfo(score=score, load_class=classify(score, config), components=components)
