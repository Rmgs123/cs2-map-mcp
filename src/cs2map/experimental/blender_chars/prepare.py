"""Headless Blender FBX cleanup used by :mod:`cs2map.characters`.

The script deliberately keeps source-specific rules in the JSON manifest.  It
can therefore be reused for a second rig without changing Python code.
"""
import bpy
import json
import math
import mathutils
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
src, out, target_height, manifest_json = argv[0], argv[1], float(argv[2]), argv[3]
manifest = json.loads(manifest_json)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src)

remove = set(manifest.get("remove_objects", []))
for obj in list(bpy.context.scene.objects):
    if obj.name in remove:
        bpy.data.objects.remove(obj, do_unlink=True)
canonical_materials = {}
for obj in bpy.context.scene.objects:
    if obj.type == "MESH":
        while len(obj.data.uv_layers) > 1:
            obj.data.uv_layers.remove(obj.data.uv_layers[-1])
        # Normalize source material names to the manifest keys.  A manifest
        # may list source_names for rigs whose FBX names are not portable.
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            original = slot.material.name
            for target, spec in (manifest.get("materials") or {}).items():
                aliases = (spec.get("source_names") if isinstance(spec, dict) else None) or [target]
                if original in aliases or any(a and (original.startswith(a) or a in original) for a in aliases):
                    if target in canonical_materials:
                        slot.material = canonical_materials[target]
                    else:
                        slot.material.name = target
                        canonical_materials[target] = slot.material
                    break

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
if not meshes or not armatures:
    raise RuntimeError("FBX must contain at least one mesh and one armature")


from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from retarget import run
reference=json.loads((Path(__file__).parent/'cs2_skeleton.json').read_text(encoding='utf-8'))
model_scale=float(manifest.get('model_scale',1.0))
for bone in reference['bones']:
    bone['position']=[float(x)*model_scale for x in bone['position']]
arm, report = run(meshes, armatures[0], manifest, reference)


def render_distortion_heatmap(objects, path):
    """Render the retargeted mesh with the per-edge ratio colour layer."""
    scene=bpy.context.scene
    old_camera=scene.camera
    old_engine=scene.render.engine
    old_res=(scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage)
    temp=[]; old_slots={}
    try:
        scene.render.engine='BLENDER_EEVEE_NEXT'
        scene.render.resolution_x=900; scene.render.resolution_y=900; scene.render.resolution_percentage=100
        if scene.world is None:
            scene.world=bpy.data.worlds.new('distortion_world')
        scene.world.color=(0.08,0.08,0.08)
        camera=bpy.data.cameras.new('distortion_camera'); cam=bpy.data.objects.new('distortion_camera',camera)
        scene.collection.objects.link(cam); temp.append(cam)
        cam.location=(125,-160,90)
        cam.rotation_euler=(mathutils.Vector((0,0,38))-cam.location).to_track_quat('-Z','Y').to_euler()
        camera.type='ORTHO'; camera.ortho_scale=105; scene.camera=cam
        for i,pos in enumerate([(70,-80,130),(-60,50,100)]):
            light=bpy.data.lights.new(f'distortion_key_{i}','AREA'); light.energy=180000; light.shape='DISK'; light.size=100
            obj=bpy.data.objects.new(light.name,light); scene.collection.objects.link(obj); obj.location=pos
            obj.rotation_euler=(mathutils.Vector((0,0,38))-obj.location).to_track_quat('-Z','Y').to_euler(); temp.append(obj)
        mat=bpy.data.materials.new('distortion_heatmap_material'); mat.use_nodes=True
        nodes=mat.node_tree.nodes; links=mat.node_tree.links; nodes.clear()
        output=nodes.new('ShaderNodeOutputMaterial'); bsdf=nodes.new('ShaderNodeBsdfPrincipled')
        attr=nodes.new('ShaderNodeVertexColor'); attr.layer_name='distortion_heatmap'
        bsdf.inputs['Roughness'].default_value=0.8; links.new(attr.outputs['Color'],bsdf.inputs['Base Color']); links.new(bsdf.outputs['BSDF'],output.inputs['Surface'])
        for obj in objects:
            old_slots[obj.name]=list(obj.data.materials); obj.data.materials.clear(); obj.data.materials.append(mat)
        scene.render.filepath=str(path); bpy.ops.render.render(write_still=True)
    finally:
        for obj in objects:
            obj.data.materials.clear()
            for old in old_slots.get(obj.name,[]): obj.data.materials.append(old)
        for obj in temp:
            bpy.data.objects.remove(obj,do_unlink=True)
        scene.camera=old_camera; scene.render.engine=old_engine
        scene.render.resolution_x,scene.render.resolution_y,scene.render.resolution_percentage=old_res


distortion=report.pop('distortion',None)
heatmap_path=Path(out).with_suffix('.distortion_heatmap.png')
metrics_path=Path(out).with_suffix('.distortion.json')
if distortion:
    metrics_path.write_text(json.dumps({'character':manifest.get('name'),**distortion},ensure_ascii=False,indent=2),encoding='utf-8')
    render_distortion_heatmap(meshes,heatmap_path)
bpy.ops.object.select_all(action='DESELECT')
for obj in meshes+[arm]: obj.select_set(True)
bpy.context.view_layer.objects.active=arm
bpy.ops.export_scene.fbx(filepath=out,use_selection=True,object_types={'ARMATURE','MESH'},
    apply_unit_scale=True,apply_scale_options='FBX_SCALE_NONE',bake_anim=False,
    add_leaf_bones=False,primary_bone_axis='X',secondary_bone_axis='Y',
    axis_forward='-Z',axis_up='Y',mesh_smooth_type='FACE',path_mode='STRIP',embed_textures=False)
from dmx_export import export
export(Path(out).with_suffix('.dmx'),meshes,reference)
bpy.ops.wm.save_as_mainfile(filepath=str(Path(out).with_suffix('.blend')))
points=[obj.matrix_world @ v.co for obj in meshes for v in obj.data.vertices]
print('JSON>'+json.dumps({'bounds':[[min(v[i] for v in points) for i in range(3)],
                                  [max(v[i] for v in points) for i in range(3)]],
 'bones':len(arm.data.bones),'meshes':len(meshes),'retarget':report,
 'triangles':sum(len(p.vertices)-2 for obj in meshes for p in obj.data.polygons),
 'distortion':({'summary':distortion.get('summary',{}),'zones':distortion.get('zones',{}),
                'edge_count':distortion.get('edge_count',0),'metrics_path':str(metrics_path),
                'heatmap_path':str(heatmap_path)} if distortion else {'status':'unavailable'})}))
