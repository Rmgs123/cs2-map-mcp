"""Headless skeletal FBX driver, invoked exclusively via blender_run."""
import json
import math
import re
import sys
from pathlib import Path
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).parent))
from walker import build, animate


def main():
    args = sys.argv[sys.argv.index('--') + 1:]
    out = Path(args[0])
    custom = Path(args[1]) if len(args) > 1 and args[1] else None
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    if custom:
        ns = {'bpy': bpy, '__file__': str(custom), '__name__': '__actor__'}
        exec(compile(custom.read_text(encoding='utf-8'), str(custom), 'exec'), ns)
        rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
        if len(rigs) != 1:
            raise ValueError('actor script must create exactly one armature')
        rig = rigs[0]
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        actions = {a.name: a for a in bpy.data.actions}
        if not {'idle', 'walk', 'turn'} <= actions.keys():
            raise ValueError('actor script needs idle, walk, turn Actions')
    else:
        rig, meshes = build()
        actions = {}
        for clip in ('idle', 'walk', 'turn'):
            animate(rig, clip)
            actions[clip] = rig.animation_data.action
    for action in actions.values():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,55}', action.name):
            raise ValueError('Action names must be ASCII identifiers of at most 56 characters')
        action.use_fake_user = True
    if not meshes or not rig.data.bones:
        raise ValueError('actor needs skinned meshes and bones')
    if 'root' not in rig.data.bones or rig.data.bones['root'].parent is not None:
        raise ValueError('actor needs a top-level bone named root')
    for obj in meshes:
        if not any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers):
            raise ValueError('all meshes must have an Armature modifier on the actor rig')
        if any(not v.groups or sum(g.weight for g in v.groups) < .99 for v in obj.data.vertices):
            raise ValueError('all mesh vertices must be weighted')
    out.mkdir(parents=True, exist_ok=True)
    rig.animation_data.action = None
    bpy.context.scene.frame_set(1)
    vertices = [obj.matrix_world @ v.co for obj in meshes for v in obj.data.vertices]
    bounds = [[min(v[i] for v in vertices) for i in range(3)],
              [max(v[i] for v in vertices) for i in range(3)]]
    records = []
    for mat in sorted({m for o in meshes for m in o.data.materials if m}, key=lambda m: m.name):
        raw = mat.name
        token = f'n_mat_{len(records)}'
        records.append({'token': token, 'name': raw, 'color': list(mat.diffuse_color)[:3],
                        'vmat': mat.get('source_vmat', '')})
        mat.name = token
    bpy.ops.wm.save_as_mainfile(filepath=str(out / 'actor.blend'))
    # The same empirically measured FBX axis/unit profile as environment models.
    transform = Matrix.Scale(1 / 2.54, 4) @ Matrix.Rotation(-math.pi / 2, 4, 'Z')
    for obj in [rig, *meshes]:
        obj.matrix_world = transform @ obj.matrix_world
    bpy.ops.object.select_all(action='DESELECT')
    for obj in [rig, *meshes]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = rig
    def export(file, animation):
        bpy.ops.export_scene.fbx(filepath=str(out / file), use_selection=True,
            object_types={'MESH', 'ARMATURE'}, add_leaf_bones=False,
            # AnimFile imports FBX translations at scale 1, unlike RenderMeshFile.
            # Bake the mesh's .0254 import compensation into the animation FBX.
            axis_forward='-Y', axis_up='Z', global_scale=.0254 if animation else 1, apply_unit_scale=True,
            apply_scale_options='FBX_SCALE_NONE', bake_anim=animation,
            bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False,
            bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0,
            use_mesh_modifiers=False, path_mode='AUTO')
    export('mesh.fbx', False)
    clips = {}
    for clip, action in actions.items():
        rig.animation_data.action = action
        start, end = map(int, action.frame_range)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = start, end
        bpy.context.scene.frame_set(start)
        export(clip + '.fbx', True)
        clips[clip] = {'frames': end - start, 'fps': bpy.context.scene.render.fps,
                       'seconds': (end - start) / bpy.context.scene.render.fps}
    result = {'ok': True, 'bounds': bounds, 'bones': [b.name for b in rig.data.bones],
              'tris': sum(len(p.vertices) - 2 for o in meshes for p in o.data.polygons),
              'materials': records, 'clips': clips, 'forward': '+X', 'base_z': bounds[0][2],
              'animation_export_scale': .0254}
    (out / 'source.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('JSON>' + json.dumps(result))


if __name__ == '__main__':
    main()
