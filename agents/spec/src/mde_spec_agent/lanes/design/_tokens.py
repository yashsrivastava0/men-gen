"""Tokens by code for one direction, cached (the same inputs always give the same bytes, doc 18 5 A4).

The validator, the gate and the step all ask for the same direction's tokens; the cache keeps that to one build.
Brand colour: only `brand_colour` (the user's Locked colour) is ever passed as brand; a model seed colour is a seed.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from typing import Any

from mde_spec_core import tokens
from mde_spec_core.graph import Direction

_FIELDS = (
    "seed_colour",
    "dark_stance",
    "depth",
    "motion_personality",
    "density",
    "type_personality",
    "shape",
)


def _key(d: Direction, brand: str | None, scripts: tuple[str, ...]) -> str:
    return json.dumps(
        [d.id, *[getattr(d, f) for f in _FIELDS], brand, list(scripts)], sort_keys=True
    )


@lru_cache(maxsize=512)
def _build(key: str) -> dict[str, Any]:
    data = json.loads(key)
    d = Direction(
        id=data[0],
        name="x",
        mood="x",
        **dict(zip(_FIELDS, data[1 : 1 + len(_FIELDS)], strict=True)),
    )
    brand, scripts = data[1 + len(_FIELDS)], data[2 + len(_FIELDS)]
    return tokens.build_tokens(d, brand, tuple(scripts))


def tokens_for(d: Direction, brand: str | None, scripts: tuple[str, ...]) -> dict[str, Any]:
    """A fresh copy of the tokens (callers may keep it in the graph). Raises `tokens.TokenError` when none exist."""
    return copy.deepcopy(_build(_key(d, brand, scripts)))
