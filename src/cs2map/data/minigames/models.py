"""Small authored props for the mini-games; all dimensions are CS2 inches."""
import math
import bpy
from mathutils import Vector

part = PARAMS['part']
colors = {
    'button': (.02, .16, .38, 1), 'target_red': (.68, .03, .02, 1),
    'target_white': (.9, .84, .72, 1), 'ring_blue': (.02, .42, .9, 1),
    'collect_gold': (.95, .58, .03, 1),
}
mats = {}
for key, color in colors.items():
    mat = bpy.data.materials.new(key)
    mat['source_vmat'] = f'materials/mcp_m/m9_{key}/m9_{key}.vmat'
    mat.diffuse_color = color
    mats[key] = mat

def finish(o, name, mat, bevel=.2):
    o.name = name; o.data.materials.append(mats[mat])
    if bevel:
        b = o.modifiers.new('soft edges', 'BEVEL'); b.width = bevel; b.segments = 2
        o.modifiers.new('weighted normals', 'WEIGHTED_NORMAL')
    return o

def box(name, pos, size, mat, bevel=.2):
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    o = bpy.context.object; o.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(o, name, mat, bevel)

def cyl(name, pos, radius, depth, mat, axis=(0,0,1), sides=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=sides, radius=radius, depth=depth, location=pos)
    o = bpy.context.object; o.rotation_euler = Vector(axis).to_track_quat('Z', 'Y').to_euler()
    return finish(o, name, mat, .2)

if part == 'button':
    box('Base', (0, 0, 8), (20, 20, 16), 'button', 1)
    cyl('Button', (0, 0, 18), 6, 4, 'collect_gold', sides=24)
    box('Base_col', (0, 0, 8), (20, 20, 16), 'button', 0)
elif part == 'target':
    # A shield with concentric rings, hinged around its lower edge in the physics prop.
    cyl('Shield', (0, 0, 32), 32, 5, 'target_red', axis=(0,1,0), sides=40)
    cyl('Middle', (0, -3, 32), 21, 2, 'target_white', axis=(0,1,0), sides=40)
    cyl('Bullseye', (0, -5, 32), 10, 2, 'target_red', axis=(0,1,0), sides=40)
    cyl('Shield_col', (0, 0, 32), 32, 6, 'target_red', axis=(0,1,0), sides=32)
elif part == 'stand':
    box('Base', (0, 0, 2), (24, 16, 4), 'target_red', .5)
    box('Stand', (0, 0, 7), (5, 5, 6), 'target_red', .5)
    box('Stand_col', (0, 0, 5), (24, 16, 10), 'target_red', 0)
elif part == 'ring':
    # Vertical checkpoint ring; its origin is on the floor and the mesh spans z=0..128.
    bpy.ops.mesh.primitive_torus_add(major_radius=56, minor_radius=8, major_segments=48,
                                     minor_segments=12, location=(0, 0, 64), rotation=(0, math.pi/2, 0))
    finish(bpy.context.object, 'Ring', 'ring_blue', .1)
    # The centre is open so a player or vehicle can pass through it.
    box('FootL', (-2, -48, 4), (8, 16, 8), 'ring_blue', .5)
    box('FootR', (-2, 48, 4), (8, 16, 8), 'ring_blue', .5)
elif part == 'collect':
    box('Foot', (0,0,4), (8,8,8), 'collect_gold', .4)
    cyl('Gem', (0, 0, 24), 12, 32, 'collect_gold', axis=(0,0,1), sides=6)
    cyl('Glow', (0, 0, 24), 15, 2, 'ring_blue', sides=24)
    box('Gem_col', (0, 0, 24), (20, 20, 34), 'collect_gold', 0)
else:
    raise ValueError('unknown mini-game part')
