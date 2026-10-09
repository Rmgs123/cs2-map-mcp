"""Deterministic projected facade wear with conservative receiving-area checks."""
from __future__ import annotations
import hashlib
import json
import math
import random
import re
from pathlib import Path
from . import checks, facades, layout, mesh, structure
from .project import OpError

FACADE_KEY = 'cs2map_facade'
PAVING_KEY = 'cs2map_paving'
WEAR_KEY = 'cs2map_wear'
_PROFILE_DIR = Path(__file__).with_name('data') / 'wear'
_ROLES = ('base_dirt','leak','eave_grime','crack','plaster_loss','poster','graffiti','ground_stain','ground_dirt')


def _num(value,label,lo,hi):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise OpError(f'{label} must be a number')
    value=float(value)
    if not math.isfinite(value) or not lo<=value<=hi:
        raise OpError(f'{label} must be between {lo:g} and {hi:g}')
    return value


def _integer(value,label,lo,hi):
    if type(value) is not int or not lo<=value<=hi:
        raise OpError(f'{label} must be an integer between {lo} and {hi}')
    return value


def _profile(style):
    if not isinstance(style,str) or not re.fullmatch(r'[a-z0-9_-]{1,48}',style):
        raise OpError('style must be a simple lowercase name')
    try: data=json.loads((_PROFILE_DIR/f'{style}.json').read_text(encoding='utf-8'))
    except (OSError,ValueError): raise OpError(f'unknown or unreadable wear style {style!r}') from None
    if not isinstance(data,dict) or data.get('schema')!=1 or set(data.get('roles',{}))!=set(_ROLES):
        raise OpError(f'wear style {style!r} has unsupported schema or roles')
    for role,spec in data['roles'].items():
        if not isinstance(spec.get('materials'),list) or not spec['materials']:
            raise OpError(f'wear role {role} needs materials')
        for path in spec['materials']:
            if not isinstance(path,str) or not path.startswith('materials/') or not path.endswith('.vmat'):
                raise OpError(f'wear role {role} has an invalid material')
        if len(spec.get('size',[]))!=2: raise OpError(f'wear role {role} needs [width,height]')
        for value in spec['size']: _num(value,f'{role} size',4,512)
        _num(spec.get('density'),f'{role} density',0,2)
        _num(spec.get('jitter'),f'{role} jitter',0,.5)
        _num(spec.get('alpha'),f'{role} alpha',0,1)
    return data


def _world(face,u,z,d=0):
    p=[0.,0.,float(z)]; axis=face['axis']
    p[axis]=face['plane']+face['normal'][axis]*d
    p[1-axis]=float(u)
    return p


def _area(poly):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1])))/2 if len(poly)>=3 else 0.


def _clip(poly,a,b,inside=True):
    """Clip to a directed half-plane; polygons are counterclockwise."""
    def side(p):
        v=(b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
        return v if inside else -v
    out=[]
    for p,q in zip(poly,poly[1:]+poly[:1]):
        sp,sq=side(p),side(q)
        if sp>=0: out.append(p)
        if (sp>=0)!=(sq>=0):
            t=sp/(sp-sq)
            out.append((p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1])))
    return out


def _rect(rect):
    a,b,c,d=rect
    return [(a,c),(b,c),(b,d),(a,d)]


def _intersects(a,b):
    return a[0]<b[1] and b[0]<a[1] and a[2]<b[3] and b[2]<a[3]


def _bounds(poly):
    return min(p[0] for p in poly),max(p[0] for p in poly),min(p[1] for p in poly),max(p[1] for p in poly)


def _ccw(poly):
    return list(reversed(poly)) if sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1]))<0 else poly


def _covered(rect,triangles):
    """Subtract triangle union from the whole quad, including internal holes.

    Unlike sampled rays or summed areas this cannot miss a small opening or
    double-count overlapping triangles. Remaining fragments are convex.
    """
    remaining=[_rect(rect)]
    for tri in triangles:
        if not _intersects(rect,_bounds(tri)): continue
        tri=_ccw(tri); next_remaining=[]
        for poly in remaining:
            inside=poly
            for a,b in zip(tri,tri[1:]+tri[:1]):
                outside=_clip(inside,a,b,False)
                if _area(outside)>1e-7: next_remaining.append(outside)
                inside=_clip(inside,a,b)
                if _area(inside)<1e-7: break
        remaining=next_remaining
        if not remaining: return True
    return sum(_area(p) for p in remaining)<1e-5


