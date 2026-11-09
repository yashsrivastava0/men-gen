"""Document and link intake (prompt 6.5): parse, chunk, redact, read, merge, report.

The lead wires these into the service: ``ingest_bytes`` / ``ingest_text`` / ``ingest_url`` return an
``IngestResult`` (never raise ``IntakeError``); ``read_url`` + ``make_httpx_fetcher`` are the SSRF-guarded web
reader; ``IntakeError`` carries a code and a plain-language ``user_message``. Document text is untrusted data.
"""

from mde_compass.intake.chunking import approx_tokens, chunk_sections, merge_pointers
from mde_compass.intake.errors import IntakeError, IntakeErrorCode
from mde_compass.intake.model import ParsedDocument, Section
from mde_compass.intake.parsers import parse_document, parse_text, safe_name
from mde_compass.intake.pipeline import (
    IngestReport,
    IngestResult,
    IntakePorts,
    check_session_limits,
    ingest_bytes,
    ingest_parsed,
    ingest_text,
    ingest_url,
    is_document_text,
    source_id_for,
    summarize,
)
from mde_compass.intake.url_reader import (
    Fetcher,
    FetchResponse,
    Resolver,
    display_url,
    ip_is_blocked,
    make_httpx_fetcher,
    read_url,
    system_resolver,
    validate_url,
)

__all__ = [
    "FetchResponse",
    "Fetcher",
    "IngestReport",
    "IngestResult",
    "IntakeError",
    "IntakeErrorCode",
    "IntakePorts",
    "ParsedDocument",
    "Resolver",
    "Section",
    "approx_tokens",
    "check_session_limits",
    "chunk_sections",
    "display_url",
    "ingest_bytes",
    "ingest_parsed",
    "ingest_text",
    "ingest_url",
    "ip_is_blocked",
    "is_document_text",
    "make_httpx_fetcher",
    "merge_pointers",
    "parse_document",
    "parse_text",
    "read_url",
    "safe_name",
    "source_id_for",
    "summarize",
    "system_resolver",
    "validate_url",
]
