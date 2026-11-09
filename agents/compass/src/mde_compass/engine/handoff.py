"""Hand-off port implementations (prompt 6.8/6.11): a no-op and a logging stub for the Research agent.

The hand-off only ever happens after Gate 1 approval of the current hash (the service guarantees it); the
consumer views contain no chat transcript.
"""

from __future__ import annotations

from pydantic import JsonValue

from mde_core.logging import get_logger

log = get_logger(__name__)


class NoopHandoff:
    async def hand_off(
        self,
        *,
        session_id: str,
        version: int,
        brief_hash: str,
        views: dict[str, dict[str, JsonValue]],
    ) -> None:
        return None


class LoggingHandoff:
    """Logs the hand-off and keeps what was delivered (tests and the dev UI can inspect it)."""

    def __init__(self) -> None:
        self.delivered: list[dict[str, JsonValue]] = []

    async def hand_off(
        self,
        *,
        session_id: str,
        version: int,
        brief_hash: str,
        views: dict[str, dict[str, JsonValue]],
    ) -> None:
        log.info(
            "compass_handoff",
            session=session_id,
            version=version,
            hash=brief_hash[:12],
            views=sorted(views),
        )
        self.delivered.append(
            {"session_id": session_id, "version": version, "brief_hash": brief_hash, "views": views}  # type: ignore[dict-item]
        )
