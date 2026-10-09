# Installing CS2 Map MCP (for an AI agent)

Follow these steps on the user's Windows PC. `setup.ps1` does the work; your job is to run it, read its lines and ask the user where a decision is theirs. Never change global settings, PATH or the registry yourself; the only user files setup writes are the MCP client configs, each backed up first, and only with `-Apply`.

## 1. Check (changes nothing outside the project)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

| Line | Meaning | What to do |
| --- | --- | --- |
| `1. Python environment ok` | `tools\python-env` exists with the locked packages | nothing |
| `would create tools\python-env` | no environment yet | `-Apply` creates it (Python 3.14 must be installed; else ask the user to install it from python.org) |
| `2. CS2 + Workshop Tools ok: <path>` | CS2 and the tools found through Steam | nothing |
| `CS2 with Workshop Tools not found` | no CS2 or no Workshop Tools | ask the user: Steam → Library → CS2 → install "CS2 Workshop Tools" (free DLC) |
| `3. Blender ok: <path> version 5.x` | Blender found (Steam library, `tools\`, Program Files) | nothing |
| `not a tested version` / `not found` | no usable Blender | ask the user to install **Blender from Steam** (free, app 365670; setup opens the install dialog with `-Apply`), or agree to download portable Blender 4.5 LTS (~400 MB) into `tools\` |
| `4. Source2Viewer CLI ok` | prop previews and weapon bases work | nothing |
| `not found` | missing | `-Apply` offers the download (~40 MB, SHA-256 checked); ask the user first |
| `5. Node.js` | optional (offline tests of mechanics) | if missing, nothing breaks |
| `6. MCP clients: [client] <file>: add/update ...` | what registration would change | show the user the list and ask which clients to register |
| `7. Server status` | the server's own `status` | `found`, `workshop_tools` true |

## 2. Install and register (with the user's agreement)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Apply                          # all three clients
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Apply -Clients codex,claude-code  # only these
```

Clients: `claude-code` (`.mcp.json` in this folder), `claude-desktop` (`%APPDATA%\Claude\claude_desktop_config.json`), `codex` (`~\.codex\config.toml`, shared by Codex CLI and the Codex app). Each changed file is copied to `<file>.before-cs2map-<time>.bak` first. Running setup again is safe.

## 3. Settings (only if needed)

Copy `config\settings.example.json` to `config\settings.json` for: another CS2 path (`cs2_path`), another addon name (`addon`, default `mcp_lab`), an explicit Blender (`blender`), disk limits (`limits`: screenshots, logs, backups).

## 4. Verify

Restart the AI client, then in a new chat: `status` (CS2, Workshop Tools, addon, Blender, Node) → `read_guide` → a small map: `map_create` → `map_edit` → `map_build` → `game_launch(map=...)` → `game_screenshot`. A first CS2 start takes 30–90 s. The game window must not be used by the user while `script_test` plays real input.

## Problems

- **Execution policy:** use the `-ExecutionPolicy Bypass -File` form (this run only).
- **Steam updated Blender** past the tested range: the server picks another installed Blender or says which version is installed; `blender` in settings pins one.
- **CS2 is running in normal mode** (playing): `game_launch` refuses; ask the user to close it or to start Workshop Tools.
- **CS2 does not respond** for ~30 s: ask the user to restart CS2 Workshop Tools; never kill `cs2.exe`.
