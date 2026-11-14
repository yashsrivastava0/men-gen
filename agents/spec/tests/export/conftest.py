"""Export tests: one real run folder made with the gateway's mock mode (no key, no network, no cost), then copies of
it that the tests change. The scripts under ``scripts/`` are loaded by path (``scripts`` is not a package)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"

PlantFn = Callable[[Path, str, str], None]


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_export_tests_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def exporter() -> ModuleType:
    return load_script("spec_export_outputs")


@pytest.fixture(scope="session")
def run_report() -> ModuleType:
    return load_script("spec_run_report")


@pytest.fixture(scope="session")
def mock_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A private Spec data folder holding one finished, approved mock run (synth:small, offline)."""
    from mde_spec_agent.service import run_to_end

    data = tmp_path_factory.mktemp("spec-data")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SPEC_DATA_DIR", str(data))
        mp.setenv("RESEARCH_DATA_DIR", str(data / "no-research"))
        mp.setenv("LLM_MODE", "mock")
        mp.setenv("MDE_FORBID_LIVE", "1")
        mp.setenv("MDE_ENV_FILE", "")
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MODEL_ROUTES"):
            mp.delenv(key, raising=False)
        result = run_to_end(synth="small", offline=True)
    assert result["status"] == "approved"
    return data


@pytest.fixture(scope="session")
def mock_run(mock_data_dir: Path) -> Path:
    runs = sorted((mock_data_dir / "runs").iterdir())
    assert len(runs) == 1
    return runs[0]


@pytest.fixture
def run_copy(mock_run: Path, tmp_path: Path) -> Path:
    """A private copy of the mock run folder that a test may change."""
    dest = tmp_path / "runs" / mock_run.name
    shutil.copytree(mock_run, dest)
    return dest


@pytest.fixture
def plant() -> PlantFn:
    """``plant(run, "AGENTS.md", text)``: append a line to a pack file AND fix its hash in MANIFEST.json, so the
    pack is still internally consistent (the way a model-written phrase would arrive in a real run)."""

    def do(run: Path, rel: str, text: str) -> None:
        path = run / "pack" / rel
        data = path.read_bytes() + f"\n{text}\n".encode()
        path.write_bytes(data)
        manifest_path = run / "pack" / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][rel]["sha256"] = hashlib.sha256(data).hexdigest()
        manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")

    return do


def tree_digest(base: Path) -> dict[str, str]:
    """Path -> sha256 of every file under ``base`` (to prove a folder was not touched)."""
    return {
        p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def digest() -> Callable[[Path], dict[str, str]]:
    return tree_digest
