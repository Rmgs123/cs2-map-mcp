"""Persisted triangulated terrain, vertex blends and conservative stock scattering."""
from __future__ import annotations

import copy
import json
import math
import random
import re
from functools import lru_cache
from pathlib import Path

from . import mesh, shapes, checks
from .config import PROJECT_ROOT, ensure_inside
from .project import OpError

PLAN_KEY = 'cs2map_terrain'
STREET_KEY = 'cs2map_street'
JUNCTION_KEY = 'cs2map_junction'
DEFAULTS = {'grass': 'materials/nature/hr_c/hr_grass_dirt_blend_001.vmat',
            'dirt': 'materials/de_overpass/nature/dirt_ground_1.vmat',
            'rock': 'materials/de_overpass/nature/rock_pile_1.vmat',
            'sides': 'materials/tools/toolsnodraw.vmat'}
FALLBACK_MODELS = {
    'vegetation_med': ['models/props/de_inferno/tree_large.vmdl',
                       'models/props/de_inferno/bushgreensmall.vmdl'],
    'rocks': ['models/props/de_inferno/hr_i/rock_collection/rock_collection_b.vmdl']}


def number(value, label, low=-32768, high=32768):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise OpError(f'{label} must be a finite number in {low}..{high}')
    return float(value)


def integer(value, label, low, high):
    v = number(value, label, low, high)
    if v != int(v):
        raise OpError(f'{label} must be an integer')
    return int(v)


def vec(value, label, n=2):
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise OpError(f'{label} needs {n} numbers')
    return [number(x, label) for x in value]


def keys(obj, allowed, label):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise OpError(f'{label}: expected object with fields {", ".join(sorted(allowed))}')


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', value):
        raise OpError('name must be 1..64 letters/digits/underscores')
    return value


def polygon(value):
    if not isinstance(value, list) or not 3 <= len(value) <= 128:
        raise OpError('polygon needs 3..128 points')
    pts = [vec(v, 'polygon') for v in value]
    if abs(mesh._polygon_area(pts)) < 1e-5:
        raise OpError('polygon has zero area')
    # Reject self intersections, including collinear overlapping nonadjacent edges.
    def cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for i,(a,b) in enumerate(zip(pts,pts[1:]+pts[:1])):
        if math.dist(a,b)<.01: raise OpError('polygon has duplicate adjacent points')
        for j in range(i+2,len(pts)):
            if i==0 and j==len(pts)-1:continue
            c,d=pts[j],pts[(j+1)%len(pts)]
            if (cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0
                    and all(max(min(a[k],b[k]),min(c[k],d[k]))<=min(max(a[k],b[k]),max(c[k],d[k])) for k in (0,1))):
                raise OpError('polygon self intersects')
    return pts


def inside(x,y,poly):
    result=False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        if ((a[1]>y)!=(b[1]>y)) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: result=not result
    return result


def path(value, z=False):
    if not isinstance(value, list) or not 2 <= len(value) <= 128:
        raise OpError('path needs 2..128 points')
    pts=[]
    for v in value:
        if z and isinstance(v,(list,tuple)) and len(v)==3: pts.append(vec(v,'path',3))
        else: pts.append(vec(v,'path'))
    for a,b in zip(pts,pts[1:]):
        if math.dist(a[:2],b[:2])<.01: raise OpError('path has zero length segment')
    return pts


def nearest_path(x,y,points):
    best=None
    for i,(a,b) in enumerate(zip(points,points[1:])):
        dx,dy=b[0]-a[0],b[1]-a[1]
        t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
        q=[a[0]+dx*t,a[1]+dy*t]
        dist=math.hypot(x-q[0],y-q[1])
        if best is None or dist<best[0]:best=(dist,i,t,q)
    return best


def smooth(t):
    t=max(0,min(1,t)); return t*t*(3-2*t)


def bounds(value):
    if isinstance(value,dict):
        keys(value,{'mins','maxs'},'bounds'); lo,hi=value.get('mins'),value.get('maxs')
    elif isinstance(value,(list,tuple)) and len(value)==2: lo,hi=value
    else: raise OpError('bounds must be [mins,maxs] or {mins,maxs}')
    n=len(lo) if isinstance(lo,(list,tuple)) else 0
    if n not in (2,3):raise OpError('bounds need XY or XYZ')
    lo,hi=vec(lo,'bounds mins',n),vec(hi,'bounds maxs',n)
    if any(lo[i]>=hi[i] for i in range(2)):raise OpError('bounds need increasing XY')
    return lo,hi


