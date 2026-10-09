"""Render stock CS2 models (glb from Source2Viewer-CLI) for the prop atlas.

argv after ``--``: a JSON file with a list of jobs
    {"glb": path, "out": png path prefix, "views": ["front"] or ["front", "back"], "size": px}
Prints ``JSON>{"results": [...]}``; one result per job, in order:
    {"ok", "reason", "lo", "hi" (Source inches), "textures", "coverage", "images": {view: png}}

Blender world = Source model space: the glTF importer gives Blender (x, y, z) =
Source (y, -x, z) in metres, so the import is turned +90 degrees about Z.
Each model is rendered in a fresh empty scene; the process exits after the list.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

INCH = 0.0254
DIRECTIONS = {'front': Vector((1.0, -0.8, 0.65)), 'back': Vector((-1.0, 0.8, 0.65))}


def material(name, color, emission=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Roughness'].default_value = 1.0
    if emission:
        bsdf.inputs['Emission Color'].default_value = color
        bsdf.inputs['Emission Strength'].default_value = emission
    return mat


def mesh_object(name, verts, faces, mat):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.materials.append(mat)
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box(name, lo, hi, mat):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return mesh_object(name, v, f, mat)


def world_bounds(objects):
    pts = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


def count_textures(meshes):
    images = set()
    for obj in meshes:
        for slot in obj.material_slots:
            mat = slot.material
            if mat and mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image and node.image.size[0] > 0:
                        images.add(node.image.name)
    return len(images)


def scene_setup(size):
    scene = bpy.context.scene
    for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    try:
        scene.eevee.taa_render_samples = 16
    except AttributeError:
        pass
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = 'Standard'
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    world = bpy.data.worlds.new('atlas')
    world.use_nodes = True
    bg = world.node_tree.nodes['Background']
    bg.inputs['Color'].default_value = (0.62, 0.66, 0.72, 1)
    bg.inputs['Strength'].default_value = 0.85
    scene.world = world
    sun = bpy.data.objects.new('sun', bpy.data.lights.new('sun', 'SUN'))
    sun.data.energy = 2.4
    sun.data.angle = math.radians(8)
    sun.rotation_euler = (math.radians(48), 0, math.radians(35))
    scene.collection.objects.link(sun)
    return scene


def frame(cam, objects, direction):
    """Orthographic camera along direction that fits every corner of objects."""
    lo, hi = world_bounds(objects)
    centre = (lo + hi) / 2
    span = max((hi - lo).length, 0.05)
    cam.location = centre + direction.normalized() * span * 4
    cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    pts = [inv @ (o.matrix_world @ Vector(c)) for o in objects for c in o.bound_box]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    cam.data.ortho_scale = max(width, height, 0.02) * 1.08
    cam.data.shift_x = (max(xs) + min(xs)) / 2 / cam.data.ortho_scale
    cam.data.shift_y = (max(ys) + min(ys)) / 2 / cam.data.ortho_scale
    cam.data.clip_start = 0.01
    cam.data.clip_end = span * 10


def coverage(scene, cam, meshes, helpers, path):
    """Fraction of a small transparent render covered by the model alone."""
    hidden = [h.hide_render for h in helpers]
    for h in helpers:
        h.hide_render = True
    old = scene.render.resolution_x
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = 48
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    alpha = list(img.pixels)[3::4]
    bpy.data.images.remove(img)
    scene.render.film_transparent = False
    scene.render.resolution_x = scene.render.resolution_y = old
    for h, was in zip(helpers, hidden):
        h.hide_render = was
    return sum(a > 0.5 for a in alpha) / max(1, len(alpha))


def render(job):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = scene_setup(int(job['size']))
    bpy.ops.import_scene.gltf(filepath=job['glb'])
    meshes = [o for o in scene.objects if o.type == 'MESH']
    if not meshes:
        return {'ok': False, 'reason': 'no mesh in export'}
    # CS2 stores wind/animation data in vertex colours; glTF multiplies them into the albedo.
    for obj in meshes:
        for attr in obj.data.color_attributes:
            attr.data.foreach_set('color', [1.0] * (4 * len(attr.data)))
    turn = Matrix.Rotation(math.radians(90), 4, 'Z')
    for obj in scene.objects:
        if obj.parent is None and obj.type not in ('LIGHT', 'CAMERA'):
            obj.matrix_world = turn @ obj.matrix_world
    bpy.context.view_layer.update()
    lo, hi = world_bounds(meshes)
    size = hi - lo
    if max(size) < 0.25 * INCH:
        return {'ok': False, 'reason': 'zero size'}
    textures = count_textures(meshes)
    centre = (lo + hi) / 2
    floor = mesh_object('floor', [(-1e3, -1e3, lo.z), (1e3, -1e3, lo.z), (1e3, 1e3, lo.z), (-1e3, 1e3, lo.z)],
                        [(0, 1, 2, 3)], material('floor', (0.33, 0.33, 0.34, 1)))
    gap = 16 * INCH
    marker = box('player', (centre.x - 16 * INCH, hi.y + gap, lo.z),
                 (centre.x + 16 * INCH, hi.y + gap + 32 * INCH, lo.z + 72 * INCH),
                 material('player', (0.22, 0.48, 0.95, 1)))
    biggest = max(size)
    length = size.x / 2 + max(4 * INCH, 0.35 * biggest)
    width = max(0.6 * INCH, length * 0.07)
    z = lo.z + 0.3 * INCH
    x0, x1, xh = centre.x, centre.x + length, centre.x + length - width * 2.2
    arrow = mesh_object('arrow_x', [(x0, centre.y - width / 2, z), (xh, centre.y - width / 2, z),
                                    (xh, centre.y - width * 1.6, z), (x1, centre.y, z),
                                    (xh, centre.y + width * 1.6, z), (xh, centre.y + width / 2, z),
                                    (x0, centre.y + width / 2, z)],
                        [(0, 1, 5, 6), (2, 3, 4)], material('arrow', (0.95, 0.12, 0.08, 1), emission=1.5))
    cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
    cam.data.type = 'ORTHO'
    scene.collection.objects.link(cam)
    scene.camera = cam
    images = {}
    cover = None
    # Small parts (< 24 units) are framed alone: next to a 72-unit player they would be a few pixels.
    framed = meshes + [arrow] + ([marker] if biggest >= 24 * INCH else [])
    if biggest < 24 * INCH:
        marker.hide_render = True
    for view in job['views']:
        frame(cam, framed, DIRECTIONS[view])
        if cover is None:
            cover = coverage(scene, cam, meshes, [floor, marker, arrow], job['out'] + '_mask.png')
        path = job['out'] + '_' + view + '.png'
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        images[view] = path
    result = {'ok': True, 'lo': [lo.x / INCH, lo.y / INCH, lo.z / INCH], 'hi': [hi.x / INCH, hi.y / INCH, hi.z / INCH],
              'textures': textures, 'coverage': round(cover, 4), 'images': images}
    if cover < 0.002:
        result.update(ok=False, reason='model invisible in render')
    elif textures == 0:
        result.update(ok=False, reason='no textures exported')
    return result


def main():
    jobs = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    results = []
    for job in jobs:
        try:
            results.append(render(job))
        except Exception as exc:  # one bad model must not lose the whole sheet
            results.append({'ok': False, 'reason': f'render failed: {type(exc).__name__}: {exc}'[:200]})
    print('JSON>' + json.dumps({'results': results}))


if __name__ == '__main__':
    main()
