"""Cassettes: recorded provider responses served by :class:`ReplayProvider` (brief 7.4).

File layout: ``<cassette_dir>/<spec_id>__v<version>/<key>.json`` (default dir ``tests/cassettes``,
overridable by ``CASSETTE_DIR``). The key is the sha256 of the canonical JSON of

    {spec_id, spec_version, model, system, user, tools, effort, schema_hash}

(the rendered messages, not the input object), so editing a prompt, a schema, the model or the tool
settings invalidates exactly the cassettes it affects. Retries do not change the key; the repair and
escalated calls have different user text / are distinct requests and get their own cassettes.

Sanitised on write: no headers, no API keys, and every text field goes through ``redact_text``
(secrets) and then ``redact_pii`` (email addresses and phone numbers: a cassette is committed, and a
real person's contact details must not be). The prompt text IS stored (redacted) so a replay miss
can be diagnosed by diffing the request.

Verified on read: the file's own ``key`` must equal the key it is stored under (its file name), and
its spec id and version must match the directory it sits in. A cassette that was renamed, copied
into another request's slot or edited so its key differs is refused with a clear error rather than
served as the answer to a request it was not recorded for.
"""

from __future__ import annotations

import json
import os
import re
import string
from pathlib import Path
from typing import Final, Literal

from pydantic import AwareDatetime

from mde_contracts.common import Frozen
from mde_contracts.gateway import Citation, Usage
from mde_core.clock import utcnow
from mde_core.errors import ConfigError
from mde_core.hashing import hash_json
from mde_core.redact import redact_text
from mde_llm_gateway.providers.base import ProviderRequest, ProviderResponse

CASSETTE_FORMAT = 1
DEFAULT_CASSETTE_DIR = Path("tests/cassettes")


# --------------------------------------------------------------------------- PII redaction

EMAIL_MARK: Final = "[REDACTED_EMAIL]"
PHONE_MARK: Final = "[REDACTED_PHONE]"
_LOCAL_CHARS: Final = frozenset(string.ascii_letters + string.digits + "._%+-")
_LABEL_CHARS: Final = frozenset(string.ascii_letters + string.digits + "-")
_MAX_LOCAL: Final = 64
_MAX_LABEL: Final = 63
_MAX_LABELS: Final = 6

# A phone number is a short run of digits, spaces, dots, dashes and parentheses (optionally with a
# leading plus sign), not glued to other word characters; the run is then checked by ``_is_phone``.
# The run length is bounded, so the work per start position is constant: the scan is linear.
_PHONE_RUN: Final = re.compile(
    r"(?<![\w.+-])(?:\+\d|\(\d{2,5}\)|\d)[\d ().-]{6,22}\d(?![\w])(?!-\w)"
)
_DATE_LIKE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")
_IP_LIKE: Final = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_DOTTED_PHONE: Final = re.compile(r"^\d{2,4}\.\d{3,4}\.\d{3,4}$")  # 555.123.4567, not 1.2.3.4.5
_BARE_MOBILE: Final = re.compile(r"(?:91|0)?[6-9]\d{9}")


def _redact_emails(text: str) -> str:
    """Replace every ``local@domain.tld`` with ``[REDACTED_EMAIL]``. Scans around each ``@``
    (bounded local part and labels), so the work is linear in the text however it is built."""
    if "@" not in text:
        return text
    out: list[str] = []
    done = 0  # text before this index is already in ``out``
    at = text.find("@")
    n = len(text)
    while at != -1:
        start = at
        while start > done and at - start < _MAX_LOCAL and text[start - 1] in _LOCAL_CHARS:
            start -= 1
        end = _domain_end(text, at + 1, n) if start < at else -1
        if end > at + 1:
            out.append(text[done:start])
            out.append(EMAIL_MARK)
            done = end
            at = text.find("@", end)
        else:
            at = text.find("@", at + 1)
    out.append(text[done:])
    return "".join(out)


def _domain_end(text: str, pos: int, n: int) -> int:
    """End index of a domain (two to six dot-separated labels) starting at ``pos``, else ``-1``.

    A label does not start with a hyphen, and the last one (the top-level domain) is at least two
    characters and starts with a letter, so ``user@10.0.0.1`` and ``name@-.com`` are not emails."""
    labels = 0
    begin = pos
    while labels < _MAX_LABELS:
        begin = pos
        while pos < n and pos - begin < _MAX_LABEL and text[pos] in _LABEL_CHARS:
            pos += 1
        if pos == begin or text[begin] == "-":
            return -1
        labels += 1
        if pos + 1 < n and text[pos] == "." and text[pos + 1] in _LABEL_CHARS:
            pos += 1
            continue
        break
    if labels >= 2 and pos - begin >= 2 and text[begin].isalpha():
        return pos
    return -1


def _is_phone(candidate: str) -> bool:
    """Is this run of digits and separators a telephone number, and not a date, an IP address, a
    version, an amount or a bare number? Conservative: a bare digit string only counts as an Indian
    mobile number (ten digits from 6-9, optionally with 91 or 0 in front)."""
    text = candidate.strip()
    if _DATE_LIKE.match(text) or _IP_LIKE.match(text):
        return False
    number = "".join(c for c in text if c.isdigit())
    if not 10 <= len(number) <= 15:
        return False
    if text.startswith(("+", "00")):
        return True
    if not any(c in text for c in " ()-."):
        return _BARE_MOBILE.fullmatch(number) is not None
    if "." in text and not any(c in text for c in " ()-"):
        return _DOTTED_PHONE.match(text) is not None  # 1.2.3.4.5.6.7.8.9.10 is a version, not this
    return True


