"""Three authored turret parts. +X is the barrel direction, +Z is up; all dimensions are inches."""
import math
import bpy
from mathutils import Vector

colors = {
    'dark': (.035, .05, .06, 1), 'metal': (.18, .23, .25, 1),
    'accent': (.8, .25, .03, 1), 'glass': (.02, .32, .45, .55),
}
mats = {}
for key, color in colors.items():
    mat = bpy.data.materials.new(key)
    mat['source_vmat'] = f'materials/mcp_t/t9_{key}/t9_{key}.vmat'
    mat.diffuse_color = color
    mats[key] = mat

def finish(obj, name, mat, bevel=.4):
    obj.name = name
    obj.data.materials.append(mats[mat])
    if bevel:
        mod = obj.modifiers.new('Machined edges', 'BEVEL'); mod.width = bevel; mod.segments = 3
        obj.modifiers.new('Weighted normals', 'WEIGHTED_NORMAL')
    return obj

def box(name, pos, size, mat='metal', bevel=.4):
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    o = bpy.context.object; o.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(o, name, mat, bevel)

def cyl(name, pos, radius, depth, mat='metal', axis=(0, 0, 1), sides=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=sides, radius=radius, depth=depth, location=pos)
    o = bpy.context.object; o.rotation_euler = Vector(axis).to_track_quat('Z', 'Y').to_euler()
    return finish(o, name, mat, .25)

def beam(name, a, b, width, mat='metal'):
    a, b = Vector(a), Vector(b); d = b - a
    o = box(name, (a + b) / 2, (width, width, d.length), mat, .25)
    o.rotation_euler = d.to_track_quat('Z', 'Y').to_euler(); return o

part = PARAMS['part']
if part == 'base':
    cyl('Base', (0, 0, 8), 30, 16, 'dark')
    cyl('BaseRing', (0, 0, 18), 25, 4, 'accent')
    for y in (-22, 22): box('Foot', (-7, y, 3), (40, 8, 6), 'metal', .8)
    # One simple convex collision hull; it encloses the fixed base without reaching the barrel.
    cyl('Base_col', (0, 0, 9), 30, 18, 'dark', sides=24)
elif part == 'platform':
    cyl('Turntable', (0, 0, 4), 22, 8, 'metal')
    box('Shoulder', (8, 0, 14), (27, 32, 12), 'dark', 1)
    for y in (-12, 12): cyl('Bearing', (0, y, 14), 5, 5, 'accent', axis=(0, 1, 0))
    # Raised grips make the E target visible to a standing player's eye ray.
    for y in (-16, 16):
        box('GripPost', (-8, y, 37), (5, 5, 46), 'metal', .5)
        box('Grip', (-8, y, 60), (10, 5, 4), 'accent', .5)
    box('Platform_col', (6, 0, 34), (40, 38, 68), 'dark', 0)
elif part == 'barrel':
    # This object is authored with its origin at the pitch/yaw hinge (0,0,0).
    box('GunBlock', (9, 0, 0), (18, 26, 20), 'dark', 1)
    beam('BarrelA', (13, -8, 0), (76, -8, 0), 5, 'metal')
    beam('BarrelB', (13, 8, 0), (76, 8, 0), 5, 'metal')
    for y in (-8, 8): cyl('Muzzle', (77, y, 0), 6, 6, 'accent', axis=(1, 0, 0))
    box('SightBridge', (44, 0, 3), (5, 21, 3), 'metal', .2)
    box('SightPost', (44, 0, 8), (3, 3, 8), 'metal', .2)
    box('Sight', (44, 0, 12), (28, 3, 3), 'glass', .2)
    # Separate convex hulls keep the two barrels and the block free of accidental gaps.
    box('Block_col', (9, 0, 0), (20, 28, 22), 'dark', 0)
    beam('BarrelA_col', (13, -8, 0), (76, -8, 0), 6, 'metal').modifiers.clear()
    beam('BarrelB_col', (13, 8, 0), (76, 8, 0), 6, 'metal').modifiers.clear()
else:
    raise ValueError('unknown turret part')
