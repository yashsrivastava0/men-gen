"""Ingest one source end to end: limits, de-duplication, parse, chunk, redact, read, merge, report.

Everything the document says is DATA (hard rule 5, IN-06). The pipeline only hands redacted chunk text to the
``ChunkReader`` port as a string to read; it never executes, follows or obeys anything in it. Text that tries to
instruct the assistant ("ignore previous instructions ...") is noted in ``injection_notes`` (the audit list) and
otherwise treated like any other words. Failures are reported plainly and skipped: ``ingest_*`` never raises
``IntakeError``, it returns ``status="rejected"`` with a user-facing message (IN-14), so the caller can carry on
with the other files.

Public entry points: ``ingest_bytes`` (uploads), ``ingest_text`` (long pasted messages), ``ingest_url``
(web links, SSRF-guarded), ``ingest_parsed`` (the shared core) and ``check_session_limits``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Literal, Protocol

from mde_compass.config import IntakeLimits
from mde_compass.domain.claims import Chunk, Claim, SourceInfo, Statement
from mde_compass.domain.common import Strict
from mde_compass.engine.ports import ChunkReader, ClaimMerger, MergeResult, TurnContext
from mde_compass.intake.chunking import chunk_sections
from mde_compass.intake.errors import IntakeError
from mde_compass.intake.model import ParsedDocument
from mde_compass.intake.parsers import parse_document, parse_text, safe_name
from mde_compass.intake.url_reader import Fetcher, Resolver, display_url, read_url
from mde_compass.safety.screen import screen
from mde_core.hashing import sha256_hex
from mde_core.redact import redact_text

from pydantic import Field  # isort: skip


class IntakePorts(Protocol):
    """The two ports intake needs; the engine's ``Ports`` satisfies this."""

    @property
    def chunk_reader(self) -> ChunkReader: ...

    @property
    def claim_merger(self) -> ClaimMerger: ...


class IngestReport(Strict):
    statements: int = 0
    needs_confirming: int = 0
    conflicts: int = 0
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)  # readable chunks / all chunks
    chunks: int = 0
    unreadable_pages: list[str] = Field(default_factory=list)  # pointers, e.g. "page 4"
    tbd_items: list[str] = Field(default_factory=list)  # open issues that become questions
    failed_chunks: list[str] = Field(default_factory=list)  # pointers the reader could not process


class IngestResult(Strict):
    status: Literal["ingested", "duplicate", "rejected"]
    source: SourceInfo | None = None
    chunks: list[Chunk] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[tuple[str, str]] = Field(default_factory=list)
    report: IngestReport = Field(default_factory=IngestReport)
    summary: str = ""  # plain language, ready to show: "read 100%; 187 statements; ..."
    duplicate_of: str | None = None  # the source id that already holds the same bytes
    error_code: str | None = None  # an ``IntakeError`` code when rejected
    message: str = ""  # the user-facing explanation when rejected or duplicate
    injection_notes: list[str] = Field(
        default_factory=list
    )  # audit: instructions found in the text
    redactions: int = 0  # chunks in which a secret or card number was masked before storing


def source_id_for(seq: int) -> str:
    return f"SRC-{seq:03d}"


def check_session_limits(
    *, file_count: int, session_bytes: int, new_bytes: int, limits: IntakeLimits
) -> None:
    """Raises ``IntakeError`` when one more file would pass the per-session limits (10 files, 60 MB)."""
    if file_count >= limits.max_files:
        raise IntakeError("too_many_files")
    if session_bytes + new_bytes > limits.max_session_bytes:
        raise IntakeError("session_too_large")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summarize(report: IngestReport, *, kind: str = "") -> str:
    """The one-line plain-language summary: ``read 100%; 187 statements; 31 need confirming; 4 conflict``."""
    if kind == "image":
        return "I can't read images yet; please paste the text they show or upload a text version."
    if report.chunks == 0:
        return "I found no readable text in it."
    n = report.needs_confirming
    c = report.conflicts
    parts = [
        f"read {round(report.coverage * 100)}%",
        _plural(report.statements, "statement", "statements"),
        f"{n} {'needs' if n == 1 else 'need'} confirming",
        f"{c} {'conflicts' if c == 1 else 'conflict'}",
    ]
    if report.unreadable_pages:
        parts.append(
            f"{_plural(len(report.unreadable_pages), 'page', 'pages')} could not be read "
            f"({', '.join(p.removeprefix('page ') for p in report.unreadable_pages[:10])})"
        )
    if report.tbd_items:
        parts.append(_plural(len(report.tbd_items), "open question", "open questions"))
    return "; ".join(parts)


def _rejected(name: str, exc: IntakeError) -> IngestResult:
    return IngestResult(
        status="rejected",
        error_code=exc.code,
        message=exc.user_message,
        summary=exc.user_message,
        source=None,
        report=IngestReport(),
    )


