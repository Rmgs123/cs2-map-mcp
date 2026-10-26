"""Headless Blender pipeline.  Scripts author +X forward, +Z up, one CS2 inch/unit."""
from __future__ import annotations
import json
import math
import sys
import uuid
from pathlib import Path
import bpy
import bmesh
from mathutils import Matrix, Vector


def bounds(objects):
    dg = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in objects:
        ev = obj.evaluated_get(dg)
        points.extend(ev.matrix_world @ Vector(c) for c in ev.bound_box)
    if not points:
        raise ValueError('no render mesh')
    return [[round(float(fn(p[i] for p in points)), 6) for i in range(3)] for fn in (min, max)]


def import_file(path):
    ext = Path(path).suffix.lower()
    if ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path, forward_axis='NEGATIVE_Z', up_axis='Y')
    elif ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == '.blend':
        # Append the file's objects. Nothing in it runs: library loading executes no scripts, and factory
        # startup keeps auto-execution (Python drivers, registered text blocks) off.
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for obj in dst.objects:
            if obj is not None:
                bpy.context.scene.collection.objects.link(obj)
    else:
        raise ValueError('file must be FBX, OBJ, GLB, GLTF or BLEND')


def evaluated_meshes():
    # Bake all world transforms and modifiers into independent meshes. Static
    # props do not export armatures, parents, cameras or animated objects.
    dg = bpy.context.evaluated_depsgraph_get()
    meshes = []
    for src in list(bpy.context.scene.objects):
        if src.type != 'MESH':
            continue
        ev = src.evaluated_get(dg)
        mesh = bpy.data.meshes.new_from_object(ev, depsgraph=dg)
        mesh.transform(src.matrix_world)
        obj = bpy.data.objects.new(src.name + '_baked', mesh)
        bpy.context.collection.objects.link(obj)
        meshes.append((obj, src.name.lower().endswith('_col')))
    for src in list(bpy.context.scene.objects):
        if src not in [o for o, _ in meshes]:
            src.hide_render = True
            src.hide_set(True)
    return meshes


