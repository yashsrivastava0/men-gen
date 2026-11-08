# `mde_prompts`

The registry of call specs (`packages/prompts`): C1 to C14 and the web search call S1 (there is no C4; the spec's numbering skips it). It holds the templates, the deterministic mock handlers and the golden fixtures. **No prompt text lives anywhere else.** It never touches the network. The gateway receives the registry at construction; `CallSpec` itself is defined in `mde_contracts` (D-007), this package only instantiates it. The generated table of every spec is [the prompt catalog](../prompts/prompt-catalog.md).

## Layout

```
packages/prompts/src/mde_prompts/
  registry.py            PromptRegistry (implements the gateway's SpecRegistry), load_spec, all_specs
  calls/<folder>/        one folder per call, the folder name comes from mde_contracts.registry.call_folder
    spec.py              SPEC: CallSpec[I, O]  (id, version, models, route, effort, max_output_tokens, tools,
                         untrusted_inputs, cacheable, repair, fallback name, mock_handler dotted path)
    system.jinja         the system prompt: short, one job
    user.jinja           renders the input model
    mock.py              def handle(input: I) -> O | FreeTextResult   (deterministic, schema-valid; D-021)
    fixtures/            golden cases used by the mock handler and the tests
  discover_testkit.py    helpers the call tests share (render, load_cases, schema depth)
```

`load_spec(call_id)` imports `mde_prompts.calls.<folder>.spec` and checks that the spec's models are the ones in `mde_contracts.registry.CALL_IO`; `all_specs()` loads the catalog. Adding a spec needs no edit to a shared file: the registry finds folders by convention (D-016).

## The calls

| Step | Calls | Owner of the call folders |
| --- | --- | --- |
| Discover | `C1.understand`, `C2.interpret_brief`, `C3.respond`, `C5.write_card`, `C11.write_summary`, `C14.interpret_change` | discovery (C11 also writes the plan summary in the builder) |
| Research | `C6.plan_research`, `S1.web_search`, `C7.extract`, `C8.synthesize_research` | research |
| Build | `C9a.screens_nav`, `C9b.data_api_roles`, `C9c.design_tokens`, `C10.screen_briefs`, `C12.critic`, `C13.repair` | spec-builder |

Call counts the engines promise (tests assert them): a tap costs 0 calls, typed text 1 (C1), a first idea or an upload 2 (C1 and C2), a question or chat 2 (C1 and C3), `compile_brief` 1 (C11). See [discovery](discovery.md), [research](research.md) and [spec-builder](spec-builder.md) for each engine's calls, fallbacks and budgets.

## Rules (also `.claude/rules/prompts.md`)

* One job per call; pass only the context the call needs (slots, the last few turns, a rolling summary), never whole transcripts. Field descriptions in the output models are part of the prompt.
* **Bump `version` on any change** to a template, an input or output model or a schema. A new version makes every older cassette stale (a replay miss): flag re-recording to the lead.
* Every spec ships a **mock handler** (so the product runs with no key) and names a **deterministic fallback** that the owning engine registers (a bank card, a canned reply, a template). The keyword logic that backs a handler is often the engine's fallback too.
* Fields that carry web, upload or otherwise re-read text go in `untrusted_inputs`. Such a spec must not enable tools: `CallSpec` refuses the combination at construction and the gateway refuses it again at run time.
* Always set `max_output_tokens`: it covers the reasoning tokens as well as the visible answer.
* Never inline prompt text in Python; templates are `.jinja`. Appendix A of [the brief](../implementation/BRIEF.md) is the prompt-writing guide (copy only what the content states, null when unknown, two or three short examples for C1 and C14 including Hindi and Hinglish).
* Output models must convert to the OpenAI strict schema (no open objects, depth 5 or less).

## Test

```powershell
uv run pytest packages/prompts -q               # every spec: mock output validates, converts to a strict schema,
                                                # templates render, untrusted plus tools is rejected, fixture cases pass
uv run python scripts/gen_prompt_catalog.py     # regenerate docs/prompts/prompt-catalog.md after any spec change
```

The discovery package's tests are the other half (`uv run pytest packages/discovery packages/prompts`): the engines exercise these handlers.

## How to add or change a call

Follow the skill `add-llm-call`: contracts first (the lead adds IO models), then the folder, spec, templates, mock handler, fixtures, the engine's fallback, tests, the regenerated catalog and the owning package page.

Owner: `discovery-engineer`, `research-engineer` and `builder-engineer` by call id; the registry and shared helpers belong to the lead.
