"""``SpecIndex``: the read-only entry point of the Spec Loader.

Reads a file path or a string, indexes ``ID -> location -> size`` of every fenced block, and
answers ``index()``, ``get(ids)``, ``slice(step_or_screen)``, ``neighbors(id)``, ``refs(id)``,
``diff(other)`` and ``verify(hash)``. It never edits the spec and does no I/O besides reading.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import cached_property
from pathlib import Path

from mde_contracts.spec import AppSpec, SpecHeader
from mde_spec_loader.blocks import Document, tokenize
from mde_spec_loader.diff import diff_specs
from mde_spec_loader.errors import UnknownIdError
from mde_spec_loader.fragments import item_text
from mde_spec_loader.graph import RefGraph, Refs, build_ref_graph
from mde_spec_loader.hashing import blank_hash, normalize_newlines, verify_hash
from mde_spec_loader.models import BlockEntry, IndexView, SectionEntry, Slice, SpecDiff
from mde_spec_loader.parse import extract_summary, parse_document, parse_header
from mde_spec_loader.slicing import build_slice, format_spec_tag
from mde_spec_loader.tokens import estimate_tokens


class SpecIndex:
    """An indexed ``APP_SPEC.md``. Cheap to build (a scan); the spec and graph load lazily."""

    def __init__(self, text: str) -> None:
        self._text = normalize_newlines(text)
        self._doc: Document = tokenize(self._text)

    @classmethod
    def from_text(cls, text: str) -> SpecIndex:
        return cls(text)

    @classmethod
    def from_path(cls, path: str | Path) -> SpecIndex:
        """Read a spec file (UTF-8; CRLF tolerated) and index it."""
        return cls(Path(path).read_bytes().decode("utf-8"))

    # ------------------------------------------------------------------ parsed views

    @property
    def text(self) -> str:
        """The LF-normalised file text."""
        return self._text

    @property
    def document(self) -> Document:
        return self._doc

    @cached_property
    def header(self) -> SpecHeader:
        return parse_header(self._doc)

    @cached_property
    def spec(self) -> AppSpec:
        """The whole file as an ``AppSpec`` (every block re-validated by its model)."""
        return parse_document(self._doc)

    @cached_property
    def graph(self) -> RefGraph:
        return build_ref_graph(self.spec)

    # ------------------------------------------------------------------ index()

    def index(self) -> IndexView:
        """Sections and ids with their sizes (estimated tokens)."""
        doc = self._doc
        blocks: list[BlockEntry] = []
        for raw in doc.blocks:
            text = doc.block_text(raw)
            if raw.kind == "header":
                text = blank_hash(text)
            blocks.append(
                BlockEntry(
                    id=raw.id,
                    kind=raw.kind,
                    section=raw.section,
                    line_start=raw.line_start,
                    line_end=raw.line_end,
                    chars=raw.end - raw.start,
                    tokens=estimate_tokens(text),
                )
            )
        sections: list[SectionEntry] = []
        for sec in doc.sections:
            ids = [b.id for b in blocks if b.section == sec.number]
            if sec.number == 1:
                ids = ["SUMMARY"]
            if sec.number == 0:
                tokens = next(b.tokens for b in blocks if b.id == "HEADER")
            else:
                tokens = estimate_tokens(doc.text[sec.start : sec.end])
            sections.append(
                SectionEntry(
                    number=sec.number,
                    key=sec.key,
                    title=sec.title,
                    ids=ids,
                    tokens=tokens,
                    line_start=sec.line_start,
                )
            )
        header = self.header
        return IndexView(
            spec_version=header.spec_version,
            app_id=header.app_id,
            hash=header.hash,
            sections=sections,
            blocks=blocks,
            total_tokens=estimate_tokens(self._text),
        )

    # ------------------------------------------------------------------ get()

    def has(self, spec_id: str) -> bool:
        """Is ``spec_id`` a block, ``SUMMARY``, or an item (entity, operation, step, ...)?"""
        try:
            self.get([spec_id])
        except UnknownIdError:
            return False
        return True

    def get(self, ids: Iterable[str] | str) -> dict[str, str]:
        """Exact text per id: blocks as the fenced text of the file, items re-dumped canonically.

        ``HEADER`` is the front matter, ``SUMMARY`` the summary prose. Unknown ids raise
        ``UnknownIdError``.
        """
        wanted = [ids] if isinstance(ids, str) else list(ids)
        out: dict[str, str] = {}
        for spec_id in wanted:
            raw = self._doc.block(spec_id)
            if raw is not None:
                out[spec_id] = self._doc.block_text(raw)
            elif spec_id == "SUMMARY":
                out[spec_id] = extract_summary(self._doc)
            else:
                text = item_text(self.spec, spec_id)
                if text is None:
                    raise UnknownIdError(spec_id)
                out[spec_id] = text
        return out

    # ------------------------------------------------------------------ slice()

    def slice(
        self,
        target: str,
        *,
        kit_signatures: Mapping[str, Sequence[str]] | None = None,
        stack_rules: Sequence[str] = (),
        tier3: bool = False,
        max_neighbors: int = 6,
    ) -> Slice:
        """The tiered context bundle for a step (``S0``..``S10``), a screen or a flow.

        ``kit_signatures`` maps a recipe id to the signatures of the kit components it uses (the
        frontend engine owns the kit); ``stack_rules`` are extra tier-0 lines; ``tier3`` adds the
        neighbouring screens.
        """
        return build_slice(
            self,
            target,
            kit_signatures=kit_signatures,
            stack_rules=stack_rules,
            tier3=tier3,
            max_neighbors=max_neighbors,
        )

    # ------------------------------------------------------------------ graph queries

    def neighbors(self, spec_id: str) -> list[str]:
        """Screens linked to ``spec_id`` by navigation, actions or flow order."""
        if not self.graph.exists(spec_id):
            raise UnknownIdError(spec_id)
        return self.graph.neighbors(spec_id)

    def refs(self, spec_id: str, *, deep: bool = True) -> Refs:
        """Entities, operations, integrations and roles a block or item references."""
        if not self.graph.exists(spec_id):
            raise UnknownIdError(spec_id)
        return self.graph.refs(spec_id, deep=deep)

    def unresolved_refs(self) -> list[str]:
        """Human-readable list of references that do not resolve (empty for a valid spec)."""
        out: list[str] = []
        for edge in self.graph.unresolved:
            detail = f" ({edge.detail})" if edge.detail else ""
            out.append(f"{edge.src} -{edge.relation}-> {edge.dst}{detail}")
        return out

    # ------------------------------------------------------------------ diff / verify / locked

    def diff(self, other: SpecIndex | str) -> SpecDiff:
        """Changed ids from this version (vA) to ``other`` (vB, an index or the file text)."""
        newer = other if isinstance(other, SpecIndex) else SpecIndex(other)
        return diff_specs(self.spec, newer.spec)

    def verify(self, expected: str | None = None) -> bool:
        """True if the file is untampered: its embedded hash matches the recomputed sha256 (and
        ``expected``, e.g. the hash an approval is bound to, when given)."""
        return verify_hash(self._text, expected)

    def locked_screens(self) -> list[str]:
        """Screens marked ``locked`` (hand-edited downstream): skipped on regeneration."""
        return [s.id for s in self.spec.screens if s.locked]

    def spec_tag(self, spec_id: str) -> str:
        """The header a generated file for ``spec_id`` carries, e.g. ``@spec SCR-014@v7``."""
        return format_spec_tag(spec_id, self.header.spec_version)