def sample(data,x,y):
    """Height and normal of the actual p00--p11 triangles, never bilinear height."""
    lo,hi=data['bounds']; nx,ny=data['cells']
    if not lo[0]-1e-4<=x<=hi[0]+1e-4 or not lo[1]-1e-4<=y<=hi[1]+1e-4:
        raise OpError('point outside terrain bounds')
    dx,dy=(hi[0]-lo[0])/nx,(hi[1]-lo[1])/ny
    u=max(0,min(nx,(x-lo[0])/dx)); v=max(0,min(ny,(y-lo[1])/dy))
    i,j=min(nx-1,int(u)),min(ny-1,int(v)); a,b=u-i,v-j
    h=data['heights']; z00,z10,z11,z01=h[i][j],h[i+1][j],h[i+1][j+1],h[i][j+1]
    if b<=a: gx,gy=(z10-z00)/dx,(z11-z10)/dy; z=z00+a*(z10-z00)+b*(z11-z10)
    else: gx,gy=(z11-z01)/dx,(z01-z00)/dy; z=z00+a*(z11-z01)+b*(z01-z00)
    normal=mesh._norm((-gx,-gy,1))
    return {'z':round(z,4),'normal':list(normal),'slope':math.degrees(math.atan(math.hypot(gx,gy)))}


