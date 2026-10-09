"""Off-road SUV and its wheel. Inches, +X front, +Y driver side (left), +Z up; body origin = ground at rest.
Wheels are separate props held by the script's suspension: the body collision has NO tyres (clearance 24),
so rocks, ruts and kerbs are taken by ray-cast wheels instead of sliding hulls. Wheel pivot = its axle."""
import math
import bmesh
import bpy
from mathutils import Vector

R = 16                      # tyre radius (32" wheel)
WHEELS = [(62, 36), (62, -36), (-60, 36), (-60, -36)]
W = 36                      # half width of the lower body

mats = {}
for key, color in {'paint': (.14, .2, .12, 1), 'roof': (.62, .6, .52, 1), 'trim': (.02, .022, .025, 1),
                   'rim': (.55, .57, .6, 1), 'window': (.02, .03, .035, 1), 'lamp': (1, .92, .7, 1),
                   'tail': (.75, .02, .02, 1), 'amber': (1, .45, .05, 1), 'tyre': (.025, .025, .028, 1)}.items():
    mat = bpy.data.materials.new(key)
    mat['source_vmat'] = f'materials/mcp_v/v9_{key}/v9_{key}.vmat'
    mat.diffuse_color = color
    mats[key] = mat


def link(name, mesh, mat):
    o = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(mats[mat])
    return o


def smooth(o, bevel=0.0, segments=3):
    if bevel:
        m = o.modifiers.new('Edges', 'BEVEL')
        m.width = bevel
        m.segments = segments
        m.limit_method = 'ANGLE'
        o.modifiers.new('Normals', 'WEIGHTED_NORMAL')
    return o


def bake(o):
    """Apply all modifiers (no operator context needed, works the same in Blender 4.5 and 5.x)."""
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(o.evaluated_get(dg), depsgraph=dg)
    o.modifiers.clear()
    o.data = mesh
    return o


def box(name, lo, hi, mat='paint', bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=[(a + b) / 2 for a, b in zip(lo, hi)])
    o = bpy.context.object
    o.name = name
    o.dimensions = [b - a for a, b in zip(lo, hi)]
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    o.data.materials.append(mats[mat])
    return smooth(o, bevel)


def prism(name, profile, y0, y1, mat='paint', bevel=0.0):
    """A side profile [(x, z), ...] (convex, counter-clockwise seen from +Y) extruded from y0 to y1."""
    bm = bmesh.new()
    a = [bm.verts.new((x, y0, z)) for x, z in profile]
    b = [bm.verts.new((x, y1, z)) for x, z in profile]
    bm.faces.new(a)
    bm.faces.new(list(reversed(b)))
    for i in range(len(profile)):
        j = (i + 1) % len(profile)
        bm.faces.new([a[i], a[j], b[j], b[i]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return smooth(link(name, mesh, mat), bevel)


def slanted(name, p0, p1, t0, t1, half, side):
    """A glass panel on the slanted face p0->p1 (x, z) of the cabin, 1 unit off the face, inset to t0..t1.
    side=+1: the face looks forward (windscreen), -1: backward (rear window)."""
    a, b = Vector((p0[0], 0, p0[1])), Vector((p1[0], 0, p1[1]))
    d = (b - a).normalized()
    n = Vector((d.z, 0, -d.x)) * side
    if n.x * side < 0:
        n = -n
    bm = bmesh.new()
    vs = [bm.verts.new(tuple(a + (b - a) * t + n * 1.0 + Vector((0, y, 0)))) for t, y in
          ((t0, -half), (t0, half), (t1, half), (t1, -half))]
    face = bm.faces.new(vs)
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    for v in [e for e in ext['geom'] if isinstance(e, bmesh.types.BMVert)]:
        v.co -= n * 1.2
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return link(name, mesh, 'window')


def plate(name, poly, y, thick, mat):
    """A flat panel in an XZ polygon at side y (thickness along Y)."""
    return prism(name, poly, y - thick / 2, y + thick / 2, mat)


def outer_only(o, outward):
    """Glass seen from the cabin: drop the panel's inner face, so the driver's camera looks through it (the
    engine culls back faces; from outside the panel looks the same)."""
    bm = bmesh.new()
    bm.from_mesh(o.data)
    out = Vector(outward).normalized()
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.dot(out) < -.5], context='FACES')
    bm.to_mesh(o.data)
    bm.free()
    return o


