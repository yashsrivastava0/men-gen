"""One hard cap for every path: a LIVE proof runner books to the Spec service's own spend record, an offline one keeps
a private record (it costs nothing)."""

from __future__ import annotations

from pathlib import Path

from evals.spec.common import make_proof_runner, proof_record_path
from mde_spec_agent.config import SpecSettings, data_dir


def test_a_live_runner_shares_the_services_spend_record(tmp_path: Path) -> None:
    settings = SpecSettings(spec_data_dir=tmp_path / "service-data")
    live = proof_record_path(tmp_path / "work", offline=False, settings=settings)
    assert live == data_dir(settings) / "spend-record.json"
    assert (
        live == tmp_path / "service-data" / "spend-record.json"
    )  # the file the service and the spend check read


def test_an_offline_runner_keeps_a_private_record_in_its_work_folder(tmp_path: Path) -> None:
    settings = SpecSettings(spec_data_dir=tmp_path / "service-data")
    assert proof_record_path(tmp_path / "work", offline=True, settings=settings) == (
        tmp_path / "work" / "spend-record.json"
    )


def test_the_default_offline_proof_runner_writes_no_money_to_the_service_record(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SPEC_DATA_DIR", str(tmp_path / "service-data"))
    runner = make_proof_runner(tmp_path / "work")
    assert runner is not None
    assert not (tmp_path / "service-data" / "spend-record.json").exists()
