"""Checks on inbound events that the contract models cannot express (before the flow sees them).

* ids: a client-generated ``event_id`` is the idempotency key, so it must be short and plain,
* uploads: **text, markdown and images only**. PDFs and everything else are refused with a friendly
  message (no PDF parsing in v1, BRIEF section 4). The API never fetches a URL a user supplies, and
  image bytes never enter an event: an image travels as an opaque ``data_ref`` string, never inline.
"""

from __future__ import annotations

import re
from typing import Final

from mde_context_api.errors import PayloadTooLarge, UnsupportedMedia
from mde_context_api.settings import ApiSettings
from mde_contracts.events import InboundEvent, UploadAdded
from mde_core.errors import ValidationFailed

TEXT_TYPES: Final = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
IMAGE_TYPES: Final = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/heic", "image/heif", "image/bmp"}
)

PDF_TEXT: Final = (
    "PDFs are not supported yet. Please paste the text you want me to use into the chat instead."
)
OTHER_TYPE_TEXT: Final = (
    "I can read plain text, Markdown and images, but not that kind of file. "
    "Please paste the text into the chat, or add an image."
)
TOO_BIG_TEXT: Final = (
    "That file is too large for me to read in one go. "
    "Please paste the part that matters most, or split it up."
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REF = re.compile(r"^[A-Za-z0-9_./:@+=-]{1,512}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_FILENAME: Final = 255


def media_type_of(raw: str) -> str:
    """``text/plain; charset=utf-8`` -> ``text/plain``."""
    return raw.split(";", 1)[0].strip().lower()


def validate_inbound(event: InboundEvent, settings: ApiSettings) -> None:
    """Raise a ``ValidationFailed`` (422), ``PayloadTooLarge`` (413) or ``UnsupportedMedia`` (415)."""
    if not _ID.match(event.event_id):
        raise ValidationFailed(
            "event_id must be 1 to 128 characters: letters, digits and _ . : - only",
            details={"field": "event_id"},
        )
    if isinstance(event, UploadAdded):
        _validate_upload(event, settings)


def _validate_upload(event: UploadAdded, settings: ApiSettings) -> None:
    if not _ID.match(event.upload_id):
        raise ValidationFailed("upload_id is not valid", details={"field": "upload_id"})
    if len(event.filename) > MAX_FILENAME or _CONTROL.search(event.filename):
        raise ValidationFailed("filename is not valid", details={"field": "filename"})
    if event.size_bytes < 0:
        raise ValidationFailed("size_bytes cannot be negative", details={"field": "size_bytes"})

    kind = media_type_of(event.media_type)
    is_text = kind in TEXT_TYPES
    is_image = kind in IMAGE_TYPES
    if not (is_text or is_image):
        pdf = kind == "application/pdf" or (
            kind == "application/octet-stream" and event.filename.lower().endswith(".pdf")
        )
        raise UnsupportedMedia(
            PDF_TEXT if pdf else OTHER_TYPE_TEXT,
            details={"media_type": kind},
        )

    if event.data_ref is not None:
        ref = event.data_ref
        if ref.lower().startswith("data:") or ";base64" in ref.lower() or not _REF.match(ref):
            raise ValidationFailed(
                "data_ref must be an opaque reference to the stored file, never the file itself",
                details={"field": "data_ref"},
            )
    if is_image:
        if event.text is not None:
            raise ValidationFailed(
                "an image is sent as a reference (data_ref), not as text",
                details={"field": "text"},
            )
        if event.size_bytes > settings.max_upload_image_bytes:
            raise PayloadTooLarge(TOO_BIG_TEXT, details={"field": "size_bytes"})
        return
    if event.text is not None and len(event.text.encode("utf-8")) > settings.max_upload_text_bytes:
        raise PayloadTooLarge(TOO_BIG_TEXT, details={"field": "text"})
    if event.size_bytes > settings.max_upload_text_bytes:
        raise PayloadTooLarge(TOO_BIG_TEXT, details={"field": "size_bytes"})
