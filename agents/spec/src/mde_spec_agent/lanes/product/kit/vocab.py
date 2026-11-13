"""The lane's closed vocabularies and hint lists. Data files own the words (hard rule 8); spec-core owns field types,
mock profiles and the graph's own enums, which are read from there so a vocabulary change is a data change."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel

from mde_spec_core import vocab as core_vocab
from mde_spec_core.graph import Entity, Flow, Operation, Stage, Story

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@cache
def _raw(name: str) -> dict[str, Any]:
    data = yaml.safe_load((DATA_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "version" not in data:
        raise ValueError(f"{name}.yaml: a mapping with a `version` key is required")
    return data


def vocab_data() -> dict[str, Any]:
    return _raw("vocab")


def hints() -> dict[str, Any]:
    return _raw("hints")


def literal_values(model: type[BaseModel], field: str) -> tuple[str, ...]:
    ann = model.model_fields[field].annotation
    return tuple(str(v) for v in get_args(ann))


@cache
def field_types() -> tuple[str, ...]:
    names = list(core_vocab.input_types())
    names += [t.name for t in core_vocab.input_types_file().field_only_types]
    return tuple(names)


def error_codes() -> tuple[str, ...]:
    return tuple(vocab_data()["error_codes"])


def recoveries() -> tuple[str, ...]:
    return tuple(vocab_data()["recoveries"])


def integration_modes() -> tuple[str, ...]:
    return tuple(vocab_data()["integration_modes"])


def provenance_kinds() -> tuple[str, ...]:
    return tuple(vocab_data()["provenance_kinds"])


def release_reasons() -> tuple[str, ...]:
    return tuple(vocab_data()["release_reasons"])


def mock_profile_names() -> tuple[str, ...]:
    return tuple(core_vocab.mock_profiles())


def emotions() -> tuple[str, ...]:
    return literal_values(Stage, "emotion")


def voices() -> tuple[str, ...]:
    return literal_values(Story, "voice")


def entity_classes() -> tuple[str, ...]:
    return literal_values(Entity, "klass")


def http_methods() -> tuple[str, ...]:
    return literal_values(Operation, "method")


def flow_variants() -> tuple[str, ...]:
    return literal_values(Flow, "variant")


ACCESS_ACTIONS = ("none", "view", "create", "edit", "delete", "manage")
