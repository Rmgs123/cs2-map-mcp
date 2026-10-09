# Sound

| Task | Take |
| --- | --- |
| Find a stock sound event | `asset_search(query, kind="sound")` → use its `event` in `add_sound` |
| Make a sound | `sound_create(name, preset=...)` (synthesized) or `sound_create(name, file=...)` (WAV/MP3/OGG) |
| A playable event | `sound_event(name, kind, sound=...)` → `mcp.<name>` |
| Place it in the map | `map_edit` `add_sound` (point), `add_ambience` (room loops), `add_soundscape` |
| Prove it plays | a view with `cmd: "snd_showstart 1..."` and `grep: "vsnd"` |

## Make a sound

`sound_create(name, preset, seconds=2, params)` synthesizes and compiles `sounds/mcp/<name>.vsnd`: `beep`, `alarm` (loop), `hum` (loop), `wind` (loop), `rain` (loop), `drone` (loop), `click`, `whoosh`, `rumble` (loop), `charge` (rising sweep), `zap` (electric crack). `seconds` .05..120; `params`: `seed`, `gain` (peak .001..0.95, default .5), `frequency` Hz, `rate` 44100/48000, `channels` 1/2, `loop`. Loops get a smooth seam; peaks never clip.

`sound_create(name, file, loop=false)` imports a local WAV (PCM 8–32 bit or float, 8–192 kHz), MP3 or OGG (decoded by Blender), mono or stereo, ≤ 120 s; output PCM16. `loop=true` adds loop markers but does not smooth a recording's seam: check `loop_seam`.

Returns `vsnd`, `seconds`, `channels`, `rate`, `loop`, `rms`, `peak`, `loop_seam` (amplitudes −1..1), `log`. It does not judge how it sounds.

```json
{"name":"g_hum","preset":"hum","seconds":2,"params":{"gain":0.3}}
```

## Events

`sound_event(name, kind, sound | sounds, volume=1, pitch=1, distance=1500, interval, radius, children, reverb, fade)` creates or replaces `mcp.<name>` in the addon's events file (others are kept, the file is backed up). `sound`: one `.vsnd`, `sounds`: up to 32 (the engine picks).

| kind | use |
| --- | --- |
| `oneshot` | 3D at the entity, plays once (unlooped file) |
| `loop` | 3D at the entity, `distance` = where it fades to zero (looped file) |
| `ambient` | 2D background (looped stereo file) |
| `ui` | 2D, no falloff, only to the local player |
| `random_around` | unlooped sounds around the listener: `interval` [min,max] s, `radius` [min,max] units |
| `soundscape` | parent of `children` events with a `reverb_*` preset (`reverb_17_smallConcrete`, `reverb_22_outsideOpen`, ...) |

`fade` (.05..10 s) adds fade-in/out curves; `reverb` works for loops and oneshots too. A loop needs a file with loop markers; a oneshot refuses one.

```json
{"name":"g_hum","kind":"loop","sound":"sounds/mcp/g_hum.vsnd","volume":0.5,"distance":700}
```

## In the map

```json
{"op":"add_sound","event":"mcp.g_hum","origin":[812,171,192],"name":"g_hum","start":true}
```

`add_sound` makes a `point_soundevent`: `event`, `origin`, `name` (targetname for I/O), `start` (default true), `parent` (follows a moving entity), `ref`. Inputs: `StartSound`, `StopSound`, `PauseSound`, `UnPauseSound`; a button: `connect` `{from:"g_button", output:"OnPressed", target:"g_hum", input:"StartSound"}`; a script: `Instance.EntFireAtName({name:"g_hum", input:"StartSound"})`. Put StopSound and StartSound of one entity in different moments: commands in the same tick may be dropped.

Room ambience (verified on `m_storm`): an `ambient` or `loop` event with `fade`, then disjoint boxes:

```json
{"name":"m_hall","kind":"ambient","sound":"sounds/mcp/m_hall.vsnd","volume":0.5,"fade":1.5}
```

```json
{"op":"add_ambience","event":"mcp.m_hall","name":"m_indoor","mins":[256,-384,0],"maxs":[1024,384,320],"fade":1.5}
```

Entering a box stops the other ambience loops, waits for their fade and starts its own; leaving stops it. Boxes may touch but not overlap (split an L-shaped room). One player.

`add_soundscape` (`env_soundscape`, `radius`) is written correctly but **Workshop Tools plays no soundscape** (not even Valve's template): use `add_ambience`.

## Prove it plays

The console shows every started sound with `snd_showstart 1`. Trigger it inside a view and keep only the matching lines:

```json
{"map":"g_sounds","views":[{"name":"g_alarm_check","pos":[640,-96,8],"ang":[0,25,0],
 "cmd":"snd_showstart 1\nent_fire g_button Press","wait":3,"grep":"vsnd"}]}
```

The line must contain `sounds\mcp\g_alarm.vsnd`. A loop that started with the map is already playing: stop and start it in two views. Turn `snd_showstart 0` off afterwards. This proves a sound started, not how loud or nice it is.

## By hand

`point_soundevent` with `soundName`, `sourceEntityName`, `startOnSpawn`; events live in `soundevents/soundevents_addon.vsndevts` (compiled with the map's assets). Room switching: `trigger_multiple` per box with `OnStartTouch` → `StopSound` the others, `StartSound` this one after the fade.
