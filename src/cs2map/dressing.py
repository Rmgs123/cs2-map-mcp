"""Measured stock palettes and conservative, deterministic room dressing.

Authoring metadata lives in JSON, never guessed FGD fields. Geometry and
entities are produced by the existing map operations and safety/save path.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import re
import subprocess
from pathlib import Path

from . import checks, kv3, lighting, mesh, resource, structure
from .config import PROJECT_ROOT
from .project import OpError

THEMES = Path(__file__).parent / 'data' / 'themes'


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise OpError(f'{label} must be a finite number in {low}..{high}')
    return float(value)


def vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError(f'{label} must be [x,y,z]')
    return [number(v, label, -32768, 32768) for v in value]


def box_bounds(value, label='bounds'):
    if isinstance(value, dict):
        value = [value.get('mins'), value.get('maxs')]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise OpError(f'{label} must be [mins,maxs] or {{mins,maxs}}')
    lo, hi = vector(value[0], label+'.mins'), vector(value[1], label+'.maxs')
    if any(a >= b for a, b in zip(lo, hi)):
        raise OpError(f'{label} needs maxs > mins on every axis')
    return lo, hi


def palette(theme):
    if not isinstance(theme, str) or theme not in [p.stem for p in THEMES.glob('*.json')]:
        raise OpError('theme must be one of '+', '.join(sorted(p.stem for p in THEMES.glob('*.json'))))
    return json.loads((THEMES/(theme+'.json')).read_text(encoding='utf-8'))


def theme_info(theme):
    """Return compact metadata; verification flags refer to opened CS2 frames."""
    if theme == 'all':
        return {'ok': True, 'themes': [theme_info(p.stem) for p in sorted(THEMES.glob('*.json'))]}
    data = palette(theme)
    return {'ok': True, 'theme': theme, 'verified': data['verified'],
            'materials': data['materials'],
            'material_verified': data.get('material_verified', {}),
            'props': [{k: p[k] for k in ('key', 'model', 'bounds', 'size', 'has_physics', 'physics_kind', 'role', 'verified', 'bounds_source')} for p in data['props']],
            'decals': data['decals'], 'sounds': data['sounds'], 'lighting': data['lighting'],
            'notes': data['notes'], 'data': str(THEMES/(theme+'.json'))}


def model_bounds(project, model):
    """Native MDAT/PHYS bounds, cached only in V's project scratch folder."""
    from .ext.models import asset_path
    model = asset_path(project, model)
    cache = getattr(project, '_dressing_bounds', {})
    if model in cache:
        return cache[model]
    for path in THEMES.glob('*.json'):
        for prop in palette(path.stem)['props']:
            if prop['model'] == model:
                cache[model] = prop['bounds']
                project._dressing_bounds = cache
                return prop['bounds']
    compiled = project.s.game_addon/(model+'_c')
    data = compiled.read_bytes() if compiled.is_file() else project.game.read(model+'_c')
    # Digest is just a collision-free internal cache filename, not an acceptance claim.
    import hashlib
    scratch = PROJECT_ROOT/'.local/scratch/V/model_bounds'
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch/(hashlib.sha256(data).hexdigest()[:24]+'.vmdl_c')
    path.write_bytes(data)
    native = project.s.game_bin/'resourceinfo.exe'
    points = []
    for block in ('MDAT', 'PHYS'):
        if block not in resource.blocks(data):
            continue
        proc = subprocess.run([str(native), '-game', str(project.s.cs2_path/'game/csgo'),
                               '-i', str(path), '-b', block, '-baremode'], capture_output=True, timeout=20)
        if proc.returncode:
            raise OpError(f'cannot measure model {model}')
        text = re.sub(r'#\[\s*(?:[0-9A-Fa-f]{2}\s*)*\]', '[]', proc.stdout.decode('utf-8-sig', errors='replace'))
        _, doc = kv3.loads(text)
        if block == 'MDAT':
            for scene in doc.get('m_sceneObjects', []):
                points.extend([scene['m_vMinBounds'], scene['m_vMaxBounds']])
        else:
            for part in doc.get('m_parts', []):
                for hull in part['m_rnShape'].get('m_hulls', []):
                    b = hull['m_Hull']['m_Bounds']
                    points.extend([b['m_vMinBounds'], b['m_vMaxBounds']])
                for pm in part['m_rnShape'].get('m_meshes',[]):
                    b=pm['m_Mesh'];points.extend([b['m_vMin'],b['m_vMax']])
    if not points:
        raise OpError(f'no supported embedded render/physics bounds: {model}; reserve it with keep_clear')
    result = [[min(p[i] for p in points)-.05 for i in range(3)],
              [max(p[i] for p in points)+.05 for i in range(3)]]
    cache[model] = result
    project._dressing_bounds = cache
    return result