def _overlaps(rect,polys):
    for poly in polys:
        clipped=_rect(rect); poly=_ccw(poly)
        for a,b in zip(poly,poly[1:]+poly[:1]): clipped=_clip(clipped,a,b)
        if _area(clipped)>1e-5: return True
    return False


def _planes(triangles,axis,sign,other_axes,lo,hi):
    """Axis-aligned outward receiving planes and their exact projected area."""
    planes={}
    for tri in triangles:
        n=checks._cross(checks._sub(tri[1],tri[0]),checks._sub(tri[2],tri[0]))
        length=math.sqrt(checks._dot(n,n))
        if length<1e-8 or n[axis]*sign/length<.99999: continue
        plane=sum(p[axis] for p in tri)/3
        if not lo<=plane<=hi: continue
        planes.setdefault(round(plane,4),[]).append([(p[other_axes[0]],p[other_axes[1]]) for p in tri])
    return planes


def _triangles(m,root=None):
    out=[]
    for node in m.iter_nodes(root):
        if node.type!='CMapMesh': continue
        mats=node['meshData']['materials']
        if mats and all(str(p).startswith('materials/tools/') for p in mats): continue
        out.extend(checks._triangles(node))
    return out


def _props(project,m,face,house):
    """Measured world bounds protect nearby props, including signs and glass."""
    axis,other=face['axis'],1-face['axis']
    windows={facades.P[k][0] for k in ('glass','frame','french','closed','balcony_door')}
    pipes={facades.P['pipe'][0]}; out=[]
    for node in m.iter_nodes():
        if node.type!='CMapEntity': continue
        model=checks._props(node).get('model')
        if not model: continue
        origin=node.get('origin') or (0,0,0)
        if not (house['from']-160<=origin[other]<=house['to']+160
                and face['z0']-160<=origin[2]<=house['top']+160
                and abs(origin[axis]-face['plane'])<=180): continue
        lo,hi=facades._model_bounds(project,str(model))  # refuse missing bounds
        scale=node.get('scales') or (1,1,1); angles=node.get('angles') or (0,0,0)
        points=[structure._add(origin,structure._rotate((x*scale[0],y*scale[1],z*scale[2]),angles))
                for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
        ds=[(p[axis]-face['plane'])*face['normal'][axis] for p in points]
        if max(ds)<-24 or min(ds)>40: continue
        rect=_bounds([(p[other],p[2]) for p in points])
        kind=('pipe' if model in pipes else 'window' if model in windows or any(
            t in str(model).lower() for t in ('window','shutter','glass','bk01_w01')) else 'prop')
        out.append({'rect':rect,'kind':kind,'id':node['nodeID']})
    return sorted(out,key=lambda x:(x['rect'],x['kind']))


def _blocked(house,margin=4):
    return [(float(raw[0])-float(raw[1])/2-margin,float(raw[0])+float(raw[1])/2+margin)
            for key in ('doors','shops') for raw in house.get(key,[])
            if len(raw)>=2 and (len(raw)<3 or float(raw[2])>-24)]


def _intervals(lo,hi,blocked,margin=0):
    pieces=[(lo,hi)]
    for a,b in blocked:
        next_pieces=[]; a,b=a-margin,b+margin
        for x,y in pieces:
            if b<=x or a>=y: next_pieces.append((x,y))
            else:
                if a>x: next_pieces.append((x,a))
                if b<y: next_pieces.append((b,y))
        pieces=next_pieces
    return [(a,b) for a,b in pieces if b-a>1]


def _wall_patch(face,house,planes,protected,u,z,size):
    rect=(u-size[0]/2,u+size[0]/2,z-size[1]/2,z+size[1]/2)
    if (rect[0]<house['from']+1 or rect[1]>house['to']-1
            or rect[2]<face['z0']+.5 or rect[3]>house['top']-16): return None
    if any(_intersects(rect,(a-2,b+2,c-2,d+2)) for a,b,c,d in (p['rect'] for p in protected)): return None
    # Nearest centre hit must support the entire quad on one plane.
    for plane in sorted(planes,key=lambda p:p*face['normal'][face['axis']],reverse=True):
        if _covered((u-.01,u+.01,z-.01,z+.01),planes[plane]):
            if not _covered(rect,planes[plane]): return None
            for front,polys in planes.items():
                if (front-plane)*face['normal'][face['axis']]>.01:
                    if _overlaps(rect,polys): return None
            p=_world(face,u,z); p[face['axis']]=plane
            return p
    return None


def _ground_patch(face,u,d,size,floor_planes,paved_areas=None):
    p=_world(face,u,face['z0'],d)
    rect=(p[0]-size[0]/2,p[0]+size[0]/2,p[1]-size[1]/2,p[1]+size[1]/2)
    if paved_areas is not None and not _covered(rect,paved_areas): return None
    # Only street-level solid planes admitted, no rooftop ray and no fallback.
    for z in sorted(floor_planes,reverse=True):
        if _covered((p[0]-.01,p[0]+.01,p[1]-.01,p[1]+.01),floor_planes[z]):
            if _covered(rect,floor_planes[z]):
                if any(front>z+.01 and _overlaps(rect,polys) for front,polys in floor_planes.items()):return None
                p[2]=z; return p
            return None
    return None


def _size(spec,rng,cap=None):
    factor=rng.uniform(1-spec['jitter'],1+spec['jitter'])
    w,h=(v*factor for v in spec['size'])
    if cap is not None and w>cap: w,h=cap,h*cap/w
    return w,h


def _uv(node,crop,flip=False,turns=0):
    """Select one inspected atlas strip, preserving upright wall artwork."""
    x0,y0,x1,y1=crop; md=node['meshData']
    stream=next(s for s in md['faceVertexData']['streams'] if s.get('semanticName')=='texcoord')
    data=[]
    for u,v in stream['data']:
        for _ in range(turns%4): u,v=v,1-u
        if flip: u=1-u
        data.append((x0+u*(x1-x0),y0+v*(y1-y0)))
    stream['data']=data


def _feather_leak(node,normal,size,material,texture_size):
    """Vertex alpha fades the sides/bottom of the inspected streak strip."""
    origin=node['origin']
    source=structure._decal_mesh(origin,normal,size,material,texture_size)
    face=source.faces[0]; u=face.u_axis; v=structure._scale(face.v_axis,-1)
    patch=mesh.Mesh()
    def point(s,t):
        return structure._add(origin,structure._add(structure._scale(u,(s-.5)*size[0]),
                                                   structure._scale(v,(t-.5)*size[1])))
    def color(s,t):
        return (1.,1.,1.,min(1.,s/.15,(1-s)/.15)*min(1.,t/.2))
    for a,b in ((0,.15),(.15,.85),(.85,1)):
        for c,d in ((0,.2),(.2,1)):
            st=((a,c),(b,c),(b,d),(a,d))
            patch.face_out([point(s,t) for s,t in st],normal,material,
                u_axis=face.u_axis,v_axis=face.v_axis,scale=face.scale,offset=face.offset,
                colors=[color(s,t) for s,t in st])
    local=mesh.Mesh().extend(patch,structure._scale(origin,-1))
    node['meshData']=local.to_dmx(origin=origin,texture_size=texture_size)


def _emit(project,m,group,role,spec,rng,origin,normal,size,anchor):
    material=rng.choice(spec['materials'])
    result=structure.add_decal(project,m,{'op':'add_decal','name':f'wear:{role}',
        'material':material,'origin':origin,'normal':normal,'size':list(size),
        'projection_far':1.,'offset':.08,'parent':group['nodeID'],
        'material_adjustments':{'ColorAlpha':spec['alpha'],'ShadingAlpha':spec['alpha']}},{})
    node=m.node(result['id'])
    if role=='leak': _feather_leak(node,normal,size,material,project.texture_size(material))
    _uv(node,spec.get('uv',[0,0,1,1]),flip=spec.get('mirror',False) and rng.random()<.5,
        turns=rng.randrange(4) if role.startswith('ground_') else 0)
    node.set('cs2map_wear_decal','string',json.dumps({'role':role,'anchor':anchor,
        'size':list(size),'normal':normal,'surface':origin},separators=(',',':')))
    return role


def _house_plan(project,m,meta,house,ground_triangles,paved_areas=None):
    face={k:meta[k] for k in ('axis','plane','normal','z0','top')}
    axis=face['axis']; sign=face['normal'][axis]
    planes=_planes(_triangles(m,m.node(int(house['id']))),axis,sign,(1-axis,2),face['plane']-10,face['plane']+10)
    planes={p:polys for p,polys in planes.items() if (p-face['plane'])*sign>=-.01}
    if not planes: raise OpError('facade metadata has no receiving front; rebuild its row')
    return (face,house,planes,_props(project,m,face,house),
            _planes(ground_triangles,2,1,(0,1),face['z0']-8,face['z0']+18),paved_areas)


def _place_house(project,m,group,profile,plan,density,seed,row_name,ground):
    face,house,planes,props,floors,paved_areas=plan
    key=f'{seed}|{row_name}|{house["from"]}|{house["to"]}'
    rng=random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'little'))
    u0,u1,z0=house['from'],house['to'],face['z0']; counts={}
    def accept(role): return rng.random()<density/2*profile['roles'][role]['density']
    def wall(role,u,z,size,anchor):
        hit=_wall_patch(face,house,planes,props,u,z,size)
        if hit is None: return False
        artwork_rng=random.Random(f'{key}|{role}|{u}|{z}')
        _emit(project,m,group,role,profile['roles'][role],artwork_rng,hit,face['normal'],size,anchor)
        counts[role]=counts.get(role,0)+1
        return True
    # Split the band at every measured low obstacle as well as entrances.
    low_blocks=_blocked(house)+[(a-3,b+3) for a,b,c,d in (p['rect'] for p in props)
                               if c<z0+26 and d>z0]
    for a,b in _intervals(u0+3,u1-3,low_blocks):
        cursor=a
        while cursor<b-12:
            size=_size(profile['roles']['base_dirt'],rng,min(b-cursor,100))
            u=cursor+size[0]/2; z=z0+size[1]/2+.75; take=accept('base_dirt')
            if take:
                for factor in (1,.65,.4):
                    scaled=(size[0]*factor,size[1]*factor)
                    if wall('base_dirt',u,z0+scaled[1]/2+.75,scaled,'plinth'): break
            cursor+=size[0]+rng.uniform(2,10)
    # Measured sill bottoms and downpipe bounds, predominantly low floors.
    anchors=[]
    for prop in props:
        a,b,c,d=prop['rect']
        if prop['kind']=='window' and c-z0<300: anchors.append(((a+b)/2,c-3,'sill'))
        elif prop['kind']=='pipe' and z0+45<c<z0+300:
            anchors.extend(((a-15,c+3,'downpipe'),(b+15,c+3,'downpipe')))
    anchors=sorted(set((round(u,1),round(z,1),kind) for u,z,kind in anchors)); occupied=[]
    for u,top,kind in anchors:
        size=_size(profile['roles']['leak'],rng); take=accept('leak')
        if take and not any(abs(u-a)<22 and abs(top-b)<60 for a,b in occupied):
            for height in (size[1],size[1]*.65,size[1]*.4):
                if wall('leak',u,top-height/2,(size[0],height),kind):
                    occupied.append((u,top)); break
    # Dark drip band under the cornice: de_inferno's most used wall decal.
    if accept('eave_grime'):
        spec=profile['roles']['eave_grime']; cursor=u0+6
        while cursor<u1-30:
            size=_size(spec,rng,min(u1-6-cursor,140))
            u=cursor+size[0]/2
            for drop in range(16,70,6):
                if wall('eave_grime',u,house['top']-drop-size[1]/2,size,'cornice'): break
            cursor+=size[0]+rng.uniform(4,30)
    # Lower corners and the band above measured entrances.
    for role in ('crack','plaster_loss'):
        spec=profile['roles'][role]
        for edge in (u0,u1):
            size=_size(spec,rng)
            if role=='plaster_loss':
                # Inferno's strips: broken plaster edge on top, brick toward the
                # bottom - they sit low, just above the plinth, near corners
                # (quoins, pipes and doors there: walk inward to a free span).
                if not accept(role): continue
                sign=1 if edge==u0 else -1; done=False
                for factor in (1,.7,.5):
                    w,hgt=size[0]*factor,size[1]*max(factor,.75)
                    for inset in range(8,200,16):
                        u=edge+sign*(w/2+inset+rng.uniform(0,6))
                        if any(wall(role,u,z0+lift+hgt/2,(w,hgt),'corner') for lift in (26,30,34,40)):
                            done=True; break
                    if done: break
                continue
            u=edge+(size[0]/2+rng.uniform(32,50))*(1 if edge==u0 else -1)
            z=z0+rng.uniform(48,125); take=accept(role)
            if take: wall(role,u,z,size,'corner')
        for raw in house.get('doors',[]):
            size=_size(spec,rng); take=accept(role)
            if len(raw)>2 and raw[2]<-24: continue
            candidates=[p['rect'][3] for p in props if p['rect'][0]<=raw[0]<=p['rect'][1]
                        and p['rect'][2]<z0+20 and p['rect'][3]<z0+180]
            if candidates and take: wall(role,raw[0],max(candidates)+size[1]/2+5,size,'lintel')
    for role in ('poster','graffiti'):
        spec=profile['roles'][role]; take=accept(role)
        candidates=[(rng.uniform(u0+20,u1-20),z0+rng.uniform(65,105),_size(spec,rng)) for _ in range(8)]
        if take and not house.get('arcade'):
            for u,z,size in candidates:
                if wall(role,u,z,size,'pedestrian'): break
    if ground:
        for role in ('ground_stain','ground_dirt'):
            spec=profile['roles'][role]; size=_size(spec,rng); along,cross=size
            u=rng.choice((u0+along/2+8,u1-along/2-8)); d=cross/2+rng.uniform(6,14)
            actual=size if face['axis']==1 else (cross,along); take=accept(role)
            if not take or u0+1>u-along/2 or u+along/2>u1-1: continue
            p=None
            for distance in (d,cross/2+6,cross/2+32):
                p=_ground_patch(face,u,distance,actual,floors,paved_areas)
                if p is not None: break
            if p is not None:
                _emit(project,m,group,role,spec,random.Random(f'{key}|{role}|{u}'),p,[0.,0.,1.],actual,'wall_corner')
                counts[role]=counts.get(role,0)+1
    return counts


