"""Beam segment for scripted shots along +X from x=0 to 128 (inches): a hot core inside a soft halo.
Both materials are additive (they only add light), so dimming the entity colour fades the beam smoothly."""
import math
import bpy

mats = {}
for key in ('core', 'halo'):
    m = bpy.data.materials.new(key)
    m['source_vmat'] = PARAMS[key]
    mats[key] = m
for key, radius, sides in (('core', .45, 8), ('halo', 1.2, 12)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=sides, radius=radius, depth=128, location=(64, 0, 0),
                                        rotation=(0, math.pi / 2, 0))
    rod = bpy.context.object
    rod.name = 'beam_' + key
    rod.data.materials.append(mats[key])
