"""Solid graded street ribbons; polygon clipping keeps T/X junctions open."""
from __future__ import annotations
import json, math
from . import mesh, terrain
from .project import OpError

ROAD_DEFAULT='materials/cs_italy/ground/cobblestone_ground_large_1.vmat'
SIDE_DEFAULT='materials/concrete/concrete_floor_04.vmat'
STOCK={'lamps':'models/props/cs_italy/it_streetlampleg.vmdl',
       'benches':'models/props/de_inferno/stone_bench.vmdl',
       'bins':'models/props_street/trashbin01.vmdl',
       'bollards':'models/generic/bollard_kit_01/bollard_01_base.vmdl',
       'manholes':'models/generic/manholecoverkit_01/manholecover_01_center.vmdl',
       'grates':'models/cs_italy/italy_sewer_kit/italy_manhole_1.vmdl'}


def cross(a,b,p):return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])


def clean(poly):
    out=[]
    for p in poly:
        if not out or math.dist(p,out[-1])>1e-5:out.append(p)
    if len(out)>1 and math.dist(out[0],out[-1])<1e-5:out.pop()
    if len(out)<3 or abs(mesh._polygon_area(out))<.001:return []
    return out if mesh._polygon_area(out)>0 else out[::-1]


def halfplane(poly,a,b,positive=True):
    out=[]
    for p,q in zip(poly,poly[1:]+poly[:1]):
        dp,dq=cross(a,b,p),cross(a,b,q); pin=dp>=-1e-7 if positive else dp<=1e-7
        qin=dq>=-1e-7 if positive else dq<=1e-7
        if pin:out.append(p)
        if pin!=qin:
            t=dp/(dp-dq);out.append([p[i]+t*(q[i]-p[i]) for i in (0,1)])
    return clean(out)


def intersect(poly,cut):
    if not bbox_overlap(poly,cut):return []
    for a,b in zip(cut,cut[1:]+cut[:1]):
        poly=halfplane(poly,a,b)
        if not poly:break
    return poly


def subtract(poly,cut):
    """Disjoint convex pieces of polygon minus convex clip polygon."""
    if not bbox_overlap(poly,cut):return [poly]
    current=poly;out=[]
    for a,b in zip(cut,cut[1:]+cut[:1]):
        part=halfplane(current,a,b,False)
        if part:out.append(part)
        current=halfplane(current,a,b)
        if not current:break
    return out


def bbox_overlap(a,b):
    return all(min(max(p[k] for p in a),max(p[k] for p in b))-max(min(p[k] for p in a),min(p[k] for p in b))>1e-5 for k in (0,1))


def difference(poly,cuts):
    todo=[poly]
    for cut in cuts:
        todo=[p for q in todo for p in subtract(q,cut)]
        if not todo:break
        if len(todo)>4096:raise OpError('street intersections exceed 4096 pieces')
    return todo


def plane(a,b,p):
    d=[b[i]-a[i] for i in (0,1)]
    t=sum((p[i]-a[i])*d[i] for i in (0,1))/sum(x*x for x in d)
    return a[2]+t*(b[2]-a[2])


def surface(spec,p):
    a,b,c=spec['vertices']
    den=cross(a,b,c);u=cross(p,b,c)/den;v=cross(a,p,c)/den;w=1-u-v
    return u*a[2]+v*b[2]+w*c[2]


def solid(poly,spec,height,thickness,material):
    pts=[[p[0],p[1],surface(spec,p)+height] for p in poly]
    bottom=[[p[0],p[1],p[2]-thickness] for p in pts]
    out=mesh.Mesh();out.face(pts,material);out.face(bottom[::-1],material)
    for i in range(len(pts)):
        j=(i+1)%len(pts);out.face([pts[j],pts[i],bottom[i],bottom[j]],material)
    return out


def ribbon(path,low,high):
    """Mitered offset line; a single turn has matching cross sections."""
    normals=[]
    for a,b in zip(path,path[1:]):
        d=[b[i]-a[i] for i in (0,1)];ln=math.hypot(*d);normals.append([-d[1]/ln,d[0]/ln])
    sections=[]
    for i,p in enumerate(path):
        if i in (0,len(path)-1):n=normals[0 if i==0 else -1];factor=1
        else:
            n=mesh._norm((*[normals[i-1][k]+normals[i][k] for k in (0,1)],0))[:2]
            factor=1/sum(n[k]*normals[i][k] for k in (0,1))
            if factor>3:raise OpError('street turn too sharp; insert a wider bend')
        sections.append([[p[k]+n[k]*factor*v for k in (0,1)] for v in (low,high)])
    return [clean([sections[i][0],sections[i+1][0],sections[i+1][1],sections[i][1]]) for i in range(len(path)-1)]


