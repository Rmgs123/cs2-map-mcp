# Kinds of maps: short recipes

The map `type` (`map_create(type=...)`, `set_map_type`) only picks checks and end tests; it never limits what you build. Below are the usual kinds and what to take for each. A kind nobody listed is `custom`: invent its rules with the base commands, test them, save them in `my/`.

| Kind | Take | End tests |
| --- | --- | --- |
| Defuse, hostage, wingman | `add_comp_plan` → cover, light, outdoor (`competitive`) | `playtest(kind="timings")`, `kind="competitive"` |
| Story, escape room, puzzle | `add_layout` → doors/buttons/logic (`physics`, `lowlevel`) → one `add_story` | `script_test(steps="auto")`, `route_test` |
| Deathmatch, arms race, casual | `mode_setup`, `add_arena` (`modes`) | `playtest(kind="mode")` |
| Surf, bhop, kz, parkour | `add_surf_ramp`, `add_bhop_platforms`, `add_movement_course`; `add_ability` (double jump, dash) for parkour | `playtest(kind="course")` |
| Aim map, 1v1 arena | `add_arena`, `mode_setup(mode="aim")` | `playtest(kind="mode")` |
| Hide and seek, prop hunt | `add_hideseek` (below) | `script_test` of a round |
| Racing | `add_controllable kind:"car"` + `add_minigame kind:"race"` (`vehicles`, `minigames`) | `script_test` with `hold` |
| Zombies / infection | `custom`: teams by `mode_setup`, zombies get `add_ability` (speed, regen, health) with `team:"t"`, a script swaps a killed CT to T | `script_test` |
| Showcase, playground | mechanics and abilities side by side (`sandbox`) | `script_test` per mechanic |

## Hide and seek: `add_hideseek`

```json
{"op":"add_hideseek","hide":40,"seek":180,"reveal":45,
 "disguise":{"models":["models/props_junk/plasticcrate01a.vmdl","models/props/de_nuke/crate_small.vmdl"]},
 "secret_walls":[{"mins":[300,600,0],"maxs":[316,700,128]}],
 "teleports":[{"from":{"mins":[100,100,0],"maxs":[164,164,64]},"to":[900,900,8]}]}
```

Hiders are **CT**, seekers **T**: CS2 itself ends the round (all hiders found → T win; time runs out → CT win). Each round: the `hide` phase (seekers frozen and blind, `blind:false` keeps their eyes open) → the hunt (`seek` seconds; the round timer shows it). Hiders keep only the knife (`hider_weapons:"default"` keeps their guns). Options:

| field | does |
| --- | --- |
| `reveal` | every N seconds of the hunt the hiders glow for 1.5 s, through walls |
| `disguise` | prop hunt: a hider becomes a prop from `models` (one follows each hider, up to 8); `key` (`inspect` = F, `attack2`, `reload`, `use`) switches the prop; `check_map` warns (`input_key_conflict`) when an ability uses the same key |
| `secret_walls` | visible walls everyone walks through (a never-solid `func_brush`) |
| `teleports` | step on `from`, arrive at `to` (feet) |
| `hud` | style keys and `title` (section `ui`) |

Verified in CS2 (`b10_hideseek`): seekers frozen and blind for the hide phase, the round clock at hide + seek, the HUD, a hider turned into a crate (F switched the prop), the reveal pulse. Blindness is a black fade held 1.2 s and renewed during the hide phase: in CS2 a one-player "stay out" `env_fade` cannot be lifted (a one-player "fade from" does nothing), so never blind a single player with it. Limits: a small accessory of some agent models stays visible on top of a disguise; disguise props must be in the map (they are, as parked transparent helpers).

The map type becomes `hideseek`. Give it spawns for both teams (8+ CT hiding spots spread out), `point_nav_walkable` for bots, and test a round: `script_test` with `{"cmd":"h10_hideseek_state t"}` and `expect` on `seek`, `found`.

## By hand: rules of your own

A kind of map is rules: who plays which team, what a round is, how one wins. The tools give the pieces: teams and bots (`mode_setup`, `game_player_equip`), the round clock (`Instance.SetRoundRemainingTime`), CS2's own win conditions (eliminate a team; `mp_default_team_winner_no_objective` for time-outs), abilities per team, HUDs and menus (`ui`), any logic (`scripting`). Start from the closest recipe above, change one rule at a time, and test each change in the game.
