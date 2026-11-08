# 01. Thesis, scope, categories and capability tiers

## 1. What we are building
An AI app builder for **mobile only** (iOS and Android through Expo / React Native / TypeScript), sold to companies and usable by non-technical people. The platform is a chain of engines. Each engine reads the previous engine's file and writes one file for the next.

```
Context Engine  ->  Front-end Engine  ->  Backend Engine  ->  QA Engine
 (6 stages)         (app + preview)       (server)            (journeys)
   G1, G2             G3 (last user gate)   G4 optional         report
```

Only the Context Engine is being designed and built now. This document fixes the vision and the limits that every other document relies on.

## 2. Thesis
**The Context Engine is a compiler for product intent.** It takes messy human input (conversation, voice notes, PRDs, decks, screenshots, links, brand assets) and emits a verified, versioned, traceable, sliceable App Spec Package.

Two consequences:
1. **Planner/worker economics.** Strong reasoning is spent once, at specification time. Generation becomes many small, independent, individually verified tasks that a small model can do. Evidence that this split works in 2026: Cursor's large-scale experiment used frontier models to plan and cheaper models to execute, cut merge conflicts from about 70,000 to under 1,000 and concluded that "accurately specifying intent was the main constraint" [S via The Decoder]; Anthropic's 16-agent compiler run succeeded because of a near-perfect verifier and work partitioned into independent failures [V]. Evidence that one-shot generation of whole apps fails: AppForge, where the best model produced only 18.8% functionally correct from-scratch Android apps and over half of those still crashed [V abstract].
2. **Predictable delivery is the product.** We do not sell "an app appears". We sell: *approve the experience on a realistic preview, and the matching backend is generated and verified automatically*, with every requirement traceable and every change controlled.

The Context Engine is also sellable on its own (requirements, wireframes, API contract and BRD/SRS in hours), which gives revenue and data before the other engines mature.

## 3. Who we serve
| Segment | What they need | Why they pay |
|---|---|---|
| IT-services firms and agencies (India, US, EU) | Faster discovery and delivery under price pressure (AI-driven price deflation of about 2-3.5% a year, shift to outcome-based deals [S]) | Productivity per project; white-label; per-program pricing |
| Enterprise digital teams (US, EU, India) | Customer and employee apps with predictability, brand/design-system fidelity, existing-API integration, code ownership | A custom enterprise mobile app costs roughly $100k-$500k to build [S]; we replace a large part of that spend |
| Self-serve builders and founders | Fast path from idea to a credible app | Top of funnel, product feedback, module-library data |

High-paying markets (US, EU, India) matter more than volume; one customer never defines the product. Everything that varies by customer or category lives in data (modules, tokens, policies).

## 4. Scope
**In:** iOS and Android apps built with Expo; responsive web build of the same code for previews and admin-style surfaces; multiple app categories; customer app plus admin/operations app in one program; mock-data front-end first, backend after approval; code export.

**Out for now:** the web-first product category (marketing sites, SaaS web apps), native-only (Swift/Kotlin) apps, privacy/security/compliance depth (owner directive; baseline only), SSO/VPC/SOC 2 (later), billing/publishing automation, chat-UI visual design beyond requirements (doc 06).

### 4.1 Categories: fixed stack, open categories (AD-05)
Categories are not hardcoded. An app is composed from **Capability Modules** (data: purpose, roles, screen recipes, entities and lifecycles, operations, rules, states, integrations, mock seeds, acceptance patterns, tier, dependencies). About 70 modules are planned, in these families: identity and access; catalog and commerce; booking and scheduling; delivery and logistics; payments and wallet; communication (chat, notifications, support); content and community; workflows (forms, approvals, tasks, field inspection); dashboards and admin (KPIs, tables, CRUD, reports); finance; health and wellness; learning; travel and hospitality; marketplaces; civic and utilities; enterprise workforce.

The model **selects, parameterizes and extends** modules; parts with no matching module become flagged `custom` modules with stricter validation. Industry vocabularies (FHIR, GS1, schema.org, BIAN) may be offered as alignment hints with confidence labels, never as silent mappings.

