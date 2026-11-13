"""Build a validator Context from the data files (vocabularies, libraries, policy and capability sheets when present)."""

from __future__ import annotations

import contextlib
from collections.abc import Collection, Mapping
from typing import Any

from mde_spec_core import libraries, vocab
from mde_spec_core.config import default_config
from mde_spec_core.graph import SpecGraph

from .base import Context, VocabView

_PROFILES = ("compact", "standard", "deep")


def vocab_view(facts: Collection[str] | Mapping[str, bool] | None = None) -> VocabView:
    facts = facts or {}
    required = {
        st: libraries.state_library_for(st, "compact").required_state_kinds()
        for st in vocab.screen_types()
        if libraries.has_state_library(st)
    }
    categories = {p: [c.name for c in vocab.behavior_categories_for(p, facts)] for p in _PROFILES}
    cap_ids: set[str] = set()
    policy_ids: set[str] = set()
    with contextlib.suppress(ImportError):  # optional sheets: absent until the data task lands them
        from mde_spec_core import platform_data, policy

        cap_ids = set(platform_data.capability_ids())
        policy_ids = set(policy.policy_ids())
    return VocabView(
        shapes=set(vocab.shapes()),
        intents=set(vocab.intents()),
        phone_blocked_intents={f.name for f in vocab.intents_file().forbidden_on_phone},
        screen_types=set(vocab.screen_types()),
        state_kinds=set(vocab.state_kinds()),
        input_types=set(vocab.input_types()) | {"id", "enum", "list", "ref"},
        motion_intents=set(vocab.motion_intents()),
        essential_motion={m for m in vocab.motion_intents() if vocab.is_essential_motion(m)},
        asset_kinds=set(vocab.asset_kinds()),
        mock_profiles=set(vocab.mock_profiles()),
        required_states=required,
        behavior_categories=categories,
        capability_ids=cap_ids,
        policy_ids=policy_ids,
    )


def make_context(graph: SpecGraph, **kw: Any) -> Context:
    kw.setdefault("config", default_config())
    kw.setdefault("vocab", vocab_view(set(graph.meta.facts)))
    return Context(graph, **kw)