def material_records(render, out_dir):
    records, seen = [], {}
    for obj in render:
        if not obj.data.materials:
            mat = bpy.data.materials.new('unassigned')
            mat.diffuse_color = (0.5, 0.5, 0.5, 1)
            obj.data.materials.append(mat)
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                mat = bpy.data.materials.new('unassigned')
                slot.material = mat
            key = mat.as_pointer()
            if key in seen:
                slot.material = seen[key]
                continue
            original = str(mat.get("source_vmat") or mat.name)
            normalized = original.replace('\\', '/')
            # Source 2 stock materials are also addressed as models/... and
            # gloves/... paths in the game VPK.  Keep any explicit relative
            # ``*.vmat`` target verbatim; generated Blender materials have no
            # slash and continue through the generated VMAT path.
            if '/' in normalized and normalized.lower().endswith('.vmat'):
                target = normalized
            elif (normalized.startswith(('materials/', 'models/', 'gloves/'))
                  and '/' in normalized and '.' not in Path(normalized).name):
                # Blender may drop a trailing `.vmat` from a material datablock
                # name.  Stock namespace + slash is unambiguous here, and the
                # source path is restored for ModelDoc remapping.
                target = normalized + '.vmat'
            else:
                target = None
            token = f'm{len(records)}'
            copy = mat.copy()
            copy.name = 'a_mat_' + uuid.uuid4().hex[:12]
            token = copy.name
            seen[key] = copy
            slot.material = copy
            color = list(mat.diffuse_color)
            rough, metal, image = 0.7, 0.0, ''
            if mat.use_nodes:
                bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                if bsdf:
                    color = list(bsdf.inputs['Base Color'].default_value)
                    rough = float(bsdf.inputs['Roughness'].default_value)
                    metal = float(bsdf.inputs['Metallic'].default_value)
                tex = next((n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None)
                if tex:
                    img = tex.image
                    src = Path(bpy.path.abspath(img.filepath))
                    if img.packed_file or img.source == 'GENERATED':
                        src = out_dir / 'textures' / f'{token}.png'
                        src.parent.mkdir(parents=True, exist_ok=True)
                        img.save_render(str(src))
                        # Keep preview independent of the user's source file.
                        tex_copy = next((n for n in copy.node_tree.nodes if n.type == 'TEX_IMAGE'), None)
                        if tex_copy:
                            tex_copy.image = bpy.data.images.load(str(src), check_existing=True)
                    elif not src.is_file():
                        raise ValueError(f'missing Image Texture for {original}: {src}')
                    image = str(src.resolve())
            records.append({'name': original, 'token': token, 'material': target,
                            'image': image, 'color': color, 'roughness': rough, 'metalness': metal})
    return records


def ensure_uv(render):
    for obj in render:
        while len(obj.data.uv_layers) > 1:
            obj.data.uv_layers.remove(obj.data.uv_layers[-1])
        if not obj.data.uv_layers:
            bpy.ops.object.select_all(action='DESELECT')
            obj.hide_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
            bpy.ops.object.mode_set(mode='OBJECT')


def export_fbx(path, objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, object_types={'MESH'},
        apply_unit_scale=True, apply_scale_options='FBX_SCALE_NONE', bake_anim=False,
        add_leaf_bones=False, mesh_smooth_type='FACE', path_mode='STRIP', embed_textures=False,
        axis_forward='-Z', axis_up='Y')


def export_weapon_tris(render, out):
    """Weapon models are rigged by cs2map.weapon_rig on a stock skeleton: triangles in author space (inches,
    +X front, +Z up) with their material tokens, and the Empties `grip`/`muzzle` (any object with that name)."""
    materials, verts, tris = [], [], []
    for obj in render:
        mesh = obj.data
        mesh.calc_loop_triangles()
        uv = mesh.uv_layers.active.data if mesh.uv_layers.active else None
        tokens = []
        for slot in obj.material_slots:
            token = slot.material.name if slot.material else 'unassigned'
            if token not in materials:
                materials.append(token)
            tokens.append(materials.index(token))
        for tri in mesh.loop_triangles:
            ids = []
            for li in tri.loops:
                p = mesh.vertices[mesh.loops[li].vertex_index].co
                n = mesh.loops[li].normal if tri.use_smooth else tri.normal
                u, v = uv[li].uv if uv else (0.0, 0.0)
                verts.append([round(p.x, 4), round(p.y, 4), round(p.z, 4), round(n.x, 4), round(n.y, 4), round(n.z, 4),
                              round(u, 5), round(1 - v, 5)])
                ids.append(len(verts) - 1)
            tris.append(ids + [tokens[tri.material_index] if tokens else 0])
    markers = {}
    for obj in bpy.data.objects:
        key = obj.name.lower().split('.')[0]
        if key in ('grip', 'muzzle') and obj.type != 'MESH':
            markers[key] = [round(x, 4) for x in obj.matrix_world.translation]
    path = out.with_name(out.stem + '_tris.json')
    path.write_text(json.dumps({'materials': materials, 'verts': verts, 'tris': tris, 'markers': markers}),
                    encoding='utf-8')
    return path.name


def export_break_fragments(render, out):
    """Clip authored render geometry into up to eight closed spatial pieces.

    These are actual render fragments, not copies of a collision hull. This
    also works for single-mesh imports. Keep author materials and UV loops.
    """
    lo, hi = bounds(render)
    centre = [(a+b)/2 for a,b in zip(lo,hi)]
    files = []
    for cell in range(8):
        objects = []
        for source in render:
            bm = bmesh.new(); bm.from_mesh(source.data)
            for axis in range(3):
                point = [0.,0.,0.]; point[axis] = centre[axis]
                normal = [0.,0.,0.]; normal[axis] = 1.
                if not bm.verts:
                    break
                bmesh.ops.bisect_plane(bm, geom=list(bm.verts)+list(bm.edges)+list(bm.faces),
                    dist=.0001, plane_co=point, plane_no=normal,
                    clear_inner=bool(cell & (1<<axis)), clear_outer=not bool(cell & (1<<axis)))
                boundary = [e for e in bm.edges if e.is_boundary]
                if boundary:
                    bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
            if len(bm.verts) >= 4 and bm.faces:
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                data = source.data.copy(); bm.to_mesh(data)
                obj = bpy.data.objects.new(f'fragment_{cell}_{source.name}',data)
                bpy.context.collection.objects.link(obj); objects.append(obj)
            bm.free()
        if objects:
            path = out.with_name(f'{out.stem}_break_{cell}.fbx')
            export_fbx(path, objects); files.append(path.name)
            for obj in objects:
                obj.hide_render=True; obj.hide_set(True)
    if len(files) < 2:
        raise ValueError('breakable geometry must produce at least two nonempty fragments')
    return files


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    mode, source, output, collision, params_file = args[:5]
    out = Path(output).resolve()
    params = json.loads(Path(params_file).read_text(encoding='utf-8'))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = 'NONE'
    bpy.context.scene.unit_settings.scale_length = 1.0
    if mode == 'import':
        import_file(source)
    else:
        scope = {'bpy': bpy, 'PARAMS': params, 'params': params, '__file__': source, '__name__': '__model_script__'}
        exec(compile(Path(source).read_text(encoding='utf-8'), source, 'exec'), scope, scope)
        if callable(scope.get('build')):
            scope['build'](params)
    bpy.context.view_layer.update()
    pairs = evaluated_meshes()
    render = [o for o, col in pairs if not col]
    physics = [o for o, col in pairs if col]
    if not render:
        raise ValueError('script/import contains no render mesh')
    if mode == 'import':
        requested = params.get('scale', 'auto')
        # Blender's FBX importer presents our Source 2 FBX interchange units at
        # 0.4 of the author-space dimensions; 2.54 restores CS2 units before
        # the exporter/ModelDoc 0.0254 conversion. glTF stores metres, OBJ is
        # conventionally already unit-sized.
        ext = Path(source).suffix.lower()
        factor = (1.0 if ext == '.obj' else 2.54 if ext == '.fbx' else 39.37) if requested == 'auto' else float(requested)
        if not math.isfinite(factor) or not 0 < factor <= 1e6:
            raise ValueError('invalid import scale')
        lo, hi = bounds(render)
        translation = Vector((-(lo[0]+hi[0])/2, -(lo[1]+hi[1])/2, -lo[2]))
        for obj, _ in pairs:
            for v in obj.data.vertices:
                v.co = (v.co + translation) * factor
            # Imported interchange files conventionally face Blender -Y.
            obj.data.transform(Matrix.Rotation(math.pi/2, 4, 'Z'))
    else:
        factor = 1.0
    ensure_uv(render)
    bpy.context.view_layer.update()
    records = material_records(render, out.parent)
    weapon_tris = export_weapon_tris(render, out) if params.get('__cs2map_weapon') else None
    # Hide originals/collision in the saved author-space .blend used by preview.
    for obj, col in pairs:
        obj.hide_render = col
        obj.hide_set(col)
    bpy.context.view_layer.update()
    lohi = bounds(render)
    tris = sum(len(poly.vertices)-2 for obj in render for poly in obj.data.polygons)
    blend = out.with_suffix('.blend')
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    # Measured with resourceinfo MDAT/PHYS: this FBX profile multiplies coordinates
    # by 100 and maps Blender +X to CS2 +Y. Bake a compensating -90 deg turn and
    # 1/2.54, retaining the agreed ModelDoc import_scale=0.0254.
    transform = Matrix.Scale(1 / 2.54, 4) @ Matrix.Rotation(-math.pi/2, 4, 'Z')
    for obj, _ in pairs:
        obj.data.transform(transform)
    bpy.context.view_layer.update()
    export_fbx(out, render)
    lod_files = []
    for index, ratio in params.get('__cs2map_lods', []):
        # A simplified copy per level (Decimate, collapse): same materials, fewer triangles.
        copies = []
        for obj in render:
            dup = obj.copy()
            dup.data = obj.data.copy()
            bpy.context.collection.objects.link(dup)
            mod = dup.modifiers.new('lod', 'DECIMATE')
            mod.ratio = float(ratio)
            bpy.context.view_layer.objects.active = dup
            bpy.ops.object.select_all(action='DESELECT')
            dup.select_set(True)
            bpy.ops.object.modifier_apply(modifier=mod.name)
            copies.append(dup)
        path = out.with_name(f'{out.stem}_lod{index}.fbx')
        export_fbx(path, copies)
        lod_files.append({'level': index, 'file': path.name,
                          'tris': sum(len(p.vertices) - 2 for o in copies for p in o.data.polygons)})
        for dup in copies:
            bpy.data.objects.remove(dup, do_unlink=True)
    implicit = not physics and collision != 'none'
    if implicit:
        physics = render
    files = []
    if collision != 'none':
        groups = [[o] for o in physics] if collision == 'hull' else [physics]
        if len(groups) > 256:
            raise ValueError('more than 256 collision pieces; author simpler *_col objects')
        for index, objects in enumerate(groups):
            path = out.with_name(f'{out.stem}_col_{index}.fbx')
            export_fbx(path, objects)
            files.append(path.name)
    break_files = export_break_fragments(render, out) if params.get('__cs2map_physics', {}).get('breakable') else []
    print('JSON>' + json.dumps({'bounds': lohi, 'tris': tris, 'materials': records,
        'collision_files': files, 'collision_objects': [o.name for o in physics],
        'objects': [o.name for o in render], 'implicit_collision': implicit,
        'source_scale': factor, 'blend': str(blend), 'uv_layers': 1, 'break_files': break_files,
        'weapon_tris': weapon_tris, 'lod_files': lod_files}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR> {type(exc).__name__}: {exc}', flush=True)
        raise
