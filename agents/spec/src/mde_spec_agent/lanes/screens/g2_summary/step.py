"""The `g2summary` step (doc 18 1.2 step 15): the G2 tables from code and ONE paragraph from the model.

Where it is stored (the Bench's G2 view reads it): `ctx.plan["g2_summary"]` in memory and the run file
`evidence/g2-summary.json` (canonical JSON). It is NOT in the graph: `GraphMeta` has no field for it and the summary
must never change the content hash that Gate 2 binds. The paragraph is checkpointed (patch key `g2_summary`); the tables
are rebuilt from the graph, so they are always current. If the call fails (after its one re-ask) the template summary from
code is used and the paragraph is OMITTED, never invented (FN-29)."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.lanes.screens.common import call_versions, lane_rules, log_rcode
from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, NumIn
from mde_spec_agent.lanes.screens.g2_summary.spec import (
    CALL_ID,
    SPEC,
    VERSION,
    out_schema_version,
    prompt_version,
)
from mde_spec_agent.lanes.screens.g2_summary.tables import build_tables, template_summary
from mde_spec_agent.lanes.screens.g2_summary.validate import trim_paragraph
from mde_spec_agent.run.context import RunContext
from mde_spec_agent.run.patch import Patch
from mde_spec_core.canon import canonical_json

FILE = "evidence/g2-summary.json"


def _input(language: str, tables: dict[str, Any]) -> G2In:
    inv = tables["inventory"]
    numbers = [
        NumIn(name="screens", value=inv["screens"]),
        NumIn(name="apps", value=len(inv["apps"])),
        NumIn(name="entities", value=inv["entities"]),
        NumIn(name="operations", value=inv["operations"]),
        NumIn(name="later", value=len(tables["phases"]["later"])),
        NumIn(name="assumed", value=tables["assumptions"]["count"]),
        NumIn(name="degraded", value=tables["degraded"]["count"]),
        NumIn(name="confirm", value=len(tables["confirm"])),
    ]
    lines: list[str] = []
    for kind, rows in tables["differs"].items():
        lines += [f"differs from the Brief ({kind}): {r}" for r in rows[:5]]
    lines += [
        f"to confirm, ranked {c['rank']} ({c['category']}): {c['text']}"
        for c in tables["confirm"][:5]
    ]
    lines += [f"needs a decision: {d['node']}: {d['why']}" for d in tables["needs_decision"][:3]]
    return G2In(
        language=language, numbers=numbers, lines=lines, max_words=lane_rules().g2_max_words
    )


async def g2summary_step(ctx: RunContext) -> None:
    g = ctx.graph
    tables = build_tables(
        g,
        research=ctx.research,
        spec_input=ctx.spec_input,
        plan=ctx.plan,
        flags=ctx.flags,
        rcode=ctx.rcode,
    )
    inp = _input(g.meta.source_language, tables)
    versions = call_versions(ctx, CALL_ID, VERSION, prompt_version(), out_schema_version())

    async def produce() -> Patch:
        if ctx.runner is None:  # code-only: the template summary stands, no paragraph
            return {
                "g2_summary": {
                    "paragraph": None,
                    "paragraph_source": "omitted",
                    "reason": "no model call",
                }
            }
        res = await ctx.runner.run(SPEC, inp, node_ids=["g2-summary"], step="g2summary")
        omitted = res.used_fallback or not res.output.paragraph.strip()
        trimmed = None if omitted else trim_paragraph(res.output.paragraph, inp.max_words)
        rcode = []
        if trimmed is not None and trimmed != " ".join(res.output.paragraph.split()):
            rcode.append(
                {
                    "kind": "drop_unknown_optional",
                    "node": "g2-summary",
                    "what": f"the paragraph was longer than {inp.max_words} words and was cut at a sentence",
                    "before": res.output.paragraph[:80],
                    "after": trimmed[:80],
                }
            )
        return {
            "g2_summary": {
                "paragraph": trimmed,
                "paragraph_source": "omitted" if omitted else "model",
                "reason": res.reason if omitted else "",
            },
            "rcode": rcode,
        }

    patch = await ctx.batch("g2summary", "summary", versions, produce)
    para = patch["g2_summary"]
    log_rcode(ctx, patch.get("rcode", []))
    summary = {
        "language": g.meta.source_language,
        **template_summary(g, tables),
        **para,
        "tables": tables,
    }
    ctx.plan["g2_summary"] = summary
    ctx.store.write_text(ctx.run_id, FILE, canonical_json(summary) + "\n")
    ctx.emit("notice", what="g2summary.done", step="g2summary", paragraph=para["paragraph_source"])
