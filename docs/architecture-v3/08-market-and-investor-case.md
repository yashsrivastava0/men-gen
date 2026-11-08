# 08. Market, competitors, pricing and the investor case

Written as founder/CEO, from the 2026 research in doc 10. Labels: **[V]** verified, **[S]** secondary or press (directional, may conflict), **[J]** judgment or hypothesis to test. Numbers about private companies are press-reported; do not quote them in a data room without checking primary sources.

## 1. Narrative in one page
**Problem.** Enterprises and IT-services firms still spend roughly $100k-$500k and months to build a custom mobile app [S]. AI builders made demos instant, but they fail on large apps: from-scratch generation of whole apps is unreliable (best model 18.8% functionally correct on AppForge [V]), conversational edits lose intent, backends do not match what was approved, and governance is weak (Gartner's June 2026 note frames vibe coding by citizen developers as a governance, security and compliance problem [V abstract]).
**Insight.** The bottleneck is not code generation; it is **specifying intent precisely** (Cursor's 2026 experiment: "accurately specifying intent was the main constraint" [S]) and **verifying** the result. Strong models should plan once; cheap models should execute small, verified tasks.
**Solution.** A spec-first, mobile-only AI app factory. A Context Engine compiles conversation and documents into a verified, traceable App Spec; a front-end engine builds a mock-data preview in parallel; the user approves the experience and the behavior once; the matching backend is generated and proven against the same journeys.
**Why now.** 1M-context small models at about $0.10 / $0.50 per million tokens make heavy specification nearly free [V/S]; spec-driven development is mainstream but developer-CLI shaped [S]; labs absorb the generic agent loop (Claude Code and Codex reported at ~70% of enterprise AI coding spend [S]) so value moves up to workflow, verification and domain; Expo is stable on one stack for iOS, Android and web [V]; MCP and AGENTS.md standardize how specs reach any agent [V]; IT-services firms face AI-driven price deflation and outcome-based contracts [S].
**Why us.** Mobile-only depth (Verified Stack Kit, Capability Module Library), measured sufficiency (SpecBench-Mobile), preview-to-backend determinism, controlled change, and a cost base and distribution into IT services from India with US/EU sales.

## 2. Market evidence [S unless marked]
| Signal | Data | Reading |
|---|---|---|
| Category funding | Lovable $400M at ~$13.3B (Aug 2026; ARR >$500M in June, >$600M by September; press disagrees on round label and date). Replit ~$9B (Mar 2026). Emergent $130M at ~$1.5B (Jul 2026) after $70M at $300M (Jan 2026). Rork $15M seed (Apr 2026) | Investors pay for this category; revenue multiples at the top are ~20-30x ARR and volatile |
| Spend | Menlo (Nov 2026 report, via secondary coverage; Menlo invests in Anthropic): enterprise AI coding spend ~$34B in 2026, largest enterprise AI category; Claude Code and Codex ~70% | Coding is where enterprises spend; the agent layer belongs to labs |
| Consolidation | Wix bought Base44 ($80M, 2025); Cognition bought Windsurf; Lovable reported seeking acquisitions | Exit routes exist for assets with distribution and workflow |
| Churn | Creation-layer traffic and retention are weak; durable products attach deploy, database and hosting (Sacra); Replit pivots to enterprise | Program-level lifecycle matters |
| Customer cost | Enterprise custom mobile app $100k-$500k; mid-complexity ~$190-280k in Western Europe vs ~$70-120k in India (agency blogs) | Large replaceable spend |
| Services pressure | Indian IT price deflation ~2-3.5%/year, shift to outcome-based deals, firms declining over-aggressive AI deals; TCS AI run-rate $2.6B | Services firms need delivery tools now |

## 3. Competitive landscape
| Player | Mobile output | Plan / spec step | Enterprise | Funding (press) | Gap we exploit |
|---|---|---|---|---|---|
| Lovable | Web-first; mobile through conversion (Capacitor / Expo) [S] | Chat and plan modes | SOC 2 Type II, SSO reported [S] | $400M at ~$13.3B | No mobile-native spec; edits via chat |
| Replit | Expo path with Expo Launch to App Store (Jan 2026) [S] | Agent 4 (Mar 2026): parallel tasks, design canvas [S] | SOC 2 Type II [V], SSO, SCIM, VPC [S] | ~$9B | Fixes conflicts after the fact; no traceable spec |
| Bolt (StackBlitz) | Official Expo partnership, Expo Router and NativeWind [S] | Basic | Not verified | Not verified | Generic, no program-level flow |
| Emergent | Mobile agent on Expo + FastAPI, paid plans [V help page] | Light | Not verified | $130M at ~$1.5B | Closest mobile + India competitor; no verified spec or G3 determinism |
| Rork | Expo / RN focus, broadening to web and games [S] | Light | Unclear | $15M seed | Prosumer; not enterprise-governed |
| Vibecode | RN through Expo [S] | Light | Unclear | Small seed (details unverified) | Prosumer |
| Rocket | General apps; "Solve" research and PRD before "Build" (Apr 7, 2026) [S] | Yes (research / PRD) | Claims | Unverified | Plan step exists; not mobile-verified, no sufficiency test, no backend determinism |
| Pega Blueprint / Microsoft Power Apps Plan Designer | Inside their own platforms [S/V] | Conversational blueprint and plan (agents for requirements, process, data, solution) | Strong | Incumbents | Platform lock-in; not React Native source code you own |
| Expo (EAS, Launch, MCP) | The runtime and distribution layer [V] | None | Enterprise plan | n/a | Could move up-stack; also our platform partner |
| Xcode 26.3, Claude Code, Codex | Native iOS toolchain adds agents (Feb 2026) [S]; labs' agents | Developer-side | n/a | n/a | Developer tools, not a business-user workflow |
Observation: "has a plan step" and "enterprise controls" are **not** differentiators any more; verified sufficiency, controlled change, preview-to-backend determinism, module depth and distribution are.

## 4. Customers
| Segment | Buyer | Job to be done | Why they buy us | Objection and answer |
|---|---|---|---|---|
| IT-services firms and agencies (India first; US/EU delivery centers) | Delivery head, practice head, agency founder | Win and deliver fixed-price / outcome apps faster; cut discovery and design effort | White-label, per-program economics, traceable specs they can hand to clients | "We will build our own": the module library, SpecBench and Stack Kit take a year of operations to replicate; we are model-agnostic |
| Enterprise digital teams (US, EU, India) | Head of digital / product, CTO | Customer and employee apps with predictability | Approve on a real preview, backend follows, code is theirs | "AI apps are risky": honest capability tiers, golden-journey proof, change control and undo |
| Founders and self-serve builders | Founder, PM | Idea to credible app quickly | Express profile, free Brief and preview | Top of funnel and data; not the revenue core |

## 5. Pricing and unit economics (hypotheses [J])
Buyers prefer predictability: hybrid pricing is most common (ICONIQ 48%, Bessemer 41% of AI companies), buyers lean to consumption (43%) over outcome (27%), and 78% of IT leaders report surprise AI charges [S].
| Plan | Shape | Notes |
|---|---|---|
| Self-serve | Free Brief and Express preview; credit packs | Hard caps; pre-run estimates |
| Team | Monthly platform fee + credits | Seats are weak units; credits track work |
| Enterprise / services | Annual platform fee + per-program fee ($5k-$50k hypothesis, anchored to $100k+ of replaced spend) + optional outcome component on delivered programs | Caps and estimates; white-label for services firms |
**COGS model.** Context pipeline: about $0.5-5 per large program (doc 07 estimate). Front-end and backend generation: unmeasured; extreme autonomous experiments cost $1.3k-$20k (Cursor SQLite rebuild; Anthropic compiler) [S/V], so our target is a small fraction by using planned, small, verified tasks (measure per program). Preview and build infrastructure, support and review time complete the picture. Target gross margin 65-75% (investors benchmark AI apps at ~60-75% inference-adjusted [S]); this is a hypothesis until pilots measure it.

## 6. Defensibility, ranked
| Moat | Why it holds | Time to build | Strength |
|---|---|---|---|
| Eval corpus and SpecBench-Mobile (plus approved-spec corpus, opt-in) | Others cannot copy measured quality and the data behind it | 3-6 months, compounds | High over time |
| Capability Module Library + Module Factory | Quality across categories without hardcoding; expensive to replicate | 6-12 months | High over time |
| Workflow as system of record (spec, approvals, change sets, traceability) integrated with Jira / Confluence / Figma / GitHub | Becomes where requirements live for AI-built apps | 12+ months | Med-High |
| Distribution through IT-services partners | Channel access is slow to copy | 6-18 months | Med-High |
| Verified Stack Kit and Backend Kit with canary CI | Operational excellence on a fast-moving stack (~3 Expo SDKs a year [V]) | Continuous | Med |
| Preview-to-backend determinism (G3 manifest, journey replay) | Process and tooling | 6-9 months | Med |
| Planner / worker economics, model-agnostic | Margin improves as models cheapen | Built in | Med |
Investors rejected "we use model X" as a moat and prefer workflow depth, proprietary data loops, privileged distribution and integration lock-in [S]; the table is built on exactly those.

## 7. Go-to-market
1. **Beachhead:** Indian IT-services firms and digital agencies (known buyer pain, reachable, price-sensitive but high volume), via 3 design partners who bring real RFPs. Show spec plus preview within days of an RFP.
2. **Expand:** Gulf and Southeast Asia through the same partners; US and EU enterprises through services partners and a direct enterprise motion once G3 and backend proofs exist.
3. **Product-led top of funnel:** Express profile and a public benchmark subset ("PRD to preview in 20 minutes", with numbers).
4. **Partnerships:** Expo ecosystem (build and distribution); model vendors (credits, enterprise endpoints); design-tool vendors (Figma, tokens).
5. **Sales evidence kit:** SpecBench results, demo programs per category family, pilot case studies, honest capability tiers.

## 8. Milestones and the raise [J]
| Window | Milestone | Proof for investors |
|---|---|---|
| 0-3 months | Context Engine end to end on 5+ category families; SpecBench v1 numbers; 2-3 design-partner LOIs | Benchmark table, demo, LOIs |
| 3-6 months | Front-end engine with G3 preview; 3 paying pilots | Pilot revenue, first-pass fidelity, change-request rate |
| 6-12 months | Backend + QA with journey-replay acceptance; first enterprise contracts; public benchmark | Gross margin per program, NRR signals, references |
**Metrics to report:** time to approved spec; requirement coverage; probe pass rate; first-pass front-end compile and render rate; change requests after G3 and lane mix; cost per approved spec and per program; gross margin per program; pilot-to-contract conversion; NRR. **Data room:** these architecture docs, SpecBench methodology and results, demo recordings, LOIs, cost model, roadmap, IP and code-ownership policy (customer owns outputs; export mandatory), an honest security-baseline statement (baseline only for now).

## 9. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Incumbents copy a plan step | Moat is measured quality, module library, G3 determinism and channel, not the plan step |
| Expo or Lovable moves up-stack | Stay model- and agent-agnostic; partner with Expo tooling; own the workflow and verification |
| Labs bundle spec and agents | Sit above them as the enterprise workflow layer; orchestrate their agents through the Spec Server |
| Platform policy (Apple 2.5.2, Google target API 36 by Aug 31, 2026 [S/V]) | AD-23; Stack Kit tracks store baselines; deliver ordinary Expo projects |
| Spec friction vs instant demo | Express profile, early Look preview, slice preview |
| Quality over-claim | Publish metrics; honest tiers; decline unsupported categories |
| Generation cost at scale unmeasured | Measure per program from the first pilots; planner/worker design; hard caps |
| Valuation volatility | Capital-efficient plan; revenue from the standalone Context Engine early |
| Execution breadth (four engines) | One engine at a time, each with an acceptance gate; sell the Context Engine alone first |
**Kill / pivot signals:** probe pass rate or canary implementability stuck below target after two model generations; change requests after G3 above 30% from insufficiency; design partners unwilling to pay for the Context Engine alone.

## 10. Investor Q&A
1. *Lovable or Replit will add this.* They can add a plan step; they cannot quickly copy a measured benchmark, a category-deep module library, a preview-to-backend contract with journey replay, or an IT-services channel. We also stay above the agent loop and orchestrate whichever agents win.
2. *Why mobile only?* Depth. Mobile has a fixed, verifiable stack (Expo), real store constraints and a high-value services market; depth is what lets weak models succeed.
3. *Models will one-shot apps soon.* Today they do not (AppForge 18.8% [V]); even if they improve, enterprises need approval, traceability and change control, and cheaper models running verified small tasks stay cheaper.
4. *Where is your data moat?* SpecBench, the approved-spec corpus (opt-in, anonymized), module library and golden-journey library.
5. *Unit economics?* Context cost is cents to a few dollars; generation cost is the unknown we measure in pilots; target gross margin 65-75%.
6. *Why would services firms buy rather than build?* Time-to-value and maintenance of a fast-moving stack; white-label; their margin pressure is immediate.
7. *Platform risk with Apple and Google?* We never ship a store app that executes generated code; outputs are normal Expo projects.
8. *Quality proof?* Published SpecBench, sufficiency probe rate, first-pass compile and render rate, change-request rate after G3.
9. *Why India?* Cost base and access to services buyers, with US/EU sales through partners; the largest private competitor in mobile (Emergent) is also India-founded, which validates the market and signals the competition.
10. *IP and code ownership?* The customer owns the outputs; code export is mandatory.
11. *Can you really generate a backend?* Contract-first with fixtures and golden journeys; the backend's creative surface is small because behavior was approved; we are explicit about tier B and hand-off items.
12. *Security and compliance?* Baseline hygiene now; regional compliance and enterprise identity controls are on the roadmap when a customer needs them. We say so plainly.

## 11. Valuation context
Leaders trade at roughly 20-30x ARR on press numbers (for example ~$13.3B on ~$500M [S]); Emergent re-rated from $300M to ~$1.5B in about six months [S]; Rork's $15M seed is a seed-stage reference [S]. These are not anchors for us; they show appetite. Early rounds are priced on team, evidence and wedge: the standalone Context Engine with benchmark numbers and design-partner proof is the wedge.
