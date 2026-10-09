"""Own low-poly walker. Blender author-space: inches, feet z=0, face +X."""
import math
import bpy
from mathutils import Vector, Quaternion


def build():
    mats = {}
    for name, color in {'shirt': (.62, .16, .055, 1), 'trousers': (.025, .22, .28, 1),
                        'skin': (.67, .39, .20, 1), 'boots': (.045, .035, .025, 1),
                        'eyes': (.012, .018, .02, 1)}.items():
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = color
        mat.use_nodes = True
        mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = color
        mat.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value = .8
        mats[name] = mat
    data = bpy.data.armatures.new('walker_skeleton')
    rig = bpy.data.objects.new('walker_rig', data)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    specs = [('root', (0, 0, 0), (0, 0, 6), None),
             ('body', (0, 0, 32), (0, 0, 50), 'root'),
             ('head', (0, 0, 50), (0, 0, 62), 'body')]
    for side, y in [('left', 5), ('right', -5)]:
        specs += [(side + '_leg', (0, y, 32), (0, y, 5), 'root'),
                  (side + '_arm', (0, y * 2, 49), (0, y * 2, 31), 'body')]
    for name, head, tail, parent in specs:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    meshes = []
    def part(name, size, center, material, bone):
        bpy.ops.mesh.primitive_cube_add(size=1, location=center)
        obj = bpy.context.object
        obj.name = name
        obj.dimensions = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        # Bake positions; skinning and armature have one identical bind basis.
        obj.data.transform(obj.matrix_world)
        obj.matrix_world.identity()
        obj.data.materials.append(mats[material])
        obj.vertex_groups.new(name=bone).add(list(range(len(obj.data.vertices))), 1, 'REPLACE')
        obj.modifiers.new('Skeleton', 'ARMATURE').object = rig
        meshes.append(obj)
    part('torso', (12, 17, 20), (0, 0, 42), 'shirt', 'body')
    part('head', (12, 12, 13), (0, 0, 57.5), 'skin', 'head')
    part('nose_forward_X', (3, 3, 3), (7.5, 0, 57), 'skin', 'head')
    for y in (-3.4, 3.4):
        part('eye', (1, 2, 2), (6.5, y, 60), 'eyes', 'head')
    for side, y in [('left', 5), ('right', -5)]:
        part(side + '_trouser', (7, 7, 24), (0, y, 18.5), 'trousers', side + '_leg')
        part(side + '_boot', (12, 8, 5), (2, y, 2.5), 'boots', side + '_leg')
        part(side + '_sleeve', (6, 6, 14), (0, y * 2, 42), 'shirt', side + '_arm')
        part(side + '_hand', (5, 5, 5), (0, y * 2, 31.5), 'skin', side + '_arm')
    return rig, meshes


def animate(rig, clip):
    """One take per FBX; stationary root, 30 fps, duplicated closing pose."""
    frames = {'idle': 60, 'walk': 24, 'turn': 24}[clip]
    rig.animation_data_create()
    rig.animation_data.action = bpy.data.actions.new(clip)
    axes = {b.name: b.matrix_local.to_3x3().inverted() @ Vector((0, 1, 0))
            for b in rig.data.bones}
    for frame in range(frames + 1):
        phase = 2 * math.pi * frame / frames
        for bone in rig.pose.bones:
            bone.rotation_mode = 'QUATERNION'
            bone.rotation_quaternion = Quaternion()
            bone.location = (0, 0, 0)
            angle = 0
            if clip == 'walk':
                if bone.name.endswith('_leg'):
                    angle = .48 * math.sin(phase) * (1 if bone.name.startswith('left') else -1)
                elif bone.name.endswith('_arm'):
                    angle = -.42 * math.sin(phase) * (1 if bone.name.startswith('left') else -1)
                elif bone.name == 'body':
                    angle = .025 * math.sin(phase * 2)
            elif clip == 'idle' and bone.name == 'body':
                angle = .022 * math.sin(phase)
            elif clip == 'turn' and bone.name == 'head':
                axis = rig.data.bones[bone.name].matrix_local.to_3x3().inverted() @ Vector((0, 0, 1))
                bone.rotation_quaternion = Quaternion(axis, .35 * math.sin(phase))
            if angle:
                bone.rotation_quaternion = Quaternion(axes[bone.name], angle)
            bone.keyframe_insert('rotation_quaternion', frame=frame + 1)
            bone.keyframe_insert('location', frame=frame + 1)
        # Pose-space root axes differ from world axes. Solve support using the
        # actual skinned geometry, not an assumed local Z or guessed hip bob.
        bpy.context.view_layer.update()
        deps = bpy.context.evaluated_depsgraph_get()
        lowest = min((obj.matrix_world @ v.co).z
            for obj in bpy.context.scene.objects if obj.type == 'MESH'
            for v in obj.evaluated_get(deps).data.vertices)
        root = rig.pose.bones['root']
        root.location = rig.data.bones['root'].matrix_local.to_3x3().inverted() @ Vector((0, 0, -lowest))
        root.keyframe_insert('location', frame=frame + 1)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, frames + 1
    bpy.context.scene.render.fps = 30
    return frames
