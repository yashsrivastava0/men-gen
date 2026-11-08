"""Validator 8, ``stack_profile``: what the spec needs is supported by the Stack Profile.

Integrations, behavior, and screen recipes/sections/actions are matched (whole words, case
insensitive) against each capability's id, label and keywords. A capability the profile does not
support is an error; one that needs a development build is a warning ("flagged"), because the
web preview cannot show it. An integration with ``needs_dev_build`` set is flagged too.
"""

from __future__ import annotations

import re

from mde_contracts.calls import ValidatorFinding
from mde_contracts.content import StackProfile
from mde_contracts.spec import AppSpec
from mde_spec_builder.validators._common import finding, id_list

NAME = "stack_profile"
_WORDS = re.compile(r"[a-z0-9]+")
_MAX_MATCHERS = 8  # profiles kept compiled (one in production; tests copy it a lot)
_MAX_CACHED_TEXTS = 20_000


def _normalize(text: str) -> str:
    return " " + " ".join(_WORDS.findall(text.lower())) + " "


def _contains(normalized: str, term: str) -> bool:
    words = _WORDS.findall(term.lower())
    return bool(words) and f" {' '.join(words)} " in normalized


class _Matcher:
    """The capability keyword lookups of one ``StackProfile``, compiled once.

    Every capability becomes one regex over its normalized terms (whole words, case insensitive),
    and the capabilities a given text hits are remembered per text. A build validates a spec after
    every proposal and almost every screen reads the same each time, so after the first pass a
    validation is one dictionary lookup per screen instead of a keyword scan of every screen.
    """

    def __init__(self, profile: StackProfile) -> None:
        self.profile = profile
        self._patterns: list[re.Pattern[str] | None] = []
        for capability in profile.capabilities:
            terms: list[str] = []
            for term in (capability.id, capability.label, *capability.keywords):
                words = _WORDS.findall(term.lower())
                if words:
                    terms.append(re.escape(" ".join(words)))
            self._patterns.append(
                re.compile(r"(?<= )(?:" + "|".join(dict.fromkeys(terms)) + r")(?= )")
                if terms
                else None
            )
        self._hits: dict[str, tuple[int, ...]] = {}

    def hits(self, text: str) -> tuple[int, ...]:
        """Indexes (in ``profile.capabilities``) of the capabilities ``text`` mentions."""
        found = self._hits.get(text)
        if found is None:
            normalized = _normalize(text)
            found = tuple(
                i
                for i, pattern in enumerate(self._patterns)
                if pattern is not None and pattern.search(normalized) is not None
            )
            if len(self._hits) >= _MAX_CACHED_TEXTS:
                self._hits.clear()
            self._hits[text] = found
        return found


_MATCHERS: dict[int, _Matcher] = {}


def _matcher(profile: StackProfile) -> _Matcher:
    found = _MATCHERS.get(id(profile))
    if found is None or found.profile is not profile:  # not seen (or an id reused after a copy)
        if len(_MATCHERS) >= _MAX_MATCHERS:
            _MATCHERS.clear()
        found = _MATCHERS[id(profile)] = _Matcher(profile)
    return found


def _corpora(spec: AppSpec) -> list[tuple[str, str]]:
    corpora: list[tuple[str, str]] = []
    for item in spec.integrations.items:
        text = " ".join(filter(None, [item.id, item.name, item.purpose, item.provider, item.notes]))
        corpora.append((item.id, text))
    behavior = spec.behavior
    behavior_text = " ".join(
        [
            behavior.offline,
            *behavior.deep_links,
            *behavior.notifications,
            *behavior.permission_prompts,
        ]
    )
    corpora.append(("BEHAVIOR", behavior_text))
    for screen in spec.screens:
        text = " ".join([screen.recipe, *screen.sections, *(a.name for a in screen.actions)])
        corpora.append((screen.id, text))
    return corpora


def stack_profile(spec: AppSpec, profile: StackProfile) -> list[ValidatorFinding]:
    """Validator 8: unsupported capabilities are errors; development-build ones are flagged."""
    out: list[ValidatorFinding] = []
    if spec.header.stack_profile != profile.id:
        out.append(
            finding(
                NAME,
                f"The spec targets stack profile {spec.header.stack_profile!r} but "
                f"{profile.id!r} was supplied",
                "HEADER",
            )
        )

    matcher = _matcher(profile)
    hit_ids: dict[int, list[str]] = {}
    for cid, text in _corpora(spec):
        for index in matcher.hits(text):
            hit_ids.setdefault(index, []).append(cid)
    for index, capability in enumerate(profile.capabilities):
        hits = hit_ids.get(index)
        if not hits:
            continue
        if not capability.supported:
            out.append(
                finding(
                    NAME,
                    f"{capability.label} is not supported by stack profile {profile.id} "
                    f"(needed by {id_list(hits)}){': ' + capability.note if capability.note else ''}",
                    *hits,
                )
            )
        elif capability.needs_dev_build:
            limit = f" {capability.preview_limit}" if capability.preview_limit else ""
            out.append(
                finding(
                    NAME,
                    f"{capability.label} needs a development build (needed by "
                    f"{id_list(hits)}).{limit}",
                    *hits,
                    severity="warn",
                )
            )

    for item in spec.integrations.items:
        if item.needs_dev_build:
            out.append(
                finding(
                    NAME,
                    f"Integration {item.id} is flagged as needing a development build",
                    item.id,
                    severity="warn",
                )
            )

    summary_text = _normalize(" ".join([spec.app.name, spec.app.purpose, spec.app.audience]))
    for unsupported in profile.unsupported_types:
        if any(_contains(summary_text, k) for k in unsupported.keywords):
            out.append(
                finding(
                    NAME,
                    f"The app description looks like {unsupported.label.lower()}, which "
                    f"{profile.id} cannot do ({unsupported.reason}); closest option: "
                    f"{unsupported.closest_option}",
                    "APP",
                    severity="warn",
                )
            )
    return out
