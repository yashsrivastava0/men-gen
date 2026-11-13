# 06. Preview and gate experience (requirements for what the user sees)

The owner's requirement: **what the user sees must be beautiful, and the full output must be shown.** Agent-UX guidance for 2026 points the same way: gate only high-stakes moments, make approval and undo primary controls, and show intent, plan and status [S]. This document specifies requirements for the review surfaces and for the front-end engine's preview. Visual design of the builder UI itself is a later step; these are the behaviors and content it must deliver. (AD-07, AD-08, AD-09, AD-13)

## 1. Principles
1. **Summary first, everything one click away.** Each gate opens on a human view; the full spec, the raw artifacts and the downloads are always reachable (Spec tab, Downloads).
2. **Nothing hidden, nothing raw.** No JSON or YAML in primary views; raw files only in the Spec tab and downloads.
3. **Show provenance.** Every item can show where it came from: you said, your PRD p.4, assumed (and why), researched (with link), pattern default.
4. **Approval and undo are first-class.** Every applied change has an undo; every gate has a visible "what changed since you last looked".
5. **Visuals come from the spec, not from decoration.** The Look tile, app map and wireframes are rendered by code from tokens and the graph, so they are accurate and reproducible.
6. **Calm and fast.** First meaningful view of any gate in under 3 seconds from the stored artifact; long runs show a timeline with real counts ("found 14 features, 62 screens, 9 entities").
7. **Language and voice.** The builder UI supports the user's language, voice dictation and file drop anywhere in the chat.

## 2. Layout
```
+----------------------------------------------------------------------------------+
| Program: <name>   v15 (B-G2)   profile: Standard   credits: 62 left   [Share]    |
+---------------------------+------------------------------------------------------+
| Chat / voice              | Artifact panel                                       |
|  messages and cards       |  [Brief] [Structure] [Plan] [Spec] [Preview] [Docs]  |
|  progress timeline        |  renders the current gate's view; tabs stay available|
|  change list + undo       |  provenance chips, diff toggle, download menu        |
+---------------------------+------------------------------------------------------+
```

## 3. Gate views owned by the Context Engine
**G1 Brief View.** What you are building (one sentence); apps and who uses them; features per app grouped by module with in / later / out toggles (the Scope Ledger) and capability badges (A/B); the look (swatch and style name); login method; name and logo; "We assumed" list (each with a toggle and a reason); sources read (with coverage). Every row is tap-to-edit. Buttons: *Looks right* / *Change something* (Enterprise: *Send for approval*).

**G1.5 Structure View (policy).** App map (screens as a graph by app and module), roles and permissions matrix, entities in plain words with their statuses, screen inventory, size versus cap, Phase 1 / Phase 2 split.

**G2 Plan View.** In order: summary; app map; flows with alternates; roles and permissions; **Look** (style tile rendered from tokens, three directions if still open); **Screen gallery** (wireframes rendered from the spec; filter by app, module, role); data in plain words (entities, statuses, key rules with `confirm` flags); what research added (each item with source link and include/exclude toggle; the full findings page is already visible while Spec runs, see doc 13 §9; toggles and rejections are collected here); assumptions; deviations and `degraded` nodes; risks and out-of-scope; size and effort estimate. Tabs: **Spec** (full document viewer with search, filter by app/role/screen, provenance chips, version diff) and **Docs** (downloads).

**Phasing of visuals.** v1: app map, section lists, style tile. v2: wireframes for every screen. v3: clickable wireframe walkthrough of the golden journeys (before any front-end build), which catches structural misunderstandings early and cheaply.

**Downloads.** `APP_SPEC.md`, `spec.json`, `tokens.dtcg.json`, `DESIGN.md`, `contract/openapi.yaml`, BRD/SRS (Word/PDF), user stories, traceability matrix (spreadsheet), Behavior Book.

