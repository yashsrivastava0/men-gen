"""The eval datasets and graders (brief 9.3): personas, the situation catalog, the sufficiency probe.

* ten personas, one writing Hindi and one Hinglish, each played offline against the real product
  and meeting its own expectations;
* the situations dataset is the spec table (``make_situations --check``) and every row has a
  scenario file;
* the sufficiency probe: its call spec is a valid, strict-schema call that stays out of the product
  catalog; the offline reader answers every question from a golden slice, and a slice that lacks a
  field is reported as a missing field (the probe can fail).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from evals.graders.sufficiency import offline_probe_client, pick_screens, run_probe
from evals.harness.briefs import load_briefs
from evals.harness.catalog import ROOT, parse_situation_table
from evals.harness.persona import load_personas, play_persona
from evals.harness.stack import NetworkGuard, build_stack
from evals.probes.sufficiency.models import ProbeInput, ProbeOutput
from evals.probes.sufficiency.questions import matches, questions_for
from evals.probes.sufficiency.spec import PROBE_ID, SPEC
from evals.runners import make_situations
from mde_contracts.registry import CALL_IO
from mde_core.redact import find_secrets
from mde_llm_gateway.strict_schema import to_strict_schema
from mde_prompts.registry import all_specs
from mde_spec_loader import SpecIndex, parse_spec

pytestmark = pytest.mark.e2e

PERSONAS = load_personas()
FULL_SESSIONS = {"P02-vague-founder-hindi", "P09-enterprise-services-firm"}
HAS_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


# --------------------------------------------------------------------------- personas


def test_there_are_ten_personas_including_hindi_and_hinglish() -> None:
    assert len(PERSONAS) == 10
    assert len({p.id for p in PERSONAS}) == 10
    languages = {p.language for p in PERSONAS}
    assert {"hi", "hi-Latn", "en"} <= languages
    text = {p.id: yaml.safe_dump(p.script, allow_unicode=True) for p in PERSONAS}
    assert any(HAS_DEVANAGARI.search(t) for t in text.values()), "no persona writes Devanagari"
    kinds = " ".join(p.id for p in PERSONAS)
    for needle in ("hi-only", "vague", "chatty", "confused", "prd", "three-apps", "ar-game",
                   "health", "enterprise", "adversarial"):  # fmt: skip
        assert needle in kinds, f"no persona for {needle}"
    assert any(p.mode == "enterprise" and p.policy for p in PERSONAS)


def test_persona_secrets_are_marked_for_the_secret_scan() -> None:
    for persona in PERSONAS:
        assert persona.path is not None
        for line in persona.path.read_text(encoding="utf-8").splitlines():
            if find_secrets(line):
                assert "secret-scan: allow" in line, f"{persona.id}: an unmarked secret fixture"


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.id)
async def test_persona_plays_to_its_expected_outcome(
    persona,
    tmp_path: Path,
    socket_guard: NetworkGuard,  # type: ignore[no-untyped-def]
) -> None:
    # two personas (Hindi, Enterprise) go all the way to the hand-off; the others stop
    # at the Brief (the e2e journeys and the mock batch cover planning for every archetype)
    until = "end" if persona.id in FULL_SESSIONS else "brief"
    run = await play_persona(build_stack(tmp_path / "projects"), persona, until=until)
    assert run.problems == [], run.problems
    if until == "end" and "state" in persona.expect:
        assert run.state == persona.expect["state"]


# --------------------------------------------------------------------------- situations dataset


def test_the_situations_dataset_is_the_spec_table() -> None:
    assert make_situations.main(["--check"]) == 0
    rows = yaml.safe_load(make_situations.OUT.read_text(encoding="utf-8"))["situations"]
    assert [r["situation"] for r in rows] == [r.situation for r in parse_situation_table()]
    assert len(rows) == 40
    assert all(r["scenario"] and (ROOT / r["scenario"]).is_file() for r in rows)


# --------------------------------------------------------------------------- the probe


def test_the_probe_is_an_eval_call_not_a_product_call() -> None:
    assert PROBE_ID not in CALL_IO
    assert PROBE_ID not in all_specs(missing_ok=True)
    assert SPEC.output_model is ProbeOutput and SPEC.input_model is ProbeInput
    assert SPEC.tools == () and SPEC.untrusted_inputs == ("slice_text",)
    assert (SPEC.template_root / SPEC.system_template).is_file()
    assert (SPEC.template_root / SPEC.user_template).is_file()
    assert Path(__file__).resolve().parents[2] in SPEC.template_root.parents
    schema = to_strict_schema(ProbeOutput)  # must be convertible to the strict subset
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("brief", load_briefs(), ids=lambda b: b.id)
async def test_every_screen_slice_of_a_golden_spec_answers_its_own_questions(brief) -> None:  # type: ignore[no-untyped-def]
    text = brief.golden_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    report = await run_probe(offline_probe_client(), text, limit=None)
    assert report.asked > 50 and report.accuracy == 1.0, (
        report.missing_fields,
        report.failures[:3],
    )
    assert report.missing_fields == []
    assert max(report.slice_tokens) <= 10_000  # the probe slices obey the consumer budget


async def test_a_slice_that_lacks_a_field_is_reported_as_a_missing_field() -> None:
    brief = next(b for b in load_briefs() if b.archetype == "commerce")
    text = brief.golden_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    index = SpecIndex(text)
    spec = parse_spec(text)
    screen = next(s for s in spec.screens if s.actions)
    questions = questions_for(spec, screen.id)
    client = offline_probe_client()
    from evals.probes.sufficiency.models import ProbeInput as Input
    from mde_contracts.gateway import CallContext

    async def answer(slice_text: str) -> dict[str, str]:
        out = await client.run(
            SPEC,
            Input(slice_text=slice_text, questions=[q.question for q in questions]),
            CallContext(request_id="req_x", budget_scope="eval:probe", purpose="eval:probe"),
        )
        return {a.id: a.answer for a in out.output.answers}  # type: ignore[union-attr]

    full = await answer(index.slice(screen.id).text)
    assert all(matches(q.expected, full[q.question.id]) for q in questions)
    # remove the actions from the slice: the action questions can no longer be answered
    stripped = re.sub(r"\nactions:\n(?:[ -].*\n)+", "\n", index.slice(screen.id).text)
    assert stripped != index.slice(screen.id).text
    partial = await answer(stripped)
    action_ids = [q.question.id for q in questions if q.question.kind == "action_target"]
    assert action_ids and all(partial[i] == "unknown" for i in action_ids)
    # a slice of ANOTHER screen answers nothing about this one
    other = next(s for s in spec.screens if s.id != screen.id)
    foreign = await answer(index.slice(other.id).text)
    assert all(v == "unknown" for v in foreign.values())


def test_the_screen_sample_is_even_and_deterministic() -> None:
    ids = [f"SCR-{n:03d}" for n in range(1, 25)]
    assert pick_screens(ids, 8) == pick_screens(ids, 8)
    assert pick_screens(ids, 8)[0] == "SCR-001" and len(pick_screens(ids, 8)) == 8
    assert pick_screens(ids, None) == ids and pick_screens(ids, 99) == ids