def streets(m):return terrain.plans(m,terrain.STREET_KEY)


def geometry_specs(data):
    if 'surfaces' in data:return data['surfaces']
    w=data['width']/2;sw=data['sidewalk']['width'];curb=data['sidewalk']['curb']
    result=[]
    for role,lo,hi,height,mat in [('road',-w,w,0,data['road_material']),
            ('sidewalk',w+curb,w+sw,curb,data['sidewalk']['material']),
            ('sidewalk',-w-sw,-w-curb,curb,data['sidewalk']['material']),
            ('curb',w,w+curb,curb,data['sidewalk']['curb_material']),
            ('curb',-w-curb,-w,curb,data['sidewalk']['curb_material'])]:
        if lo>=hi:continue
        for i,poly in enumerate(ribbon(data['path'],lo,hi)):
            if poly:
                for tri in ([poly[0],poly[1],poly[2]],[poly[0],poly[2],poly[3]]):
                    verts=[[p[0],p[1],plane(data['path'][i],data['path'][i+1],p)] for p in tri]
                    result.append({'role':role,'poly':tri,'a':data['path'][i],'b':data['path'][i+1],
                                   'vertices':verts,'height':height,'material':mat})
    return result


def kit_model(kind,override=None):
    if override:return override
    folder=terrain.PROJECT_ROOT/'src/cs2map/data/kits'
    for nm in ('italy_street','street_common'):
        file=folder/(nm+'.json')
        if not file.is_file():continue
        for p in json.loads(file.read_text(encoding='utf-8'))['parts']:
            tags=' '.join([p['key'],*p.get('tags',[])])
            stem={'lamps':'lamp','benches':'bench','bins':'bin','bollards':'bollard','manholes':'manhole','grates':'grate'}[kind]
            if stem in tags:return p['model']
    return STOCK[kind]