def plan(project,op):
    keys(op,{'op','name','bounds','cell','base_z','heightmap','height_range','ops','materials','blend','rock_slope','dirt_height','thickness','paint','ref'},'terrain')
    nm=name(op.get('name','terrain')); lo,hi=bounds(op.get('bounds'))
    base=number(op.get('base_z',lo[2] if len(lo)==3 else 0),'base_z')
    cell=number(op.get('cell',128),'cell',32,256)
    nx,ny=[math.ceil((hi[k]-lo[k])/cell) for k in (0,1)]
    if nx>512 or ny>512:raise OpError('terrain exceeds 512x512 cells; increase cell or split terrains')
    lo,hi=lo[:2],hi[:2]; xs=[lo[0]+(hi[0]-lo[0])*i/nx for i in range(nx+1)]; ys=[lo[1]+(hi[1]-lo[1])*j/ny for j in range(ny+1)]
    data={'version':1,'name':nm,'bounds':[lo,hi],'cells':[nx,ny],'heights':[[base]*(ny+1) for _ in range(nx+1)]}
    if 'heightmap' in op:
        if 'height_range' not in op:raise OpError('heightmap requires height_range [min,max]')
        source=ensure_inside(Path(op['heightmap']),PROJECT_ROOT,project.s.content_addon)
        if source.suffix.lower()!='.png' or not source.is_file():raise OpError('heightmap must be a local project/addon grayscale PNG')
        from PIL import Image
        with Image.open(source) as image:
            if image.mode not in ('L','I;16','I;16L','I;16B'):raise OpError('heightmap must be grayscale L or 16-bit PNG')
            if image.width*image.height>16777216:raise OpError('heightmap exceeds 16 million pixels')
            pixel=image.convert('F').resize((nx+1,ny+1),Image.Resampling.BILINEAR)
            hr=vec(op['height_range'],'height_range'); maximum=255 if image.mode=='L' else 65535
            if hr[1]<=hr[0]:raise OpError('height_range must increase')
            data['heights']=[[round(hr[0]+pixel.getpixel((i,j))/maximum*(hr[1]-hr[0]),4) for j in range(ny+1)] for i in range(nx+1)]
        data['heightmap']=str(source)
    elif 'height_range' in op:raise OpError('height_range requires heightmap')
    ops=op.get('ops',[])
    if not isinstance(ops,list) or len(ops)>128:raise OpError('ops must be at most 128 operations')
    for spec in ops:
        if not isinstance(spec,dict):raise OpError('terrain operation must be an object')
        kind=spec.get('op',spec.get('type'))
        common={'op','type'}
        if kind in ('hill','valley'):
            keys(spec,common|{'center','radius','height'},kind)
            c=vec(spec.get('center'),'center'); r=number(spec.get('radius'),'radius',1,32768); h=number(spec.get('height'),'height')
            def update(x,y,z):return z+(abs(h)*(-1 if kind=='valley' else 1))*smooth(1-math.dist([x,y],c)/r)
        elif kind=='noise':
            keys(spec,common|{'amplitude','scale','seed'},kind)
            amp=number(spec.get('amplitude',32),'amplitude',0,4096); sc=number(spec.get('scale',512),'scale',32,32768)
            seed=integer(spec.get('seed',0),'seed',-2147483648,2147483647); noise=shapes._value_noise(seed)
            def update(x,y,z):return z+amp*noise(x/sc,y/sc)
        elif kind in ('ridge','flatten'):
            keys(spec,common|({'path','width','height'} if kind=='ridge' else {'path','width','z','falloff'}),kind)
            pts=path(spec.get('path')); width=number(spec.get('width'),'width',1,16384)
            if kind=='ridge':
                height=number(spec.get('height'),'height')
                def update(x,y,z):return z+height*smooth(1-nearest_path(x,y,pts)[0]/(width/2))
            else:
                falloff=number(spec.get('falloff',128),'falloff',0,8192); target=spec.get('z','follow')
                zs=[sample(data,*v)['z'] for v in pts] if target=='follow' else [number(target,'flatten z')]*len(pts)
                def update(x,y,z):
                    d,i,t,_=nearest_path(x,y,pts); w=1 if d<=width/2 else 0 if falloff==0 else 1-smooth((d-width/2)/falloff)
                    return z+w*(zs[i]+t*(zs[i+1]-zs[i])-z)
        elif kind=='plateau':
            keys(spec,common|{'polygon','z','falloff'},kind); poly=polygon(spec.get('polygon'))
            target=number(spec.get('z'),'plateau z'); falloff=number(spec.get('falloff',128),'falloff',0,8192)
            def update(x,y,z):
                d=nearest_path(x,y,poly+poly[:1])[0]; w=1 if inside(x,y,poly) or d<1e-5 else 0 if falloff==0 else 1-smooth(d/falloff)
                return z+w*(target-z)
        elif kind=='bank':
            # Cuts the ground on the low side under a steep face (add_bank dresses it): 64-unit cells cannot hold
            # 60 degrees on their own, and an uncut gentle slope would cover the face (t11_builders, 25.10).
            # The face goes down to the lower level: the lowest ground within `width` of the path, per section.
            keys(spec,common|{'path','side','angle','verge','sink','width'},kind)
            pts=path(spec.get('path'),z=True); side=spec.get('side','right')
            if side not in ('left','right'):raise OpError('bank side is where the lower ground is: left or right')
            rise=math.tan(math.radians(number(spec.get('angle',60),'angle',46,85)))
            verge=number(spec.get('verge',24),'verge',0,256); sink=number(spec.get('sink',8),'sink',2,64)
            width=number(spec.get('width',512),'width',64,4096)
            sign=1 if side=='left' else -1; angle=math.degrees(math.atan(rise))
            def ground_at(x,y):
                return sample(data,min(hi[0],max(lo[0],x)),min(hi[1],max(lo[1],y)))['z']
            # Per segment, every 32 units: the top (the ground on the line, or the path's z) and the lower level.
            tops,lows=[],[]
            for a,b in zip(pts,pts[1:]):
                ln=math.dist(a[:2],b[:2]); nx,ny=-sign*(b[1]-a[1])/ln,sign*(b[0]-a[0])/ln; n=max(1,math.ceil(ln/32))
                at=[[a[0]+(b[0]-a[0])*k/n,a[1]+(b[1]-a[1])*k/n] for k in range(n+1)]
                tops.append([round(a[2]+(b[2]-a[2])*k/n if len(a)==3 and len(b)==3 else ground_at(*q),4) for k,q in enumerate(at)])
                lows.append([min(ground_at(q[0]+nx*r,q[1]+ny*r) for r in range(0,int(width)+1,16)) for q in at])
            # add_bank over this cut takes its tops from here: the face then lies exactly `sink` above the cut.
            data.setdefault('bank_cuts',[]).append({'path':[v[:2] for v in pts],'side':side,'tops':tops,
                                                     'angle':angle,'verge':verge,'sink':sink})
            def along(rows,i,t):
                row=rows[i]; k=t*(len(row)-1); k0=min(len(row)-2,int(k)); return row[k0]+(k-k0)*(row[k0+1]-row[k0])
            def update(x,y,z):
                d,i,t,_=nearest_path(x,y,pts); a,b=pts[i],pts[i+1]; ln=math.dist(a[:2],b[:2])
                u=((x-a[0])*(b[0]-a[0])+(y-a[1])*(b[1]-a[1]))/ln
                past=max(-u if i==0 else 0,u-ln if i==len(pts)-2 else 0)
                cross=sign*((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]))
                if cross<=0 or cross/ln>width:return z                               # the upper side, or too far
                d=cross/ln                                   # square to the line past the ends, as at the end section
                low=along(lows,i,t); top=along(tops,i,t)-sink; toe=verge+max(0,top-low)/rise
                if past>0:
                    # Past an end the cut climbs away at the same angle: the bank turns the corner into the ground
                    # and its end section stays buried (a straight cut showed a flat end wall, t11_builders 25.10).
                    # The lowered yard rises there too, gently (about 27 degrees), into the uncut ground.
                    plane=top-max(0,d-verge)*rise+past*rise; low+=past*.5
                elif d>toe-cell*math.sqrt(2) and d>verge:
                    # Within a cell before the toe the ground drops to the lower level at once: a grid triangle from
                    # the face down to the level would otherwise rise through the face near the toe (teeth, 25.10).
                    plane=low
                else:
                    plane=top-max(0,d-verge)*rise
                return min(z,max(low,plane))
        else:raise OpError(f'unknown terrain op {kind!r}; hill/valley/ridge/plateau/noise/flatten/bank')
        data['heights']=[[round(update(x,y,data['heights'][i][j]),4) for j,y in enumerate(ys)] for i,x in enumerate(xs)]
    values=[z for row in data['heights'] for z in row]
    thickness=number(op.get('thickness',32),'thickness',1,1024)
    if min(values)-thickness < -32768 or max(values)>32768:raise OpError('terrain exceeds world Z limits')
    data.update({'bottom':min(values)-thickness,'height_range':[min(values),max(values)],'ops':copy.deepcopy(ops),
                 'paint':paint_plan(op.get('paint',[]))})
    return data