def _facades(m,rows):
    result=[]
    for node in m.iter_nodes():
        raw=node.get(FACADE_KEY)
        if not raw: continue
        try: meta=json.loads(raw)
        except (ValueError,TypeError): raise OpError('malformed cs2map_facade metadata') from None
        if rows is not None and meta.get('name') not in rows: continue
        if meta.get('schema')!=2 or not isinstance(meta.get('houses'),list):
            raise OpError('add_wear requires cs2map_facade schema 2')
        if meta.get('axis') not in (0,1) or meta.get('normal') not in ([1,0,0],[-1,0,0],[0,1,0],[0,-1,0]):
            raise OpError('invalid facade axis/normal')
        if abs(meta['normal'][meta['axis']])!=1: raise OpError('facade axis does not match normal')
        for k in ('plane','z0','top'): _num(meta.get(k),f'facade {k}',-32768,32768)
        for house in meta['houses']:
            if not isinstance(house,dict): raise OpError('invalid facade house')
            for k in ('from','to','top'): _num(house.get(k),f'house {k}',-32768,32768)
            if house['to']-house['from']<40 or house['top']<=meta['z0']: raise OpError('invalid facade house span')
        result.append(meta)
    return sorted(result,key=lambda x:x['name'])


def _paved_areas(m):
    """Footprint of the rooms and sidewalks explicitly named by paving schema 1."""
    plans={p['name']:p for p in layout.plans(m)}
    areas=[]
    for n in m.iter_nodes():
        raw=n.get(PAVING_KEY)
        if not raw: continue
        try:
            data=json.loads(raw)
            if data.get('schema')!=1: raise ValueError('unsupported schema')
            boxes=list(data.get('sidewalks',[]))
            rooms=plans.get(data.get('layout'),{}).get('rooms',{})
            boxes += [rooms[r]['interior'] for r in data.get('rooms',[]) if r in rooms]
            for box in boxes:
                lo,hi=box['mins'],box['maxs']
                rect=_rect((lo[0],hi[0],lo[1],hi[1]))
                areas.extend(([rect[0],rect[1],rect[2]],[rect[0],rect[2],rect[3]]))
        except (ValueError,TypeError,KeyError,IndexError):
            raise OpError('invalid cs2map_paving metadata') from None
    return areas


