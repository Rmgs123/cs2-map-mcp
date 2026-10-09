"""Write Source model DMX directly, preserving CS2 bind orientations and inches."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from cs2map import dmx

def element(kind,name='',**attrs):
    e=dmx.Element(kind,name)
    for key,(typ,value) in attrs.items(): e.set(key,typ,value)
    return e

def transform(name='',position=(0,0,0),rotation=(0,0,0,1)):
    return element('DmeTransform',name,position=('vector3',position),orientation=('quaternion',rotation))

def dag(kind,name,trans=None,shape=None):
    return element(kind,name,transform=('element',trans or transform()),shape=('element',shape),
                   visible=('bool',True),children=('element_array',[]))

def export(path,meshes,reference):
    model=dag('DmeModel','character')
    bones={b['name']:dag('DmeJoint',b['name'],transform(b['name'],b['position'],b['rotation_xyzw']))
           for b in reference['bones']}
    for b in reference['bones']:
        parent=bones[b['parent']] if b['parent'] else model
        parent['children'].append(bones[b['name']])
    joints=list(bones.values()); bone_index={n:i for i,n in enumerate(bones)}
    base_states=[]
    for obj in meshes:
        mesh=obj.data; mesh.update(); mesh.calc_loop_triangles()
        data=element('DmeVertexData','bind',vertexFormat=('string_array',[
            'position$0','normal$0','texcoord$0','blendindices$0','blendweights$0']),
            jointCount=('int',4),flipVCoordinates=('bool',False))
        data.set('position$0','vector3_array',[tuple(v.co) for v in mesh.vertices])
        data.set('position$0Indices','int_array',[l.vertex_index for l in mesh.loops])
        normals=[tuple(n.vector) for n in mesh.corner_normals]
        data.set('normal$0','vector3_array',normals)
        data.set('normal$0Indices','int_array',list(range(len(mesh.loops))))
        # Blender uses bottom-left UV origin; the extracted Source2 DMX schema
        # with flipVCoordinates=false stores top-left texture coordinates.
        uvs=[(float(u.uv.x),1.0-float(u.uv.y)) for u in mesh.uv_layers[0].data] if mesh.uv_layers else [(0,0)]*len(mesh.loops)
        data.set('texcoord$0','vector2_array',uvs)
        data.set('texcoord$0Indices','int_array',list(range(len(mesh.loops))))
        indices=[];weights=[]
        for v in mesh.vertices:
            ws=[(bone_index[obj.vertex_groups[g.group].name],g.weight) for g in v.groups if g.weight>0]
            ws=sorted(ws,key=lambda x:x[1],reverse=True)[:4];total=sum(w for _,w in ws)
            if not total: raise ValueError('Unweighted vertex in retargeted mesh')
            ws=[(i,w/total) for i,w in ws]+[(0,0)]*(4-len(ws))
            indices.extend(i for i,_ in ws);weights.extend(w for _,w in ws)
        data.set('blendindices$0','int_array',indices);data.set('blendweights$0','float_array',weights)
        face_sets=[]
        for material_id,mat in enumerate(mesh.materials):
            faces=[]
            for tri in mesh.loop_triangles:
                if tri.material_index==material_id: faces.extend([*tri.loops,-1])
            if faces:
                material=element('DmeMaterial','material',mtlName=('string',mat.name+'.vmat'))
                face_sets.append(element('DmeFaceSet',mat.name,faces=('int_array',faces),material=('element',material)))
        shape=element('DmeMesh',obj.name,bindState=('element',None),currentState=('element',data),
            baseStates=('element_array',[data]),deltaStates=('element_array',[]),faceSets=('element_array',face_sets),
            deltaStateWeights=('vector2_array',[]),deltaStateWeightsLagged=('vector2_array',[]),visible=('bool',True))
        child=dag('DmeDag',obj.name,shape=shape);model['children'].append(child);joints.append(child)
        base_states.append(element('DmeTransformsList',transforms=('element_array',[transform()])))
    model.set('jointList','element_array',joints);model.set('baseStates','element_array',base_states)
    model.set('axisSystem','element',element('DmeAxisSystem',upAxis=('int',3),forwardParity=('int',1),coordSys=('int',0)))
    root=element('DmElement','root',model=('element',model),skeleton=('element',model))
    Path(path).write_bytes(dmx.dumps(dmx.Document(root,format='model',format_version=22)))