def paint_plan(value):
    """paint: dirt tracks along paths [{path, width, falloff}] (dirt roads, trails). Blend terrains get a soft dirt
    band through the vertex blend; per-face terrains get the dirt material on faces within width/2."""
    if not isinstance(value,list) or len(value)>32:raise OpError('paint must be a list of at most 32 {path,width,falloff}')
    out=[]
    for spec in value:
        keys(spec,{'path','width','falloff'},'paint')
        out.append({'path':path(spec.get('path')),'width':number(spec.get('width',200),'paint width',8,4096),
                    'falloff':number(spec.get('falloff',64),'paint falloff',0,2048)})
    return out


def paint_weight(paint,x,y):
    """0..1 dirt weight of the paint tracks at x,y (1 inside width/2, smooth falloff outside)."""
    best=0.
    for spec in paint:
        d=nearest_path(x,y,spec['path'])[0]; half=spec['width']/2
        w=1. if d<=half else 0. if spec['falloff']==0 else 1-smooth((d-half)/spec['falloff'])
        best=max(best,w)
    return best


def plans(m,key=PLAN_KEY):
    result=[]
    for n in m.iter_nodes():
        if key not in n:continue
        try:
            data=json.loads(n[key])
            if data['version']!=1:raise ValueError('unsupported version')
            if any(abs(x)>1e-5 for x in n.get('angles',(0,0,0))) or any(abs(x-1)>1e-5 for x in n.get('scales',(1,1,1))):
                raise ValueError('rotated/scaled plan')
            delta=[n['origin'][i]-data.get('origin',[0,0,0])[i] for i in range(3)]
            # Core move translates geometry as well as group origin; allow only that transform.
            if key==PLAN_KEY:
                data['bounds']=[[v[i]+delta[i] for i in (0,1)] for v in data['bounds']]
                data['heights']=[[z+delta[2] for z in row] for row in data['heights']]
                data['bottom']+=delta[2]; data['height_range']=[z+delta[2] for z in data['height_range']]
                for cut in data.get('bank_cuts',[]):
                    cut['path']=[[x+delta[0],y+delta[1]] for x,y in cut['path']]
                    cut['tops']=[[z+delta[2] for z in row] for row in cut['tops']]
            elif key==JUNCTION_KEY:
                data['polygon']=[[v[i]+delta[i] for i in (0,1)] for v in data['polygon']]
                data['cuts']=[[[v[i]+delta[i] for i in (0,1)] for v in tri] for tri in data['cuts']]
            else:
                data['path']=[[v[i]+delta[i] for i in (0,1,2)] for v in data['path']]
                for surface in data.get('surfaces',[]):
                    surface['poly']=[[v[i]+delta[i] for i in (0,1)] for v in surface['poly']]
                    surface['vertices']=[[v[i]+delta[i] for i in (0,1,2)] for v in surface['vertices']]
                    for field in ('a','b'):surface[field]=[surface[field][i]+delta[i] for i in (0,1,2)]
                for region in data.get('exclusions',[]):region['polygon']=[[v[i]+delta[i] for i in (0,1)] for v in region['polygon']]
                for lot in data.get('lots',[]):
                    for k in ('mins','maxs'):lot[k]=[v+delta[i] for i,v in enumerate(lot[k])]
                    if 'footprint' in lot:
                        for k in ('mins','maxs'):lot['footprint'][k]=[v+delta[i] for i,v in enumerate(lot['footprint'][k])]
                    lot['origin']=[v+delta[i] for i,v in enumerate(lot['origin'])]
                    lot['polygon']=[[v[i]+delta[i] for i in (0,1)] for v in lot['polygon']]
            data['id']=n['nodeID']; result.append(data)
        except (KeyError,ValueError,TypeError) as e:raise OpError(f'invalid {key} on group {n["nodeID"]}: {e}') from None
    return result


def select(m,nm):
    found=[v for v in plans(m) if v['name']==nm]
    if len(found)!=1:raise OpError(f'terrain {nm!r}: {len(found)} plans; create it first or disambiguate duplicates')
    return found[0]


def material(project,value):
    if not isinstance(value,str) or not value.startswith('materials/') or not value.endswith('.vmat') or '..' in value.split('/') or not checks._asset_exists(project,value):
        raise OpError(f'missing/invalid material {value!r}; use asset_search')
    return value


def blend_node(node,weight):
    """Observed Valve stream: BlendParams.x blends layer 2, tint remains white."""
    md=node['meshData']; pos=md['vertexData']['streams'][0]['data']; origin=node['origin']
    dest=md['edgeVertexIndices']; fs=md['edgeFaceIndices']
    values=[]
    for vi,fi in zip(dest,fs):
        p=[pos[vi][k]+origin[k] for k in range(3)]
        values.append((float(weight(p)) if fi>=0 else 0.,0.,0.,0.))
    streams=list(md['faceVertexData']['streams'])
    streams=[s for s in streams if s.name not in ('VertexPaintBlendParams:0','VertexPaintTintColor:0')]
    streams += [mesh._paint_stream('VertexPaintBlendParams',values),mesh._paint_stream('VertexPaintTintColor',[(1.,1.,1.,1.)]*len(dest))]
    md['faceVertexData']['streams']=streams


