# Prop atlas: the AI sees stock models

CS2 ships about 13 000 models (about 7 600 props). A model can choose among them by look, cheaply in time, disk and context: an index of facts, sizes on demand, offline preview sheets and short structured notes. Tools: `asset_search(kind="prop")`, `prop_preview`, `prop_note`. Code: `atlas.py`, `atlas_game.py`, `blender/prop_atlas.py`, `tools/prop_catalog.py`.

## How it works

1. **Index of facts** (`.local/cache/props/index.json`, never in git): every `*.vmdl_c` in the game's VPKs with its materials (RERL block) and whether it has physics. Built lazily on the first search (about 15 s for the whole game); keyed by the size and time of `pak01_dir.vpk`, so a CS2 update rebuilds it.
2. **Sizes** are measured lazily for search candidates: Source2Viewer CLI exports an untextured glb (about 50 ms per model) and min/max are cached in the index.
3. **Previews** (`prop_preview`): Source2Viewer CLI (textured glb) → one Blender run per sheet → up to 16 tiles. Each tile shows the model at angles `[0,0,0]`, a blue 32×32×72 player box and a red +X arrow on the floor. Tiles are cached in `.local/cache/props/img/<path+crc>.webp` with a 1 GB limit (least recently opened go first); temporary glb files live in `.local/tmp/props/<uuid>/` and are removed in `finally`. `detail=true` shows two opposite views of up to 6 models.
4. **Frame check**: an empty, clipped or untextured tile gets `status: "broken"`; only then is `game=true` allowed (a batch of shots in CS2 under the shared game lock). Light, foliage and glass are judged by a player in the game anyway.
5. **Notes** follow a strict schema: `{model, category, tags (3..6), style, front, mount, note (≤ 80 chars), by, date, source, crc, rev}`; `category` and `mount` come from fixed lists, `front` is `+x|-x|+y|-y|none`, `by` is the AI model that wrote it, `crc` the model file version (a changed Valve model makes the note stale). A `front` is accepted only after a `detail` preview.

## Where notes live (no merge conflicts)

- **Personal**: `.local/props/notes.jsonl`, append-only, one line per note, written atomically under a file lock by `prop_note`. The last note of a model wins.
- **Shared catalog**: `src/cs2map/data/props/catalog.jsonl` in git, one sorted line per model, written only by the integrator with `tools/prop_catalog.py promote` (schema check, best note wins, sort), then committed. Personal notes override shared ones; `prop_catalog.py status` lists personal notes that a newer shared one shadows.
- Different models are different lines, so git merges contributed notes by itself; a conflict means two people described the same model, which is a real choice.
- Pictures never go to git: they are Valve's images and take a second to render again.

## Axes

Previews are drawn in model axes: Source +X is the arrow, +Z up. The Source2Viewer glb is rotated back into Source axes (tested on models with known bounds). The rule for choosing `yaw` from a `front` is in `docs/TOOLS.md` ("Units, axes, facing").

## Decided against

Neural image search; rendering every model in advance; pictures in git; letting a model decide "this one needs real light".

## Files and tests

`tests/test_atlas.py` (index, sizes, preview cache, schema, promote). The catalog started with the 175 measured kit parts (street, plants, rocks, cars, market, harbour, interior, house details); one wrong front was fixed by a detail preview (`street_common/bench_inferno` → −x).
