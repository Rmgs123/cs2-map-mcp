# Panel

A page in the browser of the person at the PC: what the tools are doing now, the newest frames, the load of the PC, and switches for what the AI may do without asking. Code: `panel.py` (server, data collection, actions), `data/panel/index.html` (the page), `activity.py` (the journal), `permits.py` (focus for a while, heavy build questions, deferred tests), `config.py` (`heavy_builds`, `panel`, `panel_port`, `LIVE_KEYS`), `project.Project._refresh_settings`, `jobs.JobManager.cancel`, `surface.py` (`heavy`, `script_test`), `tools/panel.py` (launcher).

## How it works

**One process, no new tool.** `python tools/panel.py` (or the MCP server when settings `panel` is true) serves `http://127.0.0.1:<panel_port>/` with the standard library only. A second start finds the port taken and ends. The panel ends by itself when no page has asked for ten minutes, no server process runs and no job runs. It never opens CS2's console (CS2 accepts one console client), so it cannot disturb a test: everything it shows is read from files the servers already write, plus Windows itself.

| Block | Source |
| --- | --- |
| Now | job records and logs in `logs/` (stage marks of the build log; percent = time gone against the last successful build of the same map and preset, never behind the stage reached); else the tool call that is running, from the journal |
| Frames | newest files of `screenshots/` and the player marks (`.local/state/marks.json`); previews are made on request into `.local/tmp/panel-thumbs/` |
| Activity | `.local/state/activity.jsonl` |
| Waiting for you, Permissions | `.local/state/panel_state.json` and `config/settings.json` |
| Game | the CS2 process, its windows (responding, in front, minimized), `.local/state/loaded_map.json`, the journal's focus notes |
| Load | `psutil` per process group (compiler = `resourcecompiler.exe`, `vrad3.exe`; CS2; Blender) and the whole PC; GPU through the Windows counter `\GPU Engine(*)\Utilization Percentage` read with PDH: a group's share is its busiest engine, the PC's share the busiest engine of all; ten minutes of history in memory |

**The journal.** `surface.register` wraps every public tool in `activity.logged`: a start line and an end line per call (tool, subject, client name from the MCP handshake, one line of outcome, seconds, the job it started). Every server process appends to the same file with single short writes; over 1 MB the older lines are dropped. A call whose process died shows as lost. `activity.note` records facts that are not calls: each time the game window came to the front (`game._front`), each action taken in the panel.

**Standing choices follow the file.** The switches write `config/settings.json` (`game_focus`, `heavy_builds`, `build_priority`, `restart_hung_cs2`). A running server reads the keys of `config.LIVE_KEYS` again when the file changes (checked at most once a second); paths and the addon never change under a running server, and settings handed to `Project` by code are not replaced. `status` returns them as `permissions`, with the panel address.

**Heavy builds.** With `heavy_builds: "ask"` the tools `map_build` and `lighting_build` call `permits.heavy_gate` first. A build is heavy when its preset bakes light (`full`, `final`, `release`) and either the last such build of this map took two minutes or more or the map file is 20 MB or more (a release always). The tool then returns `{refused, estimate: {minutes, memory_gb, basis}}` and the question appears in the panel. "Allow once" lets the next call for that map and preset through (valid 30 minutes, used up by it); "Not now" answers the same request for ten minutes. Turning the switch on allows what is waiting. The default is `allow`: an unattended session must not stop and wait.

**Focus for a while.** With `game_focus: "never"` the panel's "Allow for 15 min" writes `focus_until`; `game.focus_policy` then answers `input_only` until that moment.

**Deferred tests.** A `script_test` whose steps include `click` or `focus: true` cannot run while the game must stay behind (`never`, no permit). The tool returns `{deferred, id}` at once and the whole scenario waits in the panel. "Run them now" starts `python -m cs2map.permits run <id>` for each, one after another, in their own processes: the test runs with the focus allowed for its mouse steps, the result (ok, passed checks, reason, log) is kept in the permits file and returned by `status` as `permissions.deferred_tests`.

**Stopping a job.** `JobManager.cancel` works for a job another process started: it writes `logs/<job>.cancel`, kills the process tree by the recorded pid, and the process that watches the job (or the next reader of its record) reports `cancelled`.

**Access.** The server binds 127.0.0.1 only. Every request must carry a `Host` of `127.0.0.1:<port>` or `localhost:<port>` (a foreign site cannot reach it by rebinding its own name). Actions are POST requests with a token that is created at start and embedded in the page, and an `Origin` header, when present, must be the panel's own. Frame paths are resolved inside `screenshots/`.

## Why it is built this way

- A page, not a tool: the person looks at it without spending the model's context, and it works the same for every AI client.
- Files, not a channel to the servers: several clients run their own server processes, each short-lived; what they already write to disk is the one thing all of them share.
- No `confirmed` parameter on `map_build`: the yes must come from the person, and a parameter would be the model's to set.
- The page is drawn with printed-form rules instead of cards, a condensed system font and colour only on data (compiler, CS2, Blender), so that a glance separates the tools' share from the rest of the PC.

## Pitfalls we hit

- `SO_REUSEADDR` on Windows lets a second process bind the same port: the server sets `SO_EXCLUSIVEADDRUSE` instead, or two panels would answer in turn.
- Redrawing a block every second swallows a click that starts before a redraw and ends after it: the page replaces a block's HTML only when it changed, and buttons live in their own containers.
- A click step on a minimized game window restores it; `game._front` minimizes it again when it gives the focus back.
- The first log line of a job is its command line: it is not shown as the build's detail.

## Tests

`tests/test_panel.py`: the journal (two lines per call, errors, lost calls, short refusals, rotation), permits (focus for a while, the heavy build question from refusal to one allowed build and "not now", size and history estimates, deferred tests, offline runs leave the user's file alone), settings that follow the file, stopping a job from a second `JobManager`, GPU shares, the view of a running build, frames and marks, and the HTTP rules (Host, token, Origin, paths). In the real programs: calls of a fresh server appear in the feed with the client name; the switch "Heavy builds without asking" off makes `map_build(full)` of a 256 MB map answer "about 7 min and up to 17 GB" and the question appears on the page; "Stop job" ends a job owned by another process (record `cancelled`, no process left); a deferred click test started from the page passes 2 of 2 checks with the game window in front for 1.5 s and minimized again afterwards.
