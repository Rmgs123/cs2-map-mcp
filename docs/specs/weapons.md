# Weapons: one operation, `add_weapon`

Guide for models: `docs/tools/weapons.md`. Code: `weapons.py`, `weapon_rig.py`, `fire.py`, `data/weapons/weapon.js`, `data/mechanics/fire.js`.

## Idea

CS2 gives an addon no new weapon classes. But a weapon model is drawn the same everywhere — in the hands (first person), by other players and on the ground: stock models have `is_unified_model = true` in `weapon_metadata`, and the hand animation moves the model through the bones `weapon` → `weapon_offset`. An own model with the same skeleton is held exactly like the stock one (verified on the AWP in first and third person and on a table, then Nova, taser, Deagle).

So `add_weapon` = a stock base weapon (slot, buying, pickup, drop, ammo and hand animations stay native) + a script on top: own model, colour, damage/magazine/stock shot sound, or an own shot engine. The older `add_custom_weapon` and `add_scripted_weapon` were merged into it; the old names are refused with a hint.

## Own model on the base skeleton (`weapon_rig.py`)

`model_build(..., weapon={base, grip, muzzle, scale, magazine})`:

1. The Blender driver in weapon mode exports triangles (inches, +X forward, +Z up) with material tokens and empties `grip` (right hand) and `muzzle`.
2. The base is read from the installed game: Source2Viewer-CLI decompiles its `.vmdl` and DMX into `.local/cache/weapon_bases/<base>-<vpk time>/` (at most 12 bases, ~1–2 MB each; Valve files never enter the repository). The DMX gives the skeleton (joints with positions and quaternions), the `.vmdl` the attachments; the hand is bone `ag1_hand_r`, the muzzle `muzzle_flash`.
3. Our vertices replace the base's mesh in its DMX: the same `DmeModel` and `jointList`, every vertex on `weapon_offset` with weight 1, scaled by `scale` and shifted so `grip` meets the base's hand.
4. The `.vmdl` is the base's KV3 document with replacements: meshes and physics hull → our DMX; clips (`dropped`, `shoot`, `reload`, inspect) → copies of the base's next to the model; `muzzle_flash` → our muzzle (in its bone's axes); `magazine_model` → empty (unless `magazine: true`).
5. The answer gives `weapon.stock` (size, hand and muzzle of the base, for picking a scale) and `weapon.size`.

Without the clips the client crashes on a dropped weapon. The model must be in the map's manifest (a hidden `prop_dynamic` under the map): `SetModel` with a model that is not loaded shows ERROR and crashes the client (`ACCESS_VIOLATION` in client.dll); `add_weapon` places that prop itself.

## The operation

Fields: `name`, `base` (any gun, `taser`, `knife`, `knife_t`; not grenades or C4), `origin` + `angles` (where it lies) and/or `give` (`all|t|ct`, handed out on spawn) and/or `shop`, `model`, `color`, `title`, `hud`; stock shot: `damage`, `clip`, `reserve`, `sound`, `impact`, `knockback`; own shot: `fire`. Entities: the weapon (if lying), `point_script`, sounds, sparks, the model preload prop and the shot engine's entities. Names `w9_<name>_*`, log `W_WEAPON <name> ...`, telemetry `w9_<name>_state`. A rigged model (`is_unified_model` in its `.vmdl`) shows in the hands too; otherwise the hands keep the stock model and the answer warns. A model rigged for another base holds the wrong grip: also a warning.

**Give**: the engine hands out the starting kit after the spawn event, so the gift comes 0.3 s later; a stock weapon of the same slot (`CSGearSlot`: 0 primary, 1 pistol, 2 knife; the taser has none) is removed with `DestroyWeapon` unless it is the same class (then it simply becomes ours); the new one is found with `FindEntitiesByClass` on the owner, retried up to 20 ticks.

**Shop** (`shop: true`): every base weapon bought or picked up is adopted as this weapon (log `W_WEAPON <name> bought`); the buy menu keeps the base's name, icon and price — CS2 does not let a map add buy-menu entries.

**HUD**: ammo, charge and title use the shared UI theme (`ui.md`); `hud` overrides colours, font and position or turns it off.

## Shot engine (`fire.js`, schema `fire.py`)

- `auto` (default true: holding keeps firing), `burst {count, interval}` (the burst finishes by itself, then `cooldown`);
- `muzzle {forward, right, down}`: where the ray, rocket and flash start relative to the eye;
- `projectile`: a pool of `prop_dynamic` (default model the rocket `models/mcp_x/rocket`), moved by `vel × dt` with gravity each tick and tested with `TraceSphere` (radius) or `TraceLine`; `homing {target: aim|enemy, turn, cone, delay}` turns the velocity at most `turn` °/s without losing speed; `life` explodes on time; a full pool explodes the oldest. The explosion: effect and sound from a three-slot ring, `BLAST` damage to everyone in the radius — players, bots and props (physics, breakable, `prop_dynamic` with health, mechanics bodies) — with `falloff`, push `knockback`, self damage `self`; only world geometry gives cover. `hit` is direct-hit damage; `explode: false` makes a slug;
- a shooter that is not a player (a turret, a machine) fires with `TraceLine` (`TraceBullet` needs a player).

Also: charge, wall penetration (`TraceBullet` to the wall, `TraceLine` back from beyond it), pellets, a beam from a pool of additive rods with a smooth fade, spring recoil, shake, a flash with light, layered sound, sparks, pushes. `env_beam` and `env_shake` are not used (crash / no effect).

## Ready assets (`assets_prepare`)

- `railgun`: a rail gun on the AWP (scale 0.55) with six synthesised sounds (`rail_audio.py`);
- `launcher`: a rocket launcher on the Nova (0.75) and the rocket;
- `zapper`: a gadget pistol on the taser (1.0).

## Own mechanics

`script_write` with `// @requires core, fire` and `// @fire <prefix> {...}` gives an own script with the shot engine; `add_script` creates its entities (`docs/tools/scripting.md`).

## Limits

No new weapon classes or hand animations (hands move as for the base, so the model is drawn for its grip); grenades and C4 cannot be a base; a knife with `fire` keeps its native swing (use the taser for gadgets); `color` does not tint a stock first-person weapon.

## Tests

`tests/test_weapons*.py` (fields, entities, give, rigged models, rockets: flight, falloff, cover, push, self damage, homing, life, pool; single and burst fire) and the Node double. In game `w9_rigtest`, `w9_weapons` (railgun, launcher — one blast hurt three crates 126/106/85 —, zapper, Deagle with bursts and give), `p9_playground`, `b10_shop` (`buy awp` gave the railgun).
