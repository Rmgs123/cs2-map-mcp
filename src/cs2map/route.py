"""Physical route checks; coordinate starts teleport only before measured input."""
from __future__ import annotations

import copy
import json
import math
import re
import time
import uuid
from contextlib import contextmanager

from . import game, scripting, story, system, vconsole

RELEASE = '-forward\n-back\n-left\n-right\n-jump\n-duck\n-use\n-attack\n-sprint'


@contextmanager
def focus_game(settings):
    if game.session(settings) is None:
        raise RuntimeError('no Workshop Tools session for this addon')
    previous=system.foreground()
    system.focus(game._window('Counter-Strike 2').hwnd)
    try:
        yield
    finally:
        if previous:
            system.focus(previous)


PROBE_CODE = r'''import { Instance, CSPlayerPawn, CSInputs } from "cs_script/point_script";
const vec=(v)=>[v.x,v.y,v.z];
Instance.RegisterCheatCommand("r2_route_probe",(args)=>{
 const [token,...a]=args.trim().split(/\s+/);
 const p=Instance.GetAllPlayerControllers().find(c=>c.GetPlayerSlot()===0&&!c.IsBot())?.GetPlayerPawn();
 let s={version:2,map:Instance.GetMapName(),alive:!!p?.IsValid()&&p.IsAlive()};
 if(s.alive){const o=p.GetAbsOrigin(),g=p.GetGroundEntity();
  Object.assign(s,{pos:vec(o),eye:vec(p.GetEyePosition()),move_type:p.GetMoveType(),pos_angles:vec({x:p.GetEyeAngles().pitch,y:p.GetEyeAngles().yaw,z:p.GetEyeAngles().roll}),velocity:vec(p.GetAbsVelocity()),health:p.GetHealth(),noclip:p.IsNoclipping(),duck:p.IsDucked(),forward:p.IsInputPressed(CSInputs.FORWARD),back:p.IsInputPressed(CSInputs.BACK),use:p.IsInputPressed(CSInputs.USE),ground:g?.IsValid()?{name:g.GetEntityName(),class:g.GetClassName()}:null});
  const eye=p.GetEyePosition(),ang=p.GetEyeAngles(),r=Math.PI/180,cp=Math.cos(ang.pitch*r);
  const useHit=Instance.TraceLine({start:eye,end:{x:eye.x+Math.cos(ang.yaw*r)*cp*96,y:eye.y+Math.sin(ang.yaw*r)*cp*96,z:eye.z-Math.sin(ang.pitch*r)*96},ignoreEntity:p});
  s.use_hit=useHit.hitEntity?.GetEntityName()??null;
  if(a.length===3){const goal={x:Number(a[0]),y:Number(a[1]),z:Number(a[2])};const dx=goal.x-o.x,dy=goal.y-o.y,n=Math.hypot(dx,dy)||1;
   const t=Instance.TracePlayer({player:p,start:{x:o.x,y:o.y,z:o.z+0.2},end:{x:o.x+dx/n*40,y:o.y+dy/n*40,z:o.z+0.2}});
   s.obstacle={hit:t.didHit,solid:t.startedInSolid,fraction:t.fraction,normal:vec(t.normal),name:t.hitEntity?.GetEntityName(),class:t.hitEntity?.GetClassName()};
  }
 }
 Instance.Msg('R_ROUTE_STATE '+token+' '+JSON.stringify(s));
});
Instance.RegisterCheatCommand("r2_route_entity",(args)=>{
 const [token,name]=args.trim().split(/\s+/);
 const e=Instance.FindEntityByName(name);
 const s={map:Instance.GetMapName(),name,found:!!e&&e.IsValid()};
 if(s.found)Object.assign(s,{class:e.GetClassName(),origin:vec(e.GetAbsOrigin()),health:e.GetHealth(),alive:e.IsAlive()});
 Instance.Msg('R_ROUTE_ENTITY '+token+' '+JSON.stringify(s));
});
'''


def angles(eye, target):
    dx, dy, dz = (target[i] - eye[i] for i in range(3))
    return [-math.degrees(math.atan2(dz, math.hypot(dx, dy))), math.degrees(math.atan2(dy, dx)), 0]


def target_position(m, name):
    matches = m.entities(targetname=name)
    if len(matches) != 1:
        raise ValueError(f'route target needs one entity named {name}')
    node = matches[0]
    bounds = [checks_bounds(c) for c in node.get('children', [])]
    bounds = [b for b in bounds if b]
    if bounds:
        return [(min(b[0][i] for b in bounds) + max(b[1][i] for b in bounds)) / 2 for i in range(3)]
    return list(node['origin'])


def checks_bounds(node):
    from .checks import _mesh_bounds
    return _mesh_bounds(node) if node.type == 'CMapMesh' else None


def ladder_for(m, start, goal, expected=None, top=None):
    """Find a nearby verified axis-aligned ladder, excluding transformed groups."""
    candidates = []
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or not _plain_world_mesh(m,node) or not any('toolsinvisibleladder' in str(x)
                                               for x in node.get('meshData', {}).get('materials', [])):
            continue
        bounds = _closed_box(node)
        if bounds is None:
            continue
        lo, hi = bounds
        if expected and any(abs(bounds[j][i]-expected[k][i])>.01
                            for j,k in enumerate(('mins','maxs')) for i in range(3)):
            continue
        if top and (math.dist(top[:2],[(lo[i]+hi[i])/2 for i in (0,1)])>80
                    or not lo[2]<top[2]<=hi[2]+32):
            continue
        if hi[2] < start[2] - 32 or lo[2] > goal[2] + 18:
            continue
        distance = math.dist(goal[:2], [(lo[i]+hi[i])/2 for i in (0,1)])
        if distance <= 80:
            candidates.append((distance, bounds))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _plain_world_mesh(m, node):
    parent = m.parent_of(node)
    while parent is not None and parent.type == 'CMapGroup':
        if (any(abs(float(v)) > .001 for v in parent.get('origin',[0,0,0]))
                or any(abs(float(v)) > .001 for v in parent.get('angles',[0,0,0]))
                or any(abs(float(v)-1) > .001 for v in parent.get('scales',[1,1,1]))):
            return False
        parent = m.parent_of(parent)
    return parent is m.world


def _closed_box(node):
    """Recognize exact axis-aligned closed boxes; never treat a hollow mesh as its AABB."""
    from .checks import _triangles, _vertices
    bounds=checks_bounds(node)
    if not bounds:
        return None
    lo,hi=bounds
    if any(hi[i]-lo[i]<.001 for i in range(3)):
        return None
    corners={(x,y,z) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])}
    vertices={tuple(v) for v in _vertices(node)}
    if vertices!=corners:
        return None
    triangles=[{tuple(v) for v in tri} for tri in _triangles(node)]
    if len(triangles)!=12:
        return None
    for axis in range(3):
        other=[j for j in range(3) if j!=axis]
        for side in (lo[axis],hi[axis]):
            face=[tri for tri in triangles if len(tri)==3 and all(v[axis]==side for v in tri)]
            if len(face)!=2 or len(face[0]|face[1])!=4:
                return None
            diagonal=list(face[0]&face[1])
            if len(diagonal)!=2 or not all(diagonal[0][j]!=diagonal[1][j] for j in other):
                return None
    return bounds