def lot_plan(data,depth,length,gap):
    result=[];w=data['width']/2+data['sidewalk']['width']+gap
    for index,(a,b) in enumerate(zip(data['path'],data['path'][1:])):
        dx,dy=b[0]-a[0],b[1]-a[1];ln=math.hypot(dx,dy);t=[dx/ln,dy/ln];n=[-t[1],t[0]]
        count=int(ln//(length+gap))
        for j in range(count):
            distance=(j+.5)*ln/max(1,count);c=[a[i]+t[i]*distance for i in (0,1)]
            for sign in (-1,1):
                center=[c[i]+n[i]*sign*(w+depth/2) for i in (0,1)]
                poly=[[center[i]+t[i]*u+n[i]*v for i in (0,1)] for u,v in [(-length/2,-depth/2),(length/2,-depth/2),(length/2,depth/2),(-length/2,depth/2)]]
                z=plane(a,b,center)
                half=min(length,depth)/2/(abs(t[0])+abs(t[1]))
                footprint={'mins':[center[0]-half,center[1]-half,z],'maxs':[center[0]+half,center[1]+half,z]}
                result.append({'name':f'{data["name"]}_{index}_{j}_{"l" if sign==1 else "r"}',
                    'polygon':poly,'origin':[*center,z],'size':[length,depth],
                    'footprint':footprint,
                    'angles':[0,math.degrees(math.atan2(dy,dx)),0],
                    'mins':[min(p[i] for p in poly) for i in (0,1)]+[z],
                    'maxs':[max(p[i] for p in poly) for i in (0,1)]+[z]})
    return result


def add_street(project,m,op,refs):
    terrain.keys(op,{'op','name','terrain','path','width','z','road_material','sidewalk','markings','details','furniture','lots','max_slope','ref'},'street')
    nm=terrain.name(op.get('name','street'));old=streets(m)
    if any(d['name']==nm for d in old):raise OpError('street name already exists; delete its group and replay')
    ground=terrain.select(m,op.get('terrain'));raw=terrain.path(op.get('path'),z=True)
    zmode=op.get('z','terrain')
    if zmode not in ('terrain','path'):raise OpError('street z must be terrain or path')
    if zmode=='path' and any(len(p)!=3 for p in raw):raise OpError('z:path needs XYZ at every point')
    path=[]
    for i,(a,b) in enumerate(zip(raw,raw[1:])):
        count=max(1,math.ceil(math.dist(a[:2],b[:2])/128))
        for j in range(count):
            t=j/count;p=[a[k]+t*(b[k]-a[k]) for k in range(len(a))]
            path.append([*p[:2],p[2] if zmode=='path' else terrain.sample(ground,*p[:2])['z']+2])
    p=raw[-1];path.append([*p[:2],p[2] if zmode=='path' else terrain.sample(ground,*p[:2])['z']+2])
    w=terrain.number(op.get('width',256),'width',48,4096);maxslope=terrain.number(op.get('max_slope',12),'max_slope',0,45)
    side=op.get('sidewalk',{});terrain.keys(side,{'width','curb','material','curb_material'},'sidewalk')
    sw=terrain.number(side.get('width',64),'sidewalk.width',16,512);curb=terrain.number(side.get('curb',6),'curb',4,8)
    data={'version':1,'origin':[0,0,0],'name':nm,'terrain':ground['name'],'path':path,'width':w,
          'road_material':terrain.material(project,op.get('road_material',ROAD_DEFAULT)),
          'sidewalk':{'width':sw,'curb':curb,'material':terrain.material(project,side.get('material',SIDE_DEFAULT)),
                      'curb_material':terrain.material(project,side.get('curb_material',SIDE_DEFAULT))}}
    slopes=[math.degrees(math.atan2(abs(a[2]-b[2]),math.dist(a[:2],b[:2]))) for a,b in zip(path,path[1:])]
    if max(slopes)>maxslope:raise OpError(f'street slope {max(slopes):.1f} exceeds max_slope {maxslope}')
    data['max_slope']=round(max(slopes),3)
    specs=geometry_specs(data)
    if zmode=='terrain':
        for s in specs:
            s['vertices']=[[p[0],p[1],terrain.sample(ground,*p)['z']+2] for p in s['poly']]
    else:
        # Every cross-section uses one shared centreline Z at both edges.
        for s in specs:
            s['vertices']=[[p[0],p[1],min(path,key=lambda q:math.dist(p,q[:2]))[2]] for p in s['poly']]
    data['surfaces']=specs
    actual_slope=0
    for s in specs:
        if s['role']!='road':continue
        a,b,c=s['vertices'];normal=mesh._norm(mesh._cross(mesh._sub(b,a),mesh._sub(c,a)))
        actual_slope=max(actual_slope,math.degrees(math.acos(max(-1,min(1,normal[2])))))
    if actual_slope>maxslope:raise OpError(f'street surface slope {actual_slope:.1f} exceeds max_slope {maxslope}')
    data['max_slope']=round(max(actual_slope,max(slopes)),3)
    # A street does not secretly rewrite terrain or existing building pads. Grade before placing roads.
    for s in specs:
        for p in s['poly']:
            z=terrain.sample(ground,*p)['z']
            if z>surface(s,p)+s['height']+.1:
                raise OpError('terrain protrudes through street; add flatten width >= road+2*sidewalk+2*cell before streets')
    oldspecs=[(d,geometry_specs(d)) for d in old]
    roads=[s for _,sp in oldspecs for s in sp if s['role']=='road']+[s for s in specs if s['role']=='road']
    for i,s in enumerate(roads):
        for prev in roads[:i]:
            common=intersect(s['poly'],prev['poly'])
            if common and max(abs(surface(s,p)-surface(prev,p)) for p in common)>.5:
                raise OpError('crossing streets differ in Z; flatten the junction to one grade')
    lot=op.get('lots',{});terrain.keys(lot,{'depth','length','gap'},'lots')
    source_path=[[*p[:2],p[2] if zmode=='path' else terrain.sample(ground,*p[:2])['z']+2] for p in raw]
    data['lots']=lot_plan({**data,'path':source_path},terrain.number(lot.get('depth',384),'lots.depth',128,2048),terrain.number(lot.get('length',384),'lots.length',128,2048),terrain.number(lot.get('gap',32),'lots.gap',0,512))
    data['exclusions']=[{'polygon':s['poly']} for s in specs]
    # Rebuild previous sidewalks when a new street cuts through them. IDs of street groups stay stable.
    updates=[];used_roads=[];used_sides=[]
    for d,sp in [*oldspecs,(data,specs)]:
        meshes=[]
        for role in ('road','sidewalk','curb'):
            for s in (v for v in sp if v['role']==role):
                cuts=used_roads if role=='road' else [r['poly'] for r in roads]+used_sides
                for poly in difference(s['poly'],cuts):
                    # Split long solids to keep lightmap charts manageable.
                    piece=solid(poly,s,s['height'],8,s['material'])
                    meshes.append(m.new_mesh(piece,texture_size=project.texture_size))
                    (used_roads if role=='road' else used_sides).append(poly)
        updates.append((d,meshes))
    furniture=op.get('furniture',{});terrain.keys(furniture,{'lamps','benches','bins','bollards','models','scales'},'furniture')
    overrides=furniture.get('models',{});terrain.keys(overrides,set(STOCK),'furniture.models')
    scales=furniture.get('scales',{});terrain.keys(scales,set(STOCK),'furniture.scales')
    props=[];furniture_counts={};details=op.get('details',{});terrain.keys(details,{'manholes','grates'},'details')
    for kind,spacing in {**furniture,**details}.items():
        if kind in ('models','scales'):continue
        if spacing is False:continue
        every=512 if spacing is True else terrain.number(spacing,kind+' spacing',96,4096)
        model=terrain.model_asset(project,kit_model(kind,overrides.get(kind)))
        bounds=terrain.model_local_bounds(project,model)
        scale=terrain.number(scales.get(kind,1),'furniture scale',.05,10)
        count=0
        for a,b in zip(source_path,source_path[1:]):
            length=math.dist(a[:2],b[:2]);n=[-(b[1]-a[1])/length,(b[0]-a[0])/length]
            for j in range(1,int(length//every)+1):
                t=j*every/length
                offset=0 if kind in ('manholes','grates') else (w/2+sw*.65)*(1 if kind in ('lamps','bollards') else -1)
                p=[a[k]+t*(b[k]-a[k])+n[k]*offset for k in (0,1)];z=plane(a,b,p)+(0 if offset==0 else curb)
                # Do not install furniture in any crossing roadway.
                if offset and any(terrain.inside(*p,r['poly']) for r in roads):continue
                yaw=math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))
                prop=terrain.make_prop(project,m,model,[*p,z],[0,yaw,0],scale,bounds,solid=kind not in ('manholes','grates'),fade=4000)
                props.append(prop);count+=1
        furniture_counts[kind]=count
    markings=op.get('markings',[])
    if not isinstance(markings,list) or len(markings)>32:raise OpError('markings need at most 32 decal specs')
    # Explicit stock decals: validate before changing old groups.
    from . import structure
    decal_nodes=[]
    for mark in markings:
        terrain.keys(mark,{'at','size','material'},'marking')
        xy=terrain.vec(mark.get('at'),'marking.at');_,index,t,_=terrain.nearest_path(*xy,path)
        z=plane(path[index],path[index+1],xy)
        markmat=terrain.material(project,mark.get('material'))
        # structure validates decal UV/projection contracts; isolate nodes in a staging map group.
        before={n['nodeID'] for n in m.iter_nodes()}
        structure.add_decal(project,m,{'op':'add_decal','origin':[*xy,z+.2],'normal':[0,0,1],'size':mark.get('size',64),'material':markmat},{})
        for n in list(m.iter_nodes()):
            if n['nodeID'] not in before: m.remove(n);decal_nodes.append(n)
    seeds=[m.new_entity('point_nav_walkable',{},[p[0],p[1],p[2]+1],defaults=project.catalog.defaults('point_nav_walkable')) for p in path]
    for d,nodes in updates[:-1]:
        group=m.node(d['id']);remove=set(d['mesh_ids']);group['children']=[c for c in group['children'] if c['nodeID'] not in remove]
        for node in nodes:m.attach(node,group)
        d['mesh_ids']=[n['nodeID'] for n in nodes];d.pop('id',None);d['origin']=list(group['origin'])
        group.set(terrain.STREET_KEY,'string',json.dumps(d,separators=(',',':')))
    nodes=updates[-1][1];data['mesh_ids']=[n['nodeID'] for n in nodes];data['furniture']=furniture_counts
    gid=terrain.commit(m,op,nm,[*nodes,*props,*seeds,*decal_nodes],data,terrain.STREET_KEY,refs)
    return {'op':'add_street','id':gid,'name':nm,'ids':data['mesh_ids'],'lots':data['lots'],'segments':len(path)-1,
            'max_slope':data['max_slope'],'nav_seeds':len(seeds),'furniture':furniture_counts,'junctions_open':True}


def street_info(project,map,street=None):
    values=streets(project.load(map))
    if street is not None:
        values=[v for v in values if v['name']==street]
        if len(values)!=1:raise OpError('street not found or ambiguous')
    return {'map':map,'streets':[{k:v for k,v in d.items() if k not in ('exclusions','origin','surfaces')} for d in values]}
