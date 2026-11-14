"""Portable call harness: pure functions around one structured model call (doc 18 section 4).

No network, no model, no vendor name. The Spec agent wires these to the gateway.
"""

from .cache import (
    BypassCache,
    InMemoryResultCache,
    ResultCache,
    checkpoint_key,
    result_cache_key,
)
from .fusion import (
    FusionDecision,
    FusionJob,
    calls_needed,
    can_fuse,
    items_per_call,
    plan_fusion,
    unfuse_needed,
)
from .ladder import FixKind, RCodeChange, RCodeLog, WeakRouteBanner, weak_route_banner
from .parse import ParseResult, TopLevelShape, json_candidates, tolerant_parse
from .prompt import (
    CLOSING_LINE,
    assemble_prompt,
    build_prompt_parts,
    estimate_tokens,
    wrap_untrusted,
)
from .reask import (
    FailedItem,
    ReaskBatch,
    ReaskLedger,
    ReaskPlan,
    build_batch_payload,
    build_reask_payload,
    plan_reasks,
)
from .schema_check import (
    CompileReport,
    Issue,
    SchemaMetrics,
    check_all,
    compile_check,
)
from .split import (
    OUTCOME_HANDLING,
    CallOutcome,
    handling_for,
    is_irreducible,
    split_on_truncation,
)

__all__ = [
    "CLOSING_LINE",
    "OUTCOME_HANDLING",
    "BypassCache",
    "CallOutcome",
    "CompileReport",
    "FailedItem",
    "FixKind",
    "FusionDecision",
    "FusionJob",
    "InMemoryResultCache",
    "Issue",
    "ParseResult",
    "RCodeChange",
    "RCodeLog",
    "ReaskBatch",
    "ReaskLedger",
    "ReaskPlan",
    "ResultCache",
    "SchemaMetrics",
    "TopLevelShape",
    "WeakRouteBanner",
    "assemble_prompt",
    "build_batch_payload",
    "build_prompt_parts",
    "build_reask_payload",
    "calls_needed",
    "can_fuse",
    "check_all",
    "checkpoint_key",
    "compile_check",
    "estimate_tokens",
    "handling_for",
    "is_irreducible",
    "items_per_call",
    "json_candidates",
    "plan_fusion",
    "plan_reasks",
    "result_cache_key",
    "split_on_truncation",
    "tolerant_parse",
    "unfuse_needed",
    "weak_route_banner",
    "wrap_untrusted",
]
