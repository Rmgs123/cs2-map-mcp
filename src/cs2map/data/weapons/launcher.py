"""Rocket launcher: a shoulder tube with flared ends, pistol grip, fore grip, sight and a warning band.
Inches, front +X, tube axis at z=10. Markers: grip (right hand), muzzle (where the rocket leaves)."""
import math
import bpy
from mathutils import Vector


def material(name, path, color):
    mat = bpy.data.materials.new(name)
    mat['source_vmat'] = path
    mat.diffuse_color = (*color, 1)
    return mat


olive = material('olive', PARAMS['olive'], (.2, .24, .14))
dark = material('dark', PARAMS['dark'], (.06, .065, .07))
band = material('band', PARAMS['band'], (.8, .6, .08))


def tube(name, x0, x1, r0, r1, mat, z=10, sides=28):
    bpy.ops.mesh.primitive_cone_add(vertices=sides, radius1=r0, radius2=r1, depth=x1 - x0,
                                    location=((x0 + x1) / 2, 0, z), rotation=(0, math.pi / 2, 0))
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


def box(name, center, size, mat, bevel=.3):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = Vector(size)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    m = obj.modifiers.new('edges', 'BEVEL')
    m.width = bevel
    m.segments = 2
    return obj


tube('body', -18, 20, 2.9, 2.9, olive)
tube('muzzle_ring', 20, 23, 3.3, 3.5, dark)
tube('exhaust', -23, -18, 3.8, 3.1, dark)
tube('band', 12, 14, 3.0, 3.0, band)
tube('rear_band', -14, -12.5, 3.0, 3.0, band)
box('grip', (-3, 0, 4.2), (2.6, 2.2, 7), dark)
box('trigger_guard', (0, 0, 6), (4, .6, .6), dark, .1)
box('fore_grip', (9, 0, 4.8), (2.2, 2, 5.5), dark)
box('sight_base', (2, -3.6, 12), (5, 1.4, 2.2), dark)
box('sight', (2.5, -4.6, 13.2), (2.2, 1.2, 2.6), dark)
box('shoulder', (-10, 0, 6.6), (6, 2.4, 1.6), dark)
for marker, at in (('grip', (-3, 0, 6)), ('muzzle', (23, 0, 10))):
    bpy.ops.object.empty_add(location=at)
    bpy.context.object.name = marker