def check_route(project,m):
    """Prove a narrow elevator/solid-box conflict; this is not a live traversal pass."""
    from . import checks, layout
    boxes=[]
    for node in m.iter_nodes():
        if node.type!='CMapMesh' or not checks._is_solid_mesh(node):
            continue
        parent=m.parent_of(node)
        transformed=False
        while parent is not None and parent.type=='CMapGroup':
            transformed |= any(abs(float(v))>.001 for v in parent.get('origin',[0,0,0])) or any(abs(float(v))>.001 for v in parent.get('angles',[0,0,0])) or any(abs(float(v)-1)>.001 for v in parent.get('scales',[1,1,1]))
            parent=m.parent_of(parent)
        if parent is not m.world or transformed:
            continue  # exclude all entity-owned/dynamic/helper geometry
        bounds=_closed_box(node)
        if bounds:boxes.append((node,bounds))
    found=[]
    for lift in m.entities(classname='func_movelinear'):
        props=lift['entity_properties']
        try:
            pitch=float(str(props.get('movedir','')).split()[0]);travel=float(props.get('movedistance',0))
        except (ValueError,IndexError):
            continue
        if abs(pitch+90)>.001 or travel<=0:
            continue
        parts=[_closed_box(n) for n in lift.get('children',[]) if n.type=='CMapMesh']
        if len(parts)!=1 or not parts[0]:
            continue
        lo,hi=parts[0]
        if min(hi[0]-lo[0],hi[1]-lo[1])<32 or hi[2]-lo[2]>32:
            continue  # tiny light carriers and tall sliding panels are not flat player platforms
        center=[(lo[i]+hi[i])/2 for i in range(2)]
        hull_lo=[center[0]-16,center[1]-16,hi[2]+.1]
        hull_hi=[center[0]+16,center[1]+16,hi[2]+travel+72]
        for obstacle,(a,b) in boxes:
            if all(min(hull_hi[i],b[i])-max(hull_lo[i],a[i])>.01 for i in range(3)):
                found.append(checks.problem('error','route_lift_obstruction',lift['nodeID'],
                    f'{props.get("targetname") or lift["nodeID"]}: standing player at platform centre hits solid mesh {obstacle["nodeID"]} during upward travel',
                    'Cut a shaft/opening or move the lift beside the floor; verify the repaired route in CS2.'))
                break
    return found


def _layout_rooms(m):
    """Floor-level room graph from add_layout plans: rooms and openings with their bounds."""
    from . import layout
    rooms, edges = {}, {}
    stairs = set()          # add_paving built solid stairs between these two rooms of a layout
    for node in m.iter_nodes():
        raw = node.get('cs2map_paving')
        if raw:
            try:
                meta = json.loads(raw)
                stairs.update((meta.get('layout'), frozenset((s['from'], s['to']))) for s in meta.get('steps', []))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
    for data in layout.plans(m):
        for rid, r in data['rooms'].items():
            rooms[(data['id'], rid)] = r
            edges.setdefault((data['id'], rid), [])
        for h in data['openings']:
            between = h.get('between')
            if not between:
                continue
            a, b = (data['id'], between[0]), (data['id'], between[1])
            if all(h['bottom'] - rooms[k]['floor_z'] <= 18 for k in (a, b))                     or (data.get('name'), frozenset(between)) in stairs:
                edges[a].append((b, h))
                edges[b].append((a, h))
    return rooms, edges


def _room_of(rooms, point, margin=24):
    """Smallest room whose interior (grown by margin horizontally) holds the point at its floor level."""
    found = []
    for key, r in rooms.items():
        lo, hi = r['interior']['mins'], r['interior']['maxs']
        if (lo[0] - margin <= point[0] <= hi[0] + margin and lo[1] - margin <= point[1] <= hi[1] + margin
                and r['floor_z'] - 24 <= point[2] <= hi[2]):
            found.append(((hi[0] - lo[0]) * (hi[1] - lo[1]), key))
    return min(found)[1] if found else None


def layout_waypoints(m, start, goal):
    """Walk points through the openings between the rooms of start and goal (empty if same room,
    unknown rooms or no floor-level path: lifts and ladders still need an explicit stage route)."""
    rooms, edges = _layout_rooms(m)
    a, b = _room_of(rooms, start), _room_of(rooms, goal)
    if a is None or b is None or a == b:
        return []
    previous, queue = {a: None}, [a]
    while queue:
        key = queue.pop(0)
        if key == b:
            break
        for nxt, hole in edges.get(key, []):
            if nxt not in previous:
                previous[nxt] = (key, hole)
                queue.append(nxt)
    if b not in previous:
        return []
    path, key = [], b
    while previous[key] is not None:
        key, hole = previous[key]
        path.append((key, hole))
    points = []
    for here, hole in reversed(path):
        lo, hi = hole['bounds']['mins'], hole['bounds']['maxs']
        centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
        axis = 0 if hi[0] - lo[0] < hi[1] - lo[1] else 1     # wall normal: the thin side
        r = rooms[here]['interior']
        inside = (r['mins'][axis] + r['maxs'][axis]) / 2
        sign = 1 if inside < centre[axis] else -1            # from here through the opening
        for offset in (-40, 40):
            q = [centre[0], centre[1], hole['bottom']]
            q[axis] += sign * offset
            points.append(q)
    return points


def through_openings(m, steps, start):
    """Insert doorway waypoints before every go step whose room differs from the previous point."""
    out, cursor = [], list(start)
    for step in steps:
        if 'go' in step:
            out.extend({'go': q, 'radius': 24, **({'stage': step['stage']} if 'stage' in step else {})}
                       for q in layout_waypoints(m, cursor, step['go']))
            cursor = list(step['go'])
        out.append(step)
    return out


def _ladder_edges(m, rooms):
    """Return room transitions for world ladder surfaces.

    Ladders deliberately have no targetname in CS2.  Their material and bounds
    are the persisted identity, so the planner keeps the bounds in the step
    instead of inventing an entity reference.
    """
    from . import layout
    found = []
    solids = layout.world_solids(m)
    for node in m.iter_nodes():
        if node.type != 'CMapMesh' or not _plain_world_mesh(m,node) or not any('toolsinvisibleladder' in str(x)
                                               for x in node.get('meshData', {}).get('materials', [])):
            continue
        bounds = _closed_box(node)
        if not bounds:
            continue
        lo, hi = bounds
        near = [k for k, r in rooms.items()
                if lo[0] - 32 <= r['interior']['maxs'][0] and hi[0] + 32 >= r['interior']['mins'][0]
                and lo[1] - 32 <= r['interior']['maxs'][1] and hi[1] + 32 >= r['interior']['mins'][1]]
        feet = [k for k in near if abs(lo[2] - rooms[k]['floor_z']) <= 18]
        tops = [k for k in near if rooms[k]['floor_z'] - 18 > lo[2]
                and hi[2] >= rooms[k]['floor_z'] - 24]
        axis=0 if hi[0]-lo[0] < hi[1]-lo[1] else 1
        for lower in feet:
            for upper in tops:
                if lower == upper:
                    continue
                c = [(lo[i] + hi[i]) / 2 for i in range(2)]
                for side in (-1,1):
                    face=c.copy();face[axis]=(lo[axis] if side<0 else hi[axis])+side*16.05
                    r=rooms[lower]['interior'];floor=rooms[lower]['floor_z']
                    if not all(r['mins'][i]<=face[i]<=r['maxs'][i] for i in (0,1)):
                        continue
                    hull={'mins':[face[0]-16,face[1]-16,floor+.05],
                          'maxs':[face[0]+16,face[1]+16,floor+72]}
                    if layout.collisions(m,hull,[0,0,hi[2]+18-floor],solids):
                        continue
                    found.append({'kind': 'ladder', 'lower': lower, 'upper': upper,
                                  'bounds': {'mins': list(lo), 'maxs': list(hi)},
                                  'centre': c, 'face':face, 'side':side,
                                  'lower_z': floor, 'upper_z': rooms[upper]['floor_z'],
                                  'node': node['nodeID']})
    return found


