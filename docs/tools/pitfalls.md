# Pitfalls: what real maps taught, and how to avoid it

Every item below cost a rebuild or a wrong frame on a real map (a big town built from photos, showcase and test maps) and was checked in CS2. Read the part that touches your task before you start it.

| Task | Take |
| --- | --- |
| Walls with many windows, roofs, rotated houses, furniture | "Geometry" and "Buildings and rooms" |
| Roads, pavements, kerbs, ground | "Roads, paving and ground" |
| Fences and the edge of the play area | "Fences and boundaries" |
| Own vehicles, riders, glass, cameras | "Vehicles" |
| Scripts and tests that behave oddly | "Scripts and tests" |
| Big maps, long sessions, generators | "Big maps and generators" |

## Geometry

- **Many windows in one big body fail as boolean cuts** (`cut_mesh`: "edge used twice", "solid mesh is open" after a few cuts). Build a wall as one mesh per footprint edge: an elevation rectangle with polygon holes (even a round window as a 28-gon), extruded inwards; frames, glass, mullions and sills as separate meshes.
- **Rotated boxes that touch or nearly touch in one `add_mesh`** can be refused as overlapping shells. Keep them in separate nodes; boxes far apart are fine.
- **Precision:** faces with holes and slanted boxes need vertices rounded to 5 decimals, not 2: a warped quad from rounded vertices breaks the planarity limit (0.002 units) and the whole batch is refused. Emit a quad that cannot be planar as two triangles.
- **Frames:** wall-mounted details along a wall that is not parallel to your planning frame must be built in the wall's own frame (a 10° skew is 17 cm per metre). A generator that works in metres multiplies by 39.37 before it asks the map anything in units.
- **One owner per object.** Before adding an object from a photo or a plan, search every generator for it (name, material): two generators building the same duct showed as "strange blocks".

## Buildings and rooms

- **Doorways in a rotated building:** the player's hull is a world-axis box, so a doorway turned by `a` needs `32 × (|cos a| + |sin a|)` plus a margin: 45 units at 45°, i.e. 1.3 m or more; stair flights likewise. A 0.9 m interior door of a 45° house stopped the player in the jamb.
- **A flat roof reads as a roof** only with a parapet and a coping along the outer outline of all touching footprints (their union, a mitred ring: per-edge boxes overlap at corners and z-fight) plus a few stock rooftop units.
- **Entrances:** many short stair steps along a long house each got a door; keep entrances 10 m or more apart and give the rest windows. Opening a sealed block of a house exposes props and trees that were hidden inside it: check models within 3 m of the opened wall.
- **Furniture:** check what each prop faces and what the room is for (the TV faces the sofa, the fridge door the room, nothing under a window players shoot from). Place by the real footprint (`add_model` `anchor`; search lines say when the origin is off centre). For a prop whose use depends on its face, look at one game frame from the room: a preview sheet once suggested the wrong front.
- **Light behind windows:** emissive curtain cards at glow ~2.5 (14 is blinding white); a card set back in a wall recess is black under baked light unless it is emissive.

## Roads, paving and ground

- **Ground meets asphalt without a slab:** take the road top at the terrain node, not the lowest top around it; keep the ground 4 units under the top out to one terrain cell past the edge; give free paved edges a chamfer down into the ground (depth = exposure + 4, 8–32 units, width 1.6 × depth). Apply the cap "ground at most paved top − 3" **last**, after every other terrain edit, with a reach of at least half the road's sample spacing (narrow roads sampled 2 m apart left grass over the road between samples).
- **Gaps and crossings:** fill soil strips up to 1.2 m wide between two pavings with up to 6 units of level difference (once: two fills z-fight). A driveway crossing a pavement needs an apron at the driveway level that ramps to the pavement ends.
- **Grades:** a road leaving a flat plate or lot ramps at 10 % or less, never with a step; a sloped lane crossing a flat lot comes to the lot level − 1 unit wherever it touches and blends back over 4 m. A blend limited by height difference once made a 14 % grade into 22 % and then a 45 % jump.
- **Joins:** a minor road meets the dominant one 1 unit under it (blend over 4 m): no z-fighting, no lip. Kerbs never stand on a carriageway; probe "does the paving continue" at several distances (3, 6, 9, 12 units), not one, before adding a kerb to a free edge; a soft top (grass, gabion) under an edge is not paving.

