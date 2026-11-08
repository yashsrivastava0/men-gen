"""Prompt rendering (brief 7.3 steps 1-2): validate input, render jinja templates, wrap untrusted.

Templates are plain files under ``spec.template_root`` and are rendered in a sandboxed environment
with ``StrictUndefined`` (a variable the input does not provide is a spec bug, not an empty string).
The template context is the input model dumped to plain JSON types:

* every input field is a top-level variable (``{{ question }}``);
* ``input`` is the same data as one mapping (``{{ input.question }}``);
* ``input_json`` is its canonical JSON text; ``untrusted_notice`` is the fixed security paragraph;
* fields named in ``spec.untrusted_inputs`` arrive already wrapped by ``wrap_untrusted``
  (a ``str`` becomes one block, a ``list`` becomes a list of blocks, anything else its JSON text);
* the ``tojson`` filter is plain ``json.dumps`` (no HTML escaping), also available as ``to_json``.

No prompt text lives in Python: the only text this module adds is the system-prompt notice for calls
with untrusted inputs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from jinja2 import FileSystemLoader, StrictUndefined, TemplateError, TemplateNotFound
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ValidationError

from mde_core.errors import ConfigError, UntrustedWithTools, ValidationFailed
from mde_core.hashing import canonical_json, sha256_hex
from mde_llm_gateway.registry import AnySpec
from mde_llm_gateway.untrusted import (
    DEFAULT_MAX_UNTRUSTED_CHARS,
    UNTRUSTED_NOTICE,
    wrap_untrusted,
)


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str
    untrusted_fields: tuple[str, ...] = ()

    @property
    def chars(self) -> int:
        return len(self.system) + len(self.user)

    @property
    def prompt_hash(self) -> str:
        """sha256 of the rendered prompt: what logs and records carry instead of the text."""
        return sha256_hex(f"{self.system}\n\x00\n{self.user}")


def _to_json(value: object, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, default=_json_default)


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def check_spec(spec: AnySpec) -> None:
    """Hard errors that must stop a call before any work or spend (brief 7.3 step 2)."""
    if spec.tools and spec.untrusted_inputs:
        raise UntrustedWithTools(
            f"{spec.id}: untrusted inputs {list(spec.untrusted_inputs)} must not be combined with tools",
            details={"call_id": spec.id},
        )
    if spec.output_model is None and not spec.tools:
        raise ConfigError(f"{spec.id}: free-text output is only allowed for the web search call")
    unknown = [n for n in spec.untrusted_inputs if n not in spec.input_model.model_fields]
    if unknown:
        raise ConfigError(f"{spec.id}: untrusted_inputs name unknown input field(s) {unknown}")


class PromptRenderer:
    def __init__(self, *, max_untrusted_chars: int = DEFAULT_MAX_UNTRUSTED_CHARS) -> None:
        self.max_untrusted_chars = max_untrusted_chars
        self._envs: dict[Path, SandboxedEnvironment] = {}

    def _env(self, root: Path) -> SandboxedEnvironment:
        env = self._envs.get(root)
        if env is None:
            env = SandboxedEnvironment(
                loader=FileSystemLoader(str(root)),
                undefined=StrictUndefined,
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
                keep_trailing_newline=False,
            )
            filters = cast("dict[str, Callable[..., object]]", env.filters)  # pyright: ignore[reportUnknownMemberType]
            filters["tojson"] = _to_json
            filters["to_json"] = _to_json
            self._envs[root] = env
        return env

    # ------------------------------------------------------------------ public
    def validate_input(self, spec: AnySpec, input: BaseModel) -> BaseModel:
        """Re-validate ``input`` against ``spec.input_model`` (instances can skip validators)."""
        if not isinstance(input, spec.input_model):
            raise ValidationFailed(
                f"{spec.id}: input must be a {spec.input_model.__name__}, got {type(input).__name__}"
            )
        try:
            return spec.input_model.model_validate(input.model_dump())
        except ValidationError as exc:
            raise ValidationFailed(
                f"{spec.id}: invalid input: {exc.error_count()} error(s)",
                details={"errors": _error_lines(exc)},
            ) from exc

    def render(self, spec: AnySpec, input: BaseModel) -> RenderedPrompt:
        check_spec(spec)
        data = cast("dict[str, object]", input.model_dump(mode="json"))
        for name in spec.untrusted_inputs:
            data[name] = self._wrap_field(f"{spec.id}.{name}", data.get(name))
        context: dict[str, object] = {
            **data,
            "input": data,
            "input_json": canonical_json(data),
            "untrusted_notice": UNTRUSTED_NOTICE,
        }
        system = self._render_one(spec, spec.system_template, context)
        user = self._render_one(spec, spec.user_template, context)
        if spec.untrusted_inputs:
            system = f"{system.rstrip()}\n\n{UNTRUSTED_NOTICE}"
        return RenderedPrompt(system=system, user=user, untrusted_fields=spec.untrusted_inputs)

    # ------------------------------------------------------------------ internals
    def _render_one(self, spec: AnySpec, template: str, context: dict[str, object]) -> str:
        env = self._env(spec.template_root)
        try:
            return env.get_template(template).render(context)
        except TemplateNotFound as exc:
            raise ConfigError(
                f"{spec.id}: template {template!r} not found under {spec.template_root}"
            ) from exc
        except TemplateError as exc:
            raise ConfigError(f"{spec.id}: template {template!r} failed: {exc}") from exc

    def _wrap_field(self, source: str, value: object) -> object:
        cap = self.max_untrusted_chars
        if value is None:
            return None
        if isinstance(value, str):
            return wrap_untrusted(value, source, max_chars=cap)
        if isinstance(value, list):
            items = cast("list[object]", value)
            return [
                wrap_untrusted(
                    x if isinstance(x, str) else _to_json(x), f"{source}[{i}]", max_chars=cap
                )
                for i, x in enumerate(items)
            ]
        return wrap_untrusted(_to_json(value), source, max_chars=cap)


def _error_lines(exc: ValidationError, limit: int = 20) -> list[str]:
    lines = [
        f"{'.'.join(str(p) for p in err['loc']) or '$'}: {err['msg']}"
        for err in exc.errors(include_url=False, include_input=False)
    ]
    return lines[:limit]