def transformed(bounds, origin=(0, 0, 0), angles=(0, 0, 0), scales=(1, 1, 1)):
    points = [structure._rotate(tuple(v[i]*scales[i] for i in range(3)), angles)
              for v in itertools.product(*zip(*bounds))]
    return [[min(p[i] for p in points)+origin[i] for i in range(3)],
            [max(p[i] for p in points)+origin[i] for i in range(3)]]


def overlaps(a, b, gap=0):
    return all(a[0][i] < b[1][i]+gap-1e-5 and a[1][i] > b[0][i]-gap+1e-5 for i in range(3))


def inside(a, b, margin=0):
    return all(a[0][i] >= b[0][i]+margin-1e-5 and a[1][i] <= b[1][i]-margin+1e-5 for i in range(3))


def room_bounds(m, value, layout=None):
    if isinstance(value, dict):
        return box_bounds(value, 'room')
    if isinstance(value, str):
        # K's stable serialized contract is supported when available; no import
        # of another agent's unfinished implementation is needed.
        found=[]
        for node in m.iter_nodes():
            metadata=node.get('cs2map_layout')
            if not metadata:continue
            try:
                data=json.loads(metadata)
                if data.get('version')!=1:raise ValueError('unsupported version')
                if layout and data['name']!=layout:continue
                if value not in data['rooms']:continue
                if any(abs(v)>1e-5 for v in node.get('angles',(0,0,0))) or any(abs(v-1)>1e-5 for v in node.get('scales',(1,1,1))):
                    raise ValueError('rotated/scaled layout group')
                a,b=box_bounds(data['rooms'][value]['interior'],'room '+value)
                delta=[node['origin'][i]-data['origin'][i] for i in range(3)]
                found.append(([a[i]+delta[i] for i in range(3)],[b[i]+delta[i] for i in range(3)]))
            except (ValueError,TypeError,KeyError) as exc:
                raise OpError(f'invalid layout metadata: {exc}') from None
        if len(found)>1:raise OpError(f'ambiguous room {value!r}; add layout:<plan name>')
        if found:return found[0]
        raise OpError(f'named room {value!r} not found; add_layout first or use room={{mins,maxs}}')
    raise OpError('room must be {mins:[x,y,z],maxs:[x,y,z]} or a layout room name')


def scene(project, m):
    obstacles, triangles = [], []
    for node in m.iter_nodes():
        if node.type == 'CMapMesh' and checks._is_solid_mesh(node):
            if node.get('physicsType')=='none':continue
            # Children of triggers, ladder/nav/lighting volumes are not walls.
            parent = m.parent_of(node)
            if parent and parent.type == 'CMapEntity':
                cls = m.classname(parent)
                if cls.startswith('trigger_') or cls in {'func_buyzone', 'func_bomb_target', 'func_ladder', 'func_nav_markup'}:
                    continue
            bounds = checks._mesh_bounds(node)
            if bounds:
                obstacles.append((bounds, node))
                triangles.extend(checks._triangles(node))
        if node.type == 'CMapEntity':
            props = checks._props(node)
            if props.get('model'):
                b = transformed(model_bounds(project, props['model']), checks._vec(node.get('origin')),
                                checks._vec(node.get('angles')), checks._vec(node.get('scales'), (1,1,1)))
                obstacles.append((b, node))
    return obstacles, triangles


