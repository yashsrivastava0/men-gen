"""Local checks of a COPY-L output: the same keys, nothing empty, every placeholder kept (ICU braces), no URL added."""

from __future__ import annotations

import re
from collections import Counter

from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, CopyLOut

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_URL = re.compile(r"https?://|www\.", re.I)
MAX_MESSAGES = 15


def validate(inp: CopyLIn, out: CopyLOut) -> list[str]:
    errors: list[str] = []
    src = {s.key: s.text for s in inp.strings}
    got = Counter(s.key for s in out.strings)
    for k in sorted(set(src) - set(got)):
        errors.append(f"{k}: missing")
    for k in sorted(set(got) - set(src)):
        errors.append(f"{k}: not a key of this call")
    for k, n in sorted(got.items()):
        if n > 1:
            errors.append(f"{k}: given {n} times")
    for s in out.strings:
        if s.key not in src:
            continue
        if not s.text.strip():
            errors.append(f"{s.key}: empty")
            continue
        if Counter(_PLACEHOLDER.findall(s.text)) != Counter(_PLACEHOLDER.findall(src[s.key])):
            errors.append(
                f"{s.key}: placeholders must be kept exactly ({', '.join(_PLACEHOLDER.findall(src[s.key])) or 'none'})"
            )
        if _URL.search(s.text) and not _URL.search(src[s.key]):
            errors.append(f"{s.key}: a URL was added")
    return errors[:MAX_MESSAGES]
