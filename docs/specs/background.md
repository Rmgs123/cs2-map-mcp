# Background work

CS2 loads maps, renders frames and runs tests while the user works in another window: the game window does not come to the front, the user's keyboard and mouse do not reach the game. Code: `vconsole.py` (`AWAKE`, `_Connection.wake`, `hold`), `game.py` (`awake`, `input_focus`, `focus_policy`, `_engine_frame`, `_game_window`, `_give_focus_back`), `system.focus`, `config.py` (`game_focus`, `build_priority`), `jobs.py` (job priority), `scripting.py` (steps `mouse`, `click`, `focus`), `route.focus_game`.

## How it works

**The engine sleeps without the focus.** A CS2 that is not the foreground window shows its last frame for ever, a map load stops at the loading screen (the server is ready, the client is not) and the client does not apply `setang`. `engine_no_focus_sleep 0` does not change that.

**"The console is in front".** The VConsole protocol has a packet `VFCS` with one byte: the console window is (1) or is not (0) in the foreground. With `r_vconsole_foregroundforcerender` and `engine_no_focus_sleep_vconsole_suppress` (engine cvars, both on by default) the engine then renders and runs its client as if it were in front. The tools are that console client: every new connection sends `VFCS 1` (`vconsole.AWAKE`). The state lives as long as the socket, so a reconnect says it again.

**Holding the connection.** The connection closes after 5 s idle (CS2 accepts one console client). `vconsole.hold()` keeps it open for one operation. The CS2 lock (`game.exclusive`) is such a hold from the block's first console command to its end, so every operation under the lock (play tests, mode and kit checks, cubemaps, marks, probes) keeps the game awake through its waits; the lock itself opens no connection. `game.awake(s)` connects at once, used by `game.views` (frames), `game_command(focus=true)` and `route.focus_game` (`script_test`, `route_test`, probes). With `game_focus: "always"` `awake` brings the game window to the front instead, as before 1.4.

**Frames.** A visible window is copied with `PrintWindow` (works when other windows cover it). A minimized window, or a blank copy, falls back to the engine's own `jpeg_screenshot`: the file appears in `<game addon>/screenshots/`, is converted to the PNG and removed. A minimized game window is not restored.

**Input.** Console input needs no focus: `+forward`, `+attack`, `+use`, `setang`, `setpos`. Real mouse input reaches only the window in front, so `game.input_focus(s, what)` brings the game window forward for one step, waits until it really is the foreground window (otherwise refuses: input never goes to another program), and gives the focus back to the window that had it. A game window that was minimized is minimized again. Each such moment is written to the activity journal; the panel shows how often and for how long the game came to the front ([panel](panel.md)).

| `game_focus` | Frames, loads, console steps | `mouse` step | `click` step, `focus: true` |
| --- | --- | --- | --- |
| `never` | background | the view is turned by `setang`: pixels × `sensitivity` × `m_yaw` / `m_pitch` | the whole `script_test` is deferred to the panel (the user runs it there, or allows the focus for 15 min); inside other tools the step is refused with a reason |
| `input_only` (default) | background | window in front for the step (under a second) | window in front for the step / until `focus: false` |
| `always` | window in front | window in front | window in front |

**Launch.** A starting CS2 opens in front. `game.launch` remembers the foreground window and hands the focus back while CS2 starts, unless the user has moved to another window meanwhile.

**The user's windows.** `system.focus` restores only a minimized window: `SW_RESTORE` on a maximized one shrinks it, which hit the user's own window when the focus was given back.

**Sound.** The tools do not change the game's sound: a game that renders behind other windows may be heard (tests fire weapons, drive cars). The user mutes it where every Windows program is muted: the volume mixer (right-click the speaker icon → Open Volume mixer → Counter-Strike 2); Windows remembers the mute for the program, and it does not affect tests or frames.

**Build priority.** `build_priority: "low"` (default) starts jobs with `BELOW_NORMAL_PRIORITY_CLASS`; the compiler, VRAD3 and Blender inherit it. A build still uses every core when nobody else wants them.

**Heavy builds and the panel.** `heavy_builds: "ask"` makes light bakes wait for the user's yes; the switches for this, for the focus and for the priority are on the status page: [panel](panel.md).

## Why it is built this way

- The console packet is Valve's own switch for exactly this case (tools in front, game behind); it needs no window tricks, no injected input and no changed game files.
- The default is `input_only`: one mouse step takes the focus for under a second, and the test stays a test of the real mouse. `never` is for a user who types while tests run; the emulated mouse turn gives scripts the same eye angles but does not prove raw mouse input.
- A real click is never emulated: HUD buttons in cursor mode have no console equivalent, so `never` refuses instead of reporting a click that did not happen.

## Pitfalls we hit

- Without `VFCS` nothing renders in the background, with any cvar we tried; the game process then uses no GPU at all.
- On a map with bots the player gets killed during a background test and becomes an observer: `getpos` and view angles are then somebody else's. Check input without bots or after `game_reset`.
- The player diagnostics answer may arrive in several packets, and two diagnostics entities may answer (the map's own and a runtime one): `player_state` waits for the counted lines and keeps one line per slot.
- `PrintWindow` gives nothing for a minimized window: the engine frame is the only way there.

## Tests

`tests/test_background.py` (the packet on every connection, hold counting, settings values, `awake` and `input_focus` by policy, a minimized window left alone, the two-answer diagnostics, job priority). In the game, with another program in front and the foreground window sampled every 30 ms: three views through `game_screenshot` with the game window visible and minimized — CS2 in front 0 times; `tools/selfcheck.py`, 7 scenarios (drone, car, turret, range, railgun, double jump, story) twice — 7/7, CS2 in front 0 s of 216 s; the `mouse` step: 0.4–0.7 s in front with `input_only`, 0 s with `never`; the connection kept through an 8 s pause under the CS2 lock and closed after it; a map reload, `game_reset`, a 20 s `playtest`, `route_test` of the self-check story (3 of 3 points) and `physics_probe` — CS2 in front 0 s of 86 s, frames fresh.