def commit(m,op,nm,nodes,metadata,key,refs):
    group=m.new_group(f'{key.removeprefix("cs2map_")}:{nm}')
    metadata={**metadata,'origin':list(group['origin'])}
    group.set(key,'string',json.dumps(metadata,separators=(',',':')))
    for n in nodes:m.attach(n,group)
    m.attach(group)
    if op.get('ref'):refs[op['ref']]=group['nodeID']
    return group['nodeID']


def layer_node(m, rel):
    """A CMapPrefab that pulls another vmap into the compiled world (the fields of a prefab Hammer saves; the
    compiler reads targetMapPath, verified 25.10 on t11_layer)."""
    from .dmx import Element
    node=Element('CMapPrefab','')
    m._node_common(node)
    m._transform(node,(0.0,0.0,0.0),(0.0,0.0,0.0),(1.0,1.0,1.0))
    for key,kind,value in (('targetMapPath','string',rel),('targetName','string',''),('fixupEntityNames','bool',True),
                           ('useTargetNameAsPrefix','bool',False),('loadIfNested','bool',True),('visexclude','bool',False),
                           ('loadAtRuntime','bool',False),('variableOverrideNames','string_array',[]),
                           ('variableOverrideValues','string_array',[]),('editorOnly','bool',False),('force_hidden','bool',False)):
        node.set(key,kind,value)
    return node


def _write_layer(project,m,name,nodes):
    """The terrain meshes in their own file maps/prefabs/e_<map>_<terrain>.vmap: a map save no longer rewrites
    them (10.10: a 257x291 terrain made every save of the town map 48 MB)."""
    from .project import DRY_RUN
    from .vmap import VMap
    if m.path is None:
        raise OpError('layer:true needs a saved map (map_create first)')
    stem=f'e_{Path(m.path).stem}_{name}'
    path=project.s.content_addon/'maps'/'prefabs'/f'{stem}.vmap'
    rel=f'maps/prefabs/{stem}.vmap'
    if DRY_RUN.get():
        return rel
    target=VMap.from_template(project.s.cs2_path,'empty')
    target.world['children']=[]
    target.doc.root['isprefab']=True
    for key in ('m_ReferencedMeshSnapshots','nodeInstanceData'):
        if key in target.doc.root:target.doc.root[key]=[]
    group=target.new_group(f'terrain:{name}')
    for n in nodes:target.attach(n,group)
    target.attach(group)
    target._reassign_ids([group])
    path.parent.mkdir(parents=True,exist_ok=True)
    target.save(path,backup_dir=project.s.backups/'maps'/'prefabs',settings=project.s)
    return rel


def expand_layers(project,m):
    """In memory only (checks): put the content of every layer prefab of this addon into the map, so
    visual_check and check_map see layered terrain as if it were inline. Never save the result."""
    from .vmap import VMap
    for node in list(m.iter_nodes()):
        if node.type!='CMapPrefab':continue
        rel=str(node.get('targetMapPath') or '')
        path=project.s.content_addon/rel
        if not rel.startswith('maps/prefabs/') or not path.is_file():continue
        layer=VMap.load(path)
        for child in list(layer.world['children']):
            clone=layer.clone(child)
            m._reassign_ids([clone])
            m.attach(clone,m.parent_of(node))
    return m