## Fences and boundaries

- **Escape routes hide in joins:** a 1 m gap between a fence run and a building let a player walk out. Clip curtains run 1.5 m past the ends of their run; a fence next to a building runs on to the building's real wall face (+0.15 m). `visual_check` reports `visual_escape` wherever a player can reach floor outside `set_play_area` from the spawns; confirm a fix in the game with a walk out and a jump.
- **No bare invisible walls:** close a gap with a real object, a clip only behind it. Drop fence runs shorter than two panels (lone panels read as junk) and benches with a house within 4 m in front of them.

## Vehicles

- **Crowns and clearances:** a stock tree can collide with its branches at roof height (search says "vehicles hit it"): place it with `add_model` `"collision": "trunk"` (an invisible core stops players, cars and bullets at the trunk). Before a long rebuild, drive the route offline: `route_test(map, route=[[x,y],...], vehicle="<name>")` reports steps, grades, the nose and the tail at grade changes, walls, clips and props the body hits (props by their real collision) and turns tighter than the turning circle, in seconds. The steering lock shrinks with speed (34° parked, 28° at 130 u/s), so an autopilot cuts or widens real arcs.
- **Script inputs:** register each input name once. A second `Instance.OnScriptInput` for the same name is refused with only a console line `already registered` at map load; scan the load log for it and for stack traces.
- **Angles of a parented player:** `GetEyeAngles` is world space, `SetEyeAngles` is relative to the parent. Convert target views into the body frame, unparent before restoring world angles, and test every vehicle parked at a non-zero heading: at yaw 0 both conventions agree and the bug hides. Do not clamp a rider's view by writing eye angles every tick (the view creeps at the limit).
- **Seat camera:** an offset along the view (`cameraOffset`) pushes the camera into the headliner or the glass when looking up or sideways; put it in `followOffset`, recomputed every tick along the horizontal view, clamped inside the side glass. Glass with back faces rendered stacks four tinted layers: use one-sided panes.
- **Boarding checks:** start the hull test 2 units over the feet and ignore `weapon_*`/`item_*` hits (a dropped gun or the bomb at the door refused entry at random); log what blocked every refusal: a silent `false` costs an hour.
- **Scaling a car:** scale the model (every `import_scale`, render and physics), then every body-frame length of the vehicle by the same factor; mass, forces and speeds stay.

## Scripts and tests

- **Shots:** the bullet-impact event can arrive before its fire event: pair them by game time, never by order (pairing by order gave a fake 73° error on a moving car).
- **Players:** after `jointeam` mid-round the player is dead until the next round (`mp_restartgame 1`, wait 3 s); bots as riders need `bot_stop 1`, `bot_crouch 1` and a spawn point per player. A one-player `env_fade` that stays out cannot be lifted; renew a held fade every 0.5 s instead. Two always-on mechanics on one key fire together (`check_map`: `input_key_conflict`).
- **Steps:** a jump pad launches the player during a `go` step: walk onto it instead. A probe that set `mat_fullbright 1` leaves it on: lit frames need `fullbright: false` explicitly.

## Big maps and generators

- **Order:** look and shape first with fast builds viewed in fullbright, full light only for looks and at stage ends, bots last: nav on an open town reached 66 000 areas and hours of compute (section `nav`). A light bake and a game holding a big map together can fill 32 GB of memory: close the game before a full bake.
- **No regressions:** keep a fixed list of reference views (the best-looking places and every place you fixed) and compare a sheet of them after every package. Look at the scene from above, from the side and from outside the play area too.
- **Generators:** fail loudly (one bad operation refuses the whole package); keep a fixed order (no loops over sets) and seeded randomness; a generator that reads another one's output belongs after it in `pipeline.json`. Before a big change, copy the generator; keep comments on their own line in one-line statements (an inline `#` once commented out half a line and emptied a map).

## By hand

None of the above needs a special tool: each item is a rule for ordinary operations and checks. When you meet a new one, write it with `my_notes(kind="note")` (and `kind="feedback"` when a tool should have caught it), so the next chat and the author learn from it.
