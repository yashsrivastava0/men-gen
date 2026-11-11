# Research agent (`mde_research_agent`)

The second agent of the Context Engine. After the user approves the Brief (Gate 1) it searches the web, reads pages, extracts facts that code verifies against the page text, and hands Spec one playbook, `research.json` (`research.v4`). This page is filled in as the agent is built; the design is `docs/architecture-v3/13-research-agent-design.md` and the build prompt is `docs/build-prompts/05-research-agent-implementation-prompt.md`.

The import name is `mde_research_agent` (distribution `mde-research-agent`) because `packages/research` already owns `mde_research`.
