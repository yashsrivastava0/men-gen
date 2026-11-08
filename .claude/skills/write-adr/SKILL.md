---
name: write-adr
description: Write an Architecture Decision Record in docs/adr/ from the template, update the docs index and the decision log. Use for any non-trivial design decision or any breaking contract change.
---

# Write an ADR

1. Find the next number: list `docs/adr/` and take the highest `NNNN-*.md` plus one (zero-padded to four digits). ADRs 0001 to 0011 exist (stack, environment, pack navigation, transactions, budget guard, spec hash, fake store, private Postgres, security review, research pipeline, contract evolution): read the neighbours for tone and length first.
2. Copy `docs/adr/TEMPLATE.md` to `docs/adr/NNNN-short-kebab-title.md` and fill every section: status (proposed, accepted or superseded by NNNN), date (today as `YYYY-MM-DD`), deciders, context, decision, alternatives considered, consequences, references; for a contract change also the version bump and the list of consumers updated.
3. Keep it to about one page. State the reason, not the history; name the code, the test and the `DECISIONS.md` entries that carry the decision.
4. Add the ADR to the ADR list in `docs/README.md` (the index is the lead's file; if you are not the lead, include the one-line index entry in your report). `uv run python scripts/docs_check.py` fails when an indexed link is broken.
5. Add a one-line entry to `docs/implementation/DECISIONS.md` (date, decision, alternatives, reason) linking the ADR (lead-owned: put the line in your report if you may not edit it).
6. A breaking change to `mde_contracts` needs: the ADR, a version bump, and every consumer updated in the same change (lead only; see [ADR 0011](../../../docs/adr/0011-contract-evolution-additive-only.md)).
7. Superseding an ADR: set the old one's status to `superseded by NNNN` and link both ways.
