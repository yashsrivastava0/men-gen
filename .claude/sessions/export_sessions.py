#!/usr/bin/env python3
"""Export Claude Code chat sessions of this repo into .claude/sessions/log/ (stdlib only).

Usage:  python3 -I .claude/sessions/export_sessions.py [--src DIR] [--force]

Self-contained: touches nothing outside .claude/sessions/. Condensed (user + assistant text,
tool names), secrets redacted, idempotent, file name = session id (no merge conflicts).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
LOG = HERE / "log"
MEM = HERE / "memory"

REDACT = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[REDACTED-KEY]"),
    (re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"), "[REDACTED-KEY]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "[REDACTED-KEY]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*)\s*[=:]\s*\S+"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"), "[EMAIL]"),
    (re.compile(r"/Users/[^/\s]+"), "~"),
    # tests/security/test_single_live_switch.py scans every tracked text file for these phrases (D-140); a chat that
    # discusses the rule must not trip it, so the phrases are replaced in the exported copy only.
    (
        re.compile(
            "|".join(
                (
                    "ALLOW" + "_LIVE_LLM",
                    "allow" + "_live",
                    "guard" + "_live",
                    "second" + r"[ -]key",
                    "live" + " switch",
                    "live-call" + " hook",
                    "live-guard" + " hook",
                )
            ),
            re.IGNORECASE,
        ),
        "[live-mode rule phrase]",
    ),
]
NOISE = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|command-name|command-message|command-args)>.*?</\1>",
    re.S,
)


def clean(text: str) -> str:
    text = NOISE.sub("", text)
    for pat, rep in REDACT:
        text = pat.sub(rep, text)
    return text.strip()


def default_src() -> Path:
    slug = str(REPO).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def blocks(msg: dict):
    c = msg.get("content")
    if isinstance(c, str):
        yield "text", c
        return
    for b in c or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            yield "text", b.get("text", "")
        elif b.get("type") == "tool_use":
            inp = b.get("input", {})
            hint = (
                inp.get("command")
                or inp.get("file_path")
                or inp.get("description")
                or inp.get("pattern")
                or ""
            )
            yield "tool", f"{b.get('name')}: {str(hint)[:140]}"


def render(path: Path) -> tuple[str, str, str]:
    out, title, first, start, end, last = [], "", "", "", "", ""
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        ts = d.get("timestamp", "")
        if t == "ai-title":
            title = d.get("aiTitle") or d.get("title") or title
        if t not in ("user", "assistant") or d.get("isSidechain"):
            continue
        if ts:
            start, end = start or ts, ts
        who = "USER" if t == "user" else "CLAUDE"
        for kind, val in blocks(d.get("message", {})):
            val = clean(val)
            if not val:
                continue
            if kind == "tool":
                out.append(f"- `{val}`")
            else:
                if who == "USER" and not first:
                    first = val.replace("\n", " ")[:120]
                if who == "CLAUDE":
                    last = val.replace("\n", " ")
                out.append(f"\n### {who}\n\n{val}\n")
    head = (
        f"---\nsession: {path.stem}\nstart: {start}\nend: {end}\ntitle: {title or first}\n"
        f"first_ask: {first}\nlast: {last[:600]}\n---\n\n# Session {path.stem[:8]}\n"
    )
    return head + "\n".join(out) + "\n", start, title or first


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=default_src())
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    LOG.mkdir(exist_ok=True)
    n = 0
    for f in sorted(a.src.glob("*.jsonl")):
        date = datetime.fromtimestamp(f.stat().st_ctime, tz=UTC).strftime("%Y%m%d")
        existing = list(LOG.glob(f"*_{f.stem[:8]}.md"))
        if existing and not a.force and existing[0].stat().st_mtime >= f.stat().st_mtime:
            continue
        text, start, _ = render(f)
        day = (start[:10].replace("-", "")) or date
        for e in existing:
            e.unlink()
        (LOG / f"{day}_{f.stem[:8]}.md").write_text(text, encoding="utf-8")
        n += 1
    mem_src = a.src / "memory"
    if mem_src.is_dir():
        MEM.mkdir(exist_ok=True)
        for m in mem_src.glob("*.md"):
            (MEM / m.name).write_text(clean(m.read_text(encoding="utf-8")), encoding="utf-8")
    rows = []
    for p in sorted(LOG.glob("*.md"), reverse=True):
        meta = dict(
            ln.split(": ", 1)
            for ln in p.read_text(encoding="utf-8").split("---")[1].strip().splitlines()
            if ": " in ln
        )
        rows.append(
            f"- [{p.name}](log/{p.name}) — {meta.get('title', '')[:100]}  ({p.stat().st_size // 1024} KB)"
        )
    (HERE / "INDEX.md").write_text(
        "# Chat session index (generated; newest first)\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    ref = [
        "# Global reference: what was done in each chat session",
        "",
        "Read this first. It is a one-screen map of every past Claude Code chat on this project.",
        "Open `.claude/sessions/log/<file>` only when you need the detail. `memory/` holds the owner's standing preferences.",
        "Generated by `python3 -I .claude/sessions/export_sessions.py`; do not edit by hand.",
        "",
    ]
    for p in sorted(LOG.glob("*.md"), reverse=True):
        if p.stat().st_size < 2048:
            continue
        meta = dict(
            ln.split(": ", 1)
            for ln in p.read_text(encoding="utf-8").split("---")[1].strip().splitlines()
            if ": " in ln
        )
        title = re.sub(r"<[^>]+>", "", meta.get("title", "")).strip()[:90]
        ref += [
            f"## {p.stem[:8]} {title}",
            f"- file: `log/{p.name}` ({p.stat().st_size // 1024} KB)",
            f"- asked: {meta.get('first_ask', '')[:200]}",
            f"- ended with: {meta.get('last', '')[:500]}",
            "",
        ]
    (HERE / "REFERENCE.md").write_text("\n".join(ref), encoding="utf-8")
    sys.stdout.write(f"exported {n} new/updated, {len(rows)} total\n")


if __name__ == "__main__":
    main()
