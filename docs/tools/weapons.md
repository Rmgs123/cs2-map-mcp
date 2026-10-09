# Weapons

One operation, `add_weapon`: a **stock CS2 weapon under the hood** (slot, buying, pickup, drop, ammo, the hands and their animations stay native) with **your model in the hands, in third person and on the ground**, a colour, your numbers, or a **scripted shot**: hitscan, pellets, charge, wall penetration, glowing beams, **projectiles** (rockets, grenades, homing) with explosions, single/burst/auto fire, spring recoil, shake, flash, layered sound.

| Task | Take |
| --- | --- |
| A stock gun with own damage, magazine, colour, sound | `add_weapon` (`base`, `damage`, `clip`, `color`, `sound`) |
| Own model held in the hands | `model_build(name, script, weapon={base, scale})` → `add_weapon` `model` |
| Railgun, rocket launcher, gadget ready-made | `assets_prepare(kind="railgun" \| "launcher" \| "zapper")` |
| Laser, beam, penetrating shot | `fire` with `beam`, `penetration` |
| Rockets, grenade launcher, homing missiles | `fire.projectile` |
| Players spawn with it | `give: "all" \| "t" \| "ct"` (replaces the stock weapon of that slot) |
| Buy it in the buy menu | `shop: true`: every base weapon bought (or picked up) becomes this weapon; the menu keeps the base's name, icon and price |
| Check it in the game | `game_reset` → `script_test` (`use`, `aim`, `hold` attack, `w9_<name>_state`) |

## Your model in the hands

CS2 draws one model per weapon everywhere and moves it by the base's bones. `model_build(weapon=...)` puts your mesh on the skeleton of the base read from the installed game, so the hands hold it like the stock gun. Mark two points in the script with Empties: `grip` (where the right hand holds it) and `muzzle` (where the shot leaves); or pass `weapon.grip`/`weapon.muzzle` `[x,y,z]` in the model's inches. `scale` resizes it (the stock AWP is 54 long, AK 38, Deagle 11; the result's `weapon.stock` gives the base's size, hand and muzzle). Build it for the same `base` you use in `add_weapon`. Without `weapon`, a model shows only in the world: the holder sees the stock gun.

```json
{"name": "plasma", "script": "C:/maps/plasma.py", "weapon": {"base": "ak47", "scale": 0.8}}
```

## Example: railgun and a rocket launcher

```json
[{"op":"add_weapon","name":"rail","base":"awp","title":"RAILGUN","origin":{"room":"yard","at":[0.3,0.5],"z":44},
  "model":"models/mcp_w/railgun/railgun.vmdl",
  "fire":{"charge":0.6,"cooldown":1,"magazine":4,"damage":300,"penetration":8,"thickness":64,
   "recoil":{"kick":5,"side":0.6,"return":0.6},"shake":{"amplitude":0.3},"flash":{"life":0.14},
   "sound":["mcp.w9_rail_crack","mcp.w9_rail_body","mcp.w9_rail_tail"],"charge_sound":"mcp.w9_rail_charge",
   "beam":{"color":[50,160,255],"width":4,"life":0.2,"fade":1}}},
 {"op":"add_weapon","name":"rpg","base":"nova","title":"ROCKETS","origin":{"room":"yard","at":[0.3,0.6],"z":44},
  "model":"models/mcp_w/launcher/launcher.vmdl",
  "fire":{"magazine":3,"cooldown":0.9,"auto":false,"kickback":60,
   "projectile":{"speed":1100,"gravity":60,"homing":{"target":"aim","turn":70},
    "explode":{"radius":260,"damage":140,"knockback":520,"self":0.4}}}},
 {"op":"add_weapon","name":"burst","base":"deagle","give":"all",
  "fire":{"magazine":9,"cooldown":0.6,"damage":34,"auto":false,"burst":{"count":3,"interval":0.09}}}]
```

## Fields

