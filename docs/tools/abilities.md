# Player abilities

| Task | Take |
| --- | --- |
| Double jump, dash, blink, speed, low gravity, jetpack, flying, auto bunny hop, super jump | `add_ability` |
| Invisibility, wallhack (enemies glow through walls), regeneration, shield, armor, more health, no fall damage | `add_ability` |
| Jump pads | `add_ability` `launch` with a `zone` source |
| A power-up to pick up, a zone, a chat command, a button | `source` |
| Something else a player should be able to do | By hand below |

```json
{"op":"add_ability","name":"parkour","abilities":{"double_jump":{},"dash":{"cooldown":1.5},"regen":{}},
 "hud":{"title":"MOVES"}}
{"op":"add_ability","name":"ghost","abilities":{"invisible":{"alpha":25},"wallhack":{"color":[255,70,40]}},
 "source":{"item":{"origin":[900,1100,24],"respawn":10}},"duration":25,"hud":{"title":"GHOST","position":"top-right"}}
{"op":"add_ability","name":"pad1","abilities":{"launch":{"to":[660,200,160],"height":120}},
 "source":{"zone":{"mins":[200,160,0],"maxs":[264,240,16]}},"hud":false}
```

One operation is one set of abilities with one `source` and an optional `duration` (seconds; 0 = while the source holds). `team` (`any`, `t`, `ct`) and `bots:true` choose who gets them. The HUD (`hud`, style keys of section `ui`, `title`) lists cooldowns, fuel and time left; `hud:false` hides it.

| ability | settings (defaults) | how it works |
| --- | --- | --- |
| `double_jump` | `jumps` 1, `power` 300 | jump again in the air (release and press) |
| `super_jump` | `power` 480 | every jump starts with this upward speed |
| `dash` | `key` `inspect` (F), `speed` 900, `cooldown` 1.5, `up` 120 | a burst along the view |
| `blink` | `key` `attack2`, `distance` 420, `cooldown` 3 | teleport to where you look (never into walls) |
| `speed` | `scale` 1.4 | faster running on the ground |
| `gravity` | `scale` 0.45 | lower (or higher) gravity in the air |
| `jetpack` | `thrust` 1600, `max_up` 260, `fuel` 3 s, `refill` 2 s | hold jump in the air; refills on the ground |
| `fly` | `speed` 360 | free flight: WASD along the view, jump up, duck down (not with gravity or jetpack) |
| `autobhop` | — | holding jump re-jumps on landing |
| `invisible` | `alpha` 0 | body and weapons fade (other players and the camera do not see you) |
| `wallhack` | `color` [255,70,40], `enemies` true | glowing outlines of other players, visible through walls |
| `regen` | `per_second` 6, `max` 100, `delay` 3 | heals after `delay` seconds without damage |
| `shield` | `reduce` 0.5 | takes this share off every hit |
| `armor` | `value` 100, `helmet` true | given once |
| `health` | `max` 200 | more health while held |
| `no_fall_damage` | — | falls never hurt |
| `launch` | `velocity` [x,y,z] or `to` [x,y,z] + `height` 96 | a jump pad: an instant throw (a ballistic arc onto `to`) |

Keys for `dash`/`blink`: `inspect` (F), `attack2`, `reload`, `use`, `walk`, `duck`; two abilities never share one.

| `source` | gives the set |
| --- | --- |
| `"always"` (default) | to every player, again after each respawn |
| `{"zone":{"mins","maxs"}}` | inside the box (for `duration` seconds when given; a `launch` throws on entry) |
| `{"item":{"origin","radius":48,"respawn":15,"model"}}` | to whoever walks over the token; it comes back after `respawn` (0 = never) |
| `{"command":"!fly"}` | chat command; again = off (without `duration`) |
| `"input"` | I/O: `RunScriptInput <name>_grant` / `<name>_revoke` on `a10_<name>_script` with the player as activator |

Death and respawn take abilities away (except `always`). Test: the console command `a10_<name>_state t` prints holders, fuel and time; `script_test` with `hold`/`click` and `expect` on `grant`, `dash`, `double_jump`.

Verified in CS2 (`b10_lab`, `b10_abilities`): jump 56 → double jump 107, dash 900 u/s, jetpack 530 up in 2 s, gravity 0.35 → a jump at 138 after 0.55 s (13 without), speed ×1.6 → 400 u/s, flight 445 up after 1.2 s, the jump pad landed 24 units from its target; the ghost item made the body and weapons invisible and the bot's silhouette glowed through a wall.

## Honest limits

- Everyone sees glow: the wallhack outlines are visible to all players, not only to the holder (CS2 has no per-player glow).
- CS2 does not glow player models: the wallhack draws invisible player-size silhouettes that follow the enemies (8 at a time).
- `trigger_gravity` and similar Valve triggers do nothing for players in CS2; gravity and speed are applied by the script every tick.
- Abilities change the player's velocity on the server; in online play with high ping a dash or a jetpack can look a little jumpy to its user.

## By hand: an ability of your own

An ability is a rule in a `point_script` that runs every tick for the players who hold it. Start from the shared pieces:

```js
// @requires core, hud
import { Instance, CSInputs } from "cs_script/point_script";
const core = Core({tag: "MY", name: "wallrun", state: "wallrun_state"});
Instance.SetThink(() => {
    for (const p of core.pawns()) {
        const v = p.GetAbsVelocity(), onGround = p.GetGroundEntity() !== undefined;
        // e.g. wall run: in the air, holding forward next to a wall -> cancel the fall
        if (!onGround && p.IsInputPressed(CSInputs.FORWARD)) {
            const side = Instance.TraceLine({start: p.GetEyePosition(), end: core.add(p.GetEyePosition(), core.basis(p.GetEyeAngles()).l, 40), ignoreEntity: p});
            if (side.didHit) p.Teleport({velocity: {x: v.x, y: v.y, z: Math.max(v.z, 0)}});
        }
    }
    Instance.SetNextThink(Instance.GetGameTime());
});
core.on("OnActivate", () => Instance.SetNextThink(Instance.GetGameTime()));
core.on("OnRoundStart", () => Instance.SetNextThink(Instance.GetGameTime()));
```

Tools in the API: `GetAbsVelocity` + `Teleport({velocity})` (impulses, flight), `GetGroundEntity`, `IsInputPressed`/`WasInputJustPressed` (`CSInputs`: FORWARD, BACK, LEFT, RIGHT, WALK, DUCK, JUMP, USE, ATTACK, ATTACK2, RELOAD, LOOK_AT_WEAPON), `SetColor` (alpha), `Glow` on props, `SetHealth`/`SetArmor`/`SetMaxHealth`, events `OnPlayerJump`, `OnPlayerLand`, `OnModifyPlayerDamage` (change or abort damage), `OnPlayerChat`. Save a working ability as a recipe in `my/`.
