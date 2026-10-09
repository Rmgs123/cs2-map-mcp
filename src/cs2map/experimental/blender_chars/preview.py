"""Render the saved retargeted mesh; run is a diagnostic pose, not a CS2 animation."""
import bpy, json, math, sys
from pathlib import Path
from mathutils import Vector

blend, destination, pose_json, manifest_path = sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=blend)
manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
root=Path(__file__).resolve().parents[4]  # project root
textures=Path(manifest.get('textures_dir') or root)
if not textures.is_absolute(): textures=root/textures
for name,spec in manifest.get('materials',{}).items():
    mat=bpy.data.materials.get(name)
    path=textures/spec.get('image','')
    if mat and path.is_file():
        mat.use_nodes=True
        mat.node_tree.nodes.clear()
        principled=mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
        output=mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
        mat.node_tree.links.new(principled.outputs['BSDF'],output.inputs['Surface'])
        node=mat.node_tree.nodes.new('ShaderNodeTexImage'); node.image=bpy.data.images.load(str(path),check_existing=True)
        mat.node_tree.links.new(node.outputs['Color'],principled.inputs['Base Color'])
        if spec.get('alpha','none')!='none':
            mat.node_tree.links.new(node.outputs['Alpha'],principled.inputs['Alpha'])
        principled.inputs['Roughness'].default_value=0.8
scene=bpy.context.scene; scene.render.engine='BLENDER_EEVEE_NEXT'
scene.render.resolution_x=900;scene.render.resolution_y=900;scene.render.resolution_percentage=100
if scene.world is None:
    scene.world=bpy.data.worlds.new('preview_world')
scene.world.color=(0.35,0.35,0.35)
camera=bpy.data.cameras.new('preview_camera'); obj=bpy.data.objects.new('preview_camera',camera)
scene.collection.objects.link(obj);obj.location=(125,-160,90)
obj.rotation_euler=(Vector((0,0,40))-obj.location).to_track_quat('-Z','Y').to_euler()
camera.type='ORTHO';camera.ortho_scale=105;scene.camera=obj
for i,pos in enumerate([(70,-80,130),(-60,50,100)]):
    light=bpy.data.lights.new(f'key_{i}','AREA'); light.energy=200000;light.shape='DISK';light.size=100
    o=bpy.data.objects.new(light.name,light);scene.collection.objects.link(o);o.location=pos
    o.rotation_euler=(Vector((0,0,40))-o.location).to_track_quat('-Z','Y').to_euler()
arm=bpy.data.objects['cs2_player']; results=[]
for pose in json.loads(pose_json):
    for bone in arm.pose.bones: bone.rotation_mode='XYZ';bone.rotation_euler=(0,0,0)
    if pose=='run':
        for name,angle in [('leg_upper_L',-25),('leg_upper_R',30),('leg_lower_R',-40),
                           ('arm_upper_L',-20),('arm_upper_R',25),('arm_lower_L',-40),('arm_lower_R',-40)]:
            arm.pose.bones[name].rotation_euler.z=math.radians(angle)
    scene.render.filepath=str(Path(destination)/f'{pose}.png')
    bpy.ops.render.render(write_still=True)
    results.append({'pose':pose,'path':scene.render.filepath,'kind':'bind_pose' if pose=='rest' else 'diagnostic_pose'})
print('JSON>'+json.dumps({'images':results,'note':'run is a Blender diagnostic pose; game animation requires character_test'}))
