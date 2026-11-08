"""A detail the person gave about one screen travels inside the screen's purpose line.

``ScreenDraftLite`` (the contract C10 reads) has no notes field, so the builder appends the note
after ``USER_DETAIL_MARK`` and the prompt tells the model what that means. ``split_detail`` is the
inverse, used by the offline handler. Pure text helpers, no imports.
"""

from __future__ import annotations

USER_DETAIL_MARK = "User detail:"


def with_detail(purpose: str, detail: str | None) -> str:
    """``purpose`` followed by the person's detail (unchanged when there is none)."""
    return f"{purpose} {USER_DETAIL_MARK} {detail}" if detail else purpose


def split_detail(text: str) -> tuple[str, str | None]:
    """``(purpose, detail)`` of a purpose line built by ``with_detail``."""
    head, mark, tail = text.partition(USER_DETAIL_MARK)
    if not mark:
        return text, None
    detail = " ".join(tail.split())
    return " ".join(head.split()), detail or None
