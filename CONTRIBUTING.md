# Contributing

Pull requests are welcome: new operations, fixes, better defaults, guide improvements, new map styles and kits. The maintainer reviews and merges them.

## The bar: verified in the real game

A change is accepted when it is checked end to end, not only by unit tests:

1. an MCP call (through `tools/mcp_call.py` on a fresh server, or a client),
2. the files it writes (`.vmap`, `.vmat`, `.vmdl`, scripts),
3. `resourcecompiler` with exit 0 and no errors in the log,
4. the map loaded in CS2,
5. frames (`game_screenshot`) you opened and looked at; mechanics through `script_test` with real input.

Say in the PR what you ran and attach a frame or a contact sheet. If you could not run the game, say so; such PRs can still be useful but are not merged as "done".

## How to add a feature without touching the core

- One feature area = one module `src/cs2map/<area>.py` + `src/cs2map/ext/<area>.py` with `register(mcp, project)`; extensions load automatically.
- New `map_edit` operations: `project.register_op(name, fn)`; shapes: `shapes.register_shape(name, fn)`; `check_map` checks: `checks.register_check(name, fn)`; batch preprocessors: `project.preprocessors`.
- Public MCP tools are few on purpose (`src/cs2map/surface.py`, description budget ≤ 7000 tokens, tested). Prefer a new operation or a field over a new tool.
- Mechanics reuse the shared JS fragments (`core`, `hud`, `camera`, `seat`, `health`, `fire`) through `mechanics.build_script` or `// @requires`; never copy a fragment.
- Call other areas' public functions; change them only with backward compatibility.
- Blender runs only through `cs2map.blender_run`; external processes get timeouts and cleanup; temporary files go to `.local/tmp/` and are removed by the code.

## Rules

- Write only to the project folder and the project's CS2 addon; never to other addons, CS2/Steam files, global configs, PATH or the registry.
- Never kill CS2 or its processes; see [AGENTS.md](AGENTS.md) for the game safety rules.
- Every JSON example in `docs/TOOLS.md` and `docs/tools/*.md` must pass `map_edit` dry run (`tests/test_doc_examples.py`).
- Code, docs and commit messages in English; no personal data (paths, names, e-mails) in public files.
- Valve's files (decompiled models, textures, maps) never go into the repository.

## Tests

```powershell
.\tools\python-env\Scripts\python.exe -m tests.run            # all (the stdio test needs CS2)
.\tools\python-env\Scripts\python.exe -m tests.run -k Weapon  # some
```

## Reporting problems and wishes

Open an issue with the templates: a bug (what you asked, the tool answer, logs, frames) or a missing feature (what a mapper wants to make and why the current tools are not enough). Missing features are welcome: the goal is that an AI can make any map and mechanic.
