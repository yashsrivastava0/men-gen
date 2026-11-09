"""Code validators for ``cmp.scan_ambiguity``: findings must point at the digest (real slot, real words); a
finding that cannot be located is dropped. The wire output has no value field, so nothing can be changed."""

from __future__ import annotations

from mde_compass.calls.read_chunk.textutil import has_phrase, normalize, tokens
from mde_compass.calls.scan_ambiguity.rules import load_rules
from mde_compass.calls.scan_ambiguity.schema import ScanInput, ScanOutput, WireFinding


def _problem(
    inp: ScanInput, f: WireFinding, slot_ids: set[str], digest_tokens: set[str]
) -> str | None:
    rules = load_rules()
    text = f.text.strip()
    if not text or len(text) > rules.max_finding_chars:
        return "empty or too long"
    if f.slot_id is not None and f.slot_id not in slot_ids:
        return "unknown slot"
    if f.kind == "conflicting":
        return None if set(tokens(text)) & digest_tokens else "not grounded in the digest"
    norm = normalize(text)
    if not norm:
        return "empty"
    candidates = [it.text for it in inp.items if f.slot_id is None or it.slot_id == f.slot_id]
    if not any(has_phrase(normalize(c), norm) for c in candidates):
        return "text is not in the digest"
    return None


def keep_valid(inp: ScanInput, findings: list[WireFinding]) -> list[WireFinding]:
    """Findings that point at something real, de-duplicated and capped."""
    rules = load_rules()
    slot_ids = {it.slot_id for it in inp.items if it.slot_id is not None}
    digest_tokens = {t for it in inp.items for t in tokens(it.text)}
    out: list[WireFinding] = []
    seen: set[tuple[str | None, str, str]] = set()
    for f in findings:
        if _problem(inp, f, slot_ids, digest_tokens) is not None:
            continue
        key = (f.slot_id, f.kind, normalize(f.text))
        if key in seen:
            continue
        seen.add(key)
        out.append(f.model_copy(update={"text": f.text.strip()}))
        if len(out) >= rules.max_findings:
            break
    return out


def validate_output(inp: ScanInput, out: ScanOutput) -> list[str]:
    """Reject the whole answer only when it has findings and none points at the digest."""
    if out.findings and not keep_valid(inp, out.findings):
        return ["scan_ambiguity: no finding points at the Brief digest"]
    return []
