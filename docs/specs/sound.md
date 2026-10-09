# Sound: stock events, own sounds, ambience

Search over every stock sound event, own sounds from files or synthesis, events in the addon's `soundevents_addon.vsndevts`, sound entities and room ambience. Code: `sound.py`, `ext/sound.py`; tools `asset_search(kind="sound")`, `sound_create`, `sound_event`; operations `add_sound`, `add_soundscape`, `add_ambience`.

## What we found in the installed game

- The contract comes from Valve's `addon_template/soundevents/soundevents_addon.vsndevts` (its comments and WAVs), the FGD of `point_soundevent`/`env_soundscape` and a decoded Dust2. Compiled events are binary KV3 v5, decoded in `sound.py` following ValveResourceFormat's reader: 193 stock files, 21 853 events, no errors.
- The resource compiler takes WAV PCM16/24, mono or stereo, 44.1/48 kHz; this install compiles 48 kHz to PCM16 44.1 kHz.
- **Loops**: a RIFF `cue` chunk (two points: 0 and N) and/or a `smpl` chunk with one forward loop (start 0, end N−1, play count 0) give `m_nLoopStart = 0`, `m_nLoopEnd = N−1` after resampling; without them the loop start is −1.
- The base `amb.looping.atXYZ.base` has a fixed world position: entity sounds override it with `use_world_position = false`, `position = [0,0,0]`, or they play at Valve's template coordinates.
- Modern maps (Train, Dust2) use `csgo_mega` parents with child events, `dsp_preset` / `override_dsp_preset` and six-number distance curves.

## How it works

- **Index**: VPK → KV3 DATA → names and references, cached in `.local/sound/stock_index.json` by the VPK folders' size and time; index errors are counted and reported.
- **Own sounds**: import (WAV PCM8/16/24/32 or float, 8–192 kHz, ≤ 120 s; MP3/OGG through Blender's `aud`) or synthesis (beep, alarm, hum, wind, rain, drone, click, whoosh, rumble) → PCM16 WAV in `sounds/mcp/` → compiled, checked for a `.vsnd_c`. Odd rates are linearly resampled to 48 kHz (not a music-grade resampler). Loop markers do not make a recording seamless; a metric reports the seam.
- **Events** (`sound_event`): parse the KV3 text, back it up, add only `mcp.<name>`, write, compile, then check that every old name is still in the compiled data and every old definition unchanged; on any error the source is restored and recompiled. Writes are serialised with a byte lock (`.local/sound/write.lock`). `reverb` sets wet 1 with source reverb allowed; `fade` (0.05..10 s) adds linear volume curves for start and stop.
- **Entities**: `add_sound` makes a `point_soundevent` (`StartSound`, `StopSound`, ...). `add_ambience` builds per room: a player `trigger_multiple`, a local `point_soundevent` (`toLocalPlayer`) and a `logic_relay`; entering cancels every pending enter, then triggers its own relay 0.05 s later, which stops other zones' speakers and starts its own after the longest fade; leaving cancels and stops. Zones may touch but not overlap; one player at a time, sequential transitions without crossfade.

## Pitfalls we hit

- **`env_soundscape` never activated by itself** in the current client (`Soundscape[0]: none`) even built from Valve's template: use `add_ambience` (verified) or direct sound entities.
- Entering a zone sent "cancel all" and "trigger mine" in the same moment; the order is not guaranteed, so a cancel could kill the fresh start and the room stayed silent. Delaying the trigger by 0.05 s fixed it (`m_storm`: hall → rain → hall heard with `snd_showstart 1`).
- `point_soundevent` has no `SetVolume` input; fades live in the event.
- Finding an event in the index is not proof it plays: playback is confirmed in the game.

## Tests

`tests/test_sound.py`, `tests/test_sound_ambience.py`; in game `point_soundevent` from a button, a script and the UI, a parent event with children, `m_storm` ambience zones.