def wall_ranges(room, wall, triangles):
    """Continuous gaps in boundary geometry sampled at both player heights."""
    lo, hi = room; axis = 0 if wall.endswith('x') else 1; tangent = 1-axis
    edge = lo[axis] if wall[0] == '-' else hi[axis]
    sign = -1 if wall[0] == '-' else 1
    length = hi[tangent]-lo[tangent]; count = max(1, math.ceil(length/16))
    gaps=[]; start=None
    for i in range(count):
        u=lo[tangent]+length*(i+.5)/count
        solid=True
        for z in (lo[2]+36,lo[2]+96):
            pt=[0.,0.,z]; pt[axis]=edge-sign*.5; pt[tangent]=u
            direction=[0.,0.,0.]; direction[axis]=sign
            if not any((t:=checks._ray(pt,direction,tri)) is not None and t<=32 for tri in triangles):
                solid=False; break
        if not solid and start is None:start=lo[tangent]+length*i/count
        if solid and start is not None:
            gaps.append([start,lo[tangent]+length*i/count]); start=None
    if start is not None:gaps.append([start,hi[tangent]])
    return gaps


def wall_triangles(m):
    """Only world walls, so a closed door cannot hide an authored opening."""
    triangles=[]
    for node in m.iter_nodes():
        if node.type!='CMapMesh' or node.get('physicsType')=='none' or not checks._is_solid_mesh(node):continue
        parent=m.parent_of(node)
        while parent is not None and parent.type!='CMapEntity':parent=m.parent_of(parent)
        if parent is None:triangles.extend(checks._triangles(node))
    return triangles


def clear_zones(project, m, room, triangles, supplied):
    lo, hi = room; centre=[(lo[i]+hi[i])/2 for i in range(3)]; zones=[]; portals=[]
    if not isinstance(supplied,list) or len(supplied)>128:
        raise OpError('keep_clear must be a list of at most 128 [mins,maxs] boxes')
    zones.extend(box_bounds(v, 'keep_clear') for v in supplied)
    # Central cross connects all wall-side portals with a corridor at least 96 wide.
    zones.extend([([lo[0],centre[1]-48,lo[2]], [hi[0],centre[1]+48,lo[2]+112]),
                  ([centre[0]-48,lo[1],lo[2]], [centre[0]+48,hi[1],lo[2]+112])])
    for wall in ('-x','+x','-y','+y'):
        axis=0 if wall.endswith('x') else 1; tangent=1-axis
        for start,end in wall_ranges(room,wall,triangles):
            portals.append({'wall':wall,'start':round(start,2),'end':round(end,2)})
            edge=lo[axis] if wall[0]=='-' else hi[axis]
            a,b=list(lo),list(hi)
            a[tangent]=start-4; b[tangent]=end+4
            if wall[0]=='-':a[axis]=edge-4; b[axis]=edge+96
            else:a[axis]=edge-96; b[axis]=edge+4
            a[2]=lo[2]; b[2]=lo[2]+112
            zones.append((a,b))
            middle=(start+end)/2
            a,b=list(lo),list(hi)
            a[tangent]=min(middle,centre[tangent])-48; b[tangent]=max(middle,centre[tangent])+48
            a[axis]=min(edge,centre[axis])-48; b[axis]=max(edge,centre[axis])+48
            a[2]=lo[2]; b[2]=lo[2]+112
            zones.append((a,b))
    for node in m.iter_nodes():
        # add_ladder: a world surface with the ladder tool material; keep its foot and face free.
        if node.type=='CMapMesh' and any('toolsinvisibleladder' in str(x) for x in node.get('meshData',{}).get('materials',[])):
            b=checks._mesh_bounds(node)
            if b:zones.append(([b[0][0]-48,b[0][1]-48,b[0][2]-8],[b[1][0]+48,b[1][1]+48,b[1][2]+16]))
    for node in m.entities():
        cls=m.classname(node); pos=checks._vec(node.get('origin'))
        if cls.startswith('info_player_'):
            zones.append(([pos[0]-64,pos[1]-64,pos[2]-16],[pos[0]+64,pos[1]+64,pos[2]+112]))
        if cls.startswith('func_door') or cls in {'func_movelinear','func_button','func_rotating','func_ladder'}:
            bs=[checks._mesh_bounds(c) for c in m.iter_nodes(node) if c.type=='CMapMesh']
            bs=[b for b in bs if b]
            if bs:
                a=[min(b[0][i] for b in bs) for i in range(3)]; b=[max(b[1][i] for b in bs) for i in range(3)]
                # A mover's endpoint/sweep is reserved conservatively from authored keys.
                props=checks._props(node)
                if cls in {'func_door','func_movelinear'}:
                    distance=float(props.get('movedistance',0)); direction=props.get('movedir','0 0 0')
                    angles=checks._vec(direction); pitch,yaw=map(math.radians,angles[:2])
                    delta=[math.cos(pitch)*math.cos(yaw)*distance,math.cos(pitch)*math.sin(yaw)*distance,-math.sin(pitch)*distance]
                    a=[min(a[i],a[i]+delta[i]) for i in range(3)]; b=[max(b[i],b[i]+delta[i]) for i in range(3)]
                zones.append(([a[0]-64,a[1]-64,a[2]-8],[b[0]+64,b[1]+64,b[2]+112]))
    return zones, portals


