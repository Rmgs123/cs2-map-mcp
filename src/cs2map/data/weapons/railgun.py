"""Railgun: twin rails, four coils, removable battery and shoulder stock. Inches, front +X, base Z=0."""
import math
import bpy
from mathutils import Vector


def material(name, path, color, metal=0):
    mat = bpy.data.materials.new(name)
    mat['source_vmat'] = path
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Metallic'].default_value = metal
    node.inputs['Roughness'].default_value = .3
    if name == 'glow':
        node.inputs['Emission Color'].default_value = (*color, 1)
        node.inputs['Emission Strength'].default_value = 3
    return mat


dark = material('housing', PARAMS['dark'], (.055, .075, .095), .7)
steel = material('rails', PARAMS['steel'], (.32, .4, .46), .85)
copper = material('coils', PARAMS['copper'], (.55, .23, .08), .8)
glow = material('glow', PARAMS['glow'], (.035, .4, 1))


def box(name, center, size, mat, bevel=.25):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = Vector(size)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    modifier = obj.modifiers.new('machined_edges', 'BEVEL')
    modifier.width = bevel
    modifier.segments = 2
    return obj


box('receiver', (-7, 0, 12), (19, 10, 8), dark, .6)
box('top_spine', (11, 0, 17), (48, 3, 2), steel)
box('battery', (-4, 0, 6), (14, 8, 4), dark, .45)
box('grip', (-14, 0, 4), (5, 5, 8), dark, .5)
box('stock_strut', (-24, 0, 11), (15, 4, 4), steel)
box('shoulder_pad', (-33, 0, 10), (4, 8, 13), dark, .6)
box('stock_cheek', (-24, 0, 16), (14, 6, 3), dark)
box('trigger_front', (-9, 0, 2), (1.5, 4, 4), steel)
box('trigger_floor', (-11.5, 0, .75), (4, 4, 1.5), steel)
box('rail_left', (22, -3.5, 11), (44, 2, 3), steel)
box('rail_right', (22, 3.5, 11), (44, 2, 3), steel)
box('muzzle_bridge', (42, 0, 7), (4, 11, 2), dark)
for side in (-1, 1):
    box('receiver_glow', (-7, side * 5.1, 12), (12, .4, 2), glow, .12)
    box('battery_charge', (-4, side * 4.1, 6), (10, .4, 1.5), glow, .1)
    box('rail_glow', (24, side * 4.65, 11), (34, .4, 1), glow, .1)
for index, x in enumerate((6, 14, 22, 30)):
    bpy.ops.mesh.primitive_torus_add(major_segments=24, minor_segments=8,
        location=(x, 0, 11), rotation=(0, math.pi / 2, 0), major_radius=5.6, minor_radius=.7)
    ring = bpy.context.object
    ring.name = 'coil_' + str(index)
    ring.data.materials.append(copper)
    for face in ring.data.polygons:
        face.use_smooth = True
    box('coil_mount_' + str(index), (x, 0, 5), (3, 8, 2), dark)
    box('coil_status_' + str(index), (x, -6.1, 11), (1.3, .5, 2.4), glow, .1)
box('sight_rear', (-13, 0, 19), (3, 3, 2), dark)
box('sight_front', (33, 0, 19), (2, 2, 2), dark)
# Weapon markers (model_build weapon=...): where the right hand holds it and where the shot leaves the barrel.
for marker, at in (('grip', (-13, 0, 5)), ('muzzle', (44, 0, 11))):
    bpy.ops.object.empty_add(location=at)
    bpy.context.object.name = marker
