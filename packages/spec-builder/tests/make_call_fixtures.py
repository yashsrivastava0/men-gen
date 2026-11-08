"""Regenerate the golden fixtures of the builder call specs (not a test: run it by hand).

    uv run python packages/spec-builder/tests/make_call_fixtures.py

Writes ``input.json`` and ``output.json`` (the mock handler's answer) into each call folder under
``packages/prompts/src/mde_prompts/calls/<folder>/fixtures``. ``invalid_proposals.json`` files are
hand-written and kept. Run it after changing a mock handler or an input model, bump the call's
``version`` if the prompt or schema changed, then re-run the prompts tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from helpers import make_note, make_request  # noqa: E402

from mde_content.packs import load_pack  # noqa: E402
from mde_content.stack import load_stack_profile  # noqa: E402
from mde_contracts.policy import Limits  # noqa: E402
from mde_contracts.spec import ScreenAction  # noqa: E402
from mde_prompts.calls.c9a_screens_nav.mock import handle as handle_9a  # noqa: E402
from mde_prompts.calls.c9b_data_api_roles.mock import handle as handle_9b  # noqa: E402
from mde_prompts.calls.c9c_design_tokens.mock import handle as handle_9c  # noqa: E402
from mde_prompts.calls.c10_screen_briefs.mock import handle as handle_10  # noqa: E402
from mde_prompts.calls.c12_critic.mock import handle as handle_12  # noqa: E402
from mde_prompts.calls.c13_repair.mock import handle as handle_13  # noqa: E402
from mde_spec_builder import context as ctx  # noqa: E402
from mde_spec_builder.baseline import build_baseline  # noqa: E402
from mde_spec_builder.proposals import quick_findings  # noqa: E402
from mde_spec_builder.research_additions import included_suggestions  # noqa: E402

ROOT = HERE.parents[2] / "packages" / "prompts" / "src" / "mde_prompts" / "calls"
NOTE = make_note(feature_applies_to=None)  # the model path: the pack does not realise RA-001
SLOTS = {
    "compliance__flags": ["precise_location", "financial_data"],
    "regions": ["in"],
    "locale": "hi",
}


def dump(folder: str, name: str, model) -> None:  # type: ignore[no-untyped-def]
    path = ROOT / folder / "fixtures" / name
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    every = [f.id for f in load_pack("commerce").features]
    # C9a, C9b, C9c: the default-feature baseline (the favorites screen is still missing)
    req = make_request("commerce", research=NOTE, **SLOTS)
    spec = build_baseline(req)
    brief = ctx.brief_context(req, spec)
    research = included_suggestions(NOTE, [])
    a = ctx.c9a_input(brief, spec, [s for s in research if s.kind == "feature"], room=8)
    dump("c9a_screens_nav", "input.json", a)
    dump("c9a_screens_nav", "output.json", handle_9a(a))
    b = ctx.c9b_input(brief, spec, [s for s in research if s.kind == "integration"])
    dump("c9b_data_api_roles", "input.json", b)
    dump("c9b_data_api_roles", "output.json", handle_9b(b))
    c = ctx.c9c_input(brief, spec, ["#E4572E"], ["playful", "warm"], [])
    dump("c9c_design_tokens", "input.json", c)
    dump("c9c_design_tokens", "output.json", handle_9c(c))
    # C10, C12, C13: the all-features baseline
    req = make_request("commerce", features=every, research=NOTE, **SLOTS)
    spec = build_baseline(req)
    brief = ctx.brief_context(req, spec)
    batch = [s for s in spec.screens if s.id in ("SCR-006", "SCR-011", "SCR-012")]
    ten = ctx.c10_input(brief, spec, batch, research[:1])
    dump("c10_screen_briefs", "input.json", ten)
    dump("c10_screen_briefs", "output.json", handle_10(ten))
    broken = next(s for s in spec.screens if s.id == "SCR-006")
    bad = broken.model_copy(
        update={"actions": [*broken.actions, ScreenAction(name="broken", go="SCR-099")]}
    )
    spec_bad = spec.model_copy(
        update={"screens": [bad if s.id == "SCR-006" else s for s in spec.screens]}
    )
    errors = [
        f for f in quick_findings(spec_bad, Limits(), load_stack_profile()) if f.severity == "error"
    ]
    twelve = ctx.c12_input(brief, spec_bad, errors)
    dump("c12_critic", "input.json", twelve)
    dump("c12_critic", "output.json", handle_12(twelve))
    thirteen = ctx.c13_input(brief, spec_bad, errors, round_number=1)
    dump("c13_repair", "input.json", thirteen)
    dump("c13_repair", "output.json", handle_13(thirteen))
    print("fixtures written")


if __name__ == "__main__":
    main()
