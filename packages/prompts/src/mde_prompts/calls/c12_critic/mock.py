"""Offline handler for C12: ``pass`` unless there are validator errors or an obvious Brief mismatch.

Deterministic and schema-valid. Obvious mismatches: no screens, or no journey at all (errors);
a feature the Brief lists that no screen, entity or operation seems to mention (a warning).
"""

from __future__ import annotations

import re

from mde_contracts.calls import C12Input, C12Output, CriticFinding

MAX_FINDINGS = 5
_WORD = re.compile(r"[a-z]{4,}")


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") else word


def _corpus(inp: C12Input) -> str:
    parts: list[str] = []
    for s in inp.outline.screens:
        parts += [s.route, s.purpose]
    for e in inp.outline.entities:
        parts += [e.name, *e.fields]
    for o in inp.outline.operations:
        parts += [o.id, o.path]
    for f in inp.outline.flows:
        parts.append(f.name)
    return " ".join(parts).lower()


def _mentioned(label: str, corpus: str) -> bool:
    words = [_stem(w) for w in _WORD.findall(label.lower())]
    return not words or any(w in corpus for w in words)


def handle(input: C12Input) -> C12Output:
    findings: list[CriticFinding] = []
    for item in input.validator_findings:
        if item.severity == "error":
            findings.append(
                CriticFinding(
                    kind="other",
                    severity="error",
                    message=item.message,
                    target_ids=item.target_ids,
                    suggested_fix="Fix the broken reference or remove the item that causes it.",
                )
            )
    if not input.outline.screens:
        findings.append(
            CriticFinding(kind="mismatch", severity="error", message="The plan has no screens.")
        )
    elif len(input.outline.screens) > 1 and not input.outline.flows:
        findings.append(
            CriticFinding(
                kind="missing_flow",
                severity="error",
                message="The plan has screens but no journey that connects them.",
            )
        )
    corpus = _corpus(input)
    for feature in input.brief.features:
        if len(findings) >= MAX_FINDINGS:
            break
        if not _mentioned(feature.label, corpus):
            findings.append(
                CriticFinding(
                    kind="missing_feature",
                    severity="warn",
                    message=f"The Brief asks for {feature.label}, but no screen or data seems to cover it.",
                    suggested_fix=f"Add a screen or data for {feature.label}.",
                )
            )
    findings = findings[:MAX_FINDINGS]
    verdict = "fix" if any(f.severity == "error" for f in findings) else "pass"
    return C12Output(verdict=verdict, findings=findings)
