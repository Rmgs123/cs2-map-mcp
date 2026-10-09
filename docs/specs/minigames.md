# Mini-games: shooting gallery, race, collect run

`add_minigame` builds a start button, the game's targets, rings or items, four sounds, an own HUD and a `point_script` on the shared fragments (`core`, `hud`, `health`) with the adapter `minigame_game`. Code: `minigames.py`, `ext/minigames.py`, `data/minigames/` (`game.js`, HUD, Blender models).

## How it works

- **gallery**: each target is a fixed stand plus a shield with `prop_data`, held by a `phys_hinge` along its bottom edge. The hinge motor gets 0° or 90° only when the state changes (no per-tick teleport). Points are stand bases; the shield faces the start button. Pop-up intervals come from a seeded PRNG between `popup` min and max. A hit on a raised shield scores 10/5/2 by distance from the centre; repeated events of the same hit (including native damage) count once.
- **race**: rings centred 64 above their points; the player's centre (+32) or a controlled body's (+12) is compared. The next ring is yellow, passed ones green, later ones dim. Entering a later checkpoint adds a 5 s penalty once; the order never changes. `vehicle` names a controllable (`d8_<name>_body`, `v9_<name>_body`) or an exact entity; the race only reads positions.
- **collect**: glowing items spin (`Move`) and are picked up by proximity; collecting all ends the run.
- **Common**: E with a 96-unit ray on the button, one active player (others get BUSY); the HUD shows score, time and best, and the result for 8 s; death, respawn and round restart reset the run. The best result lives until the map changes (cs_script has no storage across maps). Sounds are synthesised (beep, click, whoosh) and registered with `sound_event`.

Fields: name 1..40, time 1..600 s, targets/items 1..64, radius 8..256, popup 0.1..60. A preview runs before writing resources; models are cached by source version and parameters.

## Tests

`tests/test_minigames.py` (Python and Node); in game `m9_games` and `p9_playground` (gallery and drone race played with real input). Not proven by the Node test bed: two real clients, sound balance.
