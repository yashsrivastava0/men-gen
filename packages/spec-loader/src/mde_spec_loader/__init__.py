"""Read-only loader for the frontend engine: index, get, slice, neighbors, refs, diff, verify."""

from mde_spec_loader.blocks import Document, RawBlock, RawSection, tokenize
from mde_spec_loader.codec import BacklogBlock, block_data, dump_yaml, load_yaml, model_data
from mde_spec_loader.consumer import DEFAULT_BUDGET_TOKENS, simulate_consumer
from mde_spec_loader.diff import diff_specs, diff_texts
from mde_spec_loader.errors import SpecError, SpecParseError, UnknownIdError
from mde_spec_loader.fragments import all_fragments, block_bodies, fenced, item_text
from mde_spec_loader.graph import (
    Binding,
    Edge,
    RefGraph,
    Refs,
    build_ref_graph,
    parse_binding,
)
from mde_spec_loader.hashing import (
    blank_hash,
    compute_spec_hash,
    embed_hash,
    embedded_hash,
    normalize_newlines,
    verify_hash,
)
from mde_spec_loader.index import SpecIndex
from mde_spec_loader.models import (
    BlockEntry,
    ConsumerReport,
    IndexView,
    SectionEntry,
    Slice,
    SliceReport,
    SliceTier,
    SpecDiff,
)
from mde_spec_loader.parse import (
    escape_summary,
    normalize_summary,
    parse_spec,
    split_blocks,
    unescape_summary,
)
from mde_spec_loader.slicing import format_spec_tag, parse_spec_tag
from mde_spec_loader.tokens import CHARS_PER_TOKEN, estimate_tokens

__version__ = "0.1.0"

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_BUDGET_TOKENS",
    "BacklogBlock",
    "Binding",
    "BlockEntry",
    "ConsumerReport",
    "Document",
    "Edge",
    "IndexView",
    "RawBlock",
    "RawSection",
    "RefGraph",
    "Refs",
    "SectionEntry",
    "Slice",
    "SliceReport",
    "SliceTier",
    "SpecDiff",
    "SpecError",
    "SpecIndex",
    "SpecParseError",
    "UnknownIdError",
    "all_fragments",
    "blank_hash",
    "block_bodies",
    "block_data",
    "build_ref_graph",
    "compute_spec_hash",
    "diff_specs",
    "diff_texts",
    "dump_yaml",
    "embed_hash",
    "embedded_hash",
    "escape_summary",
    "estimate_tokens",
    "fenced",
    "format_spec_tag",
    "item_text",
    "load_yaml",
    "model_data",
    "normalize_newlines",
    "normalize_summary",
    "parse_binding",
    "parse_spec",
    "parse_spec_tag",
    "simulate_consumer",
    "split_blocks",
    "tokenize",
    "unescape_summary",
    "verify_hash",
]
