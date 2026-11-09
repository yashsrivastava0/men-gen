"""Hand-written model outputs for exact behaviour tests: a scenario's *provider fixture*.

A fixture is a YAML (or JSON) file ``providers/<scenario id>.yaml`` holding, per call id, the wire outputs the
model "returned" in the order the engine asks for them::

    calls:
      cmp.plan_questions:
        - {domain_hypothesis: {...}, questions: [...], covered_needs: [...], ...}
        - error: gateway          # the call failed (the gateway raised): the engine takes the flagged fallback
      cmp.audit_questions:
        - {verdicts: [...]}
    unscripted: mock              # calls that are not listed run their mock handler (default), or: fail
    then_mock: [cmp.plan_questions]   # after its queue is empty this call runs its mock handler instead of failing

A queue item may be ``{mock: true}`` (this call runs its mock handler but uses up the slot, so the order stays
checked), ``{error: gateway|timeout|crash}`` (the call failed), or, for ``cmp.audit_questions``,
``{pass_all: true, except: {Q-004: {specific: 2, issues: [...]}}}`` (a verdict per question of the request, resolved
when the call happens, because only then are the question ids known). A plan output may carry ``defer_rest: "why"``:
every must or high-impact need it does not ask is listed in ``assumed_instead``. Fields a wire output leaves out are filled
with the empty value of their type (``priority`` 3, a verdict ``specific`` 5, ``confidence`` 0.8, ...).

The runner feeds them into :class:`mde_compass.calls.testing.ScriptedGateway` (``by_spec``), so they go through
the real ``CallRunner`` (local validators, fallback, call log), the real adapters, the real validators and the
real engine; only the model is replaced. The check is strict both ways: a listed call that asks for an answer
nobody wrote fails the step (``ScriptExhausted``), and an answer never used fails the scenario
(``assert_consumed``). Each output is parsed by the call's own wire model, so a fixture that does not fit the
schema fails when it is loaded, not in the middle of a session.
"""

from __future__ import annotations

import json
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union, cast, get_args, get_origin

import yaml
from pydantic import BaseModel

from mde_compass.calls.plan_questions.schema import PlanOutput
from mde_compass.calls.registry import all_specs
from mde_compass.calls.testing import ScriptedGateway
from mde_core.clock import Clock
from mde_core.errors import GatewayError

ERROR_KINDS: dict[str, type[Exception]] = {
    "gateway": GatewayError,  # the gateway gave up (retries and its one repair spent): the call falls back
    "timeout": TimeoutError,  # likewise
    "crash": RuntimeError,  # something unexpected: not caught by the call runner, the turn fails
}

# Defaults for fields a fixture leaves out, by field name; anything else gets the empty value of its type.
NAME_DEFAULTS: dict[str, Any] = {
    "priority": 3,
    "group_id": "G1",
    "group_title": "Your project",
    "confidence": 0.8,
    "consumers": ["all"],
    "basis": "suggestion",
    "answerable": True,
    "maps_to_need": True,
    "specific": 5,
    "language": "en",
    "intent_default": "answer",
}
TYPE_DEFAULTS: dict[tuple[str, str], Any] = {
    ("QuestionWire", "type"): "text",
    ("FactWire", "type"): "context",
    ("UnderstandOutput", "intent"): "answer",
    ("EffectWire", "mode"): "set",
    ("RephraseInput", "mode"): "rephrase",
    ("SlotUpdateWire", "basis"): "user_text",
}


class MockItem:
    """Queue marker: this call runs its mock handler, and uses up the slot (the order stays checked)."""


class Lazy:
    """A queue item resolved when the call happens (it needs the call's input)."""

    def __init__(self, resolve: Callable[[BaseModel], BaseModel]) -> None:
        self.resolve = resolve