def _lift_edges(m, rooms):
    """Return persisted add_lift transitions, including generated buttons."""
    from .layout import LIFT_KEY
    found = []
    for node in m.entities(classname='func_movelinear'):
        raw = node.get(LIFT_KEY)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
            child_bounds = [checks_bounds(child) for child in node.get('children', [])]
            child_bounds = [b for b in child_bounds if b]
            if not child_bounds:
                continue
            bounds = ([min(b[0][i] for b in child_bounds) for i in range(3)],
                      [max(b[1][i] for b in child_bounds) for i in range(3)])
            if not bounds:
                continue
            # Metadata is intentionally small; resolve the owning layout by
            # matching its room ids and optional name.
            candidates = [(a, b) for a, r in rooms.items() if r['id'] == meta.get('room')
                          for b, u in rooms.items() if u['id'] == meta.get('upper') and a[0] == b[0]]
            if meta.get('layout'):
                from . import layout
                names = {d['id']: d['name'] for d in layout.plans(m)}
                candidates = [x for x in candidates if names.get(x[0][0]) == meta['layout']]
            if len(candidates)!=1:
                continue
            lower, upper = candidates[0]
            target = node['entity_properties'].get('targetname')
            if not target:
                continue
            # add_lift names its generated buttons <lift>_lower/_upper.
            button_names = {e['entity_properties'].get('targetname'): e for e in m.entities()
                            if m.classname(e) in ('func_button', 'func_physical_button')}
            lower_button = target + '_lower'
            upper_button = target + '_upper'
            if lower_button not in button_names or upper_button not in button_names:
                continue
            lo, hi = bounds
            dz = float(node['entity_properties'].get('movedistance', 0))
            direction = [float(x) for x in str(node['entity_properties'].get('movedir','')).split()]
            if (len(direction)!=3 or abs(direction[0]+90)>.001 or dz<=0
                    or abs(hi[2]+dz-rooms[upper]['floor_z'])>1):
                continue
            found.append({'kind': 'lift', 'lower': lower, 'upper': upper,
                          'node': node['nodeID'], 'target': target,
                          'lower_button': lower_button, 'upper_button': upper_button,
                          'lower_z': hi[2], 'upper_z': hi[2] + dz,
                          'origin':list(node['origin']), 'bounds':{'mins':list(lo),'maxs':list(hi)},
                          'centre': [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]})
        except (ValueError, TypeError, KeyError):
            continue
    return found


def _layout_route(m, start, goal):
    """Plan typed room transitions.  ``None`` means no layout graph exists."""
    from . import layout
    rooms, openings = _layout_rooms(m)
    if not rooms:
        return None
    a, b = _room_of(rooms, start), _room_of(rooms, goal)
    if a is None or b is None:
        return []
    if a == b:
        return []
    edges = {k: [] for k in rooms}
    for data in layout.plans(m):
        for h in data['openings']:
            if h.get('between') and all(h['bottom'] - rooms[(data['id'], rid)]['floor_z'] <= 18
                                       for rid in h['between']):
                x, y = (data['id'], h['between'][0]), (data['id'], h['between'][1])
                edges[x].append((y, {'kind': 'opening', **h})); edges[y].append((x, {'kind': 'opening', **h}))
    for edge in _lift_edges(m, rooms):
        edges[edge['lower']].append((edge['upper'], edge)); edges[edge['upper']].append((edge['lower'], edge))
    for edge in _ladder_edges(m, rooms):
        edges[edge['lower']].append((edge['upper'], edge)); edges[edge['upper']].append((edge['lower'], edge))
    previous = {a: None}; queue = [a]
    while queue:
        here = queue.pop(0)
        if here == b:
            break
        for nxt, edge in edges.get(here, []):
            if nxt not in previous:
                previous[nxt] = (here, edge); queue.append(nxt)
    if b not in previous:
        return []
    path, key = [], b
    while previous[key] is not None:
        dest=key; key, edge = previous[key]; path.append((key, edge, dest))
    path.reverse()
    # Rebuild with the actual destination room for each transition.
    result, cursor = [], a
    for _, edge, dest in path:
        if edge['kind'] == 'opening':
            lo, hi = edge['bounds']['mins'], edge['bounds']['maxs']
            centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
            axis = 0 if hi[0] - lo[0] < hi[1] - lo[1] else 1
            r = rooms[cursor]['interior']; inside = (r['mins'][axis] + r['maxs'][axis]) / 2
            sign = 1 if inside < centre[axis] else -1
            for offset in (-40, 40):
                q = [centre[0], centre[1], edge['bottom']]; q[axis] += sign * offset
                result.append({'go': q, 'radius': 24})
        elif edge['kind'] == 'ladder':
            c = edge['centre']
            if cursor == edge['lower']:
                lo,hi=edge['bounds']['mins'],edge['bounds']['maxs']
                axis=0 if hi[0]-lo[0] < hi[1]-lo[1] else 1
                side=edge['side'];foot=edge['face']
                climb=c.copy();climb[axis]-=side*2
                result.extend([{'go': [*foot,edge['lower_z']], 'radius': 6},
                               {'go': [*climb,max(hi[2],edge['upper_z'])+18], 'radius': 24}])
            else:
                result.append({'climb_down': {'to': [*edge['face'], edge['lower_z']],
                                              'top': [c[0], c[1], edge['upper_z']],
                                              'bounds': edge['bounds']}})
        elif edge['kind'] == 'lift':
            button = edge['lower_button'] if cursor == edge['lower'] else edge['upper_button']
            from_z=edge['lower_z'] if cursor==edge['lower'] else edge['upper_z']
            to_z=edge['upper_z'] if cursor==edge['lower'] else edge['lower_z']
            result.append({'_lift_ready':edge,'from_z':from_z})
            result.append({'go':[*edge['centre'],from_z],'radius':8})
            result.append({'use': button})
            result.append({'ride':edge['target'],'to_z':to_z})
            # Step onto the upper slab beside the platform, not its centre in
            # midair. Lower floors cover the full parent room.
            exit=[*edge['centre'],to_z]
            if cursor==edge['lower']:
                ul,uh=rooms[dest]['interior']['mins'],rooms[dest]['interior']['maxs']
                exit[:2]=[max(ul[i]+24,min(uh[i]-24,exit[i])) for i in (0,1)]
            result.append({'go':exit,'radius':12})
        cursor = dest
    return result


