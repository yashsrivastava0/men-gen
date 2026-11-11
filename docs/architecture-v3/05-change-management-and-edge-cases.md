# 05. Change management, scope control and the edge-case catalog

The owner expects users to ask for changes a hundred times, including features we marked out of scope. The rule that makes this survivable: **every change goes through the spec, and the spec changes in a classified, validated, reversible way** (spec-anchored, never spec-as-source; Böckeler's warning about MDD-style inflexibility plus LLM non-determinism [V]). AD-10.

## 1. Invariants (enforced in code, covered by property tests)
| # | Invariant |
|---|---|
| I-1 | No downstream stage or engine commits to the Spec Graph, creates a baseline or shows anything as approved before its gate is approved. **Amended 2026-11-10 (AD-36):** read-only or sandboxed speculative work may start earlier if it is keyed by input hash and is discarded or reconciled when the approved hash differs. |
| I-2 | Approvals are bound to artifact and section hashes; any change voids exactly the affected approvals. |
| I-3 | Approved baselines are immutable; changes create new versions. |
| I-4 | A Locked field changes only with a recorded override (who, when, why). |
| I-5 | IDs are never reused; deletion leaves a tombstone; renames add an alias. |
| I-6 | Research and assumptions can produce Default at most, never Locked. |
| I-7 | Every node has provenance; Locked requires user or document evidence. |
| I-8 | Every model output is schema-validated; invalid output goes to repair, then fallback, and never flows downstream unvalidated. |
| I-9 | A build task writes only files it owns. |
| I-10 | After G3, contract changes are additive unless approved as breaking. |
| I-11 | Documents and web content are data; calls that read them have no tools. |
| I-12 | Silence never approves. |

## 2. The change pipeline
1. **Capture.** A request arrives from chat, voice, the preview annotation tool, the feature panel, the inspector ("change this"), or a gate rejection comment. Voice or run-on text is segmented into atomic requests and echoed as a numbered list when there are three or more or any ambiguity (IV-10).
2. **Interpret.** CHNG-1 translates natural language into **patch operations** against the Spec Graph (add / edit / remove node, set field, toggle scope status). Ambiguous asks ("make it modern") go to CHNG-3, which returns three concrete variants instead of guessing (CH-09).
3. **Classify** [code]. The lane comes from the operations, not from the model (section 3).
4. **Dry run** [code]. Apply the operations to a graph clone and run the validators (reachability, references, contract lint, vocabulary). Failures are auto-fixed or rejected with a plain reason **before any code is touched** (CH-03).
5. **Impact.** Dependents are found through graph edges and `@spec` stamps: affected slices, files, tasks, operations, fixtures, scenarios. An estimate (seconds and credits) is computed and shown.
6. **Approval routing.** Risk-scaled: L1/L2 apply immediately with an undo toast (apply-then-confirm); L3 and above show the interpreted operations and impact first (confirm-then-apply). L4 uses the Scope Change Protocol. Enterprise policy can require approver roles.
7. **Apply and propagate.** Commit a new graph version; regenerate only affected tasks; rebuild the preview.
8. **Verify.** Run the affected tasks' checks and the golden journeys that touch changed nodes.
9. **Record.** The change set, its diff, the requester, approvals and the undo operation are stored; the timeline updates.

## 3. Lanes
Lane = the maximum over all operations; L4 is an override class that outranks L1-L3; L5 outranks all.
| Lane | Triggered by (operation targets) | Examples | Approval | Regeneration | Target latency [J] |
|---|---|---|---|---|---|
| L0 Explain | none | "why is this here?" | none | none | seconds |
| L1 Cosmetic | tokens, copy, Open-tier visual fields, icon or image choice | colors, type, spacing, wording, icon swap, theme | requester, auto, undo toast | recompile theme / copy; no re-plan | spec patch ≤ 10 s; preview refresh ≤ 2 min |
| L2 Local behavior | Default fields inside existing screens; optional params or fields on operations | add a field, validation, filter option, empty-state copy, change an action target between existing screens | requester, auto, undo toast (Enterprise: policy) | regenerate affected slices and tasks; additive contract only | spec patch ≤ 60 s |
| L3 Structural | add / remove screen, flow, entity, role, operation, module; navigation structure; lifecycle transitions | new screen, new role, new report | confirm-then-apply; affected sections re-approved | Blueprint patch -> dependents re-run -> plan diff | spec patch ≤ 5 min |
| L4 Scope boundary | any node whose feature is `later`, `out`, `unsupported` or `needs-engineering`; any Locked field; capability tier B/C items | "add group ordering", "remove login", "add live video" | Scope Change Protocol with recorded confirmation | as L3 plus possible G2 reopen | depends on approval |
| L5 Foundational | add / remove an app, auth model, tenancy model, swap of three or more modules, category shift | "add a rider app", "switch to organizations with sub-accounts" | new program version; G2 reopens | re-run from Stage 3 (or 1) | minutes |
Global edits stay cheap: a change that touches many screens through a token or copy key is one L1 patch, not many screen edits (CH-10).

## 4. Change sets
A change set groups operations, lane, impact, estimate, approvals and the inverse operations used for undo. Rapid-fire requests are debounced and coalesced into change sets (CH-07).
```yaml
id: CS-0071
from: preview@SCR-021   text: "add a veg-only toggle on the menu"
ops:
  - add_feature   {id: FTR-112, name: "Veg-only filter", apps: [APP-CUS], status: in}
  - add_field     {entity: ENT-MenuItem, field: isVeg, type: boolean, optional: true}      # additive
  - edit_section  {screen: SCR-021, section: menu, set: {filters: [isVeg]}}
lane: L2
impact: {slices: [SCR-021, SCR-018], operations: [OP-listMenu(+param)], fixtures: [menu], tasks: [T-0142, T-0098]}
estimate: {seconds: 45, credits: 0.4}
approvals: [requester:auto]
status: applied   version: 14 -> 15   undo: inverse-ops available
```

## 5. Scope Ledger and Feature Catalog
Every feature is a node (`FTR-###`) with a status:
| Status | Meaning | Allowed next |
|---|---|---|
| `undecided` | surfaced but not yet decided (research suggestions start here) | in / later / out |
| `in` | part of the current release; will be built | later / out (with impact) |
| `later` | wanted, deferred to Phase 2 backlog | in (Scope Change Protocol if after G2) / out |
| `out` | explicitly excluded, with reason | in (Scope Change Protocol) |
| `unsupported` | category or capability we decline (tier C) | none; may become `needs-engineering` |
| `needs-engineering` | listed hand-off task for engineers; not generated | in (with note) once engineering is acknowledged |
Sources: the user ("you said"), a document ("your PRD p.4"), a reference ("like X"), research ("seen in 3 of 5 comparable apps"), module defaults ("typical for this module"). Each feature carries size (screens, entities, operations, integrations) and capability tier so impact is computed instantly.
**"Like [existing app]".** Research builds a reference feature list from public information; the user sees it as a checklist grouped by module with in / later / out toggles. Patterns only: no brand names, assets or copy (IN-12).
**Scope caps.** If the sum of `in` features exceeds the profile cap, the engine proposes a phase 1 (top flows) and moves the rest to `later`; the user can re-prioritize.

## 6. Scope Change Protocol (L4)
Triggered when a request touches a `later`, `out`, `unsupported` or `needs-engineering` feature, a Locked field, or a tier B/C capability.
**The card shows:** why it was excluded or locked (and who decided, when); the delta (screens, entities, operations, integrations, build time, credits, backend effect, tier B/C risk); the recommended option.
**Options:** (1) add now; (2) add to Phase 2 backlog; (3) use a simpler alternative the engine proposes; (4) cancel.
**Rules.** `unsupported` features cannot be added; the options are the nearest alternative or `needs-engineering`. Overriding a Locked field needs an explicit confirmation recorded with who, when and why (CH-02). Adding now after G2 re-approves only the affected sections; after G3 it becomes a release delta (section 8). The ledger and spec update atomically with the decision.

## 7. Variants, undo, concurrency, hand edits
- **Undo / revert.** Every change set is revertible by applying its inverse operations as a new version (history is never rewritten). Reverting an approved decision warns the user (CH-08).
- **Variants.** Design exploration ("show me a dark premium look") forks a named graph variant that shares history. Variants can be previewed side by side, then merged or discarded. Approvals bind to the variant's hash.
- **Timeline.** A version timeline with change sets, authors, lane and diff; any version can be previewed; baselines (`B-G1`, `B-G2`, `B-G3`, release baselines) are pinned.
- **Concurrency.** Optimistic versioning: each operation names the version it was based on; compatible operations are rebased automatically; incompatible ones produce a conflict card for the stakeholders involved (CH-13).
- **During generation.** Changes to tasks not yet started are applied to the plan; changes to completed tasks enqueue patches; L3 and above wait at a wave barrier or cancel and replan if dependencies demand it (CH-04).
- **Hand-edited code.** Files edited by developers are locked and skipped by regeneration; a change that touches them produces a conflict card; "adopt this code into the spec" (reverse sync) is a later feature (CH-06).
- **After G3 (release deltas).** A change after approval becomes a release delta: contract diff -> additive-first backend delta -> regression through the golden journeys. FE-visible changes need re-approval of the affected preview areas; breaking changes need G4 (CH-05).
- **Cost transparency.** Every non-trivial change shows an estimate before applying; programs have credit caps to avoid surprise charges (78% of IT leaders reported unexpected AI charges [S]).

## 8. Edge-case catalog
Each row has a SpecBench-Mobile scenario named `SB-<ID>` (doc 07) and an owner (stage or engine). "Rule" is the deterministic policy; models never decide these alone.

### Intake (Stage 1)
| ID | Case | Rule |
|---|---|---|
| IN-01 | 200+ page document | Structure-aware chunking (headings, TOC), map-reduce with source pointers, coverage meter; never silent truncation |
| IN-02 | Scanned, handwritten or photographed pages | Render to images, vision extraction with per-claim confidence; below threshold flagged `needs_confirm` |
| IN-03 | Diagrams (flowcharts, ERDs, wireframes) in documents | Vision pass proposes flows and entities marked `from_diagram`; confirmed in "What I understood" |
| IN-04 | Document contradicts the chat | Chat wins; document kept in provenance; diff shown |
| IN-05 | Documents contradict each other | One resolution card naming both sources; if skipped, newer or higher-authority wins and is marked assumed |
| IN-06 | Hidden instructions in a file or URL | Treated as data; extraction has no tools; noted in the audit log |
| IN-07 | "PRD" is mostly vision and market text | Classify as business context; extract goals; elicit the missing `must` slots |
| IN-08 | Spreadsheets, Jira CSV, decks, emails | Convert to structured text; each row or slide is a source pointer mapped into the Feature Catalog |
| IN-09 | Audio or video recordings | Transcribe with speaker labels; low-confidence segments flagged; requester speech counts as user-said, others as document |
| IN-10 | Private Figma, Drive or Confluence links | Ask for an export or connection; never guess content; only public URLs fetched, SSRF-safe |
| IN-11 | Mixed languages and scripts | Detect per chunk; keep the source language in provenance; spec keywords English; copy in chosen locales |
| IN-12 | "Exactly like [brand]" | Patterns only; no brand names, assets, imagery or copy; warn once |
| IN-13 | Declined category (games, AR/3D, editors, DRM streaming, trading terminals, OS utilities, safety-critical, proprietary hardware) | Decline early with the nearest buildable alternative; ledger entry `unsupported` |
| IN-14 | Password-protected or corrupted file | Say so plainly, ask for another copy, continue with the rest |
| IN-15 | Duplicate uploads | Hash dedupe; keep the first |

### Interview (Stage 1)
| ID | Case | Rule |
|---|---|---|
| IV-01 | Silent or "you decide" | Assume the recommended option, mark assumed with a reason, list under "We assumed" |
| IV-02 | Changes mind | Latest explicit statement wins; diff shown; history kept; Locked values need a one-tap confirm |
| IV-03 | Answers a card with a question or off-topic | Answer from grounded facts, re-ask once; after two off-topic turns steer back |
| IV-04 | Very vague idea | Help mode: inspiration cards plus a three-step "tell me about your business"; one question at a time; finish with defaults |
| IV-05 | Asks price, time or guarantees | Answer only from policy facts; never invent; offer an estimate card from size and profile |
| IV-06 | Wants everything | Size estimate; MVP selection; automatic Phase 2 backlog |
| IV-07 | Stakeholders conflict (Enterprise) | Record answers by contributor and role; conflict card to the designated decision-maker; no auto-merge |
| IV-08 | Unsafe or illegal request | Brief decline; audit flag only |
| IV-09 | Language switch mid-chat | Chat follows the user; app locales change only by explicit card |
| IV-10 | Voice dictation run-ons | Segment into atomic statements; echo a numbered list for confirmation when three or more or ambiguous |
| IV-11 | Returns after days | Resume from checkpoint with a recap; research older than N days flagged stale |

### Research (Stage 2; v2, 2026-11-11: foreground after G1; full list RS-01 to RS-24 in [doc 13 §11](13-research-agent-design.md))
| ID | Case | Rule |
|---|---|---|
| RS-01 | Search disabled or empty | Model knowledge flagged `assumed`; never blocks |
| RS-02 | Conflicting sources | Primary or official wins; two-source rule for high-impact claims; else `unverified` |
| RS-03 | Reference app has a huge feature set | Rank by prevalence; top N with toggles; the rest "also seen" |
| RS-04 | Stale SDK or stack facts | Stack Kit is authoritative; research never overrides stack facts; dates shown |
| RS-05 | Research suggests unrequested scope | Default tier only; shown as toggles at G2; counts against the size budget |
| RS-06 | Page contains injected instructions | No tools in extraction; directives ignored |
| RS-07 | Search provider down, rate-limited or out of quota | Retry transport errors (max 3, backoff); fall back to the second provider if configured; else `partial` with gap `provider_down`. Never blocks (doc 13 §8) |
| RS-08 | High-impact claim backed by one source | Flag `single-source`; shown `unverified` at G2 (doc 13 R8) |
| RS-09 | A person's name or an identifier would reach a search query | Query dropped before sending (RV-06). The Brief fact is flagged for Compass to confirm, not rewritten silently |
| RS-10 | Findings arrive after a Brief change or a re-run | Never silent: G2 toggles, or a change request in the lane of its change (doc 13 R7, §9) |
| RS-11 to RS-24 | Template or thin requests, weak geography, own brand or unfindable entity, name collision, thin local-language web, time box, Skip, closed tab, missing page text, contradiction with a Locked value, more targets than budget, same-vendor checker, non-ASCII quotes, duplicate queries | See doc 13 §11 |

### Blueprint, Design, Screens (Stages 3-5)
| ID | Case | Rule |
|---|---|---|
| BP-01 | Unreachable screens or cycles | Validator fails; repair attaches or drops, logged |
| BP-02 | Screen explosion | Profile cap; Phase 1 = top flows; Phase 2 backlog; user may re-prioritize |
| BP-03 | Dense admin tables on phones | Cards on phone, table on tablet and web; dense data flagged tier B |
| BP-04 | One entity, different permissions | Single entity; field-level visibility by role; default deny |
| BP-05 | Cross-app real-time effects | Scenarios declare events; the mock bus simulates; contract lists events |
| BP-06 | Role explosion | Only roles that appear in operations or screens; matrix generated; identical roles merged |
| BP-07 | Existing enterprise APIs or systems of record | Import OpenAPI or schemas; adapter pattern; marked `external`; mocks until connected |
| BP-08 | Organization hierarchies (B2B tenants) | Organization as an entity; roles scoped to it; flagged |
| BP-09 | Approval workflows with SLAs | Lifecycle with transitions per role; timers as rules; notifications |
| DS-01 | Brand colors fail contrast | Adjust to AA keeping hue; record the adjustment |
| DS-02 | Brand assets absent or poor | Derive from logo or site; else preset; ask once |
| DS-03 | RTL, Indic, Arabic, CJK | Logical start/end layout; font fallback stacks; text-expansion notes; locale numerals and dates |
| DS-04 | Low-end Android profile | Device profile limits motion, image sizes, list windows |
| DS-05 | Icon and image needs | Allowlisted icon set; image slots with placeholder strategy; no hotlinked or copyrighted images |
| DS-06 | Dark mode and large text | Both themes defined; layouts valid at 200% type; no fixed-height text containers |
| DS-07 | Platform pattern conflicts | Per-platform behaviors (back gesture, sheets, tabs) as Default settings |
| SC-01 | Mock realism | Synthetic locale-aware data; 0 / 1 / many / long-text / RTL / emoji edges; no real personal data |
| SC-02 | Very long lists, heavy screens | Virtualization and pagination conventions required in the slice |
| SC-03 | Complex conditional forms | Declarative conditions in the spec; error-copy keys |
| SC-04 | Real-time-style UI (tracking, chat) | Scripted mock bus in preview; tier B for the real thing |
| SC-05 | Missing states | Mandatory state set per list and detail screen: loading, empty, error, offline, no-permission |

### Verify and gates
| ID | Case | Rule |
|---|---|---|
| VF-01 | Probe asks what the user cannot answer | Pattern default (Default tier, assumed); ask only must-confirm items |
| VF-02 | Repair does not converge | Two rounds max; replace with simplest valid pattern; flag `degraded`; show at G2 |
| VF-03 | Writer and critic disagree | Deterministic validators decide; otherwise one premium adjudication |
| VF-04 | Spec over budget | Compress through recipe references; split slices; never exceed tier budgets |
| VF-05 | Cross-app inconsistency | Vocabulary and shared-entity validators across apps |
| GT-01 | Edit right after approving | Void only affected sections; "changed since approval" diff |
| GT-02 | Compound reply ("ok but change the color") | Not an approval; becomes a change request; gate stays open |
| GT-03 | Approver silent | Never auto-approve; reminders; draft kept; archive after N days with restore |
| GT-04 | Approvers disagree | All required roles must approve the same hash; rejection comments become change requests |
| GT-05 | Approver lacks authority | Recorded as "recommended"; routed to an authorized role |
| GT-06 | Request to skip a gate | Policy decides; Express merges G1 and G2 on one page; G3 is never removed |

### Change, preview, parallel build, backend, operations
| ID | Case | Rule |
|---|---|---|
| CH-01 | Out-of-scope feature requested at preview | Scope Change Protocol |
| CH-02 | Contradicts a Locked item | Recorded override or rejection |
| CH-03 | Change breaks navigation or contract | Dry-run validators on the patch; auto-fix or reject before any code changes |
| CH-04 | Change during generation | Hot-patch unstarted tasks; barrier for structural changes |
| CH-05 | Change after the backend exists | Release delta; additive migrations first; golden-journey regression |
| CH-06 | Developer edits generated code | Lock plus conflict cards; reverse sync later |
| CH-07 | Rapid-fire changes (100) | Debounce and coalesce into change sets; running list with undo |
| CH-08 | A then not-A | Last wins with visible history; warn when reverting an approved decision |
| CH-09 | Ambiguous request | Three concrete variants |
| CH-10 | Change touches many screens | Token or copy-level global edit (one L1 patch) |
| CH-11 | Destructive data change | L3 with dependency list; deprecate before remove; backend impact shown |
| CH-12 | Exceeds size or profile caps | Propose phase split; estimate; explicit confirm |
| CH-13 | Concurrent edits | Optimistic versioning; rebase compatible ops; conflict card |
| PV-01 | Native module not in Expo Go or SDK mismatch | Web preview by default; dev-build link; tier B items shown as "simulated" |
| PV-02 | Unrealistic data | Seed profiles small / large / edge; reset |
| PV-03 | Stakeholder without an account | Expiring comment-only link |
| PV-04 | Visual bug vs spec | "Report issue" bound to spec id; counts as front-end defect, not a change request |
| PV-05 | Preview build fails | Last good preview retained; plain-language error; one automatic retry |
| PV-06 | Slow device | Web preview budgets; static screenshot gallery fallback |
| PL-01 | Two tasks need one file | Ownership plus code-generated shared files |
| PL-02 | Interface drift | Frozen interfaces; interface-change request re-queues dependents |
| PL-03 | Worker keeps failing | Escalate model class after two failures; recalibrate the rubric |
| PL-04 | Rate limits or outages | Failover to the second vendor; backoff; resume from task checkpoints |
| PL-05 | Build breaks after merge | Bisect by wave; revert the last task; re-run with a fix card |
| PL-06 | Hallucinated package or icon | Allowlist check before commit; auto-replace with the kit equivalent or fail |
| BK-01 | Rule hardcoded in the mock | Listed in the Behavior Book "confirm" list; required at G3 |
| BK-02 | Front-end called an endpoint not in the contract | Usage diff; add additively or fix the front-end |
| BK-03 | Existing enterprise APIs | Adapter pattern; contract marks `external`; mocks until credentials |
| BK-04 | Integration unavailable in the region | Provider alternatives; fallback mock; flagged |
| BK-05 | Golden journey fails on the real backend | Diff report; fix loop; contract ambiguity raises `needs_decision` to the spec |
| BK-06 | Demo seed data in production | Separate demo and production-empty seed profiles |
| OP-01 | Provider outage | Second-vendor failover; degrade gracefully; resume |
| OP-02 | Model upgrade or deprecation | Pinned versions; eval before switching; rollback |
| OP-03 | Cost abuse | Per-session caps; rate limits; upload caps; early validation |
| OP-04 | Non-determinism | Stage outputs cached by input hash; re-run only on changed input |
| OP-05 | Partial failure mid-pipeline | Checkpoints; resume; atomic commit; honest progress |
| OP-06 | Artifact schema drift | Versioned schemas; migration functions; reject unknown major versions |

## 9. Method: how the catalog stays small
1. **Classify first.** Every input is routed to a fixed set of intents and lanes; the model proposes, code decides.
2. **Decision tables as data.** State x intent x tier -> action tables (for example the lane table in section 3, the gate table in doc 03) live in the registry, not in prompts.
3. **Invariants over special cases.** Section 1 invariants remove whole classes of bugs (silent approval, ID reuse, unvalidated output).
4. **Fallback ladder.** retry (transport errors only) -> repair -> deterministic default -> mark `degraded` and surface at the next gate. A degraded node is visible, never hidden.
5. **Idempotent, resumable stages**, so partial failure and re-runs are boring.
6. **Every row is a test.** Each catalog ID has a scenario script; invariants get property tests; operations rows get chaos tests. New edge cases found in production become rows before they become code.