## 4. Review Mode: the front-end preview (AD-09)
The preview must let a non-technical stakeholder judge design, behavior and feature coverage. Requirements the **front-end engine** must satisfy (acceptance criteria for its design phase):
| ID | Requirement |
|---|---|
| RM-01 | Preview reachable by link within a bounded time after build; typical app ≤ 5 minutes (target [J]) |
| RM-02 | **Persona / role switcher**: every role in the permission matrix, without logging out; customer and admin apps in one workspace |
| RM-03 | Device and size switcher: small and large phone, tablet, web breakpoints, device frames |
| RM-04 | Theme toggle: light / dark and any design variants |
| RM-05 | Locale toggle including RTL |
| RM-06 | **State inspector**: force loading, empty, error, offline, no-permission and long-content on the current screen |
| RM-07 | **Scenario runner**: the golden journeys as guided walkthroughs with step highlighting, expected vs observed, reset |
| RM-08 | Seed profiles: small, large, edge-case data; reset to defaults |
| RM-09 | **Feature panel**: the Scope Ledger as a list; toggling a feature creates a change request |
| RM-10 | **Inspector**: click any element to see its spec node, tier, provenance and a "change this" action (works through `@spec` stamps and `testID`s) |
| RM-11 | **Annotation**: pin a comment on a screen region; it becomes a change-request draft with screenshot and spec id |
| RM-12 | Version switch and side-by-side compare (for example v14 vs v15) |
| RM-13 | Share links with roles (view / comment) and expiry |
| RM-14 | "Simulated" badges on tier B behaviors (payments, live location, push) |
| RM-15 | Quick accessibility and performance report per screen |
| RM-16 | Offline simulation toggle |
| RM-17 | Time control for time-based flows (order progression, reminders) |
| RM-18 | Screenshot gallery export of all screens in all key states |

**Delivery options.**
| Option | Use | Notes |
|---|---|---|
| Web preview (Expo web build of the same code) | **Default** for stakeholders and for G3 | Reliable, shareable, no store dependency. Tier B behaviors are simulated and badged |
| Dev build (QR / internal distribution) | Native fidelity for testers | Required for native modules; takes longer |
| Expo Go | Only when the SDK matches the project | In May 2026 the App Store Expo Go lagged: the SDK 55 build awaited Apple approval while SDK 54 stayed live [V]; Expo Go also runs only modules built into it |
| Streamed device in browser | Optional later | Vendor evaluation pending (OI-2); pricing not verified |
Never ship a store app that downloads or executes generated code (Apple Guideline 2.5.2, enforced against vibe-coding apps in 2026 [S]); previews are web, Expo Go or dev builds only.

## 5. G2.5 slice preview (policy)
After Wave 2 the primary flow exists as a real, interactive slice: the user sees the true look, navigation and data behavior before the remaining screens are generated. Large programs require it; Standard shows it non-blockingly; Express skips it.

## 6. G3: the last mandatory user gate
Tabs: **Experience** (live preview with journey checklist and pass marks), **Behavior Book** (plain-language rules, statuses, permissions, notifications; a *confirm* list the user must answer), **Integrations** (Activation Checklist: recommended providers, "use recommended" default, credentials needed later), **Open items and deviations** (what the front-end deviated from, known issues accepted), **What you approved** (manifest summary: spec version, contract v1, feature list, journeys). A banner shows change sets applied since G2.
Button: **Approve and build backend.** After approval the user sees backend progress (steps and estimates), the preview stays live, and later changes appear as release deltas with their own impact cards. G4 delivery sign-off (QA report, store-readiness checklist, code export) is policy-optional.

## 7. States the UI must handle gracefully
Partial failures (a stage retried or `degraded`) are explained in plain language with what was done about them; raw errors are never shown. Long runs show the timeline and allow Stop (return to the last gate) and Pause. Resuming after days shows a recap. Declined requests explain why and offer the alternative. Empty states teach (what to do next).

## 8. UX measures
Time to first view per gate; edits per gate; abandonment per gate; number of "you decide" uses; time at G3; change sets per program and lane mix; undo rate; inspector and annotation usage; percentage of G3 approvals with open `confirm` items (should be zero).

## 9. Accessibility of the builder itself
Keyboard operable; screen-reader labels on all controls and views; contrast AA in both themes; responsive from phone to desktop (non-technical founders often work from a phone); respects reduced-motion.
