"""Validator 7, ``limits``: screen count within the cap and file size within budget."""

from __future__ import annotations

from mde_contracts.calls import ValidatorFinding
from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_builder.validators._common import finding
from mde_spec_loader.index import SpecIndex

NAME = "limits"
DEFAULT_MAX_SPEC_TOKENS = 60_000  # spec 6.2 expects 30 to 40k for a 50-screen app
SPEC_WARN_FRACTION = 0.8
SCREEN_BLOCK_WARN_TOKENS = 1_500  # one screen block bigger than this should be split


def limits(
    spec: AppSpec,
    limits: Limits,
    *,
    max_tokens: int = DEFAULT_MAX_SPEC_TOKENS,
    rendered: str | None = None,
) -> list[ValidatorFinding]:
    """Validator 7: ``len(screens) <= limits.max_screens`` and the rendered file stays within
    ``max_tokens`` estimated tokens (warning from 80%); one oversized screen block is a warning."""
    out: list[ValidatorFinding] = []
    count = len(spec.screens)
    if count > limits.max_screens:
        over = [s.id for s in spec.screens[limits.max_screens :]]
        out.append(
            finding(
                NAME,
                f"The app has {count} screens, over the cap of {limits.max_screens}; "
                "move the least important ones to the Phase 2 backlog",
                *over,
            )
        )

    text = rendered if rendered is not None else render_spec(spec)
    index = SpecIndex.from_text(text).index()
    if index.total_tokens > max_tokens:
        out.append(
            finding(
                NAME,
                f"The spec file is about {index.total_tokens} tokens, over the budget of {max_tokens}",
            )
        )
    elif index.total_tokens > max_tokens * SPEC_WARN_FRACTION:
        out.append(
            finding(
                NAME,
                f"The spec file is about {index.total_tokens} tokens, close to the budget of {max_tokens}",
                severity="warn",
            )
        )
    for entry in index.blocks:
        if entry.kind == "screen" and entry.tokens > SCREEN_BLOCK_WARN_TOKENS:
            out.append(
                finding(
                    NAME,
                    f"Screen {entry.id} is about {entry.tokens} tokens; consider splitting it",
                    entry.id,
                    severity="warn",
                )
            )
    return out
