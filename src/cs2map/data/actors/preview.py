"""One labelled pose/axis contact sheet. Temporary images have bounded lifetime."""
import json
from pathlib import Path
import sys
import bpy
from mathutils import Vector


def main():
    args = sys.argv[sys.argv.index('--')+1:]
    blend, temp = map(Path, args)
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    rig = next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 12
    scene.render.resolution_x = scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.world.color = (.25,.25,.25)
    scene.view_settings.view_transform = 'Standard'
    bpy.ops.object.camera_add(location=(112,-100,88))
    cam=bpy.context.object
    cam.rotation_euler = (Vector((0,0,32))-cam.location).to_track_quat('-Z','Y').to_euler()
    cam.data.type='ORTHO';cam.data.ortho_scale=94;scene.camera=cam
    for pos, power in [((40,-50,100),1700),((-50,30,80),1200)]:
        bpy.ops.object.light_add(type='AREA',location=pos)
        lamp=bpy.context.object;lamp.data.energy=power*50;lamp.data.size=70
        lamp.rotation_euler=(Vector((0,0,30))-lamp.location).to_track_quat('-Z','Y').to_euler()
    # Axis bars lie below the feet so they cannot be mistaken for model parts.
    for axis,color in [((30,0,-2),(1,.12,.08,1)),((0,30,-2),(.15,.6,.2,1))]:
        mat=bpy.data.materials.new('axis');mat.diffuse_color=color
        bpy.ops.mesh.primitive_cube_add(size=1,location=(axis[0]/2,axis[1]/2,-2))
        obj=bpy.context.object;obj.dimensions=(30 if axis[0] else 1,30 if axis[1] else 1,1)
        obj.data.materials.append(mat)
    temp.mkdir(parents=True,exist_ok=True)
    files=[]
    for index,(clip,phase) in enumerate([('idle',0),('walk',.25),('walk',.75),('turn',.25)]):
        action=bpy.data.actions[clip]
        rig.animation_data.action=action
        a,b=action.frame_range
        scene.frame_set(round(a+(b-a)*phase))
        file=temp/f'{index}.png';files.append(file)
        scene.render.filepath=str(file);bpy.ops.render.render(write_still=True)
    print('JSON>'+json.dumps({'ok':True,'files':[str(f) for f in files]}))


if __name__=='__main__':main()