def _triangle_grid_waypoints(m, start, goal, spacing=24):
    """One-floor A*: triangle support, 32x32x72 hull and swept edges.

    Bounds only accelerate triangle selection. Hollow meshes remain hollow;
    every compressed segment is checked again, including floor support.
    """
    from . import checks, layout
    import heapq
    if abs(start[2]-goal[2]) > 24:
        return []   # vertical transitions require metadata or explicit steps
    solids = layout.world_solids(m)          # world-space, group transforms applied
    floor = start[2]
    cache = {}
    def supported(x, y):
        key = (round(x,4),round(y,4))
        if key in cache:
            return cache[key]
        nearby = [(n,b,t) for n,b,t in solids if b and b[0][0]-16 <= x <= b[1][0]+16
                  and b[0][1]-16 <= y <= b[1][1]+16]
        triangles = [tri for _,_,ts in nearby for tri in ts]
        z = checks._floor((x,y,floor+18.1),triangles,max_drop=36.2)
        if z is None:
            cache[key] = None; return None
        point = [x,y,z]
        # Eight units of clearance account for waypoint tolerance and the
        # finite input/probe interval. Tight corners must not be cut early.
        hull = {'mins':[x-24,y-24,z+.05],'maxs':[x+24,y+24,z+72]}
        if layout.collisions(m,hull,[0,0,0],nearby):
            cache[key] = None; return None
        # A centre ray alone would permit overhanging voids. Require support
        # under all four corners of the footprint as well.
        for dx,dy in ((-15,-15),(-15,15),(15,-15),(15,15)):
            height = checks._floor((x+dx,y+dy,z+18.1),triangles,max_drop=36.2)
            if height is None or abs(height-z)>18:
                cache[key] = None; return None
        cache[key] = point; return point
    def clear(a,b):
        if abs(a[2]-b[2])>18:
            return False
        # Flat floor only: avoiding a floor in the swept hull must never turn
        # a real stair/step into a fictitious clearance proof.
        if abs(a[2]-b[2])>.1:
            return False
        hull = {'mins':[a[0]-24,a[1]-24,a[2]+.05],
                'maxs':[a[0]+24,a[1]+24,a[2]+72]}
        if layout.collisions(m,hull,[b[i]-a[i] for i in range(3)],solids):
            return False
        samples = max(1,math.ceil(math.dist(a[:2],b[:2])/spacing))
        return all(supported(a[0]+(b[0]-a[0])*i/samples,
                             a[1]+(b[1]-a[1])*i/samples) is not None for i in range(samples+1))
    a,b = supported(*start[:2]),supported(*goal[:2])
    if a is None or b is None:
        return []
    if clear(a,b):
        return [b]
    # Include neighbouring cells: endpoint rounding can put a free endpoint
    # into a blocked grid cell next to a wall.
    def cell(p): return (round(p[0]/spacing),round(p[1]/spacing))
    def candidates(p):
        cx,cy=cell(p); found=[]
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                c=(cx+dx,cy+dy); q=supported(c[0]*spacing,c[1]*spacing)
                if q is not None and clear(p,q): found.append((c,q))
        return found
    starts, ends = candidates(a), dict(candidates(b))
    padding = 256
    low=[math.floor((min(a[i],b[i])-padding)/spacing) for i in (0,1)]
    high=[math.ceil((max(a[i],b[i])+padding)/spacing) for i in (0,1)]
    previous={}; costs={}; frontier=[]
    for c,q in starts:
        costs[c]=math.dist(a,q);previous[c]=None
        heapq.heappush(frontier,(costs[c]+math.dist(q,b),c))
    reached=None; visited=set()
    while frontier and len(visited)<16384:
        _,here=heapq.heappop(frontier)
        if here in visited:continue
        visited.add(here)
        if here in ends:
            reached=here;break
        q=supported(here[0]*spacing,here[1]*spacing)
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nxt=(here[0]+dx,here[1]+dy)
            if not(low[0]<=nxt[0]<=high[0] and low[1]<=nxt[1]<=high[1]):continue
            r=supported(nxt[0]*spacing,nxt[1]*spacing)
            if r is None or not clear(q,r):continue
            cost=costs[here]+math.dist(q,r)
            if cost<costs.get(nxt,float('inf')):
                costs[nxt]=cost;previous[nxt]=here
                heapq.heappush(frontier,(cost+math.dist(r,b),nxt))
    if reached is None:return []
    path=[b];cur=reached
    while cur is not None:
        path.append(supported(cur[0]*spacing,cur[1]*spacing));cur=previous[cur]
    path.append(a);path.reverse()
    simplified=[];i=0
    while i<len(path)-1:
        j=len(path)-1
        while j>i+1 and not clear(path[i],path[j]):j-=1
        simplified.append(path[j]);i=j
    return simplified


def stage_target(m, stage):
    cond = stage['until']
    if 'zone' in cond:
        lo, hi = cond['zone']['mins'], cond['zone']['maxs']
        return [(lo[0] + hi[0])/2, (lo[1] + hi[1])/2, lo[2] + 8]
    for kind in ('button', 'use'):
        if kind in cond:
            return target_position(m, cond[kind])
    return None


def plan_between(m, start, goal):
    """Build a physical route between two points.

    Layout metadata gets first choice (doors, lifts and ladders).  A map with
    no layout uses a bounded one-floor grid, then a direct go as the final
    backwards-compatible fallback.
    """
    planned = _layout_route(m, start, goal)
    has_layout = planned is not None
    if not has_layout:
        points = _triangle_grid_waypoints(m,start,goal)
        planned = [{'go':q,'radius':6} for q in points]
    if not planned:
        planned = []
    # Obstacle clearing is decided by a live player trace in Driver.go, never
    # by a guessed model footprint or by a stale authored prop origin.
    last_goal = planned[-1].get('go') if planned else None
    if last_goal is None or math.dist(last_goal[:2],goal[:2]) > .01 or abs(last_goal[2]-goal[2]) > 18:
        planned.append({'go':list(goal)})
    if has_layout:
        routed=[];cursor=list(start)
        for step in planned:
            if 'go' in step:
                points=_triangle_grid_waypoints(m,cursor,step['go'])
                routed.extend({'go':q,'radius':6} for q in points[:-1])
                cursor=list(step['go'])
            elif 'ride' in step:
                cursor[2]=step['to_z']
            elif 'climb_down' in step:
                cursor=list(step['climb_down']['to'])
                bounds=step['climb_down'].get('bounds')
                if bounds:
                    lo,hi=bounds['mins'],bounds['maxs']
                    axis=0 if hi[0]-lo[0]<hi[1]-lo[1] else 1
                    cursor[axis]+=24*(1 if cursor[axis]>(lo[axis]+hi[axis])/2 else -1)
            routed.append(step)
        planned=routed
    return [{**s, **({'_obstacles':True} if 'go' in s else {})} for s in planned]


def approach_point(m, start, target):
    """An eye-height target needs a nearby feet goal, never a go into its box."""
    distance = math.dist(start[:2],target[:2]) or 1
    floor = start[2]
    rooms,_ = _layout_rooms(m)
    key = _room_of(rooms,target)
    if key is not None: floor=rooms[key]['floor_z']
    return [target[i]+(start[i]-target[i])/distance*48 for i in (0,1)]+[floor]


def validate(route):
    if route == 'auto':
        return route
    if not isinstance(route, list) or not 1 <= len(route) <= 128:
        raise ValueError('route must be auto or 1..128 steps')
    out = copy.deepcopy(route)
    for step in out:
        story._keys(step, {'go', 'use', 'wait', 'ride', 'climb_down', 'break', 'push',
                           'to_z', 'to', 'top', 'bounds', 'jump', 'duck', 'radius',
                           'within', 'stage', 'allow_damage'}, 'route step')
        actions = {'go', 'use', 'wait', 'ride', 'climb_down', 'break', 'push'}
        if len(set(step) & actions) != 1:
            raise ValueError('route step needs exactly one of go/use/wait/ride/climb_down/break/push')
        kind = next(iter(set(step) & actions))
        allowed = {kind, 'within', 'stage', 'allow_damage'}
        if kind == 'go':
            allowed |= {'jump', 'duck', 'radius'}
        elif kind == 'ride':
            allowed |= {'to_z'}
        elif kind == 'push':
            allowed |= {'to'}
        story._keys(step, allowed, kind)
        if kind == 'go':
            step[kind] = story._vector(step[kind], 'go')
            for flag in ('jump', 'duck'):
                if flag in step and not isinstance(step[flag], bool):
                    raise ValueError(f'{flag} must be boolean')
            story._number(step.get('radius', 20), 'radius', 2, 48)
        elif kind in ('use', 'ride', 'break'):
            story.entity_name(step[kind])
            if kind == 'ride':
                story._number(step.get('to_z'), 'to_z', -1e6, 1e6)
        elif kind == 'push':
            story.entity_name(step[kind])
            step['to'] = story._vector(step.get('to'), 'push.to')
        elif kind == 'climb_down':
            value = step[kind]
            if isinstance(value, (list, tuple)):
                step[kind] = {'to': story._vector(value, 'climb_down')}
            elif isinstance(value, dict):
                story._keys(value, {'to', 'top', 'bounds'}, 'climb_down')
                value = copy.deepcopy(value)
                value['to'] = story._vector(value.get('to'), 'climb_down.to')
                if 'top' in value:
                    value['top'] = story._vector(value['top'], 'climb_down.top')
                    if value['top'][2] <= value['to'][2]:
                        raise ValueError('climb_down.top must be above the landing')
                if 'bounds' in value:
                    if not isinstance(value['bounds'], dict) or set(value['bounds']) != {'mins', 'maxs'}:
                        raise ValueError('climb_down.bounds must contain mins and maxs')
                    value['bounds'] = {'mins': story._vector(value['bounds']['mins'], 'climb_down.bounds.mins'),
                                       'maxs': story._vector(value['bounds']['maxs'], 'climb_down.bounds.maxs')}
                    if any(value['bounds']['mins'][i] >= value['bounds']['maxs'][i] for i in range(3)):
                        raise ValueError('climb_down.bounds needs positive dimensions')
                step[kind] = value
            else:
                raise ValueError('climb_down must be [x,y,z] or an object with to')
        else:
            story._number(step[kind], 'wait', 0, 30)
        story._number(step.get('within', 15), 'within', 0.1, 90)
        if not isinstance(step.get('allow_damage', False), bool):
            raise ValueError('allow_damage must be boolean')
        if 'stage' in step:
            scripting._safe_name(step['stage'])
    return out


