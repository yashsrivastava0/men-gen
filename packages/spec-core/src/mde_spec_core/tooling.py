"""Free-tool records and their one-line smoke tests (doc 18 6, AD-89).

`data/tools/free_tools.yaml` lists each tool with its licence and the name of its smoke test. The smoke tests live here
(code, not data, so no model or user text is ever evaluated); each runs offline and returns what it observed. Versions are
read from the installed package metadata, never written down.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from .config import DATA_DIR

TOOLS_PATH = DATA_DIR / "tools" / "free_tools.yaml"


class ToolsError(ValueError):
    pass


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SmokeSpec(_M):
    id: str
    one_line: str
    expect: str


class ToolRecord(_M):
    id: str
    distribution: str | None
    import_name: str | None
    purpose: str
    licence_expected: str
    licence_tokens: list[str]
    copyleft: bool
    proof_only: bool = False
    node_requirement: str | None = None
    source_url: str
    checked_on: str
    smoke: SmokeSpec | None
    caveat: str = ""

    @model_validator(mode="after")
    def _python_tools_have_a_smoke(self) -> ToolRecord:
        if not self.proof_only and (self.smoke is None or self.distribution is None):
            raise ToolsError(f"{self.id}: a Python tool needs a distribution and a smoke test")
        if self.proof_only and self.smoke is not None:
            raise ToolsError(f"{self.id}: a proof-only tool is not smoke-tested here")
        return self


class NotUsed(_M):
    id: str
    reason: str


class ToolSheet(_M):
    version: int
    checked_on: str
    tools: list[ToolRecord]
    not_used: list[NotUsed] = []

    @model_validator(mode="after")
    def _unique(self) -> ToolSheet:
        ids = [t.id for t in self.tools]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ToolsError(f"duplicate tool ids: {dupes}")
        return self

    def get(self, tool_id: str) -> ToolRecord:
        for t in self.tools:
            if t.id == tool_id:
                return t
        raise KeyError(tool_id)

    def python_tools(self) -> list[ToolRecord]:
        return [t for t in self.tools if not t.proof_only]


def load_tools(path: Path | None = None) -> ToolSheet:
    p = path or TOOLS_PATH
    return ToolSheet.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_tools() -> ToolSheet:
    return load_tools()


def installed_version(distribution: str) -> str:
    """The installed version, read from package metadata (never hard-coded)."""
    return metadata.version(distribution)


def installed_licence_text(distribution: str) -> str:
    """Everything the package metadata says about its licence, joined, for matching against `licence_tokens`."""
    meta = metadata.metadata(distribution)
    parts = [meta.get("License-Expression") or "", meta.get("License") or ""]
    parts += [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
    return " | ".join(p for p in parts if p)


# ---------------------------------------------------------------- smoke tests
@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    observed: str


def _faker_indian_locales() -> SmokeResult:
    from faker import Faker

    seen: list[str] = []
    for loc in ("en_IN", "hi_IN", "mr_IN"):
        name = Faker(loc).name()
        if not name:
            return SmokeResult(False, f"{loc} gave no name")
        seen.append(loc)
    try:
        Faker("kn_IN")
    except AttributeError as err:
        return SmokeResult(True, f"{'+'.join(seen)} ok; kn_IN raises AttributeError: {err}")
    return SmokeResult(False, "kn_IN unexpectedly exists: update the Kannada pool note")


def _babel_indian_grouping() -> SmokeResult:
    from babel.numbers import format_decimal

    out = format_decimal(1234567.891, locale="en_IN")
    return SmokeResult(out == "12,34,567.891", out)


def _phonenumbers_parse_in() -> SmokeResult:
    import phonenumbers

    number = phonenumbers.parse("+91 98765 43210")
    valid = phonenumbers.is_valid_number(number)
    text = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    return SmokeResult(valid and text == "+91 98765 43210", f"valid={valid} {text}")


def _stdnum_aadhaar_validator() -> SmokeResult:
    from stdnum.in_ import aadhaar

    ok = callable(getattr(aadhaar, "validate", None))
    return SmokeResult(ok, f"stdnum.in_.aadhaar.validate callable={ok}")


def _coloraide_wcag_contrast() -> SmokeResult:
    from coloraide import Color

    ratio = Color("black").contrast("white")
    return SmokeResult(round(ratio, 6) == 21.0, repr(ratio))


def _jsonschema_validate() -> SmokeResult:
    import jsonschema

    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    jsonschema.validate({"a": 1}, schema)
    try:
        jsonschema.validate({"a": "x"}, schema)
    except jsonschema.ValidationError:
        return SmokeResult(True, "good passes, bad raises ValidationError")
    return SmokeResult(False, "bad document passed")


def _pydantic_model() -> SmokeResult:
    from pydantic import BaseModel as Base
    from pydantic import ConfigDict as Config
    from pydantic import ValidationError

    class Probe(Base):
        model_config = Config(extra="forbid")
        a: int

    Probe.model_validate({"a": 1})
    try:
        Probe.model_validate({"a": 1, "b": 2})
    except ValidationError:
        return SmokeResult(True, "good accepted, extra key refused")
    return SmokeResult(False, "extra key accepted")


def _openapi_31_minimal() -> SmokeResult:
    from openapi_spec_validator import validate

    good = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}}
    validate(good)
    try:
        validate({"openapi": "3.1.0", "paths": {}})
    except Exception as err:  # the validator's own error classes; any refusal counts
        return SmokeResult(True, f"3.1.0 accepted; missing info refused ({type(err).__name__})")
    return SmokeResult(False, "an invalid document was accepted")


def _yaml_safe_load() -> SmokeResult:
    import yaml as y

    data = y.safe_load("a: [1, 2]")
    try:
        y.safe_load("a: !!python/object/apply:os.getcwd []")
    except y.YAMLError as err:
        return SmokeResult(
            data == {"a": [1, 2]}, f"dict ok; python tag refused ({type(err).__name__})"
        )
    return SmokeResult(False, "a python object tag was accepted")


def _jinja2_render() -> SmokeResult:
    from jinja2.sandbox import SandboxedEnvironment

    out = SandboxedEnvironment().from_string("Hello {{ name }}").render(name="x")
    return SmokeResult(out == "Hello x", out)


SMOKES: dict[str, Callable[[], SmokeResult]] = {
    "faker_indian_locales": _faker_indian_locales,
    "babel_indian_grouping": _babel_indian_grouping,
    "phonenumbers_parse_in": _phonenumbers_parse_in,
    "stdnum_aadhaar_validator": _stdnum_aadhaar_validator,
    "coloraide_wcag_contrast": _coloraide_wcag_contrast,
    "jsonschema_validate": _jsonschema_validate,
    "pydantic_model": _pydantic_model,
    "openapi_31_minimal": _openapi_31_minimal,
    "yaml_safe_load": _yaml_safe_load,
    "jinja2_render": _jinja2_render,
}


def run_smoke(smoke_id: str) -> SmokeResult:
    try:
        fn = SMOKES[smoke_id]
    except KeyError as err:
        raise ToolsError(f"no smoke test named {smoke_id!r}") from err
    return fn()


def import_tool(record: ToolRecord) -> None:
    """Import the tool's module; raises ImportError when it is missing."""
    if record.import_name is not None:
        importlib.import_module(record.import_name)