def _result(m, op, refs, items, **extra):
    ids=[i for item in items for i in item['ids']]
    out={'op':op['op'],'ids':ids,'count':len(items),'placed':items,**extra}
    if ids:
        out['id']=ids[0]
        if op.get('ref'):refs[op['ref']]=ids[0]; out['ref']=op['ref']
    return out


def _mesh_item(project,m,piece,key,options=None):
    node=m.new_mesh(piece,texture_size=project.texture_size,options=options)
    m.attach(node)
    return {'kind':key,'ids':[node['nodeID']]}


def dress_room(project,m,op,refs):
    extra=set(op)-{'op','room','layout','theme','density','keep_clear','seed','lights','decals','details','label','ref','max_props'}
    if extra:raise OpError('unknown dress_room fields: '+', '.join(sorted(extra)))
    room=room_bounds(m,op.get('room'),op.get('layout')); lo,hi=room
    if min(hi[0]-lo[0],hi[1]-lo[1])<96 or hi[2]-lo[2]<112:
        raise OpError('dress_room needs a room at least 96 x 96 x 112')
    data=palette(op.get('theme','warehouse')); density=number(op.get('density',.5),'density',0,1)
    seed=op.get('seed',0)
    if isinstance(seed,bool) or not isinstance(seed,int) or abs(seed)>2147483647:raise OpError('seed must be a 32-bit integer')
    for flag in ('lights','decals','details'):
        if flag in op and not isinstance(op[flag],bool):raise OpError(flag+' must be true or false')
    maximum=int(number(op.get('max_props',24),'max_props',0,64))
    if maximum!=op.get('max_props',24):raise OpError('max_props must be an integer')
    label=op.get('label',data['theme'].replace('_',' ').upper())
    if not isinstance(label,str) or len(label)>48:raise OpError('label must be a string of at most 48 characters')
    obstacles,triangles=scene(project,m)
    boundary_triangles=wall_triangles(m)
    zones,portals=clear_zones(project,m,room,boundary_triangles,op.get('keep_clear',[]))
    rng=random.Random(seed); items=[]; occupied=[]; reasons={}; attempts=0; used={}
    def reject(reason):reasons[reason]=reasons.get(reason,0)+1
    props=[p for p in data['props'] if p['role']!='wall']
    target=min(maximum,math.ceil(density*(2*((hi[0]-lo[0])+(hi[1]-lo[1])))/100))
    candidates=[]
    for wall in ('-x','+x','-y','+y'):
        axis=0 if wall.endswith('x') else 1; tangent=1-axis
        n=max(1,int((hi[tangent]-lo[tangent])/64))
        for i in range(n):candidates.append((wall,lo[tangent]+(i+.5)*(hi[tangent]-lo[tangent])/n))
    rng.shuffle(candidates)
    for wall,u in candidates:
        if len(occupied)>=target:break
        choices=props[:]; rng.shuffle(choices)
        choices.sort(key=lambda p:used.get(p['key'],0))
        for prop in choices:
            attempts+=1; axis=0 if wall.endswith('x') else 1; tangent=1-axis
            if prop['role']=='corner' and (min(u-lo[tangent],hi[tangent]-u)>72 or used.get(prop['key'],0)>=2):
                reject('corner_limit');continue
            if used.get(prop['key'],0)>=max(3,math.ceil(target/max(1,len(props)-1))+2):
                reject('variety_limit');continue
            yaw={'-x':0,'+x':180,'-y':90,'+y':270}[wall]
            b=transformed(prop['bounds'],angles=(0,yaw,0)); origin=[0.,0.,0.]
            origin[tangent]=u-(b[0][tangent]+b[1][tangent])/2
            origin[axis]=lo[axis]+4-b[0][axis] if wall[0]=='-' else hi[axis]-4-b[1][axis]
            origin[2]=lo[2]-b[0][2]+.1
            placed=transformed(prop['bounds'],origin,(0,yaw,0))
            if not inside(placed,room):reject('bounds'); continue
            if any(overlaps(placed,z,4) for z in zones):reject('clearance'); continue
            if any(overlaps(placed,z,8) for z in occupied):reject('prop_overlap'); continue
            probes=[((placed[0][0]+placed[1][0])/2,(placed[0][1]+placed[1][1])/2)]
            probes.extend((x,y) for x in (placed[0][0]+1,placed[1][0]-1) for y in (placed[0][1]+1,placed[1][1]-1))
            floors=[checks._floor((x,y,lo[2]+24),triangles,48) for x,y in probes]
            if any(z is None for z in floors):reject('no_floor'); continue
            if max(floors)-min(floors)>1 or abs(max(floors)-lo[2])>18:reject('uneven_floor'); continue
            origin[2]+=max(floors)-lo[2]; placed=transformed(prop['bounds'],origin,(0,yaw,0))
            if not inside(placed,room) or any(overlaps(placed,b,1) for b in zones):reject('clearance'); continue
            # A floor touched at its top is permitted; other authored solids/props aren't.
            if any(overlaps(placed,b) for b,_ in obstacles):reject('existing_obstacle'); continue
            node=m.new_entity('prop_static',{'model':prop['model']},origin,[0,yaw,0],defaults=project.catalog.defaults('prop_static'))
            m.attach(node); occupied.append(placed);used[prop['key']]=used.get(prop['key'],0)+1
            items.append({'kind':'prop','key':prop['key'],'model':prop['model'],'ids':[node['nodeID']],
                          'origin':[round(v,3) for v in origin],'angles':[0,yaw,0],'bounds':placed})
            break
    # Visual wall overlays go only on actual solid walls, away from gaps.
    if density and op.get('decals',True):
        for wall in ('-x','+x','-y','+y'):
            axis=0 if wall.endswith('x') else 1; tangent=1-axis; sign=-1 if wall[0]=='-' else 1
            u=(lo[tangent]+hi[tangent])/2
            if any(a-48<=u<=b+48 for a,b in wall_ranges(room,wall,boundary_triangles)):continue
            decal=rng.choice(data['decals']); origin=[0.,0.,lo[2]+min(96,(hi[2]-lo[2])/2)]
            origin[axis]=lo[axis]+.2 if sign<0 else hi[axis]-.2; origin[tangent]=u
            normal=[0,0,0]; normal[axis]=-sign
            result=structure.add_decal(project,m,{'material':decal['material'],'origin':origin,'normal':normal,
                                                'size':decal['size'],'projection_far':8},refs)
            items.append({'kind':'decal','key':decal['key'],'ids':[result['id']]})
    if density and op.get('lights',True):
        count=min(3,max(1,math.ceil((hi[0]-lo[0])*(hi[1]-lo[1])/250000)))
        for i in range(count):
            origin=[lo[0]+(i+1)*(hi[0]-lo[0])/(count+1),(lo[1]+hi[1])/2,hi[2]-20]
            res=lighting.add_light(project,m,{'kind':'ceiling','origin':origin,**data['lighting']},refs)
            items.append({'kind':'light','ids':res['ids'],'origin':origin})
    if density and op.get('details',True):
        # One high wall run, kept above standing clearance. Explicit boxes
        # may reserve ceiling installations too; never force decoration there.
        x0,x1=lo[0]+24,hi[0]-24; y=hi[1]-12; z=hi[2]-32
        pipe_box=[[x0-3,y-3,z-3],[x1+3,y+3,z+3]]
        if z-3>=lo[2]+112 and not any(overlaps(pipe_box,b) for b in zones) and not any(overlaps(pipe_box,b) for b,_ in obstacles):
            items.append(_mesh_item(project,m,tube([x0,y,z],[x1,y,z],3,data['materials']['metal'],12),'pipe',{'physicsType':'none'}))
        # The proven B3 world-text orientation faces the inward -Y normal.
        mid=(lo[0]+hi[0])/2
        if label and hi[2]-lo[2]>=160 and not any(a-80<=mid<=b+80 for a,b in wall_ranges(room,'+y',boundary_triangles)):
            pos=[mid-40,hi[1]-.5,lo[2]+128]
            label_box=[[pos[0],pos[1]-1,pos[2]-12],[pos[0]+100,pos[1]+.1,pos[2]+12]]
            if not any(overlaps(label_box,b) for b in zones):
                result=structure.add_world_text(project,m,{'text':label,'origin':pos,'angles':[0,0,90],'size':10,'color':'#c3b695'},refs)
                items.append({'kind':'label','ids':[result['id']],'text':label})
    return _result(m,op,refs,items,theme=data['theme'],seed=seed,props=len(occupied),target_props=target,
                   attempts=attempts,skipped=reasons,clearance_width=96,portals=portals,
                   keep_clear=[[list(a),list(b)] for a,b in zones],
                   warning='Authoring geometry heuristics; auto script_test teleports and does not prove walking.')