Launch category families (examples, not a closed list):
| Family | Examples | Typical second app | Tier B items |
|---|---|---|---|
| Commerce and marketplaces | retail, grocery, fashion, B2B ordering | seller / merchant console | payments SDK, push |
| Food, grocery and on-demand delivery | restaurant delivery, quick commerce | restaurant ops, rider app, platform admin | live tracking, maps |
| Travel, hospitality and booking | hotels, tours, tickets, appointments | property / host console | availability complexity |
| Financial services (UI layer) | banking, wallets, lending, insurance servicing | ops dashboard | KYC SDK, biometrics |
| Healthcare and wellness | clinics, telehealth shells, fitness, pharmacy ordering | clinic admin | video calls, health APIs |
| Education and learning | courses, tutoring, school-parent apps | teacher / admin console | DRM video (declined) |
| Logistics, field service and fleet | dispatch, inspections, proof of delivery | dispatcher dashboard | offline sync, scanner |
| Enterprise workforce and internal tools | HR self-service, helpdesk, approvals, expenses | admin / config | SSO (deferred) |
| Real estate, automotive, marketplaces | listings, viewings, service booking | agent / dealer console | maps |
| Civic, utilities, telecom | citizen services, bill pay, usage, outages | operator console | payments |
| Media, content and community | news, membership, forums, events | CMS / moderation console | streaming (declined), live |
| Nonprofit, membership, loyalty | donations, memberships, rewards | admin | payments |

### 4.2 Declined categories (owner: no games and no other hard-to-generate categories)
Declined at intake, politely, with the nearest buildable alternative:
| Declined | Offered instead |
|---|---|
| Games of any kind | Companion app (profile, store, community, leaderboards UI with mock data) |
| AR / VR / 3D experiences | Catalog or shell without the immersive view |
| Photo / video editors, camera filters | Gallery, sharing and account shell |
| DRM streaming players (OTT) | Browse, watchlist, profile, subscription shell; player integrated by engineers |
| Trading terminals, high-frequency charts | Portfolio, watchlist and order-ticket UI with delayed mock data |
| OS-level utilities (keyboards, launchers, system tools) | None |
| Medical-device or safety-critical software | Patient-facing scheduling and communication shells only |
| Proprietary-hardware / IoT with vendor SDKs | Dashboard shell with mocked device data |

## 5. Capability tiers (honest limits) (AD-06, AD-15)
The Stack Kit (fixed defaults, re-verified on every Expo release; AD-15) carries a capability matrix. Every feature in the Scope Ledger has a tier.

- **Tier A, reliable:** authentication and onboarding; tab / stack / drawer navigation; lists, detail pages, search, filter, sort; validated forms and wizards; profile and settings; notification center UI; standard charts and admin tables; content feeds; catalog, cart and checkout UI with provider SDK hand-off; booking and scheduling UI; chat UI (mock); i18n including RTL; dark mode; read caching.
- **Tier B, supported but flagged (dev build, native configuration or adapter; real behavior arrives with backend and integrations):** real push notifications; live location and tracking; background location; biometrics; native payment sheets and PSP SDKs; camera and barcode scanning; document capture and upload; deep and universal links; offline-first sync with conflict resolution; in-app purchases and subscriptions; Bluetooth and NFC; health and fitness APIs; real-time voice and video; complex maps (clustering, routing); dense data grids and heavy charts; PDF generation; e-signature; KYC vendor flows.
- **Engineering hand-off (inside supported apps, listed as tasks, not promised):** custom native SDK wrappers, on-device ML, collaborative real-time editing (CRDT), pixel-perfect brand reproduction from raster images alone, heavy custom animation and gesture canvases.

**Honest statement for Zomato-class programs:** we deliver the front-end shell, flows and a mock-backed experience across customer, restaurant, rider and admin apps (on the order of 60-120 screens), then a contract-verified backend for the approved behavior. Live tracking, real payments and recommendation engines are Tier B or backend work and are listed as hand-off tasks. We never promise parity with a mature production app.

## 6. Principles
1. **The spec is the product.** Everything downstream is derived from it or checked against it.
2. **Compose, don't invent.** Modules and recipes first; invention is flagged and validated harder.
3. **Strong planner, cheap workers.** Spend reasoning on specification and verification.
4. **Sufficiency is tested, not assumed.** A weak model must be able to implement each slice or the slice is a gap.
5. **Humans see views; machines read the graph.** Summary first, full output one click away.
6. **Every change goes through the spec**, never around it.
7. **Code does the plumbing; models do the creative part.** Routes, types, clients, tokens and fixtures are generated deterministically.
8. **Honest limits.** Tiers A/B/C are visible to the user; unsupported things are declined early.
9. **Model- and agent-agnostic.** The gateway and the Spec Server are the stable surfaces.
10. **Measure everything.** If we cannot score it in SpecBench-Mobile, we do not claim it.

## 7. Success criteria for the Context Engine (initial targets [J], calibrated in doc 07)
- A valid, approved spec for a 100-screen, two-app program in about 15 minutes after G1 (research and build), and for an Express app (≤ 12 screens) in about 3 minutes.
- At least 90% of explicit requirements in a supplied PRD captured; zero Locked nodes without evidence.
- A small canary model implements at least 85% of sampled screens (type-check and render) on the first attempt.
- Change requests after G3 attributable to spec insufficiency fall below 10% of all change requests.