`name` (lowercase), `base` (any gun, `taser`, `knife`, `knife_t`; not grenades or C4), `origin` (where it lies; a room point) + `angles` (lying on a table: `[0,90,90]`) and/or `give` and/or `shop`, `model`, `color` [r,g,b] (shows in the world; first person keeps the stock skin colours), `title` (HUD, 24 chars), `ref`.
Native shot (no `fire`): `damage` (head and limb multipliers stay), `clip` 1..250, `reserve`, `sound` (extra layer, up to 4 events), `impact` (sparks), `knockback` (push of the target, u/s).

## `fire`: the scripted shot (all optional)

| Field | Default and meaning |
| --- | --- |
| `charge`, `cooldown` | 0, .3 s (hold ATTACK to charge; releasing cancels) |
| `auto`, `burst` | true: holding keeps firing; false: one shot per press; `{count:3, interval:.08}` |
| `magazine`, `reload` | 10, 2 s; R or an empty magazine reloads |
| `damage`, `range`, `pellets`, `spread` | 50, 8192 units, 1, 0° (shotgun: 8 and 6) |
| `penetration`, `thickness`, `wall_loss` | 0 walls, 32 units per wall, .1 damage lost per wall |
| `beam` | `{color, width:4, life:.4, fade:.3}`: glowing rods along the shot, dimming over `fade` s |
| `projectile` | `{speed:1500, gravity:0, life:4, radius:4, model, scale:1, trail, hit:0, pool:6, homing, explode}` |
| `projectile.homing` | `{target:"aim"` (where the shooter looks) or `"enemy"` (nearest of the other team in `cone`), `turn:120°/s, cone:45, delay:.15}` |
| `projectile.explode` | `{radius:200, damage:120, falloff:1, knockback:400, self:.5, effect, sound}` or `false` (a slug: only `hit`) |
| `recoil`, `shake`, `flash` | degrees or a spring `{kick, side, return}`; `{amplitude, frequency, duration}`; `{life, light, color}` |
| `muzzle` | `{forward:40, right:5, down:5}`: where beams and projectiles start, from the eyes |
| `sound`, `charge_sound`, `reload_sound`, `empty_sound` | events; `sound` up to 4 layers |
| `impact`, `knockback`, `kickback` | sparks at hits; push of the target; push of the shooter backwards |

Explosions hurt players (bots too), physics props and anything with health (targets, vehicles); walls shield, props do not. The default rocket is `models/mcp_x/rocket/rocket.vmdl` with a flame trail, the stock HE explosion and sound.

## Test it

```json
[{"walk":[257,512,8]},{"aim":[307,512,44]},{"use":true},{"cmd":"w9_rail_state"},
 {"expect":"W_WEAPON rail state .*\"owner\":0","within":3},{"aim":[860,512,40]},
 {"hold":{"key":"attack","seconds":1.2}},{"expect":"W_WEAPON rail shot","within":2},{"shot":"rail_shot"}]
```

Console lines: `W_WEAPON <name> ready|pickup|bought|give|shot|launch|explode|hit`; `w9_<name>_state` prints weapons, owners, models, the last shots and rockets in flight. A beam along the view is hard to see: photograph it from the side.

## Limits

New weapon classes and own hand animations are not possible: the hands move as for the base, so build the model around its grip. Grenades and C4 are not bases. The knife's native slash still happens with `fire` (use the `taser` for a gadget). `color` does not tint a stock weapon in first person. `env_beam` and `env_shake` crash or are unusable (beams are rods, shakes are view offsets).

## By hand

`point_script` on a stock weapon: empty its clip each tick and feed the shared `Fire` fragment `{trigger, reload, src:{shooter, origin: eye, angles, inflictor: weapon}}`; `SetModel` a rigged model (the map must reference it, e.g. a parked `prop_dynamic`, or CS2 shows ERROR and crashes). Rockets: a pool of non-solid `prop_dynamic`, moved every tick, swept with `Instance.TraceSphere`; damage with `TakeDamage({damageTypes: CSDamageTypes.BLAST})`.

Verified in CS2 (`b10_shop`): `shop:true` on an AWP base with the railgun model — `buy awp` in a buy zone put the railgun in the hands. CS2 does not let a map add new entries to the buy menu.
