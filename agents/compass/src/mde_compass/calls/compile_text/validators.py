"""Code validators of ``cmp.compile_text``: lengths, known item ids and ``ungrounded_terms``.

``ungrounded_terms`` enforces "compile never invents": every number and every capitalized or proper-noun
token of the output must occur in the input (slot values, labels, item names and intents, counts), apart
from a small per-language stopword set and the words of our own fallback templates.
"""

from __future__ import annotations

import functools

from mde_compass.calls.compile_text.schema import (
    MAX_DESCRIPTION_CHARS,
    MAX_SUMMARY_CHARS,
    CompileTextInput,
    CompileTextOutput,
)
from mde_compass.calls.compile_text.text_utils import (
    Vocabulary,
    base_form,
    is_number,
    load_templates,
    normalize_number,
    sentence_initial_flags,
    tokens,
)

MAX_REPORTED = 8


@functools.lru_cache(maxsize=4)
def allowed_words(lang: str) -> frozenset[str]:
    """Stopwords of ``lang`` plus every word of our own templates (so the fallback never trips)."""
    table = load_templates()[lang]
    words: set[str] = {base_form(w) for w in table["stopwords"]}
    for key, value in table.items():
        if key != "stopwords" and isinstance(value, str):
            words.update(base_form(t) for t in tokens(value) if not is_number(t))
    return frozenset(words)


def ungrounded_terms(inp: CompileTextInput, out: CompileTextOutput) -> list[str]:
    """Numbers and proper-noun tokens of the output that the input never mentioned (sorted, unique)."""
    texts: list[str] = []
    for s in inp.slots:
        texts += [s.label, s.value]
    for i in inp.items:
        texts += [i.name, i.intent]
    vocab = Vocabulary(texts, [inp.app_count, inp.role_count, inp.feature_count])
    allowed = allowed_words("hi" if _is_hi(inp.language) else "en") | allowed_words("en")
    texts = [out.summary, out.pitch, *[d.text for d in out.descriptions]]
    bad: set[str] = set()
    for text in texts:
        for tok, _start in sentence_initial_flags(text):
            if is_number(tok):
                if normalize_number(tok) not in vocab.numbers:
                    bad.add(tok)
                continue
            if not tok[:1].isupper():
                continue
            word = base_form(tok)
            if word in allowed or vocab.knows(word):
                continue
            bad.add(tok)
    return sorted(bad)


def _is_hi(language: str) -> bool:
    return language.strip().lower().replace("_", "-") in ("hi", "hi-in", "hindi")


def structure_errors(inp: CompileTextInput, out: CompileTextOutput) -> list[str]:
    """Lengths, empty texts, unknown or duplicate item ids."""
    errors: list[str] = []
    if not out.summary.strip() or "\n" in out.summary.strip():
        errors.append("summary must be one non-empty line")
    if len(out.summary) > MAX_SUMMARY_CHARS:
        errors.append(f"summary longer than {MAX_SUMMARY_CHARS} characters")
    if not out.pitch.strip():
        errors.append("pitch is empty")
    # a pitch over the limit is clipped by the adapter, not a failure
    known = {i.path for i in inp.items}
    seen: set[str] = set()
    for d in out.descriptions:
        if d.item_id not in known:
            errors.append(f"description for unknown item {d.item_id!r}")
        elif d.item_id in seen:
            errors.append(f"duplicate description for {d.item_id!r}")
        seen.add(d.item_id)
        if not d.text.strip() or len(d.text) > MAX_DESCRIPTION_CHARS:
            errors.append(f"description of {d.item_id!r} is empty or too long")
    return errors


def validate(inp: CompileTextInput, out: CompileTextOutput) -> list[str]:
    """``CallRunner`` validator: an empty list accepts the output, anything else triggers the fallback."""
    errors = structure_errors(inp, out)
    if errors:
        return errors  # bounded outputs first: the grounding scan only runs on bounded text
    bad = ungrounded_terms(inp, out)
    if bad:
        return [f"ungrounded terms: {', '.join(bad[:MAX_REPORTED])}"]
    return []