def _fallback_claims(statements: list[Statement], source_id: str) -> list[Claim]:
    """Used only if the merger fails: one claim per statement, all needing confirmation (never invents)."""
    stem = source_id.removeprefix("SRC-")
    return [
        Claim(
            id=f"CLM-{stem}-{i:03d}",
            source_id=source_id,
            statement=s,
            status="needs_confirming",
        )
        for i, s in enumerate(statements, start=1)
    ]


async def ingest_parsed(
    parsed: ParsedDocument,
    *,
    name: str,
    sha256: str,
    size: int,
    source_id: str,
    ports: IntakePorts,
    ctx: TurnContext,
    limits: IntakeLimits,
) -> IngestResult:
    """Chunk, redact, read and merge an already parsed document."""
    raw_chunks = chunk_sections(
        parsed.sections, source_id=source_id, target_tokens=limits.chunk_target_tokens
    )
    chunks: list[Chunk] = []
    injection_notes: list[str] = []
    redactions = 0
    for chunk in raw_chunks:
        pointer, _ = redact_text(chunk.pointer)
        if chunk.unreadable:
            chunks.append(chunk.model_copy(update={"pointer": pointer}))
            continue
        scr = screen(chunk.text, max_chars=len(chunk.text) + 1)
        if scr.had_secret or "card_number" in scr.flags:
            redactions += 1
        if scr.injection:
            injection_notes.append(
                f"{chunk.id} ({pointer}): text that tries to instruct the assistant was found and "
                "treated as data, not obeyed"
            )
        chunks.append(chunk.model_copy(update={"text": scr.text, "pointer": pointer}))

    readable = [c for c in chunks if not c.unreadable]
    gate = asyncio.Semaphore(max(1, limits.extract_concurrency))

    async def read_one(chunk: Chunk) -> list[Statement] | None:
        async with gate:
            try:
                found = await ports.chunk_reader.read(
                    ctx, chunk_text=chunk.text, source_id=source_id, pointer=chunk.pointer
                )
            except Exception:  # one failing chunk lowers coverage; it must not lose the others
                return None
            return [
                s if s.pointer else s.model_copy(update={"pointer": chunk.pointer}) for s in found
            ]

    results = await asyncio.gather(*(read_one(c) for c in readable))
    statements: list[Statement] = []
    failed: list[str] = []
    for chunk, found in zip(readable, results, strict=True):
        if found is None:
            failed.append(chunk.pointer or chunk.id)
        else:
            statements.extend(found)

    merge = MergeResult(claims=[], statement_count=0)
    if statements:
        try:
            merge = await ports.claim_merger.merge(ctx, statements, source_id=source_id)
        except Exception:
            merge = MergeResult(
                claims=_fallback_claims(statements, source_id),
                statement_count=len(statements),
                needs_confirming=len(statements),
            )
    claim_needs = sum(1 for c in merge.claims if c.status == "needs_confirming")
    unreadable = [c.pointer or c.id for c in chunks if c.unreadable]
    coverage = (len(readable) - len(failed)) / len(chunks) if chunks else 0.0
    report = IngestReport(
        statements=max(merge.statement_count, len(statements)),
        needs_confirming=max(merge.needs_confirming, claim_needs),
        conflicts=len(merge.conflicts),
        coverage=round(max(0.0, min(1.0, coverage)), 4),
        chunks=len(chunks),
        unreadable_pages=unreadable,
        tbd_items=list(merge.tbd),
        failed_chunks=failed,
    )
    notes = list(parsed.notes)
    notes.extend(f"chunk_not_read: {p}" for p in failed)
    if injection_notes:
        notes.append(f"instructions_in_document_ignored: {len(injection_notes)}")
    if redactions:
        notes.append(f"secrets_masked_in_chunks: {redactions}")
    safe, _ = redact_text(safe_name(name) if parsed.kind != "url" else name)
    info = SourceInfo(
        id=source_id,
        kind=parsed.kind,
        name=safe[:200],
        sha256=sha256,
        bytes=size,
        pages=parsed.pages,
        coverage=report.coverage,
        notes=notes,
    )
    return IngestResult(
        status="ingested",
        source=info,
        chunks=chunks,
        claims=list(merge.claims),
        conflicts=list(merge.conflicts),
        report=report,
        summary=summarize(report, kind=parsed.kind),
        injection_notes=injection_notes,
        redactions=redactions,
    )


def _duplicate(name: str, digest: str, existing: Mapping[str, str]) -> IngestResult | None:
    first = existing.get(digest)
    if first is None:
        return None
    message = (
        f"{safe_name(name)} is the same as a file I already have ({first}); I kept the first one."
    )
    return IngestResult(status="duplicate", duplicate_of=first, message=message, summary=message)