def _segments(start,end,gaps):
    ranges=[(start,end)]
    for a,b in gaps:
        ranges=[part for lo,hi in ranges for part in ((lo,min(a,hi)),(max(b,lo),hi)) if part[1]-part[0]>.1]
    return ranges


def add_trim(project,m,op,refs):
    extra=set(op)-{'op','room','layout','theme','material','height','depth','z','walls','keep_clear','ref'}
    if extra:raise OpError('unknown add_trim fields: '+', '.join(sorted(extra)))
    room=room_bounds(m,op.get('room'),op.get('layout')); lo,hi=room
    height=number(op.get('height',8),'height',.25,64); depth=number(op.get('depth',1.5),'depth',.1,8)
    z=number(op.get('z',lo[2]),'z',lo[2],hi[2]-height)
    material=structure._material_path(project,op.get('material') or palette(op.get('theme','warehouse'))['materials']['trim'])
    walls=op.get('walls',['-x','+x','-y','+y'])
    if not isinstance(walls,list) or not walls or any(w not in {'-x','+x','-y','+y'} for w in walls):raise OpError('walls must be a non-empty list of -x,+x,-y,+y')
    triangles=wall_triangles(m); supplied=op.get('keep_clear',[])
    if not isinstance(supplied,list):raise OpError('keep_clear must be a list')
    zones=[box_bounds(b,'keep_clear') for b in supplied]; items=[]
    for wall in dict.fromkeys(walls):
        axis=0 if wall.endswith('x') else 1; tangent=1-axis
        gaps=wall_ranges(room,wall,triangles)
        for a,b in zones:
            if a[2]<z+height and b[2]>z:gaps.append([a[tangent]-1,b[tangent]+1])
        for start,end in _segments(lo[tangent],hi[tangent],gaps):
            a=list(lo); b=list(hi); a[tangent]=start; b[tangent]=end
            if wall[0]=='-':a[axis]=lo[axis]+.1; b[axis]=lo[axis]+depth
            else:a[axis]=hi[axis]-depth; b[axis]=hi[axis]-.1
            a[2]=z; b[2]=z+height
            items.append(_mesh_item(project,m,mesh.box(a,b,material),'trim',{'physicsType':'none'}))
    return _result(m,op,refs,items,material=material)


