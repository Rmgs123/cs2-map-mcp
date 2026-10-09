"""Zapper: a compact sci-fi gadget pistol for the taser base. Inches, front +X, ~11 long.
Markers: grip (the right hand), muzzle (between the prongs)."""
import math
import bpy
from mathutils import Vector


def material(name, path, color):
    mat = bpy.data.materials.new(name)
    mat['source_vmat'] = path
    mat.diffuse_color = (*color, 1)
    return mat


dark = material('dark', PARAMS['dark'], (.05, .055, .065))
metal = material('metal', PARAMS['metal'], (.45, .48, .52))
copper = material('copper', PARAMS['copper'], (.55, .25, .1))
glow = material('glow', PARAMS['glow'], (.2, .8, 1))


def box(name, center, size, mat, bevel=.15, tilt=0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center, rotation=(0, math.radians(tilt), 0))
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = Vector(size)
    obj.data.materials.append(mat)
    if bevel:
        m = obj.modifiers.new('edges', 'BEVEL')
        m.width = bevel
        m.segments = 2
    return obj


def rod(name, x0, x1, r, z, mat, sides=20):
    bpy.ops.mesh.primitive_cylinder_add(vertices=sides, radius=r, depth=x1 - x0, location=((x0 + x1) / 2, 0, z),
                                        rotation=(0, math.pi / 2, 0))
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    for f in obj.data.polygons:
        f.use_smooth = True
    return obj


box('grip', (.2, 0, 2.2), (1.6, 1.5, 4.6), dark, .3, tilt=-14)
box('body', (2.2, 0, 5.2), (8, 2, 2.4), dark, .35)
box('top_rail', (2.6, 0, 6.6), (6, .9, .5), metal, .1)
box('trigger_guard', (1.8, 0, 3.2), (2.4, .35, .3), metal, .05)
box('trigger', (1.6, 0, 3.7), (.3, .3, .9), metal, .05)
rod('emitter', 6, 8.6, .75, 5.2, metal)
bpy.ops.mesh.primitive_torus_add(major_segments=24, minor_segments=8, location=(7.3, 0, 5.2),
                                 rotation=(0, math.pi / 2, 0), major_radius=1.05, minor_radius=.22)
coil = bpy.context.object
coil.name = 'coil'
coil.data.materials.append(copper)
for y in (-.55, .55):
    box(f'prong{y}', (9.3, y, 5.2), (1.6, .3, .5), metal, .05)
    box(f'glow{y}', (2.4, y * 1.85, 5.3), (5.5, .1, .45), glow, 0)
box('power_cell', (-.9, 0, 5.4), (1.4, 1.6, 1.6), copper, .2)
for marker, at in (('grip', (0, 0, 3)), ('muzzle', (10, 0, 5.2))):
    bpy.ops.object.empty_add(location=at)
    bpy.context.object.name = marker
