"""Stock-kit buildings with sealed solids, measured mounts and real openings."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import mesh, structure
from .dressing import number, vector
from .project import OpError
from .ext.models import asset_path

KITS = Path(__file__).parent / 'data' / 'kits'
SIDES = {'-x': (-1, 0, 0), '+x': (1, 0, 0), '-y': (0, -1, 0), '+y': (0, 1, 0)}
FRONT_YAW = {'+x': 0, '+y': 90, '-x': 180, '-y': -90}


def _keys(obj, allowed, label):
    if not isinstance(obj, dict):
        raise OpError(f'{label} must be an object')
    extra = set(obj) - set(allowed.split())
    if extra:
        raise OpError(f'unknown {label} fields: {sorted(extra)}')


def _int(value, label, low, high):
    value = number(value, label, low, high)
    if value != int(value):
        raise OpError(f'{label} must be an integer')
    return int(value)


def _bool(value, label):
    if not isinstance(value, bool):
        raise OpError(f'{label} must be true or false')
    return value


def kit(style):
    if not isinstance(style, str) or not re.fullmatch(r'building_[a-z0-9_]+', style):
        raise OpError('style must be a building_* kit; call building_styles')
    path = KITS / (style + '.json')
    if not path.is_file():
        raise OpError(f'unknown building style {style}; call building_styles')
    return json.loads(path.read_text(encoding='utf-8'))


def building_styles(style=None):
    data = [kit(style)] if style else [kit(p.stem) for p in sorted(KITS.glob('building_*.json'))]
    return {'ok': True, 'styles': [{k: d[k] for k in ('kit', 'title', 'materials', 'parts')} for d in data]}


def _part(data, key, category=None):
    if not isinstance(key, str):
        raise OpError('part must be a kit key string')
    key = key.removeprefix(data['kit'] + '/')
    for part in data['parts']:
        if part['key'] == key:
            if category and part['category'] != category:
                raise OpError(f'{key} is {part["category"]}, expected {category}')
            return {**part, 'kit': data['kit']}
    raise OpError(f'unknown part {key} in {data["kit"]}')


def mount_transform(part, at, normal, scale=1):
    """At = render-bottom, tangent centre, wall plane. Rear bound touches plane."""
    normal = tuple(normal)
    if normal not in SIDES.values():
        raise OpError('building mount normal must be a cardinal XY axis')
    yaw = math.degrees(math.atan2(normal[1], normal[0])) - FRONT_YAW[part['front']]
    lo, hi = part['bounds']
    corners = [structure._rotate((x*scale, y*scale, z*scale), (0, yaw, 0))
               for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    rear = min(sum(a*b for a, b in zip(c, normal)) for c in corners)
    tangent = 1 if normal[0] else 0
    centre = (min(c[tangent] for c in corners) + max(c[tangent] for c in corners))/2
    origin = [at[i] - rear*normal[i] for i in range(3)]
    origin[tangent] -= centre
    origin[2] = at[2] - lo[2]*scale
    return origin, [0, yaw, 0]


def place_part(project, m, part, at, normal, parent=None, scale=1):
    # A's common API uses model-origin Z for wall parts. H exposes render-bottom Z.
    from . import kits
    anchor = list(at)
    if part['mount'] == 'wall':
        anchor[2] -= part['bounds'][0][2]*scale
    # The rear face sits on the wall (0.5 out, never coplanar). The old 8-unit gap only
    # dodged the AABB intersection check; visual_check now tests real triangles.
    offset = 0.5 if part['mount'] == 'wall' else 0
    ent = kits.place(project, m, part['kit']+'/'+part['key'], anchor, normal,
                     offset=offset, scale=scale, snap_floor=False, wall_anchor=True)
    for key,value in {'solid':'0','fademindist':'2200','fademaxdist':'3000'}.items():
        ent['entity_properties'].set(key,'string',value)
    m.remove(ent)
    m.attach(ent, parent)
    return ent


def _material(project, path):
    return structure._material_path(project, path)


def _wall_rects(start, end, z0, z1, holes):
    # Round the cut lines: two edges a hair apart (384 vs 384.00001) made zero-area slivers.
    xs = sorted({round(v, 3) for v in (start, end, *(v for h in holes for v in h[:2]))})
    zs = sorted({round(v, 3) for v in (z0, z1, *(v for h in holes for v in h[2:]))})
    rects = []
    for a, b in zip(xs, xs[1:]):
        for c, d in zip(zs, zs[1:]):
            if b - a < 0.01 or d - c < 0.01 or any(h[0] < (a+b)/2 < h[1] and h[2] < (c+d)/2 < h[3] for h in holes):
                continue
            rects.append([(a, c), (b, c), (b, d), (a, d)])
    return rects


def roof_meshes(lo, hi, z, spec, material, wall_material):
    """Closed roof solids; gable end faces have the wall material."""
    kind = spec.get('type', 'gable')
    over = number(spec.get('overhang', 12), 'roof.overhang', 0, 64)
    thick = number(spec.get('thickness', 8), 'roof.thickness', 2, 32)
    pitch = number(spec.get('pitch', 25), 'roof.pitch', 5, 60)
    ridge = spec.get('ridge', 'x')
    if ridge not in ('x', 'y'):
        raise OpError('roof.ridge must be x or y')
    x0,y0 = lo[0]-over, lo[1]-over
    x1,y1 = hi[0]+over, hi[1]+over
    if kind in ('flat', 'parapet'):
        out = [mesh.box([x0,y0,z], [x1,y1,z+thick], material)]
        if kind == 'parapet':
            h = number(spec.get('parapet_height', 32), 'roof.parapet_height', 8, 96)
            out += [mesh.box([x0,y0,z+thick], [x1,y0+8,z+thick+h], wall_material),
                    mesh.box([x0,y1-8,z+thick], [x1,y1,z+thick+h], wall_material),
                    mesh.box([x0,y0+8,z+thick], [x0+8,y1-8,z+thick+h], wall_material),
                    mesh.box([x1-8,y0+8,z+thick], [x1,y1-8,z+thick+h], wall_material)]
        return out, lambda x,y: z+thick
    if kind not in ('gable','hip','shed'):
        raise OpError('roof.type must be flat, parapet, gable, hip or shed')
    span = y1-y0 if ridge=='x' else x1-x0
    rise = span * math.tan(math.radians(pitch)) / (1 if kind=='shed' else 2)
    def height(x,y):
        if kind == 'shed':
            t = (y-y0)/(y1-y0) if ridge=='x' else (x-x0)/(x1-x0)
            return z+thick+rise*t
        if kind=='hip':
            return z+thick+math.tan(math.radians(pitch))*min(x-x0,x1-x,y-y0,y1-y)
        t = abs(y-(y0+y1)/2) if ridge=='x' else abs(x-(x0+x1)/2)
        return z+thick+rise-math.tan(math.radians(pitch))*t
    if kind=='hip':
        half=min(x1-x0,y1-y0)/2
        if x1-x0 >= y1-y0:
            tops=[(x0+half,(y0+y1)/2,z+thick+half*math.tan(math.radians(pitch))),
                  (x1-half,(y0+y1)/2,z+thick+half*math.tan(math.radians(pitch)))]
        else:
            tops=[((x0+x1)/2,y0+half,z+thick+half*math.tan(math.radians(pitch))),
                  ((x0+x1)/2,y1-half,z+thick+half*math.tan(math.radians(pitch)))]
        bottom=[(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z)]
        eave=[(x,y,z+thick) for x,y,_ in bottom]
        m=mesh.Mesh(); m.face_out(bottom,(0,0,-1),material)
        for i in range(4):
            j=(i+1)%4
            m.face_out([bottom[i],bottom[j],eave[j],eave[i]],
                       ((bottom[i][0]+bottom[j][0])/2-(x0+x1)/2,
                        (bottom[i][1]+bottom[j][1])/2-(y0+y1)/2,0),material)
        if x1-x0 >= y1-y0:
            faces=[[eave[0],eave[1],tops[1],tops[0]], [eave[1],eave[2],tops[1]],
                   [eave[2],eave[3],tops[0],tops[1]], [eave[3],eave[0],tops[0]]]
        else:
            faces=[[eave[0],eave[1],tops[0]], [eave[1],eave[2],tops[1],tops[0]],
                   [eave[2],eave[3],tops[1]], [eave[3],eave[0],tops[0],tops[1]]]
        for face in faces:
            # A square footprint has a single apex: remove coincident ridge vertices.
            face=list(dict.fromkeys(face)); m.face_out(face,(0,0,1),material)
        return [m], height
    # A prism section includes its lower slab, slopes, end caps and eaves.
    a0,a1=(y0,y1) if ridge=='x' else (x0,x1)
    section=[(a0,z),(a1,z),(a1,z+thick)]
    if kind=='gable': section.append(((a0+a1)/2,z+thick+rise))
    else: section[-1]=(a1,z+thick+rise)
    section.append((a0,z+thick))
    # mesh.prism's y axis polygon order is (z,x).
    polygon=section if ridge=='x' else [(b,a) for a,b in section]
    out=mesh.prism(polygon,x0 if ridge=='x' else y0,x1 if ridge=='x' else y1,ridge,material,
                   {'start':wall_material,'end':wall_material,'sides':material})
    return [out],height


def add_building(project, m, op, refs):
    _keys(op, 'op name footprint floors floor_height style wall_material wall_thickness floor_material plinth bands facades roof extras enterable ref', 'building')
    name=op.get('name')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9_]{1,48}',name):
        raise OpError('building.name must be 1..48 chars a-z0-9_')
    if any(n.name=='building:'+name for n in m.iter_nodes()):
        raise OpError(f'building {name} already exists')
    footprint=op.get('footprint');_keys(footprint,'mins maxs','footprint')
    lo=vector(footprint.get('mins'),'footprint.mins');hi=vector(footprint.get('maxs'),'footprint.maxs')
    if abs(lo[2]-hi[2])>1e-5 or any(not 128<=hi[i]-lo[i]<=2048 for i in (0,1)):
        raise OpError('footprint must have equal Z and XY widths 128..2048')
    floors=_int(op.get('floors',2),'floors',1,6)
    fh=number(op.get('floor_height',144),'floor_height',112,256)
    t=number(op.get('wall_thickness',12),'wall_thickness',8,24)
    enterable=_bool(op.get('enterable',False),'enterable')
    bands=_bool(op.get('bands',True),'bands')
    data=kit(op.get('style','building_inferno'))
    mats=data['materials'];wall=_material(project,op.get('wall_material',mats['walls']))
    trim=_material(project,mats['trim']);inner=_material(project,mats['inner'])
    floor_mat=_material(project,op.get('floor_material',mats['floor']))
    roof=op.get('roof',{});_keys(roof,'type pitch overhang material ridge thickness parapet_height','roof')
    roof_mat=_material(project,roof.get('material',mats['roof']))
    extras=op.get('extras',{});_keys(extras,'chimneys gutters','extras')
    chimneys=_int(extras.get('chimneys',0),'extras.chimneys',0,4)
    gutters=_bool(extras.get('gutters',False),'extras.gutters')
    plinth=op.get('plinth',{});_keys(plinth,'height material','plinth')
    ph=number(plinth.get('height',24),'plinth.height',0,fh-4)
    pm=_material(project,plinth.get('material',trim))
    z0=lo[2];z1=z0+floors*fh
    facades=op.get('facades',{'-y':{'windows':{'per_floor':3}}})
    _keys(facades,'-x +x -y +y','facades')
    plans={};prop_count=chimneys
    def point(side,u,z,offset=0):
        if side in ('-y','+y'):
            return [u,lo[1]-offset if side=='-y' else hi[1]+offset,z]
        return [lo[0]-offset if side=='-x' else hi[0]+offset,u,z]
    for side in SIDES:
        cfg=facades.get(side,{})
        _keys(cfg,'windows door balconies sign awnings lamps','facade '+side)
        along=0 if side in ('-y','+y') else 1
        start,end=lo[along],hi[along];length=end-start
        holes=[];parts=[];signs=[]
        def sequence(value,label):
            if not isinstance(value,list):raise OpError(label+' must be an array')
            out=[_int(v,label,0,floors-1) for v in value]
            if len(set(out))!=len(out):raise OpError(label+' contains duplicate floors')
            return out
        def detail(key,u,bottom,category,opening=False,scale=1):
            nonlocal prop_count
            part=_part(data,key,category)
            # Width must fit in the facade, and frames must not cross corners.
            _,ang=mount_transform(part,point(side,u,bottom),SIDES[side],scale)
            corners=[structure._rotate((x*scale,y*scale,0),ang) for x in (part['bounds'][0][0],part['bounds'][1][0]) for y in (part['bounds'][0][1],part['bounds'][1][1])]
            width=max(c[along] for c in corners)-min(c[along] for c in corners)
            if u-width/2<start+2 or u+width/2>end-2:
                raise OpError(f'{side}/{key} render width {width:.1f} does not fit facade at {u:.1f}')
            h=part['size'][2]*scale
            if bottom<z0-.1 or bottom+h>z1+16:
                raise OpError(f'{side}/{key} exceeds building height; increase floor_height or change part')
            if opening:
                if not part.get('opening'):raise OpError(f'{key} has no opening metadata')
                w,hh=[v*scale for v in part['opening']]
                if w > width - 2 or hh > h - 2:
                    raise OpError(f'{key} opening exceeds its measured frame; correct the kit')
                b=bottom+(part['opening_bottom']-part['bounds'][0][2])*scale
                hole=[u-w/2,u+w/2,b,b+hh]
                if hole[0]<=start+t or hole[1]>=end-t or b<z0 or b+hh>=z1:
                    raise OpError(f'{side}/{key} opening does not fit')
                if any(max(hole[0],o[0])<min(hole[1],o[1]) and max(hole[2],o[2])<min(hole[3],o[3]) for o in holes):
                    raise OpError(f'openings overlap on {side}')
                holes.append(hole)
            parts.append({'part':part,'at':point(side,u,bottom),'scale':scale,'category':category})
            prop_count+=1
            return part
        win=cfg.get('windows')
        if win is not None:
            _keys(win,'part per_floor floors bottom positions scale infill','windows')
            count=_int(win.get('per_floor',3),'windows.per_floor',1,8)
            default_floors = list(range(1, floors)) if cfg.get('door') and floors > 1 else list(range(floors))
            fs=sequence(win.get('floors',default_floors),'windows.floors')
            positions=win.get('positions',[(i+1)/(count+1) for i in range(count)])
            if not isinstance(positions,list) or len(positions)>8:raise OpError('windows.positions must have at most 8 fractions')
            positions=[number(v,'windows.positions',0,1) for v in positions]
            key=win.get('part','window_stone_40' if data['kit']=='building_inferno' else 'window_88x80')
            bottom=number(win.get('bottom',20),'windows.bottom',0,fh)
            scale=number(win.get('scale',1),'windows.scale',.5,2)
            for f in fs:
                for u in positions:
                    part = detail(key,start+u*length,z0+f*fh+bottom,'window',True,scale)
                    if bottom+part['size'][2]*scale>fh-4:
                        raise OpError(f'{side}/{key} exceeds floor height; raise floor_height or lower windows.bottom')
                    if win.get('infill'):
                        inset=_part(data,win['infill'],'window')
                        # Preserve aspect ratio and fit an inset grille/glass behind the measured hole.
                        w,h=part['opening'];iw=inset['size'][1] if inset['front'] in ('+x','-x') else inset['size'][0]
                        s=min(w/iw,h/inset['size'][2])*scale
                        zb=holes[-1][2]+(h*scale-inset['size'][2]*s)/2
                        parts.append({'part':inset,'at':point(side,start+u*length,zb,-2),'scale':s,'category':'window'})
                        prop_count+=1
        door=cfg.get('door')
        if door is not None:
            _keys(door,'part at scale','door')
            at=number(door.get('at',.5),'door.at',0,1)
            detail(door.get('part','door_wood'),start+at*length,z0,'door',True,
                   number(door.get('scale',1),'door.scale',.5,2))
            if enterable:
                # Leave the doorway open. The selected door measures its portal.
                parts.pop();prop_count-=1
                holes[-1][2]=z0
                if holes[-1][1]-holes[-1][0]<48 or holes[-1][3]-z0<80:
                    raise OpError('enterable doorway needs >=48 x 80 units')
        for field,cat,default,bottom_default in [('balconies','balcony','balcony_128',0),('awnings','awning','awning_128' if data['kit']=='building_inferno' else 'awning_01',96),('lamps','lamp','lamp_ornate',86)]:
            cfgdetail=cfg.get(field)
            if cfgdetail is None:continue
            _keys(cfgdetail,'part floors at bottom scale',field)
            fs=sequence(cfgdetail.get('floors',[1] if floors>1 else [0]),field+'.floors')
            u=start+number(cfgdetail.get('at',.5),field+'.at',0,1)*length
            b=number(cfgdetail.get('bottom',bottom_default),field+'.bottom',0,fh)
            for f in fs:detail(cfgdetail.get('part',default),u,z0+f*fh+b,cat,False,number(cfgdetail.get('scale',1),field+'.scale',.5,2))
        sign=cfg.get('sign')
        if sign is not None:
            _keys(sign,'text part at floor bottom size','sign')
            f=_int(sign.get('floor',0),'sign.floor',0,floors-1)
            u=start+number(sign.get('at',.5),'sign.at',0,1)*length
            b=z0+f*fh+number(sign.get('bottom',fh-24),'sign.bottom',0,fh)
            if 'part' in sign:detail(sign['part'],u,b,'sign')
            elif isinstance(sign.get('text'),str) and 1<=len(sign['text'])<=40:
                size=number(sign.get('size',10),'sign.size',4,24)
                if len(sign['text'])*size*.65>length-16:raise OpError('sign text does not fit facade')
                signs.append({'text':sign['text'],'at':point(side,u,b,3),'size':size})
            else:raise OpError('sign needs a kit part or 1..40 characters of text')
        plans[side]={'holes':holes,'parts':parts,'signs':signs,'start':start,'end':end}
    if enterable and not any(c.get('door') for c in facades.values()):
        raise OpError('enterable building needs a facade door')
    # Preflight roof topology and resource existence before attaching any nodes.
    roof_pieces,height=roof_meshes(lo,hi,z1,roof,roof_mat,wall)
    for piece in roof_pieces:
        if -1 in piece.halfedges()[1]:raise OpError('building roof must be closed')
    roof_bounds = [[min(piece.bounds()[j][i] for piece in roof_pieces) if j == 0 else
                    max(piece.bounds()[j][i] for piece in roof_pieces) for i in range(3)] for j in range(2)]
    if any(abs(v)>32768 for corner in roof_bounds for v in corner):raise OpError('roof exceeds map coordinate limits')
    for node in m.iter_nodes():
        raw=node.get('cs2map_building')
        if not raw:continue
        other=json.loads(raw)
        rb=other.get('roof_bounds')
        if not rb:continue
        base=other['footprint']['mins'];delta=[node['origin'][i]-other['group_origin'][i] for i in range(3)]
        rb=[[v+delta[i] for i,v in enumerate(corner)] for corner in rb]
        if all(roof_bounds[0][i]<rb[1][i]-.01 and roof_bounds[1][i]>rb[0][i]+.01 for i in range(3)):
            raise OpError(f'roof overhang intersects building {other["name"]}; increase gap or reduce overhang')
    if gutters:
        prop_count += math.ceil((hi[0]-lo[0])/128)*2 + math.ceil((z1-z0)/128)*2
    if prop_count>60:raise OpError(f'building would need {prop_count} models; limit is 60, reduce windows/extras')
    for plan in plans.values():
        for part in plan['parts']:asset_path(project,part['part']['model'])
    created=[];mesh_ids=[];prop_ids=[];facade_ids={}
    def group(label,parent=None):
        n=m.new_group(label,origin=((lo[0]+hi[0])/2,(lo[1]+hi[1])/2,z0))
        structure._set_group_defaults(n);m.attach(n,parent);created.append(n['nodeID']);return n
    g=group('building:'+name)
    def solid(piece,parent=g,visual=False):
        n=m.new_mesh(piece,texture_size=project.texture_size,options={'physicsType':'none'} if visual else None)
        m.attach(n,parent);mesh_ids.append(n['nodeID']);return n
    for side,normal in SIDES.items():
        fg=group('facade:'+name+':'+side,g);facade_ids[side]=fg['nodeID'];plan=plans[side]
        if side in ('-y','+y'):
            span=(lo[1],lo[1]+t) if side=='-y' else (hi[1]-t,hi[1])
            start,end=lo[0],hi[0];along_x=True
        else:
            span=(lo[0],lo[0]+t) if side=='-x' else (hi[0]-t,hi[0])
            start,end=lo[1]+t,hi[1]-t;along_x=False
        solid(mesh.region_solid(_wall_rects(start,end,z0,z1,plan['holes']),span,along_x,wall),fg)
        for d in plan['parts']:
            n=place_part(project,m,d['part'],d['at'],normal,fg,d['scale']);prop_ids.append(n['nodeID'])
        # Inset solid dark backings close windows; the first-floor door is a portal when enterable.
        for a,b,c,d in plan['holes']:
            is_portal=enterable and abs(c-z0)<.01
            if is_portal:
                for aa,bb,cc,dd in [(a-4,a,c,d),(b,b+4,c,d),(a-4,b+4,d,d+5)]:
                    p0=point(side,aa,cc,.1);p1=point(side,bb,dd,3)
                    solid(mesh.box([min(v,w) for v,w in zip(p0,p1)],[max(v,w) for v,w in zip(p0,p1)],trim),fg,True)
                continue
            p0=point(side,a,c,-t-4);p1=point(side,b,d,-t-6)
            mins=[min(p0[i],p1[i]) for i in range(3)];maxs=[max(p0[i],p1[i]) for i in range(3)]
            solid(mesh.box(mins,maxs,inner),fg)
        for s in plan['signs']:
            # A framed plaque uses no generated material and stays in the H file zone.
            width=len(s['text'])*s['size']*.65+16;h=s['size']+10
            p0=point(side,s['at'][0 if along_x else 1]-width/2,s['at'][2]-h/2,1)
            p1=point(side,s['at'][0 if along_x else 1]+width/2,s['at'][2]+h/2,2)
            solid(mesh.box([min(a,b) for a,b in zip(p0,p1)],[max(a,b) for a,b in zip(p0,p1)],trim),fg,True)
            yaw={'-y':0,'+y':180,'-x':-90,'+x':90}[side]
            r=structure.add_world_text(project,m,{'op':'add_world_text','text':s['text'],'origin':s['at'],
                       'angles':[0,yaw,90],'size':s['size'],'color':'#294c31'},refs)
            n=m.node(r['id']);m.remove(n);m.attach(n,fg)
        # Decorative plinth/bands are split at all openings so they never obstruct a doorway.
        strips=[(z0,z0+ph,pm,2)] if ph else []
        if bands:strips += [(z0+f*fh-6,z0+f*fh,trim,3) for f in range(1,floors+1)]
        for c,d,mat,depth in strips:
            rects=_wall_rects(plan['start'],plan['end'],c,d,[[a,b,max(c,e),min(d,f)] for a,b,e,f in plan['holes'] if max(c,e)<min(d,f)])
            fixed=lo[1] if side=='-y' else hi[1] if side=='+y' else lo[0] if side=='-x' else hi[0]
            outer=sorted((fixed+normal[0 if not along_x else 1]*.1,fixed+normal[0 if not along_x else 1]*depth))
            if rects:solid(mesh.region_solid(rects,tuple(outer),along_x,mat),fg,True)
    # 1 unit above the street: never coplanar with the ground it stands on (z-fighting).
    solid(mesh.box([lo[0]+t,lo[1]+t,z0-7],[hi[0]-t,hi[1]-t,z0+1],floor_mat))
    # Solid ceiling seals attic; enterable first-floor room gets a ceiling at floor_height.
    ceiling_z=z0+fh-8 if enterable else z1-8
    solid(mesh.box([lo[0]+t,lo[1]+t,ceiling_z],[hi[0]-t,hi[1]-t,ceiling_z+8],wall))
    for piece in roof_pieces:solid(piece)
    if enterable:
        light=m.new_entity('light_omni2',{'brightness_lumens':900,'color':'255 226 184','range':512},
                           [(lo[0]+hi[0])/2,(lo[1]+hi[1])/2,z0+fh-28],defaults=project.catalog.defaults('light_omni2'))
        m.attach(light,g)
    if chimneys:
        part=_part(data,'chimney_01','chimney')
        for i in range(chimneys):
            x=lo[0]+(hi[0]-lo[0])*(i+1)/(chimneys+1);y=(lo[1]+hi[1])/2
            bottom=height(x,y)
            # A real masonry pedestal fills the slope below the flat chimney base.
            w=part['size'][0]/2;d=part['size'][1]/2
            low=min(height(x+dx,y+dy) for dx in (-w,w) for dy in (-d,d))
            # Stock chimney bounds include a 46-unit lower body. Hip roofs
            # expose their ridge apex in the mesh bounds, so lift that form
            # over the plane; the visual masonry collar below fills the slope.
            lift = 48 if roof.get('type') == 'shed' else 0
            bottom=max(height(x+dx,y+dy) for dx in (-w,w) for dy in (-d,d)) + lift
            # The masonry collar is a visual slope filler; the roof and shell
            # carry collision, so it must not trigger a deep prop intersection.
            solid(mesh.box([x-w,y-d,low-1],[x+w,y+d,bottom+1],pm), g, True)
            n=place_part(project,m,part,[x-w,y,bottom+1],(1,0,0),g);prop_ids.append(n['nodeID'])
    if gutters:
        for side in ('-y','+y'):
            fg=m.node(facade_ids[side]);length=hi[0]-lo[0];count=math.ceil(length/128)
            part=_part(data,'gutter_128','gutter')
            scale=length/(count*128)
            for i in range(count):
                at=point(side,lo[0]+(i+.5)*length/count,z1-7)
                n=place_part(project,m,part,at,SIDES[side],fg,scale);prop_ids.append(n['nodeID'])
            pipe=_part(data,'downpipe_128','gutter');count=math.ceil((z1-z0)/128)
            scale=(z1-z0)/(count*pipe['size'][2])
            at_u=lo[0]+8
            for i in range(count):
                n=place_part(project,m,pipe,point(side,at_u,z0+i*pipe['size'][2]*scale),SIDES[side],fg,scale)
                prop_ids.append(n['nodeID'])
    meta={'schema':1,'name':name,'style':data['kit'],'footprint':{'mins':lo,'maxs':hi},'floors':floors,
          'floor_height':fh,'facades':facade_ids,'openings':{s:p['holes'] for s,p in plans.items()},'enterable':enterable}
    meta.update(roof_bounds=roof_bounds,group_origin=list(g['origin']))
    g.set('cs2map_building','string',json.dumps(meta,separators=(',',':')))
    if op.get('ref'):refs[op['ref']]=g['nodeID']
    return {'op':'add_building','id':g['nodeID'],'group':g['nodeID'],'groups':created,'facades':facade_ids,
            'name':name,'style':data['kit'],'mesh_ids':mesh_ids,'prop_ids':prop_ids,'models':len(prop_ids),
            'openings':meta['openings'],'enterable':enterable,'operations_per_building':1}