def add_terrain(project,m,op,refs):
    layer=op.get('layer',False)
    if not isinstance(layer,bool):raise OpError('layer must be true or false')
    data=plan(project,{k:v for k,v in op.items() if k!='layer'})
    if any(v['name']==data['name'] for v in plans(m)):raise OpError('terrain name already exists; delete its group and replay')
    mats=op.get('materials',{})
    keys(mats,{'grass','dirt','rock','sides','blend'},'materials')
    blend=op.get('blend',True)
    if not isinstance(blend,bool):raise OpError('blend must be boolean')
    effective={k:material(project,mats.get(k,v)) for k,v in DEFAULTS.items() if k!='dirt' or not blend}
    blendmat=material(project,mats.get('blend',effective['grass'])) if blend else None
    rock_slope=number(op.get('rock_slope',42),'rock_slope',1,89)
    dirt_height=number(op.get('dirt_height',data['height_range'][0]+64),'dirt_height')
    lo,hi=data['bounds']; nx,ny=data['cells']; h=data['heights']; dx,dy=(hi[0]-lo[0])/nx,(hi[1]-lo[1])/ny
    def point(i,j):return [lo[0]+dx*i,lo[1]+dy*j,h[i][j]]
    nodes=[]; steep=0.; total=0.; material_faces={}
    for i0 in range(0,nx,64):
        for j0 in range(0,ny,64):
            i1,j1=min(nx,i0+64),min(ny,j0+64); piece=mesh.Mesh()
            for i in range(i0,i1):
                for j in range(j0,j1):
                    for indices in (((i,j),(i+1,j),(i+1,j+1)),((i,j),(i+1,j+1),(i,j+1))):
                        pts=[point(*v) for v in indices]; n=mesh._norm(mesh._cross(mesh._sub(pts[1],pts[0]),mesh._sub(pts[2],pts[0])))
                        slope=math.degrees(math.acos(max(-1,min(1,n[2]))))
                        cx,cy=sum(p[0] for p in pts)/3,sum(p[1] for p in pts)/3
                        mat=(effective['rock'] if slope>=rock_slope else blendmat if blend else effective['dirt']
                             if sum(p[2] for p in pts)/3<dirt_height or paint_weight(data['paint'],cx,cy)>=.5 else effective['grass'])
                        area=dx*dy/2/max(1e-8,n[2])
                        piece.face(pts,mat); total+=area; steep+=area if slope>45 else 0; material_faces[mat]=material_faces.get(mat,0)+1
            perimeter=([point(i,j0) for i in range(i0,i1+1)]+[point(i1,j) for j in range(j0+1,j1+1)]
                       +[point(i,j1) for i in range(i1-1,i0-1,-1)]+[point(i0,j) for j in range(j1-1,j0,-1)])
            for a,b in zip(perimeter,perimeter[1:]+perimeter[:1]):
                piece.face([b,a,[a[0],a[1],data['bottom']],[b[0],b[1],data['bottom']]],effective['sides'])
            piece.face([[p[0],p[1],data['bottom']] for p in reversed(perimeter)],effective['sides'])
            node=m.new_mesh(piece,texture_size=project.texture_size,options={'keep_vertices':True})
            if blend:
                def weight(p):
                    s=sample(data,p[0],p[1])
                    return max(smooth((dirt_height+96-p[2])/192),smooth((s['slope']-20)/max(1,rock_slope-20)),
                               paint_weight(data['paint'],p[0],p[1]))
                blend_node(node,weight)
            nodes.append(node)
    data.update({'materials':effective,'blend':blend,'blend_material':blendmat,'steep_fraction':steep/total,'mesh_ids':[n['nodeID'] for n in nodes]})
    if layer:
        data['layer']=_write_layer(project,m,data['name'],nodes)
        data['mesh_ids']=[]
        nodes=[layer_node(m,data['layer'])]
    gid=commit(m,op,data['name'],nodes,data,PLAN_KEY,refs)
    return {'op':'add_terrain','id':gid,'name':data['name'],'ids':data['mesh_ids'],'chunks':len(nodes),'cells':[nx,ny],
            **({'layer':data['layer']} if layer else {}),
            'height_range':data['height_range'],'steep_fraction':round(steep/total,4),'material_faces':material_faces,'blend':blend}


def preprocess(project,m,op,refs):
    def visit(value):
        if isinstance(value,list):return [visit(v) for v in value]
        if not isinstance(value,dict):return value
        if 'terrain' in value and 'at' in value and 'op' not in value:
            keys(value,{'terrain','at','z','offset'},'terrain point')
            xy=vec(value['at'],'at'); z=value.get('z','ground')
            s=sample(select(m,value['terrain']),*xy)
            return xy+[(s['z'] if z=='ground' else number(z,'z'))+number(value.get('offset',0),'offset')]
        return {k:visit(v) for k,v in value.items()}
    return visit(op)


def terrain_info(project,map,terrain=None):
    values=plans(project.load(map))
    if terrain is not None:values=[select(project.load(map),terrain)]
    return {'map':map,'terrains':[{k:v for k,v in data.items() if k not in ('heights','ops')} for data in values],
            'streets':[{k:v for k,v in d.items() if k not in ('exclusions','surfaces')} for d in plans(project.load(map),STREET_KEY)]}


def terrain_sample(project,map,terrain,points):
    if not isinstance(points,list) or not 1<=len(points)<=256:raise OpError('points need 1..256 XY points')
    data=select(project.load(map),terrain)
    return {'map':map,'terrain':terrain,'samples':[{'at':vec(v,'point'),**sample(data,*vec(v,'point'))} for v in points]}


def kit_parts(kit):
    name(kit)
    file=PROJECT_ROOT/'src/cs2map/data/kits'/f'{kit}.json'
    if not file.is_file():return []
    data=json.loads(file.read_text(encoding='utf-8'))
    if data.get('schema')!=1 or data.get('kit')!=kit:raise OpError('unsupported kit schema')
    return data['parts']


def model_asset(project,value):
    if isinstance(value,str) and not value.startswith('models/') and value.count('/')==1:
        kit,key=value.split('/');parts=[p for p in kit_parts(kit) if p['key']==key]
        if len(parts)!=1:raise OpError(f'kit part {value} not found')
        value=parts[0]['model']
    from .ext.models import asset_path
    return asset_path(project,value)


@lru_cache(maxsize=256)
def model_local_bounds(project,model):
    from .visual import model_bounds
    b=model_bounds(project,model)
    if not b:raise OpError(f'cannot measure {model}; choose a model with supported native bounds')
    return [list(b[0]),list(b[1])]


