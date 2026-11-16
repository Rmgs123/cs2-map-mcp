---
description: Turn photos of a real place into a playable CS2 map, generator by generator
arguments: map | Map name (a-z, 0-9, _) ; photos | Folder with the photos (and any outline, plan or elevation data)
---
Build the map `{map}` from the photos in `{photos}` with the cs2map tools. The photos are the truth: the map should look like the place.

Follow the recipe "A big map from photos" in `read_guide(section="recipes")` and the parts of `read_guide(section="pitfalls")` that touch each step; read `read_guide(section="pipeline")` before the first generator.

1. Sort the photos by place into folders with a short note each (what, where from, which way) and one numbered index. List what the photos leave open (levels, playable or background areas, which entrances are real) and ask me before building those parts.
2. Ground first: terrain, road centrelines, building outlines and levels. Then stages of packages; every package is a generator in `my/maps/{map}/` listed in `my/maps/{map}/pipeline.json`. Never edit the map by hand: fix the generator and apply with `map_edit(name="{map}", packages="my/maps/{map}/pipeline.json")`.
3. For every package: zone checks with `visual_check(box=...)`, a region build `map_build("{map}", "fast", region=...)`, fullbright frames from the place and direction of each of its photos, side by side with the photo. Fix what differs before the next package.
4. Keep a list of reference views (the best places and every fix) and compare their sheet after every package: nothing may get worse.
5. After each package tell me in a few lines what changed, with the frames.
