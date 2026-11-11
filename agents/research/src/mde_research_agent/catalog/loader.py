"""Load and cross-validate the data folder into a frozen :class:`Catalog`.

``load_catalog()`` is the only entry point. It never reads the environment; tests pass ``data_dir``.
Validation happens at load time so a typo in a data file fails at startup, not in the middle of a run:
every slot kind exists in its job's kinds, every kind has a lane, every event type has a label template,
the ceilings cover every load class, every job is present, and ``catalog_hash`` (sha256 of the canonical
JSON of everything loaded) identifies exactly which data a run used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mde_core.errors import ConfigError
from mde_core.hashing import hash_json
from mde_research_agent.catalog.models import (
    AuthorityEntry,
    Catalog,
    CeilingsConfig,
    IntentWords,
    JobName,
    JobSpec,
    Labels,
    LanesConfig,
    LanguagesConfig,
    LoadConfig,
    NeedsConfig,
    PlanConfig,
    PrevalenceConfig,
    SourceClassRules,
    TemplatePatterns,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOB_NAMES: tuple[JobName, ...] = (
    "features",
    "structure",
    "flows",
    "data",
    "services",
    "design",
    "screens",
    "local",
)
EVENT_TYPES: tuple[str, ...] = (
    "run.started",
    "plan.ready",
    "target.started",
    "target.covered",
    "target.not_found",
    "target.cancelled",
    "query.issued",
    "search.results",
    "page.reading",
    "page.read",
    "page.skipped",
    "fact.found",
    "fact.verified",
    "fact.rejected",
    "entity.discovered",
    "round.completed",
    "gap.detected",
    "warning",
    "run.finished",
    "run.skipped",
    "run.failed",
)


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read data file {path.name}: {exc}") from exc


def _authority(data_dir: Path) -> tuple[AuthorityEntry, ...]:
    out: list[AuthorityEntry] = []
    for path in sorted((data_dir / "authority").glob("*.yaml")):
        raw = _yaml(path)
        country = str(raw.get("country", path.stem)).lower()
        for item in raw.get("entries", []):
            out.append(AuthorityEntry.model_validate({**item, "country": country}))
    return tuple(out)


def _labels(data_dir: Path) -> dict[str, Labels]:
    out: dict[str, Labels] = {}
    for path in sorted((data_dir / "labels").glob("*.yaml")):
        out[path.stem] = Labels.model_validate(_yaml(path))
    return out


def _validate(cat_parts: dict[str, Any]) -> None:
    jobs: dict[str, JobSpec] = cat_parts["jobs"]
    lanes: LanesConfig = cat_parts["lanes"]
    labels: dict[str, Labels] = cat_parts["labels"]
    ceilings: CeilingsConfig = cat_parts["ceilings"]
    load: LoadConfig = cat_parts["load"]
    problems: list[str] = []
    for name in JOB_NAMES:
        if name not in jobs:
            problems.append(f"job {name!r} has no data file")
    for name, job in jobs.items():
        if job.job != name:
            problems.append(f"jobs/{name}.yaml declares job {job.job!r}")
        for kind in job.kinds:
            if kind not in lanes.kinds:
                problems.append(f"job {name}: kind {kind!r} has no entry in lanes.yaml")
        seen_ids: set[str] = set()
        for target in job.targets:
            if target.id in seen_ids:
                problems.append(f"job {name}: duplicate target id {target.id!r}")
            seen_ids.add(target.id)
            seen_slots: set[str] = set()
            for slot in target.slots:
                if slot.kind not in job.kinds:
                    problems.append(
                        f"job {name}/{target.id}: slot {slot.key!r} kind {slot.kind!r} not in job kinds"
                    )
                if slot.key in seen_slots:
                    problems.append(f"job {name}/{target.id}: duplicate slot {slot.key!r}")
                seen_slots.add(slot.key)
                if slot.type == "distinct_values" and not slot.attr:
                    problems.append(
                        f"job {name}/{target.id}: distinct_values slot {slot.key!r} needs attr"
                    )
    for kind in lanes.high_impact_kinds + lanes.legal_adjacent_kinds:
        if kind not in lanes.kinds:
            problems.append(f"lanes.yaml: unknown kind {kind!r}")
    if "en" not in labels:
        problems.append("labels/en.yaml is required")
    for lang, lab in labels.items():
        missing = [e for e in EVENT_TYPES if e not in lab.events]
        if missing:
            problems.append(f"labels/{lang}.yaml lacks event labels: {', '.join(missing)}")
    bounds = [c.below for c in load.classes]
    if [c.name for c in load.classes] != ["focused", "standard", "deep"] or bounds[-1] is not None:
        problems.append("load.yaml classes must be focused, standard, deep with the last unbounded")
    for cls in ("focused", "standard", "deep"):
        cs = ceilings.for_class(cls)  # type: ignore[arg-type]
        if (
            cs.hard.searches < cs.soft.searches
            or cs.hard.pages < cs.soft.pages
            or cs.hard.seconds < cs.soft.seconds
        ):
            problems.append(f"ceilings.yaml: {cls} hard limits are below the soft ones")
    if problems:
        raise ConfigError("invalid research data files: " + "; ".join(problems))


def load_catalog(data_dir: Path | None = None) -> Catalog:
    """Load, validate and hash every data file under ``data_dir`` (default: the packaged data)."""
    root = data_dir or DATA_DIR
    parts: dict[str, Any] = {
        "load": LoadConfig.model_validate(_yaml(root / "load.yaml")),
        "ceilings": CeilingsConfig.model_validate(_yaml(root / "ceilings.yaml")),
        "jobs": {
            p.stem: JobSpec.model_validate(_yaml(p)) for p in sorted((root / "jobs").glob("*.yaml"))
        },
        "authority": _authority(root),
        "source_rules": SourceClassRules.model_validate(_yaml(root / "source_classes.yaml")),
        "intent_words": IntentWords.model_validate(_yaml(root / "intent_words.yaml")),
        "template_patterns": TemplatePatterns.model_validate(
            _yaml(root / "template_patterns.yaml")
        ),
        "prevalence": PrevalenceConfig.model_validate(_yaml(root / "prevalence.yaml")),
        "lanes": LanesConfig.model_validate(_yaml(root / "lanes.yaml")),
        "needs": NeedsConfig.model_validate(_yaml(root / "need_keywords.yaml")),
        "plan": PlanConfig.model_validate(_yaml(root / "plan.yaml")),
        "languages": LanguagesConfig.model_validate(_yaml(root / "languages.yaml")).languages,
        "labels": _labels(root),
    }
    _validate(parts)
    digest = hash_json({k: _dump(v) for k, v in parts.items()})
    return Catalog(**parts, catalog_hash=digest)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    return value