async def ingest_bytes(
    name: str,
    data: bytes,
    *,
    session_id: str,
    source_seq: int,
    existing_hashes: Mapping[str, str],
    ports: IntakePorts,
    ctx: TurnContext,
    limits: IntakeLimits,
    session_files: int = 0,
    session_bytes: int = 0,
) -> IngestResult:
    """Ingest one uploaded file.

    ``existing_hashes`` maps sha256 to the ``SRC-###`` id already stored in this session (a duplicate returns
    ``duplicate_of`` and nothing else, IN-15). ``session_files`` and ``session_bytes`` are what the session
    already holds, for the 10-file and 60 MB limits. ``session_id`` is accepted for the caller's symmetry and
    audit; ids come from ``source_seq``.
    """
    del session_id
    label = safe_name(name)
    try:
        if len(data) > limits.max_file_bytes:
            raise IntakeError("too_large", label)
        digest = sha256_hex(data)
        dup = _duplicate(label, digest, existing_hashes)
        if dup is not None:
            return dup
        check_session_limits(
            file_count=session_files,
            session_bytes=session_bytes,
            new_bytes=len(data),
            limits=limits,
        )
        parsed = parse_document(label, data, limits=limits)
    except IntakeError as exc:
        return _rejected(label, exc)
    return await ingest_parsed(
        parsed,
        name=label,
        sha256=digest,
        size=len(data),
        source_id=source_id_for(source_seq),
        ports=ports,
        ctx=ctx,
        limits=limits,
    )


def is_document_text(text: str, limits: IntakeLimits) -> bool:
    """A pasted message longer than ``pasted_text_is_document_over`` characters is treated as a document."""
    return len(text) > limits.pasted_text_is_document_over


async def ingest_text(
    text: str,
    *,
    name: str = "pasted text",
    session_id: str,
    source_seq: int,
    existing_hashes: Mapping[str, str],
    ports: IntakePorts,
    ctx: TurnContext,
    limits: IntakeLimits,
    markdown: bool = False,
    session_files: int = 0,
    session_bytes: int = 0,
) -> IngestResult:
    """Ingest a long pasted message as a document (kind ``text``, or ``markdown`` with ``markdown=True``)."""
    del session_id
    data = text.encode("utf-8", errors="replace")
    try:
        if len(data) > limits.max_file_bytes:
            raise IntakeError("too_large", name)
        digest = sha256_hex(data)
        dup = _duplicate(name, digest, existing_hashes)
        if dup is not None:
            return dup
        check_session_limits(
            file_count=session_files,
            session_bytes=session_bytes,
            new_bytes=len(data),
            limits=limits,
        )
        parsed = parse_text(text, markdown=markdown)
        if not parsed.sections:
            parsed.notes.append("no_readable_text")
    except IntakeError as exc:
        return _rejected(name, exc)
    return await ingest_parsed(
        parsed,
        name=name,
        sha256=digest,
        size=len(data),
        source_id=source_id_for(source_seq),
        ports=ports,
        ctx=ctx,
        limits=limits,
    )


async def ingest_url(
    url: str,
    *,
    session_id: str,
    source_seq: int,
    existing_hashes: Mapping[str, str],
    ports: IntakePorts,
    ctx: TurnContext,
    limits: IntakeLimits,
    url_count: int = 0,
    session_files: int = 0,
    session_bytes: int = 0,
    resolver: Resolver | None = None,
    fetcher: Fetcher | None = None,
) -> IngestResult:
    """Read a public web page (SSRF-guarded) and ingest its text. ``url_count`` is how many links the session
    already holds (limit ``limits.max_urls``). The source hash is the sha256 of the extracted text."""
    del session_id
    shown = display_url(url)
    try:
        if url_count >= limits.max_urls:
            raise IntakeError("too_many_urls", shown)
        if session_files >= limits.max_files:
            raise IntakeError("too_many_files", shown)
        parsed = await read_url(url, limits=limits, resolver=resolver, fetcher=fetcher)
        body = "\n".join(
            f"{s.heading}\n{s.text}" if s.heading else s.text for s in parsed.sections
        ).encode("utf-8", errors="replace")
        digest = sha256_hex(body)
        dup = _duplicate(shown, digest, existing_hashes)
        if dup is not None:
            return dup
        check_session_limits(
            file_count=session_files,
            session_bytes=session_bytes,
            new_bytes=len(body),
            limits=limits,
        )
    except IntakeError as exc:
        return _rejected(shown, exc)
    return await ingest_parsed(
        parsed,
        name=shown,
        sha256=digest,
        size=len(body),
        source_id=source_id_for(source_seq),
        ports=ports,
        ctx=ctx,
        limits=limits,
    )
