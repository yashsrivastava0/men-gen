"""Fold stored events into the state a page needs: the plan, topic statuses, entities, the feed, counters.

Pure. The snapshot endpoint folds the run's events with this, so a reloaded page draws exactly what a live page
showed; the front end can run the same logic on the stream.
"""

from __future__ import annotations

from typing import Any

from mde_research_agent.domain.events import TERMINAL_EVENTS, Counters, Event, TargetView

FEED_SIZE = 60
TARGET_EVENT_STATUS = {
    "target.started": "running",
    "target.covered": "covered",
    "target.not_found": "not_found",
    "target.cancelled": "cancelled",
}


def fold(events: list[Event]) -> dict[str, Any]:
    targets: dict[str, TargetView] = {}
    order: list[str] = []
    declined: list[str] = []
    entities: list[str] = []
    started: dict[str, Any] = {}
    counters = Counters()
    phase: str | None = None
    round_no = 0
    terminal: Event | None = None
    for e in events:
        counters = e.counters
        if e.phase:
            phase = e.phase
        if e.round:
            round_no = max(round_no, e.round)
        d = e.detail
        if e.type == "run.started":
            started = dict(d)
        elif e.type == "plan.ready":
            for t in d.get("targets", []):
                tv = TargetView(
                    id=t["id"],
                    label=t["label"],
                    job=t["job"],
                    lane=t["lane"],
                    priority=int(t.get("priority", 0)),
                )
                targets[tv.id] = tv
                order.append(tv.id)
            declined = list(d.get("declined", []))
        elif e.type in TARGET_EVENT_STATUS and e.target_id in targets:
            tv = targets[e.target_id]
            targets[e.target_id] = tv.model_copy(
                update={
                    "status": TARGET_EVENT_STATUS[e.type],
                    "slots_filled": int(d.get("slots_filled", tv.slots_filled)),
                    "slots_required": int(d.get("slots_required", tv.slots_required)),
                }
            )
        elif e.type == "entity.discovered" and d.get("name") and d["name"] not in entities:
            entities.append(str(d["name"]))
        if e.type in TERMINAL_EVENTS:
            terminal = e
    return {
        "targets": [targets[i] for i in order],
        "declined": declined,
        "entities": entities[-40:],
        "started": started,
        "counters": counters,
        "phase": phase,
        "round": round_no,
        "feed": events[-FEED_SIZE:],
        "terminal": terminal,
        "last_seq": events[-1].seq if events else 0,
    }
