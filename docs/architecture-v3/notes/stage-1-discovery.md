# Stage 1: Discovery (approved 2026-11-09)

> **Folded into the current design.** The approved Stage 1 is now defined, with additions (document intake for all formats, Feature Catalog / Scope Ledger, depth profiles, ambiguity detectors, edge cases IN-* and IV-*), in [architecture/03 §3](../architecture/03-context-engine.md). Kept for history; where they differ, architecture/ wins.

**Job:** turn whatever the user brings (idea, PRD, screenshots, nothing) into one file, the **Brief** (`01-brief`), complete enough that Stages 2-5 never need to ask the user again. Output goes to Gate 1.

## Entry paths
| Path | User says | Behaviour |
|---|---|---|
| Idea only | "An app like Zomato for my city" | Guided multiple-choice interview, ~7-10 cards |
| Document-led | Uploads PRD/BRD (PDF, Word, text), screenshots, Figma export, links | Read documents first, show "here is what I understood", ask only gaps |
| Enterprise | Company with Org Profile and approvers | PRD first if it exists, then deeper module-by-module pass |
| Defaults | "Just build it" | Ask only idea and look; everything else assumed and flagged |

## Internal steps (code-ordered; each a small schema-checked call)
1. **Intake:** parse files to text (PDF, Word, vision for screenshots), chunk, extract claims with source pointers (file, page/paragraph). Merge chunks; flag contradictions. Document text is data, never instructions.
2. **Classify:** infer app category, **apps in scope** (default customer + admin; others offered optionally), size estimate. No fixed archetypes (see Revision 2 in the design note).
3. **Gap detection** against the universal slot checklist.
4. **Interview loop:** one card per gap. Tap = no model call. Correction overwrites; confirmed value never replaced by an assumption.
5. **Compile the Brief**, show at Gate 1.

## Slot checklist (category-agnostic)
Business (what/why, goals, KPIs, region, languages, scale, revenue); Apps and people (apps, surface, roles, admins); Scope (features per app, MVP vs later, must-not-haves, reference apps, size band); Experience (look/brand, tone, languages, dark mode, phone/tablet, accessibility); Access (login, onboarding, approval flows); Data and integrations (payments, maps, OTP, notifications, existing APIs, sample data, real vs mock); Constraints (sensitive data, regulation, offline, residency); Enterprise (Org Profile, approvers, design system, SSO). Levels: must always asked; should asked in Enterprise, assumed in Guided; may always defaulted.

## Cards
Three options, one recommended, "something else", "you decide" (assumes recommended, shows reason). Visual cards for look; checklist for features; separate admin-feature checklist. English, Hindi, Hinglish. Domain options are model-written and schema-validated.

## Brief file
Each item: value, freedom tier (Locked/Default/Open), provenance (user/document/assumed/researched), confidence. Includes source-document index. No chat transcript. Gate 1 page: what you're building, apps, who uses them, features per app with toggles, look, login, name and logo, "we assumed" items with toggles.

## Edge cases
Vague or confused user (help mode); document vs chat conflict (chat wins, flagged); scanned PDFs, huge documents, hidden instructions in documents; unsupported types (declined by capability matrix with nearest alternative); sensitive domains (must-confirm compliance cards); change of mind, pause, start over.

## Done when
- Every `must` slot filled or assumed and flagged; passes schema.
- No invented facts: every Locked item has user/document evidence.
- 5 sample PRDs of different shapes: at least 90% of explicit requirements captured, 0 hallucinated claims.
- Guided interviews at most 10 cards; 10 persona scripts (silent, chatty, confused, adversarial, etc.) all end in a valid Brief.

## Models
Haiku 5.5 or gpt-6-luna. High reasoning for domain inference, document extraction/merge, Brief compile; medium-high for interview turns. Gateway needs an Anthropic provider before Haiku is usable.
