# apps/web: the global front end

One front end for every agent (Global rules B). Today it is a static test console in plain HTML, CSS and vanilla JS with **no build step**; a React app is reserved for later. It is never part of an agent folder: an agent exposes only its HTTP API (and the shared JSON Schemas), and nothing here imports agent code.

## Layout

```
apps/web/
  console/                      what the dev server serves at /ui/
    index.html                  the shell: top bar with the agent switcher, a mount point, the toast region
    shared/
      tokens.css                design tokens only: the :root custom properties, light and dark
      shell.css                 the shell's own rules (switcher, mount points, load error)
      components.js             window.MobigenUI: store, $, h, appendChild, clear, randomId, toast
      registry.js               window.MobigenRegistry: one entry per agent
      shell.js                  fills the switcher, mounts the chosen agent
    agents/compass/
      compass.html             the Compass screens as an HTML fragment
      compass.js               the Compass behaviour (starts with the /*CONTRACT {...} CONTRACT*/ block)
      compass.css              the Compass styles
  app/                          reserved for the later React app (.gitkeep)
  tests/                        UI smoke tests (pytest, offline)
```

## How the shell works

`index.html` loads `registry.js`, `components.js` and `shell.js` with `<script src defer>`. `shell.js` fills `<select id="agent-select">` from the registry, picks the agent (the `?agent=<id>` query, else the last choice kept in `localStorage`, else the first entry), sets the page title, then loads that agent's stylesheet and HTML fragment and only then its script, so the script finds its elements. Elements marked `data-slot="topbar"` in the fragment join the shared top bar; the rest go into `#agent-mount`. Agent scripts keep global listeners and cannot be unloaded, so choosing another agent reloads the page with `?agent=<id>`. If a file cannot be loaded the shell says so in the page instead of staying blank.

A registry entry:

| Field | Meaning |
| --- | --- |
| `id` | short name, the `?agent=` value, the folder `agents/<id>/` |
| `title` | the label in the switcher and the page title |
| `base` | the agent's API prefix (`/v1/compass`); the screens reach the agent only through it |
| `markup`, `script`, `style` | the three files of the screen set, relative to `console/` |

Adding an agent is a new `agents/<id>/` folder with those three files plus one registry entry. A second agent is not built yet.

## Rules for every file under `console/`

* Vanilla JS, no framework, no build step, no `eval`. User and model text is written with `textContent` or text nodes (`h()`), never as markup (`innerHTML` and friends are banned by a test). The one markup parse is `DOMParser` on the agent's own fragment in `shell.js`, which drops `script`, `style`, `link`, `iframe`, `object` and `embed` nodes.
* The server sends a strict CSP (`script-src 'self'`, `style-src 'self'`): no inline script, no inline style attribute, no inline handler, no external resource, no web fonts. Dynamic values that need a style (a bar width) go through `el.style[...]` (the `css` key of `h()`), which the CSP allows.
* Colours, spacing, radii and fonts are tokens from `shared/tokens.css`; a screen's CSS uses `var(--name)` and defines none.

## Run it

The Compass dev server serves this directory:

```bash
uv run python -m mde_compass --memory --port 8110      # no database; open http://127.0.0.1:8110/ui/
just compass-dev                                       # the same with the database
```

`MDE_WEB_DIR` points the server at another directory (a relative path is resolved against the repository root; the default is `apps/web/console`). `mde_compass.app.web_dir()` reads it each time and `create_app()` raises `FileNotFoundError` at start when the directory or its `index.html` is missing. Dev sign-in (token, tenant, user) is in the Settings dialog of the Compass screen.

## Tests

```bash
uv run pytest apps/web/tests -q
```

Offline and fast. They fetch every console file through the real app (CSP and content types), check that the shell and registry reference only files that exist, that the switcher lists the registered agents, that the tokens define the light and dark themes, that no inline script, style or handler exists anywhere under `console/`, that every script parses (`node --check`, skipped without Node), that every id `compass.js` looks up exists in the markup, and that the keys it reads exist in the agent's view-model, card and event schemas (`test_ui_contract.py`, the one test that imports agent models). `conftest.py` imports `create_app` and `ScriptedService` to serve the console; the console itself imports nothing.