PHYSICS_CLASSES = ('prop_physics', 'prop_physics_override', 'prop_physics_multiplayer')


def validate_references(m, steps):
    """Check literal entities for every supplied stage before acquiring the game."""
    for step in steps:
        for key in ('use', 'ride', 'break', 'push'):
            if key not in step:
                continue
            target_position(m, step[key])
            node = m.entities(targetname=step[key])[0]
            cls = m.classname(node)
            if key == 'ride' and cls != 'func_movelinear':
                raise ValueError('ride requires a named func_movelinear platform')
            if key == 'push' and cls not in PHYSICS_CLASSES:
                raise ValueError('push requires a named physics prop')
            if key == 'break' and (cls not in PHYSICS_CLASSES + ('func_breakable',)
                                   or float(node['entity_properties'].get('health', 0)) <= 0):
                raise ValueError('break requires a breakable or physics prop with positive authored health')


def failure(state, goal, initial_health, stalled=False, allow_damage=False):
    if not state.get('alive'):
        return 'death'
    if state.get('noclip'):
        return 'noclip_enabled'
    if state.get('health', 100) < initial_health and not allow_damage:
        return 'damage'
    if goal is not None:
        dz = state['pos'][2] - goal[2]
        if dz < -96 and state.get('velocity', [0, 0, 0])[2] < -150 and not allow_damage:
            return 'fall'
        if stalled:
            obstacle = state.get('obstacle', {})
            if obstacle.get('solid'):
                return 'inside_solid'
            if obstacle.get('hit'):
                return 'height' if abs(dz) > 18 or abs(obstacle.get('normal', [0, 0, 0])[2]) > 0.5 else 'wall'
            return 'height' if abs(dz) > 18 else 'no_progress'
    return None


def _probe_source(project):
    source = project.s.content_addon / 'maps/scripts/mcp/r2_route_runtime.js'
    compiled = project.s.game_addon / 'maps/scripts/mcp/r2_route_runtime.vjs_c'
    if not source.exists() or source.read_text(encoding='utf-8') != PROBE_CODE or not compiled.exists() or compiled.stat().st_mtime < source.stat().st_mtime:
        result = scripting.script_write(project, 'r2_route_runtime', PROBE_CODE)
        if not result['ok']:
            raise RuntimeError(f'route probe compilation failed: {result.get("log")}')