def _empty(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return None if len(args) != len(get_args(annotation)) else _empty(args[0])
    if origin is list or annotation is list:
        return []
    if origin is dict or annotation is dict:
        return {}
    if origin is Literal:
        return get_args(annotation)[0]
    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is str:
        return ""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return fill(annotation, {})
    return None


def _inner_model(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return _inner_model(args[0]) if args else None
    if origin in (Union, types.UnionType):
        for a in get_args(annotation):
            found = _inner_model(a)
            if found is not None:
                return found
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def fill(model: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with every field it leaves out set to a default (recursively for nested wire models)."""
    out = dict(data)
    for name, info in model.model_fields.items():
        inner = _inner_model(info.annotation)
        if name in out:
            if inner is not None:
                value = out[name]
                out[name] = (
                    [fill(inner, v) if isinstance(v, dict) else v for v in value]
                    if isinstance(value, list)
                    else fill(inner, value)
                    if isinstance(value, dict)
                    else value
                )
            continue
        if (model.__name__, name) in TYPE_DEFAULTS:
            out[name] = TYPE_DEFAULTS[(model.__name__, name)]
        elif name in NAME_DEFAULTS:
            out[name] = NAME_DEFAULTS[name]
        else:
            out[name] = _empty(info.annotation)
    return out


def _shorthand(spec_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Small conveniences that keep a fixture readable."""
    out = dict(item)
    if spec_id == "cmp.plan_questions":
        for i, q in enumerate(out.get("questions", []), start=1):
            q.setdefault(
                "id", f"Q-{i:03d}"
            )  # the engine renumbers; the wire needs a unique Q-### anyway
            q["options"] = [{"label": o} if isinstance(o, str) else o for o in q.get("options", [])]
        if "covered_needs" not in out:
            out["covered_needs"] = sorted(
                {s for q in out.get("questions", []) for s in q.get("serves", [])}
            )
    return out


@dataclass
class ProviderScript:
    """The parsed fixture: wire outputs (or errors) per call id."""

    path: Path
    calls: dict[str, list[Any]]  # BaseModel | Exception | MockItem | Lazy
    unscripted: str = "mock"  # mock | fail
    then_mock: set[str] = field(
        default_factory=set
    )  # these calls run their mock handler once the queue is empty
    notes: dict[str, str] = field(default_factory=dict)

    def total(self) -> int:
        return sum(len(v) for v in self.calls.values())


def _output_models() -> dict[str, type[BaseModel]]:
    return {
        cs.id: cast(type[BaseModel], cs.spec.output_model)
        for cs in all_specs()
        if cs.spec.output_model is not None
    }


def _error(raw: dict[str, Any]) -> Exception:
    kind = str(raw["error"])
    cls = ERROR_KINDS.get(kind)
    if cls is None:
        raise ValueError(f"unknown error kind {kind!r} (use one of {sorted(ERROR_KINDS)})")
    return cls(str(raw.get("message", f"scripted {kind} failure")))


def plan_output(raw: dict[str, Any]) -> PlanOutput:
    """A hand-written ``cmp.plan_questions`` output (the shorthand of a fixture) as the wire model."""
    from mde_compass.calls.plan_questions.schema import PlanOutput

    return PlanOutput.model_validate(fill(PlanOutput, _shorthand("cmp.plan_questions", dict(raw))))


def _audit_resolver(item: dict[str, Any]) -> Lazy:
    overrides: dict[str, dict[str, Any]] = dict(item.get("except") or {})

    def resolve(inp: BaseModel) -> BaseModel:
        from mde_compass.calls.audit_questions.schema import AuditOutput

        verdicts = []
        for q in getattr(inp, "questions", []):
            base: dict[str, Any] = {"card_id": q.card_id}
            base.update(overrides.get(q.card_id, {}))
            verdicts.append(base)
        return AuditOutput.model_validate({"verdicts": [fill_verdict(v) for v in verdicts]})

    return Lazy(resolve)


def _plan_resolver(item: dict[str, Any]) -> Lazy:
    """``defer_rest: "why"``: every must or high-impact need the batch neither asks nor lists is assumed for now
    (``assumed_instead``), which is what a planner does for a thin idea. Resolved at call time, from the needs the
    engine sent."""
    reason = str(item["defer_rest"])
    base = {k: v for k, v in item.items() if k != "defer_rest"}

    def resolve(inp: BaseModel) -> BaseModel:
        from mde_compass.calls.plan_questions.schema import PlanOutput

        data = _shorthand("cmp.plan_questions", dict(base))
        accounted = {s for q in data.get("questions", []) for s in q.get("serves", [])}
        accounted |= set(data.get("covered_needs", []))
        accounted |= {a["need_id"] for a in data.get("assumed_instead", [])}
        assumed = list(data.get("assumed_instead", []))
        for need in getattr(inp, "needs", []):
            owed = need.state in ("missing", "partial") and (
                need.level == "must" or need.impact >= 4
            )
            if owed and need.need_id not in accounted:
                assumed.append(
                    {"need_id": need.need_id, "assumption": "to be asked later", "reason": reason}
                )
        data["assumed_instead"] = assumed
        return PlanOutput.model_validate(fill(PlanOutput, data))

    return Lazy(resolve)


def fill_verdict(raw: dict[str, Any]) -> dict[str, Any]:
    from mde_compass.calls.audit_questions.schema import VerdictWire

    return fill(VerdictWire, raw)


def parse_provider(raw: dict[str, Any], path: Path | str = "<provider>") -> ProviderScript:
    models = _output_models()
    calls: dict[str, list[Any]] = {}
    for spec_id, items in (raw.get("calls") or {}).items():
        if spec_id not in models:
            raise ValueError(f"{path}: {spec_id!r} is not a Compass call (have {sorted(models)})")
        queue: list[Any] = []
        for i, item in enumerate(items or []):
            if isinstance(item, dict) and "error" in item and set(item) <= {"error", "message"}:
                queue.append(_error(item))
            elif isinstance(item, dict) and item.get("mock") is True and len(item) == 1:
                queue.append(MockItem())
            elif (
                spec_id == "cmp.audit_questions" and isinstance(item, dict) and item.get("pass_all")
            ):
                queue.append(_audit_resolver(item))
            elif (
                spec_id == "cmp.plan_questions" and isinstance(item, dict) and "defer_rest" in item
            ):
                queue.append(_plan_resolver(item))
            else:
                try:
                    data = fill(models[spec_id], _shorthand(spec_id, dict(item)))
                    queue.append(models[spec_id].model_validate(data))
                except Exception as exc:  # a fixture that does not fit the schema is a test bug
                    raise ValueError(
                        f"{path}: {spec_id}[{i}] does not fit its wire schema: {exc}"
                    ) from exc
        calls[spec_id] = queue
    unscripted = str(raw.get("unscripted", "mock"))
    if unscripted not in ("mock", "fail"):
        raise ValueError(f"{path}: unscripted is mock or fail")
    then_mock = {str(c) for c in raw.get("then_mock") or []}
    if not then_mock <= set(models):
        raise ValueError(f"{path}: then_mock names unknown calls {sorted(then_mock - set(models))}")
    return ProviderScript(
        Path(str(path)), calls, unscripted, then_mock, dict(raw.get("notes") or {})
    )


def load_provider(path: Path | str) -> ProviderScript:
    text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    return parse_provider(raw or {}, path)


def find_provider(scenario_dir: Path | str, scenario_id: str, name: str = "") -> Path | None:
    """``providers/<id>.yaml`` (or ``.json``) next to the scenario, or the file named by ``provider:``."""
    base = Path(scenario_dir) / "providers"
    if name:
        return base / name if (base / name).exists() else Path(scenario_dir) / name
    for suffix in (".yaml", ".json"):
        candidate = base / f"{scenario_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


class HybridGateway:
    """A scripted gateway for the calls a fixture lists, the real mock gateway for all the others.

    Every call is recorded in ``trace`` as ``(spec id, source)`` with source ``scripted``, ``mock`` or
    ``unscripted`` (not listed in the fixture), and its input in ``inputs``, so a test can check which calls
    ran, in what order, and what the engine sent."""

    def __init__(self, script: ProviderScript, mock: Any | None) -> None:
        self.script = script
        self.scripted = ScriptedGateway(
            by_spec=cast(Any, {k: list(v) for k, v in script.calls.items()})
        )
        self.mock = mock
        self.trace: list[tuple[str, str]] = []
        self.inputs: list[tuple[str, BaseModel]] = []

    @property
    def calls(self) -> list[tuple[str, BaseModel]]:
        return self.inputs

    def assert_consumed(self) -> None:
        left = self.unconsumed()
        if left:
            raise AssertionError(f"scripted model answers never used: {left}")

    def unconsumed(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.scripted.by_spec.items() if v}

    async def _mock(self, spec: Any, inp: BaseModel, ctx: Any) -> Any:
        if self.mock is None:
            raise AssertionError(f"{spec.id} was not scripted and this provider forbids mock calls")
        return await self.mock.run(spec, inp, ctx)

    async def run(self, spec: Any, inp: BaseModel, ctx: Any) -> Any:
        self.inputs.append((spec.id, inp))
        queue = self.scripted.by_spec.get(spec.id)
        if queue is None:
            self.trace.append((spec.id, "unscripted"))
            return await self._mock(spec, inp, ctx)
        if not queue and spec.id in self.script.then_mock:
            self.trace.append((spec.id, "mock"))
            return await self._mock(spec, inp, ctx)
        if queue and isinstance(queue[0], MockItem):
            queue.pop(0)
            self.trace.append((spec.id, "mock"))
            return await self._mock(spec, inp, ctx)
        if queue and isinstance(queue[0], Lazy):
            queue[0] = queue[0].resolve(inp)
        self.trace.append((spec.id, "scripted"))
        return await self.scripted.run(spec, inp, ctx)

    async def budget_status(self) -> Any:  # pragma: no cover - unused in tests
        return await self.scripted.budget_status()

    def called(self, spec_id: str) -> int:
        return sum(1 for s, _ in self.trace if s == spec_id)

    def last_input(self, spec_id: str) -> BaseModel | None:
        return next((i for s, i in reversed(self.inputs) if s == spec_id), None)


def mock_gateway(clock: Clock | None = None) -> Any:
    """The real gateway pipeline in mock mode over every Compass call (what ``build_service`` makes)."""
    from mde_core.settings import load_settings
    from mde_llm_gateway import (
        GatewayConfig,
        InProcessGatewayClient,
        StaticRegistry,
        create_gateway,
    )

    settings = load_settings(llm_mode="mock")
    config = GatewayConfig(rpm={"fast": 60_000, "standard": 60_000, "deep": 60_000})
    return InProcessGatewayClient(
        create_gateway(
            settings, StaticRegistry([cs.spec for cs in all_specs()]), clock=clock, config=config
        )
    )


def hybrid_for(script: ProviderScript, clock: Clock | None = None) -> HybridGateway:
    return HybridGateway(script, mock_gateway(clock) if script.unscripted == "mock" else None)


def trace_gateway(clock: Clock | None = None) -> HybridGateway:
    """No scripted answers at all: every call runs its mock handler, and the call sequence is recorded."""
    return HybridGateway(ProviderScript(Path("<trace>"), {}), mock_gateway(clock))


def sequence(gateway: HybridGateway) -> Sequence[str]:
    return [spec_id for spec_id, _ in gateway.trace]
