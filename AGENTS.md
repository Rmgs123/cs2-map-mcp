# CS2 Map MCP — rules for agents

This repository is an MCP server through which an AI builds complete Counter-Strike 2 maps: geometry, materials, light, entities, settings and scripts. The work goes through files (VMAP/DMX, vmat, vmdl), Valve's compiler and the running game. Clicking in Hammer is only a helper path.

## Getting started

- **Making maps with the MCP**: call `status`, then read the guide (`read_guide`, the core is [docs/TOOLS.md](docs/TOOLS.md)); read a feature section (`read_guide(section=...)`) only when you need it. Notes from earlier chats on this computer: `read_guide(section="my")`.
- **Changing the code**: read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the spec of the area in [docs/specs/](docs/specs/); tools and their descriptions live in `src/cs2map/surface.py`, the guide in `docs/TOOLS.md` and `docs/tools/`. Contribution rules: [CONTRIBUTING.md](CONTRIBUTING.md). Something broken: [REPAIR.md](REPAIR.md).
- Install and client registration: [docs/INSTALL.md](docs/INSTALL.md).

## The main rule: verify in the real programs

A feature is done only when the result is checked end to end: MCP call → files → `resourcecompiler` (exit 0, no errors in the log) → the map loaded in CS2 → frames you actually opened and looked at. Gameplay goes through `script_test` with real input (`use`, `hold`, `aim`, `mouse`, `click`), with `game_reset` before and after. Unit tests are needed but not enough. If the real check is impossible (for example, the user is playing CS2), say so plainly; never replace it with paperwork.

## Principles

- **Anything a mapper can do.** If a tool is missing, the guide's "By hand" parts explain raw entities, I/O, `cs_script` and resource files. A missing primitive is a gap in the core: report it.
- **Any idea, not only ready operations.** High-level operations (houses, streets, wear) are accelerators, not the ceiling: base geometry (meshes, extrude, revolve, cuts, `edit_mesh`), materials on any face (UV, tint, vertex blend), any entity and I/O, scripts and own resources are always available.
- **Quality without waste.** Cheap paths first (offline previews, static checks, dry runs); start CS2 when nothing else proves the point, not "just in case".
- **Report problems with this MCP.** A tool error you did not cause, a wrong guide or a missing feature you built by hand: one short entry with `my_notes(kind="feedback")` (`my/feedback.md`, sent to the author by the user).
- **Short answers.** Tool answers stay short; long data goes to a file and the answer gives its path; images come as one labelled sheet.
- **No litter.** Temporary files only in `.local/tmp/`, removed by the code (`try/finally`); caches have size limits and keys that include the source version; external processes (Blender, Source2Viewer, `resourcecompiler`, the CS2 console client) have timeouts and guaranteed cleanup.
- **Axes and units.** 1 unit = 1 inch, +Z up, player 32×32×72, eyes at 64. Yaw 0 looks along +X, 90 along +Y. A model's front is never guessed from its name: take it from the atlas or a preview with labelled axes.
- **A clean scene.** Objects neither intersect nor float unless intended. Surfaces of different objects never share a plane (z-fighting): join them edge to edge or sink one by at least 1 unit — models too (glass, shutters, panels in a niche stand 1 unit off its back). Labels and stains are decals, not thin boxes. Wall details hang on walls, furniture keeps clearances. Look at the scene from above, from the side and from outside the play area — scenery included.

## Where you may write

- The project folder (code, docs, map backups, logs) and the project's CS2 addon (`mcp_lab` by default, set in `config/settings.json`): `<CS2>/content/csgo_addons/<addon>` and `<CS2>/game/csgo_addons/<addon>`.
- Never touch other addons, CS2 or Steam files, global configs, PATH or the registry.
- Never delete irreversibly: move unneeded files to `_to_delete/`; caches and temporary files that can be recreated may be removed by the code.
- Large downloads only with the user's permission.

## Map and game safety

- Every `.vmap` write makes a copy in `backups/maps/` (the code does it). A map open in Hammer with unsaved changes (an asterisk in the title) is refused.
- Never close or restart CS2 while the user plays (`cs2.exe` without `-tools`); ask first.
- The CS2 console accepts one client. A VConsole2 window takes it silently: the code closes it with `WM_CLOSE`. Never kill CS2 or its child processes. If CS2 does not respond for ~30 s, ask the user to restart it at once.

## Docs

- `CHANGELOG.md`: one to three lines per notable change.
- `docs/TOOLS.md` and `docs/tools/*.md` change with the tools: the server hands them to models. Every JSON example must pass `map_edit` dry run (`tests/test_doc_examples.py`).
- Public files are in English and contain no personal data.

## Commands

```powershell
# tests (no CS2 needed, except the stdio end-to-end test)
.\tools\python-env\Scripts\python.exe -m tests.run
# the MCP server (stdio)
.\tools\python-env\Scripts\python.exe -m cs2map.server
# call a tool on a fresh server (checks new code)
.\tools\python-env\Scripts\python.exe tools\mcp_call.py status '{}'
```

Python 3.14 and the dependencies live in `tools/python-env` (created by `setup.ps1`). Blender comes from Steam (`config.find_blender`).
