"""``mde sim``: scripted personas against the API, the player's step vocabulary and its checks."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from mde_cli.client import ApiClient
from mde_cli.console import Out
from mde_cli.errors import CliError
from mde_cli.main import app
from mde_cli.persona import (
    Persona,
    PersonaError,
    PersonaRun,
    check_expectations,
    find_personas_dir,
    load_personas,
    select_personas,
)
from mde_cli.sim import SimOptions, render_runs, run_scripted
from mde_contracts.cards import Card, CardInstance
from mde_contracts.events import AssistantMessage, CardEvent, Notice, SpecReady


def sim(runner: CliRunner, projects: Path, *args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(
        app, ["sim", "--local", "--memory", "--projects-dir", str(projects), *args]
    )


def write_persona(folder: Path, name: str, **fields: Any) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    body = {"id": name, "title": f"Persona {name}", "language": "en", "mode": "guided", **fields}
    path = folder / f"{name}.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


def persona(**fields: Any) -> Persona:
    base: dict[str, Any] = {
        "id": "P00-x",
        "title": "x",
        "who": "",
        "language": "en",
        "mode": "guided",
        "policy": None,
        "script": [],
        "expect": {},
    }
    return Persona(**{**base, **fields})


# --------------------------------------------------------------------------- the dataset


def test_the_default_folder_is_the_repository_s_persona_dataset() -> None:
    folder = find_personas_dir()
    assert folder.name == "personas" and folder.parent.name == "datasets"
    personas = load_personas(folder)
    assert len(personas) == 10 and personas[0].id == "P01-hi-only"
    assert {p.mode for p in personas} == {"guided", "enterprise"}
    assert all(p.script and p.path is not None for p in personas)


def test_the_folder_is_found_from_the_working_directory_or_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = tmp_path / "evals" / "datasets" / "personas"
    mine.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "evals")
    assert find_personas_dir() == mine
    assert find_personas_dir(Path("elsewhere")) == Path("elsewhere")


def test_selecting_by_id_prefix_fragment_or_all() -> None:
    everyone = load_personas(find_personas_dir())
    assert len(select_personas(everyone, "all")) == 10
    assert [p.id for p in select_personas(everyone, "P04-confused-hinglish")] == [
        "P04-confused-hinglish"
    ]
    assert [p.id for p in select_personas(everyone, "p04")] == ["P04-confused-hinglish"]
    assert [p.id for p in select_personas(everyone, "hinglish")] == ["P04-confused-hinglish"]
    assert select_personas(everyone, "nobody") == []


def test_a_malformed_persona_file_is_a_bad_input_error_naming_the_file(tmp_path: Path) -> None:
    (tmp_path / "P01-bad.yaml").write_text("id: P01\n", encoding="utf-8")  # no script
    with pytest.raises(CliError, match=r"P01-bad\.yaml") as raised:
        load_personas(tmp_path)
    assert raised.value.exit_code == 2
    (tmp_path / "P01-bad.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(CliError, match=r"P01-bad\.yaml"):
        load_personas(tmp_path)
    (tmp_path / "P01-bad.yaml").write_text("id: P01\nscript: nope\n", encoding="utf-8")
    with pytest.raises(CliError, match=r"P01-bad\.yaml"):
        load_personas(tmp_path)
    (tmp_path / "P01-bad.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(CliError, match=r"P01-bad\.yaml"):
        load_personas(tmp_path)
    with pytest.raises(CliError, match="is not a directory"):
        load_personas(tmp_path / "nope")


# --------------------------------------------------------------------------- expectations


def run_with(
    events: list[Any],
    *,
    state: str = "HANDED_OFF",
    slots: dict[str, str] | None = None,
    **expect: Any,
) -> PersonaRun:
    run = PersonaRun(persona(expect=expect), state=state, events=events, slots=slots or {})
    return run


def card_event(n: int) -> CardEvent:
    card = Card(id=f"Q-{n}", slot="idea.archetype", type="choice3", text="?")
    return CardEvent(
        event_id=f"evt_c{n}",
        session_id="ses_1",
        seq=n,
        card=CardInstance(instance_id=f"crd_{n}", card=card),
    )


def test_every_expectation_is_checked_against_what_the_api_showed() -> None:
    spec = SpecReady(
        event_id="evt_s", session_id="ses_1", seq=9, app_id="app_1", version=1, hash="a" * 64,
        git_sha="b" * 40, path="APP_SPEC.md", repo_dir="d",
    )  # fmt: skip
    say = AssistantMessage(
        event_id="evt_m", session_id="ses_1", seq=1, text="Here is what I understood"
    )
    note = Notice(
        event_id="evt_n", session_id="ses_1", seq=2, code="secret_redacted", text="Removed it"
    )
    good = run_with(
        [say, note, spec, card_event(3), card_event(4)],
        slots={
            "idea.archetype": "Commerce",
            "scope.apps": "customer",
            "behavior.analytics": "false",
        },
        state="HANDED_OFF",
        spec_ready=1,
        max_cards=10,
        cards_shown_max=3,
        notices=["secret_redacted"],
        text_contains=["here is what i understood", "removed"],
        slots_={},
    )
    good.persona.expect.pop("slots_")
    good.persona.expect["slots"] = {
        "idea.archetype": "commerce",
        "scope.apps": ["customer"],
        "behavior.analytics": False,
    }
    assert check_expectations(good) == [] and good.cards_asked == 2 and good.spec_ready == 1
    bad = run_with(
        [card_event(1), card_event(2), card_event(3)],
        state="PLANNING",
        slots={"idea.archetype": "custom"},
        spec_ready=1,
        max_cards=2,
        cards_shown_max=1,
        notices=["injection_suspected"],
        text_contains=["never said"],
    )
    bad.persona.expect["state"] = "HANDED_OFF"
    bad.persona.expect["slots"] = {"idea.archetype": "commerce", "locale": "hi"}
    problems = check_expectations(bad)
    assert "state 'PLANNING', expected 'HANDED_OFF'" in problems
    assert "spec_ready x0, expected x1" in problems
    assert "3 cards asked, the cap is 2" in problems and "3 cards shown, the limit is 1" in problems
    assert "slot idea.archetype: 'custom', expected 'commerce'" in problems
    assert "slot locale: None, expected 'hi'" in problems
    assert any("notice 'injection_suspected' was never shown" in p for p in problems)
    assert "no message contains 'never said'" in problems


def test_the_result_lines_are_compact_and_list_the_problems() -> None:
    ok = PersonaRun(persona(id="P01-a"), state="DISCOVERING", seconds=0.25)
    failed = PersonaRun(persona(id="P02-longer-name"), state="PLANNING", problems=["state wrong"])
    lines = render_runs([ok, failed], scripted=True)
    assert lines[0].startswith(f"{'P01-a':<15}  ok    DISCOVERING")
    assert lines[1].startswith("P02-longer-name  FAIL  PLANNING")
    assert lines[2] == "    - state wrong" and lines[3] == "1 of 2 personas met their expectations."
    llm = render_runs([ok, failed], scripted=False)
    assert llm[0].split()[1] == "done" and llm[1].split()[1] == "STOP"
    assert llm[-1] == "2 persona(s) played by a model; 1 without problems."


# --------------------------------------------------------------------------- the command


def test_the_default_persona_set_includes_hi_only_and_it_passes(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = sim(runner, tmp_path / "p", "--persona", "P01")
    assert result.exit_code == 0, result.output
    assert "P01-hi-only" in result.stdout and "DISCOVERING" in result.stdout
    assert "cards 0" in result.stdout and "spec_ready 0" in result.stdout
    assert "1 of 1 personas met their expectations." in result.stdout


def test_a_hinglish_persona_plays_to_a_handed_off_spec(runner: CliRunner, tmp_path: Path) -> None:
    result = sim(runner, tmp_path / "p", "--persona", "P04")
    assert result.exit_code == 0, result.output
    assert "P04-confused-hinglish" in result.stdout and "HANDED_OFF" in result.stdout
    assert "spec_ready 1" in result.stdout


def test_the_enterprise_persona_has_every_required_role_approve_as_a_person_of_its_own(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = sim(runner, tmp_path / "p", "--persona", "P09", "--transcript")
    assert result.exit_code == 0, result.output
    assert "=== P09-enterprise-services-firm: IT services firm with a policy" in result.stdout
    assert "Spec ready." in result.stdout and "1 of 1 personas met" in result.stdout


def test_the_adversarial_persona_gets_its_notices_and_nothing_is_echoed(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = sim(runner, tmp_path / "p", "--persona", "P10", "--transcript")
    assert result.exit_code == 0, result.output
    assert "sk-proj-AbCdEf" not in result.output  # the pasted key never comes back
    assert "1 of 1 personas met" in result.stdout


def test_no_match_is_a_bad_input_error(runner: CliRunner, tmp_path: Path) -> None:
    result = sim(runner, tmp_path / "p", "--persona", "nobody")
    assert result.exit_code == 2 and "no persona matches 'nobody'" in result.stderr


def test_a_missing_folder_or_a_broken_file_is_a_bad_input_error(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = sim(runner, tmp_path / "p", "--personas", str(tmp_path / "nope"))
    assert result.exit_code == 2 and "is not a directory" in result.stderr
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "P01-x.yaml").write_text("id: P01\n", encoding="utf-8")
    result = sim(runner, tmp_path / "p", "--personas", str(broken))
    assert result.exit_code == 2 and "P01-x.yaml" in result.stderr


def test_a_failing_expectation_is_a_fail_line_and_exit_1(runner: CliRunner, tmp_path: Path) -> None:
    folder = tmp_path / "personas"
    write_persona(folder, "P01-fails", script=[{"user": "hi"}], expect={"state": "HANDED_OFF"})
    write_persona(folder, "P02-passes", script=[{"user": "hi"}], expect={"state": "DISCOVERING"})
    result = sim(runner, tmp_path / "p", "--personas", str(folder))
    assert result.exit_code == 1, result.output
    assert "P01-fails" in result.stdout and "FAIL" in result.stdout
    assert "    - state 'DISCOVERING', expected 'HANDED_OFF'" in result.stdout
    assert (
        "P02-passes" in result.stdout and "1 of 2 personas met their expectations." in result.stdout
    )


def test_every_step_of_the_vocabulary_is_played(runner: CliRunner, tmp_path: Path) -> None:
    folder = tmp_path / "personas"
    write_persona(
        folder,
        "P01-steps",
        script=[
            {"user": "An online shop for handmade candles"},
            {"tap": {"option": "A"}},  # an option by id
            {"tap": {"you_decide": True}},
            {"tap": {"skip": True}},
            {"tap": {"recommended": True}},
            {"upload": {"filename": "notes.md", "text": "We sell candles and want a shop app."}},
            {"ui": {"action": "finish_with_defaults"}},
            {"approve": "brief"},
            {"plan": True},
            {"approve": "plan"},
        ],
        expect={"state": "HANDED_OFF", "spec_ready": 1},
    )
    result = sim(runner, tmp_path / "p", "--personas", str(folder))
    assert result.exit_code == 0, result.output
    assert "P01-steps" in result.stdout and "HANDED_OFF" in result.stdout


def test_a_step_that_cannot_be_played_is_a_problem_naming_the_step(
    runner: CliRunner, tmp_path: Path
) -> None:
    folder = tmp_path / "personas"
    write_persona(folder, "P01-nocard", script=[{"tap": {"recommended": True}}])
    write_persona(folder, "P02-nogate", script=[{"approve": "brief"}])
    write_persona(folder, "P03-typo", script=[{"say": "hello"}])
    write_persona(
        folder,
        "P04-badoption",
        script=[{"user": "An online shop for candles"}, {"tap": {"option": "ZZZ"}}],
    )
    write_persona(folder, "P05-refused", script=[{"user": "hi"}, {"ui": {"action": "teleport"}}])
    result = sim(runner, tmp_path / "p", "--personas", str(folder))
    out = result.stdout
    assert result.exit_code == 1
    assert "step 1 (tap): There is no question waiting for an answer right now." in out
    assert "step 1 (approve): no brief page is open" in out
    assert "step 1 (say): unknown persona step: ['say']" in out
    assert "step 2 (tap): 'ZZZ' is not an option of this question" in out
    assert "step 2 (ui): the API refused it" in out  # an unknown ui action is a 422 from the API


async def test_the_player_over_the_buffered_transport(api: ApiClient) -> None:
    buffer = io.StringIO()
    personas = [persona(id="P01-a", script=[{"user": "hi"}], expect={"state": "DISCOVERING"})]
    runs = await run_scripted(api, personas, SimOptions(transcript=True), Out(buffer))
    assert [r.ok for r in runs] == [True] and runs[0].session_id.startswith("ses_")
    assert "=== P01-a: x" in buffer.getvalue() and "Assistant:" in buffer.getvalue()


async def test_an_api_error_in_one_persona_does_not_stop_the_others(api: ApiClient) -> None:
    class Refusing(ApiClient):
        async def create_session(self, *args: Any, **kwargs: Any) -> Any:
            from mde_cli.client import ApiError

            raise ApiError(422, "validation_failed", "The request is not valid.")

    refusing = Refusing(api.http, "x", api.identity, buffered=True)
    runs = await run_scripted(refusing, [persona(id="P01-a")], SimOptions(), Out(io.StringIO()))
    assert not runs[0].ok and "the API refused a request" in runs[0].problems[0]


def test_persona_error_is_a_plain_exception() -> None:
    assert issubclass(PersonaError, Exception)