def add_wear(project,vmap,op,refs):
    extra=set(op)-{'op','name','rows','style','density','seed','ground','ref'}
    if extra: raise OpError(f'add_wear unknown field(s): {sorted(extra)}')
    name=op.get('name')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,59}',name):
        raise OpError('name must be 1..59 letters, digits, _ or -')
    ref=op.get('ref',f'wear_{name.replace("-","_")}')
    if not isinstance(ref,str) or not re.fullmatch(r'\w{1,64}',ref): raise OpError('invalid wear ref')
    if any(n.get(project.REF_KEY)==ref or (ref in refs and n.get('nodeID')==refs[ref])
           for n in vmap.iter_nodes()): raise OpError(f'ref {ref!r} is already used')
    if any(n.name==f'wear:{name}' for n in vmap.iter_nodes()): raise OpError('wear group already exists; delete it first')
    profile=_profile(op.get('style','inferno'))
    density=_num(op.get('density',1.),'density',0,2)
    seed=_integer(op.get('seed',7),'seed',-(2**63),2**63-1)
    ground=op.get('ground',True)
    if type(ground) is not bool: raise OpError('ground must be boolean')
    raw_rows=op.get('rows','all')
    if raw_rows=='all': rows=None
    elif isinstance(raw_rows,list) and raw_rows and all(isinstance(x,str) and x for x in raw_rows): rows=set(raw_rows)
    else: raise OpError('rows must be "all" or a non-empty list of row names')
    found=_facades(vmap,rows)
    if rows is not None and rows-{x['name'] for x in found}: raise OpError('facade row(s) not found')
    if not found: raise OpError('add_wear needs cs2map_facade rows')
    # Paving metadata opts in to ground wear. Mesh tops limited to street level
    # are the receiving authority.
    paved_areas=_paved_areas(vmap) if ground else []
    ground=ground and bool(paved_areas)
    ground_tris=[t for n,b,ts in layout.world_solids(vmap) for t in ts
                 if not all(str(p).startswith('materials/tools/') for p in n['meshData']['materials'])] if ground else []
    plans=[(meta['name'],_house_plan(project,vmap,meta,h,ground_tris,paved_areas)) for meta in found
           for h in sorted(meta['houses'],key=lambda h:(h['from'],h['to']))] if density else []
    # Bounds and materials are checked before mutating the map.
    for spec in profile['roles'].values():
        for material in spec['materials']: structure._material_path(project,material)
    group=vmap.new_group(f'wear:{name}')
    structure._set_group_defaults(group); group['randomSeed']=abs(seed)%2147483646+1
    vmap.attach(group); counts={}
    try:
        for row,plan in plans:
            current=_place_house(project,vmap,group,profile,plan,density,seed,row,ground)
            for role,count in current.items(): counts[role]=counts.get(role,0)+count
    except Exception:
        vmap.remove(group)
        raise
    refs[ref]=group['nodeID']; group.set(project.REF_KEY,'string',ref)
    group.set(WEAR_KEY,'string',json.dumps({'schema':1,'name':name,'style':profile['style'],
        'seed':seed,'density':density,'rows':[x['name'] for x in found],'ground':ground,'roles':counts},separators=(',',':')))
    return {'op':'add_wear','id':group['nodeID'],'ref':ref,'name':name,'decals':sum(counts.values()),
            'roles':counts,'rows':len(found),'ground':ground,'seed':seed}


def material_candidates(project,query='de_inferno decals',limit=50):
    if not isinstance(query,str): raise OpError('query must be a string')
    _integer(limit,'limit',1,200)
    return project.asset_search(query,'material',limit)