def cylinder(name, center, radius, depth, axis, mat, sides=32, bevel=0.0):
    rot = {'x': (0, math.pi / 2, 0), 'y': (math.pi / 2, 0, 0), 'z': (0, 0, 0)}[axis]
    bpy.ops.mesh.primitive_cylinder_add(vertices=sides, radius=radius, depth=depth, location=center, rotation=rot)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(mats[mat])
    return smooth(o, bevel, 2)


def tube(name, a, b, radius, mat='trim'):
    a, b = Vector(a), Vector(b)
    d = b - a
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=d.length, location=(a + b) / 2)
    o = bpy.context.object
    o.name = name
    o.rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
    o.data.materials.append(mats[mat])
    return o


def arch_flare(name, x, side):
    """Upper half of a flattened ring around a wheel arch."""
    bpy.ops.mesh.primitive_torus_add(major_radius=R + 4.5, minor_radius=2.4, major_segments=40, minor_segments=10,
                                     location=(x, side * (W + .6), R), rotation=(math.pi / 2, 0, 0))
    o = bpy.context.object
    o.name = name
    bpy.context.view_layer.update()
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if (o.matrix_world @ v.co).z < R + 1.5], context='VERTS')
    bm.to_mesh(o.data)
    bm.free()
    o.scale = (1, 1.5, 1)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    o.data.materials.append(mats['trim'])
    return smooth(o)


def wheel(prefix, center=(0, 0, 0), axis='y', radius=R, width=11, tread=True):
    """Chunky off-road wheel: tyre, tread blocks, recessed rim with a hub and lug nuts."""
    cx, cy, cz = center
    tyre = cylinder(prefix + 'Tyre', center, radius - .9, width, axis, 'tyre', 48, 1.2)
    bake(tyre)
    cuts = []
    for s in (-1, 1):
        # A 1.5-unit recess in each sidewall: the rim sits in it instead of hiding inside a solid disc.
        c = (cx, cy + s * width / 2, cz) if axis == 'y' else (cx + s * width / 2, cy, cz)
        cuts.append(cylinder(f'{prefix}Cut{s}', c, radius * .66, 3, axis, 'tyre', 32))
    for c in cuts:
        m = tyre.modifiers.new('Recess', 'BOOLEAN')
        m.operation = 'DIFFERENCE'
        m.object = c
    bake(tyre)
    for c in cuts:
        bpy.data.objects.remove(c, do_unlink=True)
    parts = [tyre]
    blocks = 32
    if tread:
        for i in range(blocks):
            a = i * math.tau / blocks
            # Tread lugs: two staggered rows of low blocks (mud-terrain pattern), 0.9 above the carcass.
            for row in (-1, 1):
                off = row * width * .24 + (.8 if i % 2 else -.8)
                size = (2.4, width * .42, 1.8)
                if axis == 'y':
                    pos = (cx + (radius - .9) * math.sin(a), cy + off, cz + (radius - .9) * math.cos(a))
                    o = box(f'{prefix}Lug{i}_{row}', [p - s / 2 for p, s in zip(pos, size)],
                            [p + s / 2 for p, s in zip(pos, size)], 'tyre', .3)
                    o.rotation_euler = (0, a, 0)
                else:
                    pos = (cx + off, cy + (radius - .9) * math.sin(a), cz + (radius - .9) * math.cos(a))
                    sz = (size[1], size[0], size[2])
                    o = box(f'{prefix}Lug{i}_{row}', [p - s / 2 for p, s in zip(pos, sz)],
                            [p + s / 2 for p, s in zip(pos, sz)], 'tyre', .3)
                    o.rotation_euler = (-a, 0, 0)
                parts.append(o)
    for s in (-1, 1):
        # Rim disc in the recess (its face 0.6 below the sidewall), hub and five lug nuts proud of it.
        shift = s * (width / 2 - 1.1)
        rim_c = (cx, cy + shift, cz) if axis == 'y' else (cx + shift, cy, cz)
        parts.append(cylinder(f'{prefix}Rim{s}', rim_c, radius * .62, 1.0, axis, 'rim', 32, .25))
        hub_shift = s * (width / 2 - .3)
        hub_c = (cx, cy + hub_shift, cz) if axis == 'y' else (cx + hub_shift, cy, cz)
        parts.append(cylinder(f'{prefix}Hub{s}', hub_c, 2.6, 1.2, axis, 'trim', 20, .3))
        for i in range(6):
            # Six dark vent holes of a steel wheel, just proud of the rim face.
            a = (i + .5) * math.tau / 6
            u, v = 7.2 * math.sin(a), 7.2 * math.cos(a)
            hole = (cx + u, cy + s * (width / 2 - .55), cz + v) if axis == 'y' else (cx + s * (width / 2 - .55), cy + u, cz + v)
            parts.append(cylinder(f'{prefix}Vent{s}_{i}', hole, 1.5, .3, axis, 'trim', 12))
        for i in range(5):
            a = i * math.tau / 5
            u, v = 4.6 * math.sin(a), 4.6 * math.cos(a)
            nut = (cx + u, cy + s * (width / 2 - .45), cz + v) if axis == 'y' else (cx + s * (width / 2 - .45), cy + u, cz + v)
            parts.append(cylinder(f'{prefix}Nut{s}_{i}', nut, .7, 1.2, axis, 'rim', 6))
    return parts