class Driver:
    """Owns no lock: caller holds game.exclusive for the complete segment."""
    def __init__(self, project, deadline, lines):
        self.p, self.deadline, self.lines = project, deadline, lines
        self.samples, self.commands = [], []
        self.state = None
        self.interactions = []
        self.held = set()

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('route deadline exceeded')
        return left

    def command(self, cmd):
        commands = []
        for line in cmd.splitlines():
            if line.startswith('+'):
                if line[1:] in self.held:
                    continue
                self.held.add(line[1:])
            elif line.startswith('-'):
                self.held.discard(line[1:])
            commands.append(line)
        self.commands.extend(commands)
        token = 'r_ack_' + uuid.uuid4().hex[:10]
        ok, lines = vconsole.run_until(commands+['echo '+token], token, timeout=min(2,self.remaining()))
        if not ok:
            raise RuntimeError('console did not acknowledge route input')

    def pause(self, seconds):
        time.sleep(min(seconds, self.remaining()))
        self.remaining()

    def probe(self, goal=None):
        token = uuid.uuid4().hex[:12]
        prefix = 'R_ROUTE_STATE ' + token + ' '
        cmd = 'r2_route_probe ' + token + (' ' + ' '.join(map(str, goal)) if goal else '')
        lines = game.query(self.p.s, cmd, prefix, timeout=min(2, self.remaining()))
        found = next((s[len(prefix):] for s in lines if s.startswith(prefix)), None)
        if found is None:
            raise RuntimeError('route probe did not return authoritative player state')
        self.state = json.loads(found)
        if self.state.get('map') != self.map:
            raise RuntimeError('live map changed during route test')
        self.samples.append({'seconds': round(time.monotonic() - (self.deadline - self.timeout), 3), **self.state})
        return self.state

    def entity(self, name):
        token = uuid.uuid4().hex[:12]
        prefix = 'R_ROUTE_ENTITY ' + token + ' '
        lines = game.query(self.p.s, 'r2_route_entity ' + token + ' ' + name, prefix,
                           timeout=min(2, self.remaining()))
        found = next((s[len(prefix):] for s in lines if s.startswith(prefix)), None)
        if found is None:
            raise RuntimeError('route entity probe did not return authoritative state')
        result = json.loads(found)
        if result.get('map') != self.map:
            raise RuntimeError('live map changed during entity probe')
        return result

    def install(self):
        try:
            state = self.probe()
            if state.get('version') == 2:
                return state
        except RuntimeError:
            pass
        self.command('ent_fire r2_route_runtime_probe Kill\nent_create point_script {"targetname" "r2_route_runtime_probe" "cs_script" "maps/scripts/mcp/r2_route_runtime.vjs"}')
        self.pause(.3)
        return self.probe()

    def go(self, step):
        goal, radius = step['go'], step.get('radius', 20)
        until = min(self.deadline - 4, time.monotonic() + step.get('within', 15))
        state = self.probe(goal)
        if state['pos'][2] - goal[2] > 64 and self.p is not None:
            ladder = ladder_for(self.p.load(self.map), state['pos'], goal)
            if ladder:
                return self.climb_down({'climb_down': {'to': goal},
                                        'within': step.get('within', 15),
                                        'allow_damage': step.get('allow_damage', False)})
        health = state.get('health', 100)
        allow_damage = bool(step.get('allow_damage', False))
        best, progressed = float('inf'), time.monotonic()
        jumped = False; cleared = set()
        try:
            self.command('-forward\n' + ('+duck' if step.get('duck') else '-duck'))
            while True:
                reason = failure(state, goal, health, allow_damage=allow_damage)
                if reason:
                    return reason
                dist = math.dist(state['pos'][:2], goal[:2])
                if dist <= radius and abs(state['pos'][2] - goal[2]) <= 18:
                    return None
                metric = dist + abs(state['pos'][2] - goal[2])
                if metric < best - 3:
                    best, progressed = metric, time.monotonic()
                stuck = time.monotonic() - progressed > 1.8
                obstacle = state.get('obstacle', {})
                name = obstacle.get('name')
                if step.get('_obstacles') and obstacle.get('hit') and name and name not in cleared:
                    matches = self.p.load(self.map).entities(targetname=name)
                    if len(matches)==1 and obstacle.get('class') in ('func_breakable','prop_physics','prop_physics_override','prop_physics_multiplayer'):
                        self.command('-forward\n-jump\n-duck')
                        kv=matches[0]['entity_properties']; live=self.entity(name)
                        if live.get('found') and live.get('health',0)>0 and float(kv.get('health',0))>0:
                            action={'break':name,'within':12,'allow_damage':allow_damage}
                            issue=self.break_target(action)
                        else:
                            origin=live.get('origin',list(matches[0]['origin']))
                            length=math.dist(state['pos'][:2],goal[:2]) or 1
                            destination=[origin[i]+(goal[i]-state['pos'][i])/length*96 for i in (0,1)]+[origin[2]]
                            action={'push':name,'to':destination,'within':12,'allow_damage':allow_damage}
                            issue=self.push(action)
                        self.interactions.append({**action,'reason':issue})
                        if issue:return issue
                        cleared.add(name); progressed=time.monotonic();best=float('inf')
                        until=min(self.deadline-4,time.monotonic()+step.get('within',15))
                        state=self.probe(goal);continue
                if stuck or time.monotonic() >= until:
                    return failure(state, goal, health, stalled=True, allow_damage=allow_damage) or 'point_timeout'
                ang = angles([state['pos'][0], state['pos'][1], state['pos'][2]+64], goal)
                # Horizontal movement; ladder waypoints use pitch toward the vertical goal.
                pitch = ang[0] if abs(goal[2]-state['pos'][2]) > 64 else 0
                cmd = f'setang {pitch:g} {ang[1]:g} 0\n+forward'
                obstacle = state.get('obstacle', {})
                if step.get('jump') and not jumped and (obstacle.get('hit') or dist < 100):
                    cmd += '\n+jump'; jumped = True
                self.command(cmd)
                # Pulse long enough to accelerate: crouched speed is ~85 u/s, standing ~250.
                speed = 85 if step.get('duck') else 250
                self.pause(min(.25, max(.12 if step.get('duck') else .06, (dist-radius)/speed)))
                self.command('-forward\n-jump')
                state = self.probe(goal)
        finally:
            self.command('-forward\n-jump\n-duck')

    def climb_down(self, step):
        """Descend a toolsinvisibleladder by real input from its top edge."""
        value = step['climb_down']
        target = value if isinstance(value, (list, tuple)) else value['to']
        state = self.probe(target)
        if (abs(state['pos'][2]-target[2]) <= 18 and math.dist(state['pos'][:2], target[:2]) <= 24
                and state.get('move_type') != 6):
            return None
        bounds = ladder_for(self.p.load(self.map), state['pos'], target,
                            value.get('bounds') if isinstance(value,dict) else None,
                            value.get('top') if isinstance(value,dict) else None)
        if bounds is None:
            return 'ladder_not_found'
        lo, hi = bounds; axis = 0 if hi[0]-lo[0] < hi[1]-lo[1] else 1
        centre = [(lo[i]+hi[i])/2 for i in (0,1)]
        # The lower landing determines the exposed face.  When a waypoint is
        # in the ladder itself, use the side opposite the upper approach.
        side = 1 if target[axis] >= centre[axis] else -1
        if abs(target[axis]-centre[axis]) < 1:
            side = 1 if state['pos'][axis] < centre[axis] else -1
        contact = centre.copy()
        contact[axis] = (hi[axis] if side > 0 else lo[axis]) + side * 16.05
        contact += [min(state['pos'][2], hi[2])]
        inward = [0.,0.,0.]; inward[axis] = -side
        yaw = math.degrees(math.atan2(inward[1],inward[0]))
        until = min(self.deadline - 4, time.monotonic() + step.get('within', 15))
        health = state.get('health', 100); best = float('inf'); progressed = time.monotonic()
        attached = False
        try:
            self.command('-forward\n-back\n+duck')
            while time.monotonic() < until:
                state = self.probe(target)
                reason = failure(state, target, health, allow_damage=step.get('allow_damage', False))
                if reason:
                    return reason
                attached |= state.get('move_type') == 6
                if abs(state['pos'][2]-target[2]) <= 18 and math.dist(state['pos'][:2], target[:2]) <= 48:
                    if not attached:return 'ladder_not_entered'
                    landing=[*contact[:2],target[2]];landing[axis]+=side*24
                    issue=self.go({'go':landing,'radius':8,'duck':True,
                                   'within':max(.1,until-time.monotonic()),
                                   'allow_damage':step.get('allow_damage',False)})
                    if issue:return issue
                    self.command('-forward\n-duck');self.pause(.15)
                    state=self.probe(target)
                    return failure(state,target,health,allow_damage=step.get('allow_damage',False)) or (
                        'ladder_not_exited' if state.get('move_type')==6 else None)
                distance = math.dist(state['pos'][:2], contact[:2])
                metric = distance + state['pos'][2] - target[2]
                if metric < best-2:
                    best, progressed = metric, time.monotonic()
                elif time.monotonic()-progressed > 2:
                    return 'ladder_down_blocked'
                on_wrong_face = (state['pos'][axis]-centre[axis])*side < 0
                if state.get('move_type') == 6 and on_wrong_face:
                    # Some ladders rise above the upper floor. Climb over
                    # their lip before descending the opposite exposed face.
                    opposite_yaw=math.degrees(math.atan2(-inward[1],-inward[0]))
                    self.command(f'setang -80 {opposite_yaw:g} 0\n+forward')
                elif state.get('move_type') == 6 or (distance < 3 and state['pos'][2] < hi[2]-1):
                    # Keep pressure into the face; steep positive pitch moves
                    # down in CSMoveType.LADDER rather than away from it.
                    self.command(f'setang 80 {yaw:g} 0\n+forward')
                else:
                    approach = angles(state['pos'], contact)
                    self.command(f'setang 0 {approach[1]:g} 0\n+forward')
                self.pause(.08 if distance < 12 else .18)
            return 'point_timeout'
        finally:
            self.command('-forward\n-back\n-duck')

    def break_target(self, step):
        until = min(self.deadline - 4, time.monotonic() + step.get('within', 15))
        name = step['break']; m=self.p.load(self.map); node=m.entities(targetname=name)[0]
        target = target_position(m, name)
        if m.classname(node).startswith('prop_physics'):
            from . import physics
            bounds=physics.model_physics(self.p,node['entity_properties'].get('model')).get('bounds')
            if not bounds:return 'break_bounds_unavailable'
            target=[node['origin'][i]+(bounds[0][i]+bounds[1][i])/2 for i in range(3)]
        state = self.probe(); health = state.get('health', 100)
        if math.dist(state['pos'][:2], target[:2]) > 72:
            reason = self.go({'go': [target[0], target[1], state['pos'][2]], 'radius': 52,
                              'within': max(.1,until-time.monotonic()), 'allow_damage': step.get('allow_damage', False)})
            if reason:
                return reason
        try:
            while time.monotonic() < until:
                info = self.entity(name)
                if not info.get('found'):
                    return None
                state = self.probe()
                reason = failure(state, None, health, allow_damage=step.get('allow_damage', False))
                if reason:
                    return reason
                eye = [state['pos'][0], state['pos'][1], state['pos'][2] + (46 if state.get('duck') else 64)]
                aim=[target[i]+info['origin'][i]-node['origin'][i] for i in range(3)]
                ang = angles(eye, aim)
                self.command(f'setang {ang[0]:g} {ang[1]:g} 0\n+attack')
                self.pause(.5)
                self.command('-attack')
            return 'break_timeout'
        finally:
            self.command('-attack')

    def push(self, step):
        until = min(self.deadline - 4, time.monotonic() + step.get('within', 15))
        name, destination = step['push'], step['to']
        info = self.entity(name)
        if not info.get('found'):
            return 'push_target_missing'
        origin = info.get('origin') or target_position(self.p.load(self.map), name)
        dx, dy = destination[0] - origin[0], destination[1] - origin[1]
        n = math.hypot(dx, dy) or 1
        approach = [origin[0] - dx / n * 64, origin[1] - dy / n * 64, origin[2]]
        reason = self.go({'go': approach, 'radius': 28, 'within': max(.1,until-time.monotonic()),
                          'allow_damage': step.get('allow_damage', False)})
        if reason:
            return reason
        health = self.probe().get('health', 100); best = math.dist(origin, destination); stalled = time.monotonic()
        try:
            while time.monotonic() < until:
                info = self.entity(name)
                if not info.get('found'):
                    return 'push_target_missing'
                current = info.get('origin', origin); distance = math.dist(current, destination)
                if distance <= 48:
                    return None
                if distance < best - 3:
                    best, stalled = distance, time.monotonic()
                elif time.monotonic() - stalled > 2:
                    return 'push_blocked'
                state = self.probe(); reason = failure(state, None, health, allow_damage=step.get('allow_damage', False))
                if reason:
                    return reason
                eye = [state['pos'][0], state['pos'][1], state['pos'][2] + 64]
                ang = angles(eye, destination)
                self.command(f'setang 0 {ang[1]:g} 0\n+forward')
                self.pause(.18)
                self.command('-forward')
            return 'push_timeout'
        finally:
            self.command('-forward')

    def use(self, name):
        target = target_position(self.p.load(self.map), name)
        state = self.probe()
        issue=failure(state,None,state.get('health',100))
        if issue:
            return issue
        eye = [state['pos'][0], state['pos'][1], state['pos'][2]+(46 if state.get('duck') else 64)]
        if math.dist(eye, target) > 100:
            return 'use_out_of_range'
        ang = angles(eye, target)
        self.command(f'setang {ang[0]:g} {ang[1]:g} 0\n-use')
        self.pause(.1)
        try:
            aimed = self.probe()
            if aimed.get('use_hit') != name:
                return 'use_target_not_hit'
            self.command('+use')
            # The eye ray must hit before pressing. A lift can move the eye
            # away before the post-input snapshot; ride verifies its effect.
            for _ in range(4):
                self.pause(.05)
                state = self.probe()
                if state.get('use'):
                    return None
            return 'use_target_not_hit'
        finally:
            self.command('-use')

    def ride(self, step):
        until = min(self.deadline - 4, time.monotonic() + step.get('within', 12))
        health, boarded, state = self.probe().get('health', 100), False, self.state
        while time.monotonic() < until:
            reason = failure(state, [*state.get('pos', [0,0,0])[:2], step['to_z']], health,
                             allow_damage=step.get('allow_damage', False))
            if reason:
                return reason
            boarded |= (state.get('ground') or {}).get('name') == step['ride']
            if abs(state['pos'][2]-step['to_z']) <= 18:
                return None if boarded else 'not_on_lift'
            self.pause(.15); state = self.probe()
        return 'lift_blocked' if boarded else 'not_on_lift'

    def lift_ready(self, step):
        edge=step['_lift_ready'];m=self.p.load(self.map)
        live=self.entity(edge['target'])
        if not live.get('found'):return 'lift_missing'
        current=edge['lower_z']+live['origin'][2]-edge['origin'][2]
        if abs(current-step['from_z'])<=3:return None
        # Call only by a real accessible button with a matching existing I/O
        # connection. Default add_lift buttons depart each floor; they cannot
        # call an absent platform from that floor.
        desired='Close' if step['from_z']==edge['lower_z'] else 'Open'
        floor=step['from_z']
        buttons=[e for e in m.entities() if m.classname(e) in ('func_button','func_physical_button')
                 and e['entity_properties'].get('targetname') and abs(target_position(m,e['entity_properties']['targetname'])[2]-floor-52)<40
                 and any(c.get('targetName')==edge['target'] and c.get('inputName')==desired
                         for c in e.get('connectionsData',[]))]
        if not buttons:return 'lift_call_unavailable'
        name=buttons[0]['entity_properties']['targetname']
        state=self.probe();point=approach_point(m,state['pos'],target_position(m,name))
        issue=self.go({'go':point,'radius':10}) or self.use(name)
        if issue:return issue
        until=min(self.deadline-4,time.monotonic()+12)
        while time.monotonic()<until:
            live=self.entity(edge['target'])
            if not live.get('found'):return 'lift_missing'
            current=edge['lower_z']+live['origin'][2]-edge['origin'][2]
            if abs(current-floor)<=3:return None
            self.pause(.15)
        return 'lift_call_timeout'


