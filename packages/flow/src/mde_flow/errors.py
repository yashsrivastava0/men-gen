"""Flow-specific errors (the API layer maps ``code`` to an HTTP status)."""

from __future__ import annotations

from mde_core.errors import MdeError


class Forbidden(MdeError):
    """The caller may not do this on this session (not the owner, not a required approver)."""

    code = "forbidden"


class HandoffFailed(MdeError):
    """Committing the approved spec to the project repo failed; nothing was emitted."""

    code = "handoff_failed"
