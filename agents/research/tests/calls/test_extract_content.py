"""The extraction calls for the content kinds: provider facts and platform constraints (D-159, D-162).

Any schema or prompt change bumps the call spec version; the mock handler still produces valid output; the model gets no tool and
no URL, action or tool field in its output schema; web content stays untrusted.
"""

from __future__ import annotations

from datetime import date

import pytest

from mde_llm_gateway import to_strict_schema
from mde_research_agent.calls.extract import (
    JOB_KINDS,
    KIND_GUIDE,
    SPEC_VERSION,
    SPECS,
    ExtractIn,
    extract_out_model,
    mock_extract,
)
from mde_research_agent.catalog.content import dossier_config
from mde_research_agent.engine.state import PageRecord
from mde_research_agent.engine.verify import Candidate, candidates_from_output

TEXT = (
    "Card payments are charged 2.9 percent per transaction and the provider settles funds in 2 business days. "
    "A sandbox with test keys is available for every account and webhook events are signed. "
    "Apps that sell digital goods must use the store billing system on both platforms."
)


def extract_in(job: str) -> ExtractIn:
    kinds = list(JOB_KINDS[job])
    return ExtractIn(
        job=job,
        question="What do the pages say?",
        anchor="payments",
        place="",
        language="en",
        focus="focus",
        kinds=kinds,
        want_kinds=kinds,
        kind_guides=[KIND_GUIDE[k] for k in kinds],
        slots=["providers"],
        page_title="Docs",
        page_domain="docs.example",
        page_published=None,
        passages=TEXT,
        retry_keys=[],
    )


def page() -> PageRecord:
    return PageRecord(
        id="pg_1",
        url="https://docs.example/p",
        final_url="https://docs.example/p",
        domain="docs.example",
        source_class="official",
        title="Docs",
        text=TEXT,
        content_hash="h",
        text_source="fetch",
        published=None,
        retrieved_on=date(2026, 11, 11),
    )


def test_the_jobs_and_their_kinds() -> None:
    assert JOB_KINDS["platform"] == ("platform_constraint",)
    assert JOB_KINDS["services"][0] == "provider_fact" and "service_option" in JOB_KINDS["services"]
    for kinds in JOB_KINDS.values():
        assert all(k in KIND_GUIDE for k in kinds)


def test_every_extraction_spec_is_bumped_for_the_schema_and_prompt_change() -> None:
    assert SPEC_VERSION >= 2
    assert {s.spec.version for s in SPECS.values()} == {SPEC_VERSION}
    assert "rsch.extract.platform" in {s.spec.id for s in SPECS.values()}


def test_the_provider_fact_guide_lists_every_dossier_field_from_the_data_file_and_asks_for_one_fact() -> (
    None
):
    guide = KIND_GUIDE["provider_fact"]
    for key in dossier_config().extractable():
        assert key in guide, key
    assert "official_docs" not in guide, "code sets it; the model is not asked for it"
    assert "ONE fact" in guide and "ONE quote" in guide and "typed" in guide


def test_the_platform_guide_says_the_effect_on_the_build_is_only_what_the_page_states() -> None:
    guide = KIND_GUIDE["platform_constraint"]
    assert (
        "ONLY when the page says" in guide
        and "needs_development_build" in guide
        and "needs_config_plugin" in guide
    )


@pytest.mark.parametrize("job", ["services", "platform"])
def test_the_schemas_are_strict_safe_with_no_tools_and_untrusted_inputs(job: str) -> None:
    spec = SPECS[job].spec
    to_strict_schema(extract_out_model(job), max_depth=5)  # type: ignore[arg-type]
    assert not spec.tools and set(spec.untrusted_inputs) >= {"passages", "page_title"}
    assert spec.mock_handler and SPECS[job].fallback


@pytest.mark.parametrize("job", ["services", "platform"])
def test_the_mock_handler_gives_valid_items_that_survive_the_code_checks(job: str) -> None:
    out = mock_extract(extract_in(job))
    kinds = JOB_KINDS[job]
    assert set(out.model_dump()) == set(kinds)
    found = candidates_from_output(out, page())
    assert found, "the mock extracts something from real sentences"
    assert all(isinstance(c, Candidate) for c in found), [
        c for c in found if not isinstance(c, Candidate)
    ]
    for c in found:
        if c.kind == "provider_fact":
            assert c.data["field"] in dossier_config().extractable() and c.data["typed"] is None


def test_the_nothing_fallback_is_valid_for_the_new_job() -> None:
    inp = extract_in("platform")
    out = SPECS["platform"].fallback(inp)  # type: ignore[misc]
    assert out.model_dump() == {"platform_constraint": []}


def test_the_prompt_tells_the_model_that_these_kinds_are_atoms() -> None:
    from pathlib import Path

    from mde_research_agent.calls import extract

    text = (Path(extract.__file__).parent / "templates" / "extract.system.jinja").read_text(
        encoding="utf-8"
    )
    assert "ATOMS" in text and "one fact, one quote" in text
