"""Rolling summary: one short line per thing worth remembering, compacted when it grows.

Prompts carry the summary instead of the transcript, so it must stay small. The budget is about
1.5k tokens (estimated as characters / 4); past it the oldest lines are dropped, keeping the first
line (the idea) and the newest ones. Deterministic, no LLM.
"""

from __future__ import annotations

SUMMARY_BUDGET_TOKENS = 1500
KEEP_FRACTION = 0.7
MAX_LINE_CHARS = 200


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def append_line(summary: str, line: str, budget_tokens: int = SUMMARY_BUDGET_TOKENS) -> str:
    """Add ``line`` (one short sentence) and compact when the budget is exceeded."""
    clean = " ".join(line.split())[:MAX_LINE_CHARS]
    if not clean:
        return summary
    lines = [ln for ln in summary.split("\n") if ln.strip()]
    entry = f"- {clean}"
    if entry in lines:
        return summary
    lines.append(entry)
    return compact("\n".join(lines), budget_tokens)


def compact(summary: str, budget_tokens: int = SUMMARY_BUDGET_TOKENS) -> str:
    """Drop the oldest lines (after the first) until the summary fits 70% of the budget."""
    if estimate_tokens(summary) <= budget_tokens:
        return summary
    lines = [ln for ln in summary.split("\n") if ln.strip()]
    head, rest = lines[:1], lines[1:]
    target = int(budget_tokens * KEEP_FRACTION)
    while rest and estimate_tokens("\n".join([*head, *rest])) > target:
        rest.pop(0)
    return "\n".join([*head, *rest])
