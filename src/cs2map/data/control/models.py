"""Authored quadcopter / twin-stick remote / separate propeller; +X front, inches."""
import math
import bpy
from mathutils import Vector

part = PARAMS['part']
colors = {'shell':(.02,.04,.07,1),'accent':(.85,.34,.015,1),'rubber':(.007,.009,.012,1),'screen':(.018,.42,.63,1)}
mats = {}
for key, color in colors.items():
    mat = bpy.data.materials.new(key)
    mat['source_vmat'] = f'materials/mcp_d/d8_{key}/d8_{key}.vmat'
    mat.diffuse_color = color
    mats[key] = mat

def finish(obj, name, mat, bevel=0):
    obj.name = name
    obj.data.materials.append(mats[mat])
    if bevel:
        mod = obj.modifiers.new('Rounded machined edges','BEVEL'); mod.width=bevel; mod.segments=3
        obj.modifiers.new('Normals','WEIGHTED_NORMAL')
    return obj

def box(name, pos, size, mat, bevel=.3):
    bpy.ops.mesh.primitive_cube_add(size=1,location=pos)
    obj=bpy.context.object; obj.dimensions=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    return finish(obj,name,mat,bevel)

def cylinder(name,pos,radius,depth,mat,axis=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=20,radius=radius,depth=depth,location=pos)
    obj=bpy.context.object
    if axis: obj.rotation_euler=Vector(axis).to_track_quat('Z','Y').to_euler()
    return finish(obj,name,mat,.2)

def beam(name,a,b,width,mat):
    delta=Vector(b)-Vector(a)
    obj=box(name,(Vector(a)+Vector(b))/2,(width,width,delta.length),mat)
    obj.rotation_euler=delta.to_track_quat('Z','Y').to_euler()
    return obj

if part=='drone':
    box('Hull', (0,0,13),(22,24,10),'shell',2)
    box('Top stripe',(0,0,18.5),(13,14,1.5),'accent',.5)
    box('Front camera housing',(13,0,10),(6,8,6),'shell',1)
    cylinder('Lens',(16.2,0,10),2.1,1,'screen',(1,0,0))
    for x in (-26,26):
        for y in (-26,26):
            beam('Arm',(x/5,y/5,12),(x,y,14),4,'shell')
            cylinder('Motor',(x,y,16),4,8,'shell')
            cylinder('Motor accent',(x,y,18),4.1,1.5,'accent')
    for y in (-11,11):
        for x in (-8,8): beam('Landing strut',(x,y,10),(x,y*1.25,2),1.5,'shell')
        box('Landing skid',(0,y*1.25,1),(26,2,2),'rubber',.6)
    # Physics = bullet hitbox of a prop: one convex hull per real part, so shots
    # through the gaps between arms miss. Rotor discs cover the spinning blades.
    # Names must stay unique: Blender's '.001' suffix would hide the '_col' marker.
    box('Hull_col',(0,0,13.6),(22,24,11.3),'shell',0)
    box('Camera_col',(13.3,0,10),(7.4,8,6),'shell',0)
    i = 0
    for x in (-26,26):
        for y in (-26,26):
            i += 1
            beam(f'Arm{i}_col',(x/5,y/5,12),(x,y,14),4,'shell').modifiers.clear()
            cylinder(f'Motor{i}_col',(x,y,16),4.1,8,'shell').modifiers.clear()
            cylinder(f'Rotor{i}_col',(x,y,21.2),16.5,2.4,'rubber').modifiers.clear()
    for y in (-11,11):
        for x in (-8,8): beam(f'Strut{x}{y}_col',(x,y,10),(x,y*1.25,2),1.5,'shell').modifiers.clear()
        box(f'Skid{y}_col',(0,y*1.25,1),(26,2,2),'rubber',0)
elif part=='rotor':
    cylinder('Hub',(0,0,1.2),2.5,2.4,'accent')
    box('Blade A',(8,0,1),(17,2.8,1),'rubber',.45)
    box('Blade B',(-8,0,1),(17,2.8,1),'rubber',.45)
    box('Rotor_col',(0,0,1.2),(33,3,2.4),'rubber',0)
elif part=='remote':
    box('Remote body',(0,0,2.5),(14,22,5),'shell',1.5)
    for y in (-11,11):box('Grip',(-1,y,3),(12,4,6),'rubber',1)
    box('Display surround',(3,0,5.4),(6,10,1.8),'accent',.5)
    box('Display',(3,0,6.5),(4.5,8,1),'screen',.3)
    for y in (-6,6):
        cylinder('Stick base',(-3,y,5.6),2.5,1.8,'accent')
        cylinder('Stick',(-3,y,7),.65,3,'shell')
        cylinder('Thumb pad',(-3,y,8.8),1.5,.8,'rubber')
    for y in (-7,7):cylinder('Antenna',(6,y,10),.6,13,'shell')
    box('Remote_col',(0,0,3),(14,26,6),'shell',0)
else:
    raise ValueError('unknown part')
