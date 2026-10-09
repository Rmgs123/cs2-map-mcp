# Quarter metadata: paving and facades

`add_paving` (`paving.py`) and `add_facade` (`facades.py`) write JSON metadata into the map. Wear, street dressing, the skyline, routes and the radar read it to find sidewalks, walls, doors and shop fronts without guessing geometry. Only the integrator changes the format; new fields are added, old ones never removed.

## Common rules

- Inches, +Z up. A layout room's floor never moves: `add_paving` repaints floor slabs and adds solid sidewalks and steps on top.
- Facades use wall coordinates: `axis` (0 = X, 1 = Y) is the wall normal's axis, `plane` the face coordinate on it, `normal` the unit vector out of the wall into the street, `u` the coordinate along the wall, `d` the distance from the face into the street (negative = into the house). World point: `p[axis] = plane + normal[axis]·d`, `p[1 − axis] = u`, `p[2] = z`.
- The face (`d = 0`) stays where the layout put it, so routes keep their width. Window, door and shop niches go to `d < 0`; details (frames, sills, awnings, lamps, pipes) stick out at most about 14 units above head height and about 4 near the ground.

## `cs2map_paving` (group `paving:<name>`)

```json
{"schema":1,"name":"quarter","layout":"q_layout","style":"inferno","rooms":["mid","a"],
 "surfaces":{"mid":"street","a":"plaza"},
 "sidewalks":[{"room":"mid","side":"-y","mins":[0,0,-1],"maxs":[1024,40,6]}],
 "steps":[{"from":"low_room","to":"high_room","mins":[0,0,0],"maxs":[128,64,48],"steps":4}]}
```

Sidewalk boxes include the curb, start one unit below the floor and end at the sidewalk top; they are solid and carry doors and plinths. Use `paving.sidewalk_top(m, axis, plane, out, start, end, z0)` for heights along a wall. `style` is `inferno`, `mirage` or `my:<own style>`. `steps` are stairs in the lower room at an opening: room graphs and timings treat them as a path, and facades leave the opening clear down to the floor.

## `cs2map_facade` (group `facade_row:<name>`), schema 2

```json
{"schema":2,"name":"main_n","axis":1,"plane":384.0,"normal":[0,-1,0],"z0":6.0,"top":598.0,
 "houses":[{"id":123,"from":0.0,"to":288.0,"material":"materials/...vmat","tint":[243,219,179],"floors":3,
            "top":438.0,"depth":224.0,"arcade":false,"loggia":true,"arcade_depth":0,
            "doors":[[96, 48, 0]],"shops":[[200, 96, 0]]}],
 "raised":[[0,0,0],[0,0,0]]}
```

- `z0`: the level floors count from (street or sidewalk top); a house's `top` is its cornice, the roof above overhangs the street by 24.
- `tint` (optional): the plaster tint (the house meshes' `tintColor`, like de_inferno).
- `depth > 0`: a real block (a 24-thick front with niches and a body to `−depth`); `0`: a thin skin on a shared wall (no niches).
- `doors`/`shops`: centre `u`, width and `face_d`, the depth of the wall they stand in (0 = the face, `−(24 + arcade_depth)` = the back of an arcade). Keep the space in front of them free.
- `arcade`: a walkable ground-floor gallery `arcade_depth` deep (piers in the face plane, floor 2 above `z0`); `loggia`: an open top floor.
- Windows and balconies are not listed: their niches are in `d ∈ [−24, 0]` every floor height (144 by default) from `z0`; find walls with rays or through the facade group.
