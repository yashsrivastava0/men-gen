"""Parse ``APP_SPEC.md`` text back into an ``AppSpec`` (the inverse of the renderer)."""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from mde_contracts.spec import (
    SINGLETON_BLOCK_IDS,
    AcceptanceBlock,
    ApiBlock,
    AppBlock,
    AppSpec,
    AssumptionsBlock,
    BehaviorBlock,
    BuildSequence,
    DataBlock,
    DesignBlock,
    FlowBlock,
    IntegrationsBlock,
    NavigationBlock,
    RevisionBlock,
    RolesBlock,
    ScreenBlock,
    SpecHeader,
)
from mde_spec_loader.blocks import Document, tokenize
from mde_spec_loader.codec import (
    BacklogBlock,
    as_map,
    block_model,
    load_yaml,
)
from mde_spec_loader.errors import SpecParseError
from mde_spec_loader.hashing import normalize_newlines

_T = TypeVar("_T", bound=BaseModel)

_HEADING_LOOKALIKE = re.compile(r"^## \d+\. ")


# --------------------------------------------------------------------------- summary text


def normalize_summary(summary: str) -> str:
    """The form in which a summary is stored in the file (LF newlines, no outer blank lines)."""
    return summary.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def _needs_escape(line: str) -> bool:
    return (
        bool(_HEADING_LOOKALIKE.match(line))
        or line.startswith(("```", "~~~"))
        or line.startswith("\\")
    )


def escape_summary(summary: str) -> str:
    """Prefix lines that would be mistaken for a section heading or a fence with a backslash."""
    lines = normalize_summary(summary).split("\n")
    return "\n".join("\\" + line if _needs_escape(line) else line for line in lines)


def unescape_summary(text: str) -> str:
    lines = text.split("\n")
    return "\n".join(
        line[1:] if line.startswith("\\") and _needs_escape(line[1:]) else line for line in lines
    )


# --------------------------------------------------------------------------- header


def parse_header(doc: Document) -> SpecHeader:
    raw = doc.block("HEADER")
    if raw is None:  # pragma: no cover - tokenize always yields the front matter
        raise SpecParseError("missing front matter", line=1)
    data = dict(as_map(load_yaml(doc.body_text(raw), line=2), "front matter", line=2))
    if "schema" in data:
        data["schema_id"] = data.pop("schema")
    try:
        return SpecHeader.model_validate(data)
    except ValidationError as exc:
        fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
        raise SpecParseError(f"front matter is invalid ({fields})", line=2) from exc


def extract_summary(doc: Document) -> str:
    section = doc.section(1)
    if section is None:
        raise SpecParseError("missing section 1 (Summary)")
    newline = doc.text.find("\n", section.start)
    body_start = section.end if newline == -1 else min(newline + 1, section.end)
    return unescape_summary(doc.text[body_start : section.end].strip("\n"))


# --------------------------------------------------------------------------- assembly


def _expect(model: BaseModel, cls: type[_T]) -> _T:
    if not isinstance(model, cls):  # pragma: no cover - block_model returns the kind's class
        raise SpecParseError(f"expected {cls.__name__}")
    return model


def parse_document(doc: Document) -> AppSpec:
    """Build the ``AppSpec`` of a tokenized document (every block re-validated by its model)."""
    singles: dict[str, BaseModel] = {}
    screens: list[ScreenBlock] = []
    flows: list[FlowBlock] = []
    backlog: BacklogBlock | None = None
    canonical = {v: k for k, v in SINGLETON_BLOCK_IDS.items()}

    for raw in doc.blocks:
        if raw.kind == "header":
            continue
        body = load_yaml(doc.body_text(raw), line=raw.line_start + 1)
        model = block_model(raw.kind, body, raw.id, line=raw.line_start)
        if raw.kind == "screen":
            screens.append(_expect(model, ScreenBlock))
        elif raw.kind == "flow":
            flows.append(_expect(model, FlowBlock))
        elif raw.kind == "backlog":
            if raw.id != "BACKLOG":
                raise SpecParseError("the backlog block must have id BACKLOG", line=raw.line_start)
            backlog = _expect(model, BacklogBlock)
        else:
            if canonical.get(raw.id) != raw.kind:
                raise SpecParseError(
                    f"block id {raw.id} does not belong to kind {raw.kind}", line=raw.line_start
                )
            singles[raw.kind] = model

    def need(kind: str) -> BaseModel:
        model = singles.get(kind)
        if model is None:
            raise SpecParseError(f"missing required block {SINGLETON_BLOCK_IDS[kind]}")
        return model

    try:
        return AppSpec(
            header=parse_header(doc),
            summary=extract_summary(doc),
            app=_expect(need("app"), AppBlock),
            design=_expect(need("design"), DesignBlock),
            navigation=_expect(need("navigation"), NavigationBlock),
            data=_expect(need("data"), DataBlock),
            api=_expect(need("api"), ApiBlock),
            roles=_expect(need("roles"), RolesBlock),
            integrations=_expect(need("integrations"), IntegrationsBlock),
            behavior=_expect(need("behavior"), BehaviorBlock),
            screens=screens,
            flows=flows,
            build=_expect(need("build_sequence"), BuildSequence),
            acceptance=_expect(need("acceptance"), AcceptanceBlock),
            assumptions=_expect(need("assumptions"), AssumptionsBlock),
            revision=_expect(need("revision"), RevisionBlock),
            backlog=[] if backlog is None else backlog.items,
        )
    except ValidationError as exc:  # pragma: no cover - blocks were validated one by one
        raise SpecParseError(f"spec is invalid: {exc.error_count()} error(s)") from exc


def parse_spec(text: str) -> AppSpec:
    """Parse the text of an ``APP_SPEC.md`` into an ``AppSpec``; raises ``SpecParseError``."""
    return parse_document(tokenize(normalize_newlines(text)))


def split_blocks(text: str) -> dict[str, str]:
    """Block id -> exact fenced block text (fences included), in file order."""
    doc = tokenize(normalize_newlines(text))
    return {raw.id: doc.block_text(raw) for raw in doc.blocks}


__all__ = [
    "escape_summary",
    "extract_summary",
    "normalize_summary",
    "parse_document",
    "parse_header",
    "parse_spec",
    "split_blocks",
    "unescape_summary",
]