def car():
    # Lower body: one bevelled prism along the side profile, wheel arches cut out.
    lower = prism('Lower', [(-91, 24), (93, 24), (95.5, 44), (93, 54), (40, 58), (-88, 58), (-91, 52)], -W, W, 'paint', 2.4)
    bake(lower)
    cutters = [cylinder(f'ArchCut{i}', (x, y, R), R + 3.2, 20, 'y', 'trim', 40) for i, (x, y) in enumerate(WHEELS)]
    for c in cutters:
        m = lower.modifiers.new('Arch', 'BOOLEAN')
        m.operation = 'DIFFERENCE'
        m.object = c
    bake(lower)
    for c in cutters:
        bpy.data.objects.remove(c, do_unlink=True)
    # Cabin with tumblehome (narrower than the body), two-tone roof.
    prism('Cabin', [(38.5, 58), (14, 83), (-84, 83), (-88, 58)], -33.5, 33.5, 'paint', 2.2)
    box('RoofPanel', (-82, -31, 82.6), (11, 31, 84.4), 'roof', .8)
    for i, (x, y) in enumerate(WHEELS):
        arch_flare(f'Flare{i}', x, 1 if y > 0 else -1)
    # Windows: a dark panel set into each glass area (opaque glossy, no translucent sorting in game).
    for s in (-1, 1):
        y = s * 33.5
        for i, poly in enumerate([[(33, 60.5), (15.8, 79.5), (-12, 79.5), (-12, 60.5)],
                                  [(-16, 60.5), (-16, 79.5), (-50, 79.5), (-50, 60.5)],
                                  [(-54, 60.5), (-54, 79.5), (-83.5, 79.5), (-84.7, 60.5)]]):
            outer_only(plate(f'Window{s}_{i}', poly, y + s * .9, 1.2, 'window'), (0, s, 0))
        # Door seams, handles, mirrors, side step.
        for x in (30, -14, -48):
            box(f'Seam{s}_{x}', (x - .3, s * W - .4, 37), (x + .3, s * W + .4, 57), 'trim')
        for x in (6, -30):
            box(f'Handle{s}_{x}', (x - 3, s * (W + .3) - .7, 51), (x + 3, s * (W + .3) + .7, 52.6), 'rim', .3)
        tube(f'MirrorArm{s}', (34, s * 33, 60.5), (37, s * 39.5, 63), .6)
        box(f'Mirror{s}', (35, s * 39 - 2.8, 61), (39.5, s * 39 + 2.8, 66.5), 'trim', .9)
        box(f'Step{s}', (-40, min(s * 35, s * 40.5), 24.5), (24, max(s * 35, s * 40.5), 26.5), 'trim', .4)
    # Windscreen and rear window on the slanted faces, set off by 1 unit along each face normal.
    outer_only(slanted('Windscreen', (38.5, 58), (14, 83), .1, .9, 29, 1), (25, 0, 24.5))
    outer_only(slanted('RearWindow', (-88, 58), (-84, 83), .14, .88, 25, -1), (-25, 0, 4))
    # Interior for the hood camera (seen only through the glass from inside): dashboard, steering wheel on the
    # driver's side, A-pillars along the windscreen. The roof panel's underside is the headliner.
    box('Dashboard', (22, -32, 58), (34, 32, 62), 'trim', .8)
    box('DashTop', (24, -31, 62), (31, -6, 63.5), 'trim', .5)
    tube('SteeringColumn', (30, 17, 61), (19.5, 17, 64.8), 1.0)
    bpy.ops.mesh.primitive_torus_add(major_radius=6.5, minor_radius=.9, major_segments=32, minor_segments=8,
                                     location=(19, 17, 65), rotation=(0, math.radians(-62), 0))
    wheel_rim = bpy.context.object
    wheel_rim.name = 'SteeringWheel'
    wheel_rim.data.materials.append(mats['trim'])
    for s in (-1, 1):
        tube(f'APillar{s}', (35.5, s * 32, 59.5), (13, s * 31.5, 82), 1.4)
    # Front: grille with slats, round headlights in chrome bezels, amber indicators, bumper and bull bar.
    box('Grille', (94.6, -17, 33), (96.2, 17, 44.5), 'trim', .4)
    for z in (35.5, 38.5, 41.5):
        box(f'Slat{z}', (96, -15.5, z - .45), (96.8, 15.5, z + .45), 'rim', .2)
    for s in (-1, 1):
        cylinder(f'HeadBezel{s}', (95.6, s * 25, 46), 6, 1.6, 'x', 'rim', 28, .3)
        cylinder(f'Headlight{s}', (96.3, s * 25, 46), 4.8, 1.0, 'x', 'lamp', 28)
        box(f'Indicator{s}', (95.2, s * 25 - 3, 38.5), (96.4, s * 25 + 3, 41), 'amber', .3)
        box(f'Taillight{s}', (-92.2, s * 32 - 2.5, 40), (-90.7, s * 32 + 2.5, 52), 'tail', .3)
        box(f'Reverse{s}', (-92.2, s * 32 - 2.5, 36.5), (-90.7, s * 32 + 2.5, 39.3), 'lamp', .3)
    box('FrontBumper', (92.5, -37.5, 24), (100, 37.5, 33.5), 'trim', 1.2)
    for y in (-15, 15):
        tube(f'BarUp{y}', (99.5, y, 33), (101.5, y, 51), 1.3)
    tube('BarTop', (101.5, -15, 51), (101.5, 15, 51), 1.3)
    tube('BarMid', (100.6, -15, 42), (100.6, 15, 42), 1.1)
    box('Plate', (100.2, -7, 27), (100.8, 7, 31), 'roof', .2)
    box('RearBumper', (-96, -37, 24), (-90.5, 37, 32), 'trim', 1.2)
    # Spare wheel on the rear door, roof rack, snorkel.
    wheel('Spare', (-96.5, -6, 47), 'x', 14.5, 9, tread=False)
    box('SpareMount', (-92, -10, 43), (-90.5, -2, 51), 'trim')
    for y in (-29, 29):
        tube(f'RackRail{y}', (-78, y, 88.5), (6, y, 88.5), .9)
        for x in (-74, -30, 2):
            tube(f'RackLeg{y}_{x}', (x, y, 84.2), (x, y, 88.5), .7)
    for x in (-66, -44, -22, 0):
        tube(f'RackBar{x}', (x, -29, 88.5), (x, 29, 88.5), .7)
    tube('SnorkelLow', (35, -37.6, 46), (35, -37.6, 60), 1.8)
    tube('SnorkelHigh', (35, -37.6, 60), (24, -35.5, 86), 1.8)
    box('SnorkelHead', (22.5, -38, 85), (28.5, -33, 90), 'trim', .8)
    # Underbody skid plate (visual; the collision floor is 24).
    box('Skid', (-70, -26, 22.5), (78, 26, 24.5), 'trim', .6)
    # Collision: lower body, cabin, front bumper/bull bar. No tyres: the suspension holds the body.
    box('Lower_col', (-96, -37, 24), (96, 37, 58), 'trim')
    box('Cabin_col', (-88, -33.5, 58), (30, 33.5, 86), 'trim')
    box('Bumper_col', (95, -37.5, 24), (102.5, 37.5, 52), 'trim')


if PARAMS['part'] == 'wheel':
    wheel('')
    cylinder('Wheel_col', (0, 0, 0), R, 11, 'y', 'tyre', 24)
elif PARAMS['part'] == 'car':
    car()
else:
    raise ValueError('unknown vehicle part')