def _redact_phones(text: str) -> str:
    def swap(match: re.Match[str]) -> str:
        return PHONE_MARK if _is_phone(match.group()) else match.group()

    return _PHONE_RUN.sub(swap, text)


def redact_pii(text: str) -> str:
    """Email addresses and phone numbers replaced by ``[REDACTED_EMAIL]`` / ``[REDACTED_PHONE]``.

    Conservative on purpose: both patterns are bounded and linear, and a date, an IP address, a
    version, an amount or a plain number is left alone. This is a safety net for fixtures, not a
    privacy product: recorded prompts should not carry real contact details in the first place."""
    return _redact_phones(_redact_emails(text))


def _clean(text: str) -> str:
    """Secrets first (``redact_text``), then contact details."""
    return redact_pii(redact_text(text)[0])


class CassetteRequest(Frozen):
    spec_id: str
    spec_version: int
    model: str
    effort: str
    schema_hash: str
    tools: list[dict[str, object]]
    kind: str
    system: str
    user: str


class CassetteResponse(Frozen):
    text: str
    status: Literal["completed", "incomplete"] = "completed"
    incomplete_reason: str | None = None
    refusal: str | None = None
    citations: list[Citation] = []
    search_calls: int = 0
    usage: Usage | None = None


class Cassette(Frozen):
    format: int = CASSETTE_FORMAT
    key: str
    recorded_at: AwareDatetime
    request: CassetteRequest
    response: CassetteResponse


def cassette_key(request: ProviderRequest) -> str:
    """The lookup key for ``request`` (see the module docstring for what goes in)."""
    return hash_json(
        {
            "spec_id": request.spec.id,
            "spec_version": request.spec.version,
            "model": request.model,
            "system": request.system,
            "user": request.user,
            "tools": [t.model_dump(mode="json") for t in request.tools],
            "effort": request.effort,
            "schema_hash": request.schema_hash,
        }
    )


def cassette_path(root: Path, spec_id: str, spec_version: int, key: str) -> Path:
    return root / f"{spec_id}__v{spec_version}" / f"{key}.json"


def build_cassette(request: ProviderRequest, response: ProviderResponse, key: str) -> Cassette:
    """Sanitised cassette for one real exchange."""
    return Cassette(
        key=key,
        recorded_at=utcnow(),
        request=CassetteRequest(
            spec_id=request.spec.id,
            spec_version=request.spec.version,
            model=request.model,
            effort=request.effort,
            schema_hash=request.schema_hash,
            tools=[t.model_dump(mode="json") for t in request.tools],
            kind=request.kind,
            system=_clean(request.system),
            user=_clean(request.user),
        ),
        response=CassetteResponse(
            text=_clean(response.text),
            status=response.status,
            incomplete_reason=response.incomplete_reason,
            refusal=_clean(response.refusal) if response.refusal else None,
            citations=[
                c.model_copy(update={"title": _clean(c.title) if c.title else None})
                for c in response.citations
            ],
            search_calls=response.search_calls,
            usage=response.usage,
        ),
    )


def write_cassette(root: Path, cassette: Cassette) -> Path:
    """Atomically write ``cassette`` (temp file + replace)."""
    path = cassette_path(
        root, cassette.request.spec_id, cassette.request.spec_version, cassette.key
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cassette.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_cassette(path: Path) -> Cassette:
    """The cassette in ``path``, if it really is the one stored there.

    Raises ``ConfigError`` when the file's own ``key`` differs from the key in its file name (the
    lookup key), or when its spec id and version differ from its directory: a renamed, copied or
    edited cassette is never served as the answer to another request. (A file that cannot be parsed
    raises the usual ``ValueError`` subclass.)
    """
    cassette = Cassette.model_validate_json(path.read_text(encoding="utf-8"))
    wanted = path.stem
    if cassette.key != wanted:
        raise ConfigError(
            f"cassette {path} is refused: its own key ({cassette.key[:16]}...) does not match "
            f"the key it is stored under ({wanted[:16]}...). It was renamed, copied or edited; "
            "re-record it with a live batch (lead only) or delete it.",
            details={"path": str(path)},
        )
    directory = f"{cassette.request.spec_id}__v{cassette.request.spec_version}"
    if path.parent.name != directory:
        raise ConfigError(
            f"cassette {path} is refused: it records {directory} but sits in {path.parent.name}. "
            "Move it back or re-record it.",
            details={"path": str(path)},
        )
    return cassette


def response_from_cassette(cassette: Cassette) -> ProviderResponse:
    r = cassette.response
    return ProviderResponse(
        text=r.text,
        usage=r.usage,
        citations=list(r.citations),
        search_calls=r.search_calls,
        status=r.status,
        incomplete_reason=r.incomplete_reason,
        refusal=r.refusal,
        cassette_key=cassette.key,
    )