def tube(start,end,radius,material,sides):
    delta=[end[i]-start[i] for i in range(3)]; length=math.sqrt(sum(v*v for v in delta))
    if length<.1:raise OpError('tube points must be distinct (>= 0.1 units)')
    normal=[v/length for v in delta]; other=[0,0,1] if abs(normal[2])<.9 else [0,1,0]
    u=structure._unit(structure._cross(normal,other),'tube axis'); v=structure._cross(normal,u)
    points=[]
    for centre in (start,end):
        for i in range(sides):
            angle=2*math.pi*i/sides
            points.append(tuple(centre[j]+radius*(math.cos(angle)*u[j]+math.sin(angle)*v[j]) for j in range(3)))
    result=mesh.Mesh()
    result.face([points[i] for i in reversed(range(sides))],material)
    result.face([points[i] for i in range(sides,2*sides)],material)
    for i in range(sides):
        j=(i+1)%sides; result.face([points[k] for k in (i,j,j+sides,i+sides)],material)
    return result


def add_pipes(project,m,op,refs):
    extra=set(op)-{'op','points','radius','sides','theme','material','fittings','ref'}
    if extra:raise OpError('unknown add_pipes fields: '+', '.join(sorted(extra)))
    points=op.get('points')
    if not isinstance(points,list) or not 2<=len(points)<=33:raise OpError('points must contain 2..33 world positions')
    points=[vector(v,'points') for v in points]; radius=number(op.get('radius',4),'radius',.25,32)
    sides=int(number(op.get('sides',12),'sides',6,32))
    if sides!=op.get('sides',12):raise OpError('sides must be an integer 6..32')
    data=palette(op.get('theme','industrial'))
    material=structure._material_path(project,op.get('material') or data['materials']['metal'])
    fitting=op.get('fittings',False)
    if not isinstance(fitting,bool):raise OpError('fittings must be true or false')
    pieces=[tube(a,b,radius,material,sides) for a,b in zip(points,points[1:])]
    items=[_mesh_item(project,m,piece,'pipe',{'physicsType':'none'}) for piece in pieces]
    if fitting:
        prop=next((p for p in data['props'] if p['key']=='pipe_fitting'),None)
        if not prop:raise OpError('this theme has no pipe_fitting; use industrial/warehouse/harbor/sewer')
        for pt in points[1:-1]:
            node=m.new_entity('prop_dynamic',{'model':prop['model'],'solid':0},pt,defaults=project.catalog.defaults('prop_dynamic'));m.attach(node)
            items.append({'kind':'fitting','ids':[node['nodeID']],'model':prop['model']})
    return _result(m,op,refs,items,material=material,visual_only=True)


def add_cables(project,m,op,refs):
    extra=set(op)-{'op','start','end','sag','radius','segments','material','theme','ref'}
    if extra:raise OpError('unknown add_cables fields: '+', '.join(sorted(extra)))
    start=vector(op.get('start'),'start'); end=vector(op.get('end'),'end')
    sag=number(op.get('sag',12),'sag',0,256); radius=number(op.get('radius',.75),'radius',.1,4)
    count=int(number(op.get('segments',12),'segments',2,32))
    if count!=op.get('segments',12):raise OpError('segments must be an integer')
    points=[]
    for i in range(count+1):
        t=i/count; pt=[start[j]+(end[j]-start[j])*t for j in range(3)]; pt[2]-=4*sag*t*(1-t); points.append(pt)
    result=add_pipes(project,m,{'op':'add_pipes','points':points,'radius':radius,'sides':6,
                               'theme':op.get('theme','industrial'),**({'material':op['material']} if op.get('material') else {})},refs)
    result['op']='add_cables'
    if result['ids'] and op.get('ref'):refs[op['ref']]=result['ids'][0];result['ref']=op['ref']
    return result
