"""Canonical YAML codec for the blocks of ``APP_SPEC.md`` (both directions).

One module owns both the dump and the load so the renderer (``mde_spec_builder.render``) and the
loader cannot drift apart. Canonical form:

* keys in the models' declared field order; fields that are ``None`` or equal to their default are
  omitted (the loader's models fill them back in); free-form maps (colour tokens, ``copy``,
  ``states``, ...) are written with sorted keys so equal specs have equal bytes;
* short leaf mappings and scalar lists use flow style, longer ones block style, multi-line
  strings use ``|`` blocks; sequences are indented; ``allow_unicode``; LF newlines; no aliases;
* three renames between the models and the file: a screen's ``texts`` is the YAML key ``copy``,
  a state-machine ``Transition.source`` is ``from``, a header's ``schema_id`` is ``schema``;
* screen actions are written compactly: ``- add_to_cart: {calls: API-addCartItem}``; a screen or
  flow block body omits ``id`` (the fence info string carries it).

The pure-Python ``SafeDumper`` is used on purpose: the C emitter can differ in edge cases and the
bytes must be identical on every machine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Final, cast

import yaml
from pydantic import BaseModel, ValidationError

from mde_contracts.common import Frozen
from mde_contracts.spec import (
    AcceptanceBlock,
    ApiBlock,
    AppBlock,
    AssumptionsBlock,
    BacklogItem,
    BehaviorBlock,
    BuildSequence,
    DataBlock,
    DesignBlock,
    FlowBlock,
    IntegrationsBlock,
    NavigationBlock,
    RevisionBlock,
    RolesBlock,
    ScreenAction,
    ScreenBlock,
    Transition,
)
from mde_spec_loader.errors import SpecParseError

WIDTH: Final = 100
FLOW_LIMIT: Final = 100  # a leaf mapping/list is written in flow style up to this many chars

Scalar = str | int | float | bool | date


class BacklogBlock(Frozen):
    """Body of the ``BACKLOG`` block (Phase 2 backlog); not part of ``AppSpec`` blocks proper."""

    items: list[BacklogItem]


MODELS: Final[dict[str, type[BaseModel]]] = {
    "app": AppBlock,
    "design": DesignBlock,
    "navigation": NavigationBlock,
    "data": DataBlock,
    "api": ApiBlock,
    "roles": RolesBlock,
    "integrations": IntegrationsBlock,
    "behavior": BehaviorBlock,
    "screen": ScreenBlock,
    "flow": FlowBlock,
    "build_sequence": BuildSequence,
    "acceptance": AcceptanceBlock,
    "assumptions": AssumptionsBlock,
    "backlog": BacklogBlock,
    "revision": RevisionBlock,
}

# --------------------------------------------------------------------------- YAML dumper


class _FlowMap(dict[str, object]):
    """A mapping written in flow style."""


class _FlowSeq(list[object]):
    """A list written in flow style."""


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)  # indent sequences under their key

    def ignore_aliases(self, data: object) -> bool:
        return True  # never emit anchors or aliases


def _repr_flow_map(dumper: yaml.SafeDumper, data: _FlowMap) -> yaml.MappingNode:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


def _repr_flow_seq(dumper: yaml.SafeDumper, data: _FlowSeq) -> yaml.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _repr_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    # NEL (U+0085) is folded to a space inside single quotes by PyYAML's scanner: always escape it
    style = '"' if "\x85" in data else "|" if "\n" in data else None
    return dumper.represent_scalar(  # pyright: ignore[reportUnknownMemberType]
        "tag:yaml.org,2002:str", data, style=style
    )


_Dumper.add_representer(_FlowMap, _repr_flow_map)
_Dumper.add_representer(_FlowSeq, _repr_flow_seq)
_Dumper.add_representer(str, _repr_str)


def _is_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool, date))


def _flow_len(value: object) -> int:
    """Approximate length of ``value`` in flow style (deterministic, only used to choose style)."""
    if isinstance(value, Mapping):
        items = cast("Mapping[str, object]", value)
        return 2 + sum(len(k) + 2 + _flow_len(v) + 2 for k, v in items.items())
    if isinstance(value, list):
        seq = cast("list[object]", value)
        return 2 + sum(_flow_len(v) + 2 for v in seq)
    return len(str(value)) + (2 if isinstance(value, str) else 0)


def _has_newline(value: object) -> bool:
    return isinstance(value, str) and "\n" in value


def _stylize(node: object) -> object:
    """Mark short leaf lists and mappings for flow style (bottom-up, deterministic)."""
    if isinstance(node, dict):
        mapping = cast("dict[str, object]", node)
        out: dict[str, object] = {k: _stylize(v) for k, v in mapping.items()}
        if (
            out
            and all(_is_scalar(v) or isinstance(v, _FlowSeq) for v in out.values())
            and not any(_has_newline(v) for v in out.values())
            and _flow_len(out) <= FLOW_LIMIT
        ):
            return _FlowMap(out)
        return out
    if isinstance(node, list):
        items = cast("list[object]", node)
        seq: list[object] = [_stylize(v) for v in items]
        if (
            seq
            and all(_is_scalar(v) for v in seq)
            and not any(_has_newline(v) for v in seq)
            and _flow_len(seq) <= FLOW_LIMIT
        ):
            return _FlowSeq(seq)
        flowed = [isinstance(v, _FlowMap) for v in seq]
        if any(flowed) and not all(flowed):
            # a list of mappings is written in one style: block unless every item is short
            seq = [dict(v) if isinstance(v, _FlowMap) else v for v in seq]
        return seq
    return node


def dump_yaml(data: Mapping[str, object]) -> str:
    """Canonical YAML for one block body, ending with exactly one newline (the newline is part of
    the body: a string value that ends with a line break must not lose it). ``data`` is a mapping."""
    top: dict[str, object] = {k: _stylize(v) for k, v in data.items()}
    text = yaml.dump(
        top,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=WIDTH,
        indent=2,
        line_break="\n",
    )
    return text


def load_yaml(text: str, *, line: int | None = None) -> object:
    """Parse YAML (safe loader, pure Python) or raise ``SpecParseError``."""
    try:
        return cast("object", yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise SpecParseError(f"invalid YAML: {exc}", line=line) from exc


# --------------------------------------------------------------------------- model -> data


def _rename(data: dict[str, object], old: str, new: str) -> dict[str, object]:
    """Rename a key keeping its position."""
    return {(new if k == old else k): v for k, v in data.items()}


def _action_data(action: ScreenAction) -> dict[str, object]:
    body: dict[str, object] = {}
    if action.calls is not None:
        body["calls"] = action.calls
    if action.go is not None:
        body["go"] = action.go
    if action.on_success is not None:
        body["on_success"] = action.on_success
    return {action.name: body}


def value_data(value: object) -> object:
    """Canonical data tree of any value: models, maps (sorted), lists, scalars."""
    if isinstance(value, ScreenAction):
        return _action_data(value)
    if isinstance(value, BaseModel):
        out: dict[str, object] = {}
        for name, field in type(value).model_fields.items():
            current = cast("object", getattr(value, name))
            if current is None:
                continue
            if not field.is_required() and current == field.get_default(call_default_factory=True):
                continue
            out[name] = value_data(current)
        if isinstance(value, ScreenBlock):
            out = _rename(out, "texts", "copy")
        elif isinstance(value, Transition):
            out = _rename(out, "source", "from")
        return out
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, object]", value)
        return {k: value_data(mapping[k]) for k in sorted(mapping)}
    if isinstance(value, (list, tuple)):
        return [value_data(v) for v in cast("Sequence[object]", value)]
    return value


def model_data(model: BaseModel) -> dict[str, object]:
    """Canonical (unstyled) data of one model."""
    return cast("dict[str, object]", value_data(model))


def block_data(kind: str, model: BaseModel) -> dict[str, object]:
    """The YAML body data of a block of ``kind`` (screen and flow omit their ``id``)."""
    data = model_data(model)
    if kind in ("screen", "flow"):
        data.pop("id", None)
    return data


# --------------------------------------------------------------------------- data -> model


def as_map(value: object, what: str, *, line: int | None = None) -> dict[str, object]:
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    raise SpecParseError(f"{what} must be a YAML mapping", line=line)


def _expand_action(raw: object, owner: str) -> dict[str, object]:
    if isinstance(raw, dict):
        mapping = cast("dict[str, object]", raw)
        if len(mapping) == 1:
            ((name, body),) = mapping.items()
            if body is None:
                return {"name": str(name)}
            if isinstance(body, dict):
                inner = cast("dict[str, object]", body)
                return {"name": str(name), **inner}
        return dict(mapping)
    raise SpecParseError(f"{owner}: an action must be a mapping")


def _screen_in(data: dict[str, object], block_id: str) -> dict[str, object]:
    out = dict(data)
    if "id" in out and out["id"] != block_id:
        raise SpecParseError(f"block {block_id}: body id {out['id']!r} differs from the fence id")
    out["id"] = block_id
    if "copy" in out:
        out = _rename(out, "copy", "texts")
    actions = out.get("actions")
    if isinstance(actions, list):
        out["actions"] = [_expand_action(a, block_id) for a in cast("list[object]", actions)]
    return out


def _data_in(data: dict[str, object]) -> None:
    entities = data.get("entities")
    if not isinstance(entities, list):
        return
    for entity in cast("list[object]", entities):
        if not isinstance(entity, dict):
            continue
        machine = cast("dict[str, object]", entity).get("state_machine")
        if not isinstance(machine, dict):
            continue
        transitions = cast("dict[str, object]", machine).get("transitions")
        if not isinstance(transitions, list):
            continue
        for index, item in enumerate(cast("list[object]", transitions)):
            if isinstance(item, dict) and "from" in item:
                transitions_list = cast("list[object]", transitions)
                transitions_list[index] = _rename(cast("dict[str, object]", item), "from", "source")


def _first_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    more = exc.error_count() - len(parts)
    return "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")


def block_model(kind: str, raw: object, block_id: str, *, line: int | None = None) -> BaseModel:
    """Validate a parsed YAML body into the model of ``kind`` (reverse of ``block_data``)."""
    model_cls = MODELS.get(kind)
    if model_cls is None:
        raise SpecParseError(f"unknown block kind {kind!r} (id {block_id})", line=line)
    data = as_map(raw if raw is not None else {}, f"block {block_id}", line=line)
    if kind == "screen":
        data = _screen_in(data, block_id)
    elif kind == "flow":
        if "id" in data and data["id"] != block_id:
            raise SpecParseError(f"block {block_id}: body id {data['id']!r} differs from fence id")
        data = {**data, "id": block_id}
    elif kind == "data":
        _data_in(data)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise SpecParseError(f"block {block_id}: {_first_errors(exc)}", line=line) from exc
