# Base geometry: any shape, any surface

The primitives that let a model build anything: free polygon meshes with holes, sweeps along paths, lathe solids, cuts, piece-by-piece edits (`edit_mesh`), and material, UV, tint and two-material blends on chosen faces. Code: `meshops.py` (polygons, sweeps, CSG, selection, UV, paint), `meshedit.py` (`edit_mesh`), `mesh.py` (half-edge encoding, smoothed normals), `ext/geometry.py` (shapes `mesh`/`extrude`/`revolve`, operations `cut_mesh`, `texture_faces`, `paint_blend`, `edit_mesh`, `map_info(about="geometry")`). No external processes.

## How it works

**Polygon meshes.** `mesh` takes world XYZ vertices and faces (outer ring, optional holes, material, UV). Faces are planar (a curved surface is given as triangles). Each face is projected into its own orthonormal basis; rings must be simple, holes strictly inside and disjoint; holes are bridged from their rightmost vertex and ear-clipped; the triangle area is checked against outer minus holes. Convex faces stay polygons; all parts of a face share its normal and UV.

Before encoding every solid is checked: half-edge structure, no boundary edges, one fan per vertex, no intersections between non-adjacent faces, positive volume of every connected shell. An inverted shell is refused, never "fixed" by guessing. `solid: false` makes an open, render-only surface (`physicsType: none`, no baked light); brush entities need solids.

**Extrude** normalises the profile (outer CCW, holes CW). At each station the tangent is the normalised sum of the neighbouring segment directions; profile X = cross(up, tangent), Y = cross(tangent, X); up is +Z (or +Y for a vertical first leg). Singular frames, 180° reversals, stations too close and self-intersections fail. It is a fixed-up sweep, not a spline with twist; arcs are discretised the same way; non-planar side quads become two triangles; `closed` joins the last station to the first without caps.

**Revolve** takes (radius, height) points, an origin and an axis; a full turn joins the ends, vertices on the axis are welded, degenerate faces skipped; a partial turn gets caps. An open profile is solid on a full turn when both ends lie on the axis.

**Cuts.** `cut_mesh` subtracts a box or a convex prism (convexity checked against every plane) from one mesh with BSP CSG. Every corner carries position, UV, RGBA tint and four blend weights, all interpolated at intersections; the original face data stays, cutter faces get the given material; node id, origin, options and ref are kept. BSP tolerance 0.002 sits above the core's 0.001 vertex grid; afterwards close vertices are welded, T-junctions inserted with interpolated attributes and sub-grid slivers collapsed; closure and winding are checked again. Depth ≤ 180 and ≤ 8192 faces.

**Edits** (`edit_mesh`, `meshedit.py`): the mesh is decoded into polygons, the steps run (move, extrude, inset, bevel, subdivide, bend, twist, taper, noise, smooth, mirror, weld) and the result is encoded once, so a failing step changes nothing. Vertices are identified by position on the 0.001 grid. A bevel cuts each convex edge with local prisms (the fan of triangles between the edge's arc and a point outside the corner, extended a little along the edge so neighbouring cuts mitre); only the faces that reach a prism take part in its CSG, and coplanar fragments are merged back into convex faces. Changed faces get world UV like Hammer's (or keep their UV with `uv: "keep"`); bent faces are split into triangles.

**Normals.** Hammer stores smoothed normals in the mesh: corners shared by faces less than `smoothingAngle` (40°) apart get one averaged normal. Generated meshes do the same (`mesh.smooth_normals`), so revolves, bends and rocks look round instead of faceted.

**Material, UV, tint.** Faces are selected by all/top/bottom/sides/±XYZ (dot > 0.707), indices of the real faces, centroids in a box, a plane, a facing direction or "last". UV modes: world/box (dominant axis), planar (one basis for the selection), cylinder (angle around an axis, seam moved to a face edge, arc length in units), fit (the image once over each face). Scale is units per texel, offset texels, rotate degrees. A corner tint is 0..1; a mesh tint becomes the node's `tintColor`.

**Blends.** `paint_blend` first proves that the compiled material is `csgo_environment_blend.vfx` with `g_tColor1` and `g_tColor2` (reading the installed `vmat_c` through `resourceinfo`, 20 s timeout, a bounded cache keyed by the compiled bytes), never trusting "blend" in a name. Selected faces are split by the world grid (x/y/z = k·cell), weights from `bottom` (fade up from `base_z`), `corners` and seeded `noise`, combined by maximum and scaled by `strength`, go to `VertexPaintBlendParams.x` (second vertex buffer, like Valve's maps).

## Pitfalls we hit

- Fan triangles of cut faces produced zero-area slivers between painted neighbours; splitting by the world grid with the same BSP as cuts fixed it.
- Meshes with node rotation or scale, Hammer subdivision, unknown vertex streams or edge flags are refused: unknown data is never thrown away silently. Bake them first.
- A plane-cut chamfer of a cube corner meets in a point (three chamfer faces), not in a corner triangle as in vertex bevels; both are valid looks.
- The intersection check uses strict interiors, so faces that merely touch are not errors; borderline cases are why compiler and game acceptance stay mandatory.

## Limits

512 points per ring, 8192 vertices and faces, 256 path stations, 3..128 revolve segments, edits up to 32 steps and 160 bevel cuts; geometry below the 0.001 grid is not for map meshes. Sculpting beyond this is a Blender model with explicit collision.

## Tests

`tests/test_geometry.py`, `tests/test_geometry_operations.py`, `tests/test_edit_mesh.py`; in game `g8_any` (round tower with a dome, stairs with rails, an arched gallery), `p8_mechanics` (tower with a painted stone base) and `a10_depth` (twisted column, bent arch, bevelled bench, noise rock, tapered obelisk).
