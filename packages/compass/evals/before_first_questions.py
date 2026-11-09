"""Phase 0 "before" evidence for build prompt 02: the first questions the CURRENT (frame-driven) engine asks for
five very different ideas, in mock mode, side by side.

    cd packages/compass && PYTHONPATH=src python -m evals.before_first_questions [--cards 6] [--no-write]

Offline and free (``LLM_MODE=mock`` is forced; every call runs its mock handler on a fake clock). The result is
written to ``docs/compass/evidence/before-first-questions.json`` of the repository and a short side-by-side table is
printed. The point it documents: apart from words taken from the idea, the questions come from a fixed list of
frames, so a tiffin service, a clinic app and a pharmacy inventory tool are asked nearly the same things.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

IDEAS: dict[str, str] = {
    "tiffin_pune": (
        "I want an app for my tiffin delivery service in Pune. Office workers order a daily home-cooked "
        "lunch and my riders deliver it before noon."
    ),
    "clinic_appointments": (
        "An appointment booking app for my family clinic in Indore: patients pick a doctor and a time slot, "
        "and the receptionist manages the day's schedule."
    ),
    "pharmacy_inventory_b2b": (
        "A B2B inventory tool for pharmacies: shop owners track stock and expiry dates, and reorder from "
        "their wholesalers when medicines run low."
    ),
    "kids_language_learning": (
        "A language-learning app for children aged 5 to 10 with short games and stories; parents see their "
        "child's progress."
    ),
    "senior_ride_sharing": (
        "A ride-sharing app for senior citizens: simple booking, trusted drivers, and a family member can "
        "follow the trip."
    ),
}

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "docs" / "compass" / "evidence" / "before-first-questions.json"


def _quiet() -> None:
    logging.disable(logging.CRITICAL)
    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))


def _card_record(card) -> dict:  # type: ignore[no-untyped-def]
    return {
        "id": card.id,
        "frame": card.frame,
        "type": card.type,
        "question": card.question,
        "why_we_ask": card.why_we_ask,
        "options": [{"label": o.label, "basis": o.basis} for o in card.options],
        "fallback_card": bool(card.fallback),
    }


async def collect(cards: int) -> dict:
    from mde_compass.domain import Card
    from mde_compass.scenarios.runner import new_service
    from mde_compass.testing import Conversation

    out: dict = {}
    for key, idea in IDEAS.items():
        conv = await Conversation.start(new_service())
        await conv.say(idea)
        seen: list[dict] = []
        ids: set[str] = set()
        for _ in range(
            cards + 3
        ):  # a few extra turns so welcome/playback cards cannot starve the list
            card: Card | None = conv.last_card()
            if card is None:
                break
            if card.id not in ids and card.frame not in {"welcome", "variants"}:
                ids.add(card.id)
                seen.append(_card_record(card))
            if len(seen) >= cards:
                break
            await conv.tap_recommended()
        out[key] = {"idea": idea, "cards": seen}
    return out


def table(data: dict, rows: int = 4) -> str:
    lines = [
        "Frame order and question wording for the first cards (mock mode, current engine):",
        "",
    ]
    for n in range(rows):
        lines.append(f"-- question {n + 1}")
        for key, rec in data.items():
            cards = rec["cards"]
            if n < len(cards):
                c = cards[n]
                lines.append(f"  {key:<26} [{c['frame']}] {c['question']}")
    lines += ["", summary(data)]
    return "\n".join(lines)


def summary(data: dict) -> str:
    """How much of the question text is verbatim shared between different ideas (the 'generic' measure)."""
    texts: dict[str, set[str]] = {}
    total = 0
    for key, rec in data.items():
        for c in rec["cards"]:
            total += 1
            texts.setdefault(c["question"], set()).add(key)
    shared = {q for q, ideas in texts.items() if len(ideas) >= 2}
    in_shared = sum(1 for rec in data.values() for c in rec["cards"] if c["question"] in shared)
    return (
        f"{total} questions shown in total, {len(texts)} distinct wordings; "
        f"{in_shared} of {total} ({100 * in_shared // max(total, 1)}%) use a wording that another idea "
        f"also got word for word."
    )


def main(argv: list[str] | None = None) -> int:
    os.environ["LLM_MODE"] = "mock"  # never live from here
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cards", type=int, default=6)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    _quiet()
    data = asyncio.run(collect(args.cards))
    if not args.no_write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(table(data) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