def make_prop(project,m,model,ground,angles,scale,bounds,solid=True,fade=3000,sink=0.):
    """A prop standing on `ground`: its lowest corner sits on ground z, then it is pushed `sink` units into it."""
    from .structure import _rotate
    corners=[_rotate((x*scale,y*scale,z*scale),angles) for x in (bounds[0][0],bounds[1][0])
             for y in (bounds[0][1],bounds[1][1]) for z in (bounds[0][2],bounds[1][2])]
    origin=[*ground[:2],ground[2]-min(p[2] for p in corners)-sink]
    if any(abs(origin[i]+p[i])>32768 for p in corners for i in range(3)):raise OpError('prop exceeds world bounds')
    return m.new_entity('prop_static',{'model':model,'solid':6 if solid else 0,'fademindist':max(0,fade-600),
                        'fademaxdist':fade},origin,angles,[scale]*3,defaults=project.catalog.defaults('prop_static'))


def footprint_ground(data,x,y,b,scale,yaw):
    """Lowest ground under a model's footprint (corners, edge midpoints and centre, turned by yaw): a prop set on
    the centre height floats on its downhill side and over every bump (rocks hung in the air on slopes)."""
    lo,hi=data['bounds'];c,s=math.cos(math.radians(yaw)),math.sin(math.radians(yaw));zs=[]
    for u in (b[0][0],0.,b[1][0]):
        for v in (b[0][1],0.,b[1][1]):
            px,py=x+(u*c-v*s)*scale,y+(u*s+v*c)*scale
            if lo[0]<=px<=hi[0] and lo[1]<=py<=hi[1]:zs.append(sample(data,px,py)['z'])
    return min(zs) if zs else sample(data,x,y)['z']