def route_test(project, map, route='auto', timeout=90, start='spawn'):
    story._number(timeout, 'timeout', 5, 90)
    if start not in ('spawn', 'current'):
        start = story._vector(start, 'start')
    project.s.map_path(map)
    route = validate(route)
    m = project.load(map)
    specs = story.map_stories(project, m) if route == 'auto' else []
    if route == 'auto' and len(specs) != 1:
        raise ValueError('auto requires exactly one add_story')
    # Validate all literal references before touching the live game.
    if isinstance(route, list):
        validate_references(m, route)
    else:
        for stage in specs[0][1]['stages']:
            if stage.get('route'):
                validate_references(m, validate(stage['route']))
    _probe_source(project)
    queued = time.monotonic()
    began = queued
    deadline = began + timeout
    lines, completed, shots = scripting._ConsoleTrace(), [], []
    stage_started, stage_seconds = {}, {}
    reason, failed, driver, conn = None, None, None, None
    logpath = project.s.logs / f'route-test-{map}-{time.time_ns()}.json'
    try:
        with game.exclusive(wait=60), focus_game(project.s):
            began=time.monotonic()
            deadline=began+timeout
            if game.session(project.s) is None:
                raise RuntimeError('no Workshop Tools session for this addon')
            conn = vconsole._connection(vconsole.PORT)
            driver = Driver(project, deadline, lines)
            driver.timeout, driver.map = timeout, map
            # First connection can receive a large historic console backlog.
            begun=game.query(project.s,'echo R_ROUTE_BEGIN','R_ROUTE_BEGIN',timeout=min(8,driver.remaining()),retries=1)
            if not any('R_ROUTE_BEGIN' in s for s in begun):
                conn.close()
                conn=vconsole._connection(vconsole.PORT)
                begun=game.query(project.s,'echo R_ROUTE_BEGIN','R_ROUTE_BEGIN',timeout=min(8,driver.remaining()),retries=1)
                if not any('R_ROUTE_BEGIN' in s for s in begun):
                    raise RuntimeError('CS2 console unavailable before physical test')
            with conn.cv:
                conn.listeners.append(lines)
            try:
                spawn = next(iter(m.entities(classname='info_player_terrorist')), None)
                ang = list(spawn['angles']) if spawn is not None else [0,0,0]
                # No pos field: screenshot never teleports the player here. It also loads the map, so it
                # comes before a coordinate start: then the frame shows the spawn, hence its name.
                first = 'route_start_' if start == 'spawn' else 'route_spawn_'
                start_frame=project.game_screenshot(map=map, views=[{'name':first+map, 'ang':ang}], hide_hud=False, deadline=deadline)
                shots.extend(start_frame['shots'])
                driver.command(RELEASE+'\nnoclip 0\nslot3')
                if start == 'spawn':
                    driver.command('mp_restartgame 1')
                    driver.pause(2.8)
                    game.wait_for_player(project.s, timeout=min(8,driver.remaining()))
                    driver.command('noclip 0\nslot3')
                # A round restart (ours, a bot joining, a kick) starts a freeze period (~6 s even with
                # mp_freezetime 0, longer in competitive): inputs are ignored until it ends.
                unfreeze=time.monotonic()+min(25,driver.remaining()-5)
                while game.player_state(project.s).get('freeze') and time.monotonic()<unfreeze:
                    driver.pause(.4)
                if isinstance(start, list):
                    # Setup after freeze, before movement and timing. Noclip
                    # stays disabled and no step teleports the player.
                    driver.command('setpos %g %g %g\nnoclip 0\nslot3' % tuple(start))
                    driver.pause(.25)
                driver.install()
                initial = copy.deepcopy(driver.state)
                if not initial.get('alive') or initial.get('noclip'):
                    raise RuntimeError('physical route needs a live player with noclip off')
                sequence = through_openings(m, route, initial['pos']) if isinstance(route, list) else route
                if route == 'auto':
                    spec = specs[0][1]
                    driver.command(f'ent_fire {spec["name"]}_script RunScriptInput TestReset')
                    sequence = []
                    # Without an explicit stage.route, walk through the layout openings (add_layout plans).
                    cursor = list(initial['pos'])
                    for stage in spec['stages']:
                        if stage.get('route'):
                            segment = through_openings(m, validate(stage['route']), cursor)
                        elif 'zone' in stage['until']:
                            goal = stage_target(m,stage)
                            segment = plan_between(m, cursor, goal)
                        elif any(k in stage['until'] for k in ('button','use')):
                            kind = 'button' if 'button' in stage['until'] else 'use'
                            pos = stage_target(m,stage)
                            foot = approach_point(m,cursor,pos)
                            segment = plan_between(m,cursor,foot)
                            segment += [{'use':stage['until'][kind]}]
                        elif 'time' in stage['until']:
                            segment = [{'wait':stage['until']['time']}]
                        else:
                            raise ValueError(f'auto cannot physically satisfy stage {stage["id"]}; add stage.route')
                        sequence.extend([{**s,'stage':stage['id']} for s in segment])
                        last = next((s for s in reversed(segment) if 'go' in s or 'target' in s), None)
                        if last:
                            cursor = list(last.get('go') or last['target'])
                movement_started=time.monotonic()
                for i, step in enumerate(sequence):
                    step_started=time.monotonic()
                    failed = {'point':i,'step':step}
                    if step.get('stage') and step['stage'] not in stage_started:
                        stage_started[step['stage']] = time.monotonic() - began
                    if '_lift_ready' in step:
                        reason=driver.lift_ready(step)
                    elif 'approach' in step:
                        state=driver.probe(); target=step['target']; d=math.dist(state['pos'][:2],target[:2]) or 1
                        goal=[target[j]+(state['pos'][j]-target[j])/d*48 for j in range(2)]+[state['pos'][2]]
                        reason=driver.go({'go':goal,'radius':10,
                                          'allow_damage':step.get('allow_damage', False)})
                    elif 'go' in step:
                        reason=driver.go(step)
                    elif 'climb_down' in step:
                        reason=driver.climb_down(step)
                    elif 'use' in step:
                        reason=driver.use(step['use'])
                    elif 'break' in step:
                        reason=driver.break_target(step)
                    elif 'push' in step:
                        reason=driver.push(step)
                    elif 'ride' in step:
                        reason=driver.ride(step)
                    else:
                        health=driver.probe().get('health',100)
                        driver.pause(step['wait']); reason=failure(driver.probe(),None,health,
                                                                   allow_damage=step.get('allow_damage', False))
                    if reason:
                        break
                    now = time.monotonic() - began
                    stage = step.get('stage')
                    completed.append({'point':i,'stage':stage,'seconds':round(now, 2),
                                      'duration':round(time.monotonic()-step_started,2),
                                      'pos':driver.probe().get('pos')})
                    if route == 'auto' and (i+1==len(sequence) or sequence[i+1].get('stage')!=step.get('stage')):
                        pattern=r'S_STORY '+re.escape(spec['name'])+r' stage '+re.escape(step['stage'])+r' done\b'
                        end=min(deadline-4,time.monotonic()+3)
                        while not any(re.search(pattern,s) for s in lines) and time.monotonic()<end:
                            driver.pause(.1)
                        if not any(re.search(pattern,s) for s in lines):
                            reason='stage_condition_not_satisfied'; break
                    if stage and (i+1==len(sequence) or sequence[i+1].get('stage')!=stage):
                        stage_seconds[stage] = round(time.monotonic()-began-stage_started[stage],2)
                driver.command(RELEASE)
                driver.pause(.2)
                driver.probe()
                movement_seconds=round(time.monotonic()-movement_started,2)
                if time.monotonic() < deadline-2:
                    # Capture only after motion has stopped and the final physical state was saved.
                    snapshot=copy.deepcopy(driver.state)
                    view={'name':'route_'+('stuck_' if reason else 'done_')+map,
                          'ang':[15,snapshot.get('pos_angles',[0,0,0])[1]+180,0]}
                    captured=project.game_screenshot(map=map, views=[view],hide_hud=False,deadline=deadline)
                    shots.extend(captured['shots'])
                    driver.state=snapshot
            finally:
                # Release every held input even on timeout/error, then restore physical mode.
                vconsole.run_until(RELEASE.splitlines()+['noclip 0','echo R_ROUTE_RELEASED'],'R_ROUTE_RELEASED',timeout=2)
                if driver.state and time.monotonic()<deadline-1:
                    snapshot=copy.deepcopy(driver.state)
                    cleanup=copy.deepcopy(driver.probe())
                    driver.state=snapshot
                    if cleanup.get('noclip') or cleanup.get('forward') or cleanup.get('back') or cleanup.get('use'):
                        reason=reason or 'input_cleanup_failed'
                with conn.cv:
                    conn.listeners[:]=[s for s in conn.listeners if s is not lines]
    except Exception as exc:
        reason=reason or str(exc)
    errors=scripting._script_errors(lines)
    result={'ok':reason is None and not errors,'map':map,'mode':'physical','start':start,
            'reached':len(completed),'point_count':len(sequence) if 'sequence' in locals() else 0,
            'seconds':round(time.monotonic()-began,2),'reason':reason,'stuck':failed if reason else None,
            'queue_seconds':round(began-queued,2), 'stage_seconds':stage_seconds,
            'movement_seconds':locals().get('movement_seconds'),
            'position':driver.state.get('pos') if driver and driver.state else None,
            'shots':[{'name':s['name'],'path':s['path']} for s in shots], 'log':str(logpath)}
    details={**result,'initial':locals().get('initial'),'cleanup':locals().get('cleanup'),'completed':completed,'samples':driver.samples if driver else [],
             'commands':driver.commands if driver else [],'interactions':driver.interactions if driver else [],
             'console':lines,'errors':errors}
    logpath.parent.mkdir(parents=True,exist_ok=True)
    logpath.write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
    if errors:
        result['errors']=[message[:180] for message in errors[:2]]
        result['error_count']=len(errors)
    while len(json.dumps(result,ensure_ascii=False).encode())>2048:
        if result['shots']:result['shots'].pop()
        elif isinstance(result.get('stuck'),dict) and 'step' in result['stuck']:result['stuck'].pop('step')
        elif result['stage_seconds']:
            result['stage_seconds'].pop(next(reversed(result['stage_seconds'])))
            result['timings_in_log']=True
        elif result.get('errors'):
            result['errors'].pop()
            result['errors_in_log']=True
        else:result['reason']=str(result.get('reason'))[:180];break
    return result


