# Context Engine: simplified sequential design (6 stages, one file each)

> **Folded into the current design.** This early draft (2026-11-09, first half of the day) is superseded by [`architecture/`](../architecture/00-index-and-decisions.md) (v3.1), especially [architecture/03](../architecture/03-context-engine.md). Kept for history; where they differ, architecture/ wins.

## Context
Owner's direction: Context Engine = gather context from the user (plus our assumptions and web research) and produce ONE detailed source of truth that a front-end engine (even a small model) can turn into a beautiful, correct React Native app with mock data. Then the backend engine runs after the user approves the front end. One session builds BOTH the customer app and the admin app in a single go. Sequential flow, few agents, each agent writes one file (JSON, MD, anything). Performance over cost; Haiku 5.5 / gpt-6-luna are fine. No git talk: this is a chat-style design exercise. Scope here is the Context Engine only.

Based on the existing documents in `projects/mobile-design-engine/` (final spec rev 3, 10 steps C1-C14). That design is sound but has weak spots for the owner's goal (below). Repo state is unchecked and is not needed for this design pass.

## How a real mobile SDLC works, mapped to us
Discovery -> requirements (BRD/PRD) -> UX (journeys, IA, wireframes, design system, copy, states) -> technical design -> plan/acceptance -> build. The Context Engine covers discovery, PRD, UX, design system, and the data/API *shape* a front-end needs for mock data. Deep backend design is left to the backend engine.

## The 6 stages (down from 10), each = one file, each reads only the previous files it needs
| # | Stage | Job | Output file |
|---|---|---|---|
| 1 | **Discovery** (intake + interview) | Takes idea / PRD / PDF / Word / screenshots / links; extracts claims with source; asks fewest multiple-choice questions; deeper module-by-module for enterprise. Detects the apps in scope (customer, admin, driver...) | `01-brief` **(Gate 1)** |
| 2 | **Research** | Competitor/pattern scan, compliance pointers, SDK/Expo facts, user's brand. Every fact sourced; digested, never dumped | `02-research` |
| 3 | **Blueprint** (product + UX architecture + data/behavior) | PRD, roles and permissions matrix, modules, MVP vs later, journeys, navigation per app, screen inventory, entities, business rules and state lifecycles, API operation shapes, mock-data plan | `03-blueprint` |
| 4 | **Design** | Tokens, typography, spacing rhythm, component style and variants, icon/imagery strategy, motion, dark mode, tone of voice, layout patterns per screen type, anti-patterns. Direction and constraints, not pixel layouts | `04-design` |
| 5 | **Screens** | Per-screen detail for every app, in small batches: sections top to bottom, components, data bindings, actions and results, validation, all states (loading/empty/error/offline/no-permission), copy, accessibility, phone/tablet notes, seed data | `05-screens` |
| 6 | **Verify and package** | Code validators (every reference resolves) + critic vs Brief + a **sufficiency probe**: a weak model reads each screen slice and lists any question it would need to ask; any question = a gap to repair. Renders the final single file | `APP_SPEC` **(Gate 2)** |

Each stage is one external "agent" but internally runs small, schema-checked calls (one repair, then fallback). That is what lets small models do this reliably. Code controls order. No free-roaming loops.

## Source-of-truth rules (so a weak model cannot fail)
- **Three freedom tiers on every item:** Locked (user said it / compliance), Default (our recommendation, front-end may change), Open (front-end decides). Prevents binding the designer and prevents invention.
- **Provenance tag on every fact:** user-said / assumed / researched(+source).
- **No pollution:** no chat transcripts, raw research, or contradictions pass downstream; each stage receives only the files it needs.
- **Self-contained slices:** one screen slice contains everything needed to build that screen.
- **One program, one file:** shared sections (product, roles, design, data, rules, integrations) + per-app sections (navigation, screens). Admin surface (`mobile` or `web-dashboard`, Expo) is a field, not a separate pipeline.
- Intermediate files JSON (validated), final file Markdown with fenced YAML blocks (readable by people, sliceable by the Spec Loader).

## Sufficiency checklist (what a developer would demand before starting)
Goals/KPIs; personas and roles with permission matrix; apps and entry/auth/onboarding; navigation map (tabs, stacks, modals, deep links); per-screen detail (above); end-to-end flows incl. alternates; entities + realistic localized mock seeds; status lifecycles (e.g. order states); business rules; operation shapes; design system; global behavior (auth/session, permissions, push, offline, localization, error handling); integrations tagged mock/adapter/real; compliance and privacy; non-functional (performance, accessibility); admin specifics (tables, filters, bulk actions, charts); build sequence + acceptance criteria per flow; assumptions, open decisions, out-of-scope/Phase 2.

## What changes vs the existing design
1. Merge 10 steps into 6 stages (Intake+Interview; Product+UX+Data; Greeting is already part of Discovery).
2. **Add document intake** (PDF/DOCX -> text -> chunked extraction with traceability). Currently out of scope.
3. **Multi-app in one session** (was one session = one app).
4. **Freedom tiers + sufficiency probe** (new).
5. Domain packs for more than 5 archetypes, plus Research-assisted baselines for unknown domains.
6. Model routing configurable (Anthropic provider optional); higher reasoning effort on Blueprint, Screens, Critic; second critic pass.
7. Spec size grows (two apps); verify tokens/screen on live output.

## Next steps once approved (agent by agent, pipeline order)
1. Save this design into the project memory (STATE.md, session log, INDEX.md), one line in chat.
2. Deep dive **Stage 1 Discovery**: exact inputs, questions/slots, document-intake behavior, the Brief file schema, edge cases, acceptance tests. Owner approves, then we move to Stage 2. Same for each stage.
3. Only after stages are designed: map each to the existing code and decide implementation prompts.

## Open items
- Admin app: Expo mobile vs web dashboard default (O17).
- Existing build: refine vs redesign (owner's answer was unclear; assumed "refine and map", confirm at implementation time).
- Gate 2 shows a plain-language plan with a screen map and look preview; confirm what the owner wants to see.

## Verification
Design review by owner per stage; later, sufficiency probe pass rate and the consumer simulator on a 2-app sample spec.

---
# Revision 2 (2026-11-09, owner clarification): nothing hardcoded by app category
Owner: the engine will generate many categories of app for many customers and use cases. Only the tech stack is fixed (Expo, React Native, TypeScript). Haiku 5.5 and gpt-6-luna may run at high reasoning.

- **Fixed:** stack capability matrix, component kit and screen patterns, file schemas and validators, universal question slots.
- **Generated per app:** category, apps in scope, roles, features, flows, rules, entities, mock data, copy, domain questions and options.
- Archetypes are no longer required. The model infers category, apps, roles and a feature checklist at high reasoning from its own knowledge (no web in Discovery; research stays after Gate 1 and corrects/extends the Brief).
- Unsupported apps are declined by the capability matrix, not by category.
- Domain card options are model-written and schema-validated (three options, one recommended, what each sets). Fixed card templates for universal slots.
- Domain packs become optional accelerators: approved sessions can be saved as reusable packs.
- Reasoning: high for domain inference, document extraction/merge, Brief compile; medium-high for interview turns. Anthropic provider needed in the gateway before Haiku can be used.
- Defaults chosen by me (owner declined the questions): customer + admin apps in one go, other apps offered optionally; PRD-first then gaps only for enterprise; admin = Expo responsive web dashboard by default, switchable to mobile.
