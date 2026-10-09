"""Render named views of an author-space .blend, with real image-texture shading."""
import json
import sys
from pathlib import Path
import bpy
from mathutils import Vector, Matrix


def main():
    source, output, size, views_json = sys.argv[sys.argv.index('--') + 1:]
    size = int(size)
    views = json.loads(views_json)
    if Path(source).suffix.lower() == '.blend':
        bpy.ops.wm.open_mainfile(filepath=source)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=source)
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and not o.hide_render]
    if not meshes:
        raise ValueError('no visible render mesh')
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    centre = (hi + lo) * .5
    span = max(hi[i]-lo[i] for i in range(3))
    if span <= 0:
        raise ValueError('degenerate bounds')
    transform = Matrix.Scale(2/span, 4) @ Matrix.Translation(-centre)
    for obj in meshes:
        obj.matrix_world = transform @ obj.matrix_world
    bpy.ops.object.camera_add()
    cam = bpy.context.object
    cam.data.type = 'ORTHO'
    cam.data.ortho_scale = 3.1
    scene = bpy.context.scene
    scene.camera = cam
    scene.world = bpy.data.worlds.new('ModelPreviewWorld')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (.1, .1, .1, 1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = .8
    for position, energy, light_size in [((3,-4,6),600,4),((-4,3,3),400,3)]:
        bpy.ops.object.light_add(type='AREA', location=position)
        light = bpy.context.object
        light.data.energy = energy
        light.data.shape = 'DISK'
        light.data.size = light_size
        light.rotation_euler = (-light.location).to_track_quat('-Z','Y').to_euler()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = 'Standard'
    scene.render.image_settings.file_format = 'PNG'
    directions = {'front':(5,0,0), 'side':(0,-5,0), 'top':(0,0,5), 'iso':(4,-5,3)}
    paths = {}
    for view in views:
        cam.location = directions[view]
        cam.rotation_euler = (-cam.location).to_track_quat('-Z','Y').to_euler()
        path = Path(output).with_name(Path(output).stem + '_' + view + '.png')
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths[view] = str(path)
    print('JSON>' + json.dumps({'previews': paths}))


if __name__ == '__main__':
    main()
