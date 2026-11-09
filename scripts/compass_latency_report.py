#!/usr/bin/env python3
"""Compass latency and concurrency report: inspects call logs and timeline per turn.

Usage:
    uv run python scripts/compass_latency_report.py <session_id> [--tenant-id default] [--limit 200]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from mde_compass.store.ports import CallLogEntry
from mde_compass.store.postgres import PostgresStore, create_store_engine
from mde_core.settings import load_settings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an ASCII latency & concurrency report for a Compass session."
    )
    parser.add_argument("session_id", help="Compass session ID (ses_...)")
    parser.add_argument("--tenant-id", default="default", help="Tenant ID (default: default)")
    parser.add_argument("--limit", type=int, default=200, help="Maximum calls to inspect (default: 200)")
    return parser.parse_args(argv)


def _format_bar(start_offset_ms: int, duration_ms: int, total_span_ms: int, width: int = 40) -> str:
    if total_span_ms <= 0:
        return "[" + "=" * width + "]"
    start_col = int((start_offset_ms / total_span_ms) * width)
    bar_len = max(1, int((duration_ms / total_span_ms) * width))
    if start_col + bar_len > width:
        bar_len = width - start_col
    end_space = max(0, width - start_col - bar_len)
    return "[" + (" " * start_col) + ("=" * bar_len) + (" " * end_space) + "]"


async def fetch_calls(session_id: str, tenant_id: str, limit: int) -> list[CallLogEntry]:
    settings = load_settings()
    engine = create_store_engine(settings.database_url)
    store = PostgresStore(engine)
    try:
        async with store.begin(tenant_id) as uow:
            return await uow.call_log.list(session_id, limit=limit)
    finally:
        await engine.dispose()


def generate_report(session_id: str, entries: list[CallLogEntry]) -> str:
    if not entries:
        return f"No model call entries found for session: {session_id}"

    utc_min = datetime.min.replace(tzinfo=UTC)

    # Sort chronologically
    sorted_entries = sorted(entries, key=lambda e: e.at or utc_min)

    # Group by turn_id (fallback to event_id, then "untracked")
    turns: dict[str, list[CallLogEntry]] = defaultdict(list)
    for e in sorted_entries:
        key = e.turn_id or e.event_id or "untracked"
        turns[key].append(e)

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f" COMPASS LATENCY & CONCURRENCY REPORT: {session_id}")
    lines.append("=" * 80)
    lines.append(f"Total Calls Logged: {len(sorted_entries)} across {len(turns)} turn(s)\n")

    overall_sequential_ms = 0
    overall_wall_ms = 0
    max_concurrency_seen = 1

    for turn_idx, (turn_key, calls) in enumerate(turns.items(), start=1):
        lines.append(f"Turn #{turn_idx}: {turn_key} ({len(calls)} call{'s' if len(calls) > 1 else ''})")
        lines.append("-" * 80)

        # Compute times
        calls_with_time = [c for c in calls if c.at is not None]
        seq_ms = sum(c.latency_ms for c in calls)
        overall_sequential_ms += seq_ms

        if calls_with_time:
            turn_starts = [c.at - timedelta(milliseconds=c.latency_ms) for c in calls_with_time]  # type: ignore[operator]
            turn_ends = [c.at for c in calls_with_time]
            earliest = min(turn_starts)
            latest = max(turn_ends)
            turn_wall_ms = max(1, int((latest - earliest).total_seconds() * 1000))
        else:
            earliest = utc_min
            turn_wall_ms = seq_ms

        overall_wall_ms += turn_wall_ms
        savings_ms = max(0, seq_ms - turn_wall_ms)
        savings_pct = (savings_ms / seq_ms * 100) if seq_ms > 0 else 0

        # Calculate concurrency intervals
        events: list[tuple[datetime, int]] = []
        for c in calls_with_time:
            c_start = c.at - timedelta(milliseconds=c.latency_ms)  # type: ignore[operator]
            events.append((c_start, 1))
            events.append((c.at, -1))
        events.sort(key=lambda x: (x[0], -x[1]))

        curr_concurrency = 0
        peak_concurrency = 1 if calls else 0
        for _, change in events:
            curr_concurrency += change
            if curr_concurrency > peak_concurrency:
                peak_concurrency = curr_concurrency
        if peak_concurrency > max_concurrency_seen:
            max_concurrency_seen = peak_concurrency

        # Print table header
        lines.append(f"{'Spec ID':<26} {'Latency':<9} {'Timeline':<42} {'Tokens (In/Out)':<16} Outcome")
        lines.append(f"{'-'*26} {'-'*9} {'-'*42} {'-'*16} -------")

        for c in calls:
            if c.at and earliest != utc_min:
                c_start = c.at - timedelta(milliseconds=c.latency_ms)
                offset_ms = max(0, int((c_start - earliest).total_seconds() * 1000))
            else:
                offset_ms = 0
            bar = _format_bar(offset_ms, c.latency_ms, turn_wall_ms, width=40)
            toks = f"{c.tokens_in} / {c.tokens_out}"
            lines.append(f"{c.spec_id:<26} {c.latency_ms:>5}ms   {bar} {toks:<16} {c.outcome}")

        lines.append("")
        lines.append(
            f"  Sequential: {seq_ms:>6}ms | Wall Time: {turn_wall_ms:>6}ms | "
            f"Saved: {savings_ms:>5}ms ({savings_pct:>4.1f}%) | Peak Concurrency: {peak_concurrency}x"
        )
        lines.append("\n")

    overall_savings_ms = max(0, overall_sequential_ms - overall_wall_ms)
    overall_savings_pct = (overall_savings_ms / overall_sequential_ms * 100) if overall_sequential_ms > 0 else 0

    lines.append("=" * 80)
    lines.append(" SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Total Sequential Model Time : {overall_sequential_ms:,} ms")
    lines.append(f"Total Elapsed Wall Time     : {overall_wall_ms:,} ms")
    lines.append(f"Total Parallel Savings       : {overall_savings_ms:,} ms ({overall_savings_pct:.1f}%)")
    lines.append(f"Max Concurrent Calls Observed: {max_concurrency_seen}x")
    lines.append("=" * 80)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        entries = asyncio.run(fetch_calls(args.session_id, args.tenant_id, args.limit))
        print(generate_report(args.session_id, entries))
        return 0
    except Exception as exc:
        print(f"Error fetching calls: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
