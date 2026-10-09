"""Offline author-space assembly sheet: hinges and labelled axes, no game required."""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

source, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
bpy.ops.wm.read_factory_settings(use_empty=True)
for part, height in [('base', 0), ('platform', 20), ('barrel', 34)]:
    old = set(bpy.data.objects)
    exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), {'PARAMS': {'part': part}})
    for obj in set(bpy.data.objects) - old:
        obj.location.z += height
        if obj.name.endswith('_col') or '_col.' in obj.name:
            obj.hide_render = True

def material(name, color):
    m = bpy.data.materials.new(name); m.diffuse_color = (*color, 1); return m

def rod(a, b, mat, radius=.6):
    a, b = Vector(a), Vector(b)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius, depth=(b-a).length, location=(a+b)/2)
    o = bpy.context.object; o.rotation_euler = (b-a).to_track_quat('Z', 'Y').to_euler(); o.data.materials.append(mat)

origin = (-40, -40, 0)
axes = [('X', (100, -40, 0), material('axis X', (1, .12, .08))),
        ('Y', (-40, 55, 0), material('axis Y', (.15, .65, .25))),
        ('Z', (-40, -40, 100), material('axis Z', (.15, .45, 1)))]
texts = []
for name, end, mat in axes:
    rod(origin, end, mat)
    bpy.ops.object.text_add(location=end)
    o = bpy.context.object; o.data.body = '+' + name; o.data.size = 6; o.data.materials.append(mat); texts.append(o)
for height in (20, 34):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=2, location=(0, -22, height))
    bpy.context.object.data.materials.append(material('hinge', (1, .7, .1)))

scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'MATERIAL'
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.display.shading.background_type = 'WORLD'
scene.world = bpy.data.worlds.new('Turret preview')
scene.world.color = (.12, .14, .17)
scene.render.resolution_x = scene.render.resolution_y = 480
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
bpy.ops.object.camera_add(); cam = bpy.context.object; cam.data.type = 'ORTHO'; cam.data.ortho_scale = 175
scene.camera = cam; target = Vector((20, 0, 42))
files = []
for view, pos in [('iso', (180, -230, 170)), ('side', (20, -280, 55)), ('top', (20, 0, 320))]:
    cam.location = pos; cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
    for text in texts: text.rotation_euler = cam.rotation_euler
    file = output / (view + '.png'); scene.render.filepath = str(file)
    bpy.ops.render.render(write_still=True); files.append(str(file))
print('JSON>' + json.dumps({'ok': True, 'files': files}))