def scatter(project,m,op,refs):
    keys(op,{'op','name','terrain','area','models','category','density','slope_max','height','min_distance',
             'random_yaw','scale','seed','align_normal','solid','fademaxdist','route','keep_clear','sink','ref'},'scatter')
    sink_op=op.get('sink','auto')
    if sink_op!='auto':sink_op=number(sink_op,'sink',0,512)
    nm=name(op.get('name','scatter'));data=select(m,op.get('terrain'))
    if any(n.name=='scatter:'+nm for n in m.iter_nodes()):raise OpError('scatter name already exists')
    area=op.get('area','terrain')
    if area=='terrain':
        lo,hi=data['bounds'];poly=[lo,[hi[0],lo[1]],hi,[lo[0],hi[1]]]
    elif isinstance(area,dict):
        keys(area,{'box','polygon','terrain'},'area')
        if 'box' in area:
            lo,hi=bounds(area['box']);poly=[lo[:2],[hi[0],lo[1]],hi[:2],[lo[0],hi[1]]]
        elif 'polygon' in area:poly=polygon(area['polygon'])
        elif area.get('terrain')==data['name']:
            lo,hi=data['bounds'];poly=[lo,[hi[0],lo[1]],hi,[lo[0],hi[1]]]
        else:raise OpError('area terrain must match selected terrain')
    else:poly=polygon(area)
    for p in poly:sample(data,*p)
    density=number(op.get('density',10),'density',0,1000);mind=number(op.get('min_distance',96),'min_distance',0,2048)
    slope=number(op.get('slope_max',35),'slope_max',0,89);seed=integer(op.get('seed',0),'seed',-2147483648,2147483647)
    hr=vec(op.get('height',[-32768,32768]),'height');sc=op.get('scale',[.8,1.2]);sc=vec(sc,'scale') if isinstance(sc,(list,tuple)) else [number(sc,'scale',.01,100)]*2
    if hr[1]<hr[0] or not .01<=sc[0]<=sc[1]<=100:raise OpError('height/scale ranges must increase; scale .01..100')
    for k in ('random_yaw','align_normal','solid'):
        if k in op and not isinstance(op[k],bool):raise OpError(k+' must be boolean')
    fade=number(op.get('fademaxdist',3000),'fademaxdist',0,32768)
    raw=op.get('models');category=op.get('category','vegetation_med');parts=kit_parts(category)
    if raw is None:raw=[p['model'] for p in parts] or FALLBACK_MODELS.get(category)
    if not isinstance(raw,list) or not 1<=len(raw)<=32:raise OpError('models need 1..32 paths or kit/key names; category vegetation_med/rocks')
    models=[model_asset(project,v) for v in raw];model_bounds={v:model_local_bounds(project,v) for v in models}
    tags={p['model']:p.get('category') for p in parts}
    # All exclusion geometry is saved authoring metadata; no live game lock is needed.
    roads=[]
    for street in plans(m,STREET_KEY):roads += [r['polygon'] for r in street.get('exclusions',[])]
    from . import layout
    rooms=[]
    for lp in layout.plans(m):
        for room in lp['rooms'].values():
            lo,hi=room['interior']['mins'],room['interior']['maxs'];rooms.append([lo[:2],[hi[0],lo[1]],hi[:2],[lo[0],hi[1]]])
    # Building footprints are protected independently of the optional H metadata schema.
    from . import checks
    for n in m.iter_nodes():
        if n.type=='CMapGroup' and n.name.startswith('building:'):
            boxes=[checks._mesh_bounds(c) for c in m.iter_nodes(n) if c.type=='CMapMesh']
            if boxes:
                lo=[min(b[0][i] for b in boxes) for i in (0,1)];hi=[max(b[1][i] for b in boxes) for i in (0,1)]
                rooms.append([lo,[hi[0],lo[1]],hi,[lo[0],hi[1]]])
    spawns=[n['origin'] for n in m.entities() if m.classname(n) in ('info_player_terrorist','info_player_counterterrorist')]
    routes=[]
    if 'route' in op:routes.append(path(op['route']))
    from . import story
    for _,spec in story.map_stories(project,m):
        for stage in spec['stages']:
            points=[s['go'][:2] for s in stage.get('route',[]) if 'go' in s]
            if len(points)>=2:routes.append(points)
    clear=op.get('keep_clear',[])
    if not isinstance(clear,list) or len(clear)>128:raise OpError('keep_clear needs at most 128 boxes')
    for box in clear:
        lo,hi=bounds(box);rooms.append([lo[:2],[hi[0],lo[1]],hi[:2],[lo[0],hi[1]]])
    rng=random.Random(seed);area_units=abs(mesh._polygon_area(poly));target=int(area_units*density/1e6)
    if target>2048:raise OpError('scatter exceeds 2048 objects; lower density/split area')
    minx,maxx=min(p[0] for p in poly),max(p[0] for p in poly);miny,maxy=min(p[1] for p in poly),max(p[1] for p in poly)
    placed=[];nodes=[];skips={k:0 for k in ('area','slope','height','road','room','spawn','route','distance')}
    occupied=[]
    for n in m.entities('prop_static'):
        model=n['entity_properties'].get('model')
        if not model:continue
        b=model_local_bounds(project,model)
        occupied.append((n['origin'][0],n['origin'][1],max(math.hypot(x,y) for x in (b[0][0],b[1][0]) for y in (b[0][1],b[1][1]))*max(n.get('scales',[1,1,1]))))
    def near_poly(x,y,p,r):
        if x<min(q[0] for q in p)-r or x>max(q[0] for q in p)+r or y<min(q[1] for q in p)-r or y>max(q[1] for q in p)+r:return False
        return inside(x,y,p) or nearest_path(x,y,p+p[:1])[0]<r
    attempts=0
    for _ in range(min(100000,max(1,target)*40)):
        if len(nodes)>=target:break
        attempts+=1;x,y=rng.uniform(minx,maxx),rng.uniform(miny,maxy)
        model=rng.choice(models);scale=rng.uniform(*sc);b=model_bounds[model]
        radius=max(math.hypot(a,c) for a in (b[0][0],b[1][0]) for c in (b[0][1],b[1][1]))*scale
        if not inside(x,y,poly):skips['area']+=1;continue
        s=sample(data,x,y)
        if s['slope']>slope:skips['slope']+=1;continue
        if not hr[0]<=s['z']<=hr[1]:skips['height']+=1;continue
        if any(near_poly(x,y,p,radius+16) for p in roads):skips['road']+=1;continue
        if any(near_poly(x,y,p,radius+16) for p in rooms):skips['room']+=1;continue
        if any(math.hypot(x-p[0],y-p[1])<128+radius for p in spawns):skips['spawn']+=1;continue
        if any(nearest_path(x,y,p)[0]<64+radius for p in routes):skips['route']+=1;continue
        if any(math.hypot(x-p[0],y-p[1])<max(mind,radius+p[2]) for p in occupied):skips['distance']+=1;continue
        yaw=rng.uniform(0,360) if op.get('random_yaw',True) else 0
        pitch=roll=0.;isrock=tags.get(model)=='rock' or 'rock' in model
        if op.get('align_normal',isrock):
            r=math.radians(yaw);normal=s['normal'];lx=normal[0]*math.cos(r)+normal[1]*math.sin(r);ly=-normal[0]*math.sin(r)+normal[1]*math.cos(r)
            pitch=math.degrees(math.atan2(lx,normal[2]));roll=-math.degrees(math.asin(max(-1,min(1,ly))))
        small=max(b[1][i]-b[0][i] for i in range(3))*scale<160
        # Grounded: on the lowest terrain under the footprint and sunk in, rocks by a fifth of their height
        # (their bounding box is wider than the base they rest on), plants and props by a little.
        gz=footprint_ground(data,x,y,b,scale,yaw)
        sink=sink_op if sink_op!='auto' else (.22*(b[1][2]-b[0][2])*scale if isrock else 3.)
        node=make_prop(project,m,model,[x,y,gz],[pitch,yaw,roll],scale,b,solid=op.get('solid',not small),fade=fade,sink=sink)
        nodes.append(node);occupied.append((x,y,radius));placed.append({'id':node['nodeID'],'model':model,'ground':[round(x,2),round(y,2),s['z']],
                               'scale':round(scale,3),'angles':[round(pitch,2),round(yaw,2),round(roll,2)],'solid':op.get('solid',not small),
                               'sink':round(sink,1)})
    metadata={'version':1,'name':nm,'terrain':data['name'],'seed':seed,'area':poly,'density':density,'target':target,'placed':placed}
    gid=commit(m,op,nm,nodes,metadata,'cs2map_scatter',refs)
    return {'op':'scatter','id':gid,'target':target,'placed':len(nodes),'ids':[n['nodeID'] for n in nodes],'attempts':attempts,'skipped':skips}
