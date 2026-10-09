"""Static quality checks for a VMAP before it is loaded in CS2.

The checks intentionally report actionable findings instead of raising on the
first problem.  They are geometry heuristics (the compiler/game remain the
authority for final collision and lighting), but catch the common authoring
mistakes that otherwise waste a build.
"""
from __future__ import annotations

import math
import fnmatch
import json
from pathlib import Path
from typing import Any

from .dmx import Element
from .vmap import VMap


def hidden_helper(props: dict) -> bool:
    """A parked mechanics helper (disguise prop, glow marker): never drawn and never solid until a script uses it."""
    return str(props.get('solid', '')).strip() == '0' and str(props.get('renderamt', '')).strip() == '0'


def _props(node: Element) -> dict[str, Any]:
    p = node.get('entity_properties')
    return {k: a.value for k, a in p.attrs.items()} if isinstance(p, Element) else {}


def _vec(value: Any, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if isinstance(value, str):
        try:
            parts = [float(x) for x in value.replace(',', ' ').split()]
            return tuple((parts + list(default))[:3])  # type: ignore[return-value]
        except ValueError:
            return default
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return tuple(float(x) for x in value[:3])
    return default


def _mesh_bounds(node: Element) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    if node.type != 'CMapMesh':
        return None
    md = node.get('meshData')
    if not isinstance(md, Element):
        return None
    streams = md.get('vertexData', {}).get('streams', []) if isinstance(md.get('vertexData'), Element) else []
    if not streams or not streams[0].get('data'):
        return None
    pos = _vertices(node)
    lo = tuple(min(p[i] for p in pos) for i in range(3))
    hi = tuple(max(p[i] for p in pos) for i in range(3))
    return lo, hi


def _all_meshes(m: VMap):
    for node in m.iter_nodes():
        if node.type == 'CMapMesh':
            bounds = _mesh_bounds(node)
            if bounds:
                yield node, bounds


def _is_solid_mesh(node: Element) -> bool:
    if node.get('physicsType') == 'none':      # render-only: light shafts, trim, pipes, cables
        return False
    mats = node.get('meshData', {}).get('materials', [])
    # Skybox, trigger and lightmap helper volumes are not player-blocking
    # geometry for spawn placement heuristics.
    helpers = ('toolsskybox','toolstrigger','toolslightmapres','postprocess_volume')
    return not mats or not all(any(h in str(mat) for h in helpers) for mat in mats)


def _transform(node, points):
    """Apply a node's own scales, angles (roll, pitch, yaw) and origin: its space -> its parent's."""
    origin = _vec(node.get('origin'))
    scales = _vec(node.get('scales'), (1, 1, 1))
    pitch, yaw, roll = (math.radians(x) for x in _vec(node.get('angles')))
    cp,sp,cy,sy,cr,sr=math.cos(pitch),math.sin(pitch),math.cos(yaw),math.sin(yaw),math.cos(roll),math.sin(roll)
    result=[]
    for p in points:
        x,y,z=(float(p[i])*scales[i] for i in range(3))
        y,z=cr*y-sr*z,sr*y+cr*z
        x,z=cp*x+sp*z,-sp*x+cp*z
        x,y=cy*x-sy*y,sy*x+cy*y
        result.append((x+origin[0],y+origin[1],z+origin[2]))
    return result


def _vertices(node):
    md = node['meshData']
    streams = md['vertexData']['streams']
    data = next((st['data'] for st in streams if st.get('semanticName') == 'position'), streams[0]['data'])
    return _transform(node, data)


def _triangles(node):
    """Decode Hammer half-edges; triangulate convex authored polygons."""
    md=node['meshData']; vertices=_vertices(node)
    dest,nxt=md['edgeVertexIndices'],md['edgeNextIndices']
    for start in md['faceEdgeIndices']:
        points=[];edge=start;seen=set()
        while edge not in seen and 0 <= edge < len(dest):
            seen.add(edge);points.append(vertices[dest[edge]]);edge=nxt[edge]
        for i in range(1,len(points)-1):
            yield points[0],points[i],points[i+1]


def _sub(a,b): return tuple(a[i]-b[i] for i in range(3))
def _dot(a,b): return sum(a[i]*b[i] for i in range(3))
def _cross(a,b): return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def _ray(point,direction,tri):
    a,b,c=tri;e1,e2=_sub(b,a),_sub(c,a)
    h=_cross(direction,e2);det=_dot(e1,h)
    if abs(det)<1e-9:return None
    s=_sub(point,a);u=_dot(s,h)/det
    if u < -1e-7 or u > 1+1e-7:return None
    q=_cross(s,e1);v=_dot(direction,q)/det
    if v < -1e-7 or u+v > 1+1e-7:return None
    t=_dot(e2,q)/det
    return t if t > 1e-5 else None


def _inside_mesh(point,triangles):
    # Oblique ray avoids axis-aligned face/edge degeneracies; dedupe coplanar
    # triangles hit at the same distance (one quad must count as one crossing).
    hits={round(t,5) for tri in triangles if (t:=_ray(point,(1,.173,.097),tri)) is not None}
    return len(hits)%2 == 1


def _floor(point,triangles,max_drop=4096):
    hits=[];x,y,z=point[0],point[1],point[2];bottom=z-max_drop-1e-3
    for tri in triangles:
        a,b,c=tri
        # A vertical ray only meets triangles whose box holds it (1e-3: the ray's barycentric tolerance).
        if (x<min(a[0],b[0],c[0])-1e-3 or x>max(a[0],b[0],c[0])+1e-3 or y<min(a[1],b[1],c[1])-1e-3
                or y>max(a[1],b[1],c[1])+1e-3 or min(a[2],b[2],c[2])>z or max(a[2],b[2],c[2])<bottom):continue
        normal=_cross(_sub(tri[1],tri[0]),_sub(tri[2],tri[0]))
        if normal[2] <= 1e-6:continue
        t=_ray(point,(0,0,-1),tri)
        if t is not None and t<=max_drop:hits.append(point[2]-t)
    return max(hits,default=None)


def _asset_exists(project,rel):
    rel=rel.replace('\\','/')
    alternatives=[rel,rel+'_c']
    if rel.endswith('.vjs'):alternatives.append(rel[:-4]+'.js')
    return any((project.s.content_addon/p).is_file() or (project.s.game_addon/p).is_file()
               or project.game.exists(p) for p in alternatives)


def _model_bounds(project, model: str, origin, angles, scales):
    """Return a world-space AABB for an owned ``models/mcp`` model.json."""
    rel = str(model).replace('\\', '/')
    if not rel.casefold().startswith('models/mcp/') or not rel.casefold().endswith('.vmdl'):
        return None
    path = project.s.content_addon / Path(rel).with_name('model.json')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        bounds = data.get('bounds')
        if not (isinstance(bounds, list) and len(bounds) == 2):
            return None
        lo, hi = bounds
        corners = [(float(x), float(y), float(z)) for x in (lo[0], hi[0])
                   for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        scale = _vec(scales, (1.0, 1.0, 1.0))
        pitch, yaw, roll = (math.radians(x) for x in _vec(angles))
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        cr, sr = math.cos(roll), math.sin(roll)
        world = []
        base = _vec(origin)
        for x, y, z in corners:
            x, y, z = x * scale[0], y * scale[1], z * scale[2]
            y, z = cr * y - sr * z, sr * y + cr * z
            x, z = cp * x + sp * z, -sp * x + cp * z
            x, y = cy * x - sy * y, sy * x + cy * y
            world.append((x + base[0], y + base[1], z + base[2]))
        return (tuple(min(p[i] for p in world) for i in range(3)),
                tuple(max(p[i] for p in world) for i in range(3)))
    except (OSError, ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _inside(point, bounds, margin=0.0) -> bool:
    lo, hi = bounds
    return all(lo[i] - margin <= point[i] <= hi[i] + margin for i in range(3))


def _covers_xy(point, bounds, margin=0.0) -> bool:
    lo, hi = bounds
    return lo[0] - margin <= point[0] <= hi[0] + margin and lo[1] - margin <= point[1] <= hi[1] + margin


def _problem(severity: str, code: str, node_id: int | None, message: str, fix: str) -> dict:
    return {'severity': severity, 'code': code, 'id': node_id, 'message': message, 'fix': fix}


problem = _problem

# Checks added by area modules (ext/*.py): name -> fn(project, vmap) -> list of problem() dicts.
_EXTRA_CHECKS: dict[str, Any] = {}


def register_check(name: str, fn) -> None:
    """Add an area check to check_map; registering the same name again replaces it."""
    _EXTRA_CHECKS[name] = fn


# Keys supplied by the common entity base classes may be absent from a
# particular FGD declaration even though Hammer writes them into every node.
_COMMON_KEYS = {
    'targetname', 'vscripts', 'parentname', 'parentAttachmentName', 'local.origin', 'local.angles',
    'local.scales', 'useLocalOffset', 'enabled', 'StartDisabled', 'globalname', 'spawnflags', 'filtername',
    'renderamt', 'rendercolor', 'renderfx', 'rendermode', 'disablereceiveshadows', 'fademindist', 'fademaxdist',
    'lightgroup', 'lightmapstatic', 'object_culling', 'mincpulevel', 'maxcpulevel', 'mingpulevel', 'maxgpulevel',
    'disableX360', 'priority', 'range', 'nearclipplane', 'renderdiffuse', 'renderspecular', 'rendertransmissive',
    'indirectlight', 'cascadecrossfade', 'cascadedistancefade', 'sunlightminbrightness', 'skydirectlight',
    'ambient_occlusion', 'max_occlusion_distance', 'fully_occluded_fraction', 'occlusion_exponent', 'fog_type',
    'angular_fog_max_end', 'angular_fog_max_start', 'angular_fog_min_start', 'angular_fog_min_end',
}


def _map_bounds(meshes) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    items = [b for _, b in meshes]
    if not items:
        return None
    return tuple(min(b[0][i] for b in items) for i in range(3)), tuple(max(b[1][i] for b in items) for i in range(3))


def _is_story_map(project, m) -> bool:
    """A map with an add_story script is single-player: competitive checks do not apply."""
    try:
        from .story import map_stories
        return bool(map_stories(project, m))
    except Exception:
        return False


def check_map(project, name: str) -> dict:
    """Return ``{ok, problems}`` for map *name* without modifying it."""
    m = project.load(name)
    problems: list[dict] = []
    meshes = list(_all_meshes(m))
    solids=[(n,b) for n,b in meshes if _is_solid_mesh(n)]
    playable = _map_bounds(solids)
    tris_by_mesh=[(n,list(_triangles(n))) for n,_ in solids]
    triangles=[t for _,ts in tris_by_mesh for t in ts]
    entities = list(m.entities())
    story = _is_story_map(project, m)

    # Entity schema, asset and I/O checks.
    targetnames = {(_props(e).get('targetname') or '') for e in entities}
    targetnames.discard('')
    for ent in entities:
        props = _props(ent)
        cls = props.get('classname', '')
        try:
            known = project.catalog.get(cls)
            allowed = (set(project.catalog.props(cls)) | project.catalog.helper_keys(cls)
                       | _COMMON_KEYS | {'classname'})
        except Exception:
            problems.append(_problem('error', 'unknown_class', ent.get('nodeID'),
                                    f'Unknown entity class {cls!r}', 'Use entity_info to choose a class from the installed FGD.'))
            allowed = {'classname'}
        for key in props:
            # game_player_equip takes item names as keys (weapon_ak47 "1"); the FGD cannot list them.
            if cls == 'game_player_equip' and key.startswith(('weapon_', 'item_')):
                continue
            if key not in allowed:
                problems.append(_problem('warning', 'unknown_key', ent.get('nodeID'),
                                        f'{cls} has key {key!r}, which is not in the installed FGD',
                                        'Remove the key or verify its spelling in entity_info.'))
        for ref in ent.get('connectionsData', []):
            target = ref.get('targetName', '')
            if target and not target.startswith('!') and not any(fnmatch.fnmatchcase(t,target) for t in targetnames):
                problems.append(_problem('error', 'io_target_missing', ent.get('nodeID'),
                                        f'I/O target {target!r} does not exist',
                                        'Add the target entity or update the connection targetname.'))
        for key, value in props.items():
            if isinstance(value, str) and any(value.lower().endswith(s) for s in ('.vmat', '.vmdl', '.vpcf', '.vpost', '.vjs')):
                rel = value.replace('\\', '/')
                source = project.s.content_addon / rel
                compiled = rel + '_c'
                if not _asset_exists(project, rel):
                    problems.append(_problem('error', 'missing_asset', ent.get('nodeID'),
                                            f'Asset {rel!r} referenced by {cls}.{key} is missing',
                                            'Use asset_search or create/compile the referenced asset.'))
    for ref in m.asset_references():
        rel = ref.replace('\\', '/')
        if not _asset_exists(project,rel) and not any(p['code']=='missing_asset' and repr(rel) in p['message'] for p in problems):
            problems.append(_problem('error', 'missing_asset', None, f'Material/asset {rel!r} is missing',
                                    'Use asset_search or material_create and rebuild the map.'))

    # Spawns and player hull sanity.
    spawns = []
    for team, cls in ((2, 'info_player_terrorist'), (3, 'info_player_counterterrorist')):
        team_spawns = [(e, _vec(e.get('origin'))) for e in entities if _props(e).get('classname') == cls]
        spawns += [(team, e, p) for e, p in team_spawns]
        if not team_spawns:
            if not (story and team == 3):    # story players join T
                problems.append(_problem('error', 'spawn_missing', None, f'No {cls} spawn exists',
                                        f'Add at least one {cls} on a solid floor.'))
        elif len(team_spawns) < 5 and not story:
            problems.append(_problem('warning', 'spawn_count', team_spawns[0][0].get('nodeID'),
                                    f'{cls} has only {len(team_spawns)} spawn(s)', 'Use five or more spawns per team for a playtest.'))
        for ent, point in team_spawns:
            # Start just above the feet: a spawn exactly on the floor plane must find that floor.
            floor_z = _floor((point[0],point[1],point[2]+2),triangles,130)
            if floor_z is None or point[2] - floor_z > 20:
                problems.append(_problem('error', 'spawn_not_on_floor', ent.get('nodeID'),
                                        f'{cls} at {tuple(round(x, 1) for x in point)} is not on a nearby floor',
                                        'Place the spawn 8 units above the floor; its origin is the player feet.'))
            elif point[2] - floor_z < 2:
                # Verified in CS2: a spawn on the floor plane is rejected ("all teams are full", no player).
                problems.append(_problem('error', 'spawn_in_floor', ent.get('nodeID'),
                                        f'{cls} at {tuple(round(x, 1) for x in point)} touches the floor plane '
                                        f'(z {floor_z:g}); CS2 rejects it and nobody can join',
                                        f'Set its z to {floor_z+8:g} (8 above the floor).'))
            # A spawn origin is normally 8-16 units above a floor.  Treat a
            # point near the top face as valid; only flag a solid that rises
            # well above the player's feet and encloses the XY position.
            def enclosed(bounds):
                lo, hi = bounds
                return (lo[0] + 1 < point[0] < hi[0] - 1 and lo[1] + 1 < point[1] < hi[1] - 1
                        and lo[2] + 1 < point[2] < hi[2] - 16)
            if any(_inside_mesh(point,ts) for _,ts in tris_by_mesh):
                problems.append(_problem('error', 'spawn_inside_geometry', ent.get('nodeID'),
                                        f'{cls} is inside solid geometry', 'Move the spawn into open space.'))
            blocked=False
            for dx in (-15,0,15):
                for dy in (-15,0,15):
                    foot=(point[0]+dx,point[1]+dy,point[2]+.1)
                    if any(_inside_mesh(foot,ts) for _,ts in tris_by_mesh) or any(
                        (t:=_ray(foot,(0,0,1),tri)) is not None and t < 72 for tri in triangles):
                        blocked=True
            if blocked:
                problems.append(_problem('error','spawn_clearance',ent.get('nodeID'),
                    f'{cls} lacks a clear 32 × 32 × 72 player hull','Clear geometry above and around the spawn.'))
            if playable and not (playable[0][0] <= point[0] <= playable[1][0] and playable[0][1] <= point[1] <= playable[1][1]):
                problems.append(_problem('warning', 'spawn_outside_playable', ent.get('nodeID'),
                                        f'{cls} lies outside the map geometry bounds', 'Move the spawn onto the playable area.'))
        for i, (_, _, a) in enumerate([(team, e, p) for e, p in team_spawns]):
            for _, _, b in [(team, e, p) for e, p in team_spawns][i + 1:]:
                if math.dist(a, b) < 48:
                    problems.append(_problem('error', 'spawn_too_close', team_spawns[i][0].get('nodeID'),
                                            'Two same-team spawns are closer than 48 units', 'Separate player origins by at least 48 units.'))

    # Owned environment props are easy to place on top of a spawn when an AI
    # composes many map_edit operations.  model.json is the source of bounds
    # for models built by this project; stock models are left to
    # the compiler/game because their collision bounds are not locally known.
    for ent in entities:
        classname = _props(ent).get('classname')
        if classname not in ('prop_static', 'prop_dynamic') or hidden_helper(_props(ent)):
            continue
        prop_bounds = _model_bounds(project, _props(ent).get('model', ''),
                                    ent.get('origin'), ent.get('angles'), ent.get('scales'))
        if not prop_bounds:
            continue
        plo, phi = prop_bounds
        for team, spawn, point in spawns:
            hull_lo = (point[0] - 16, point[1] - 16, point[2])
            hull_hi = (point[0] + 16, point[1] + 16, point[2] + 72)
            overlap = all(plo[i] <= hull_hi[i] and phi[i] >= hull_lo[i] for i in range(3))
            if overlap:
                model = str(_props(ent).get('model'))
                problems.append(_problem('warning', 'prop_over_spawn', ent.get('nodeID'),
                    f'{model} overlaps {"T" if team == 2 else "CT"} spawn hull at {tuple(round(x, 1) for x in point)}',
                    'Move the prop away from the spawn or use a model with a clear player hull.'))

    # Gameplay volumes and navigation seeds.
    classes = {_props(e).get('classname') for e in entities}
    if not any(c == 'func_buyzone' for c in classes) and not story:
        problems.append(_problem('error', 'buyzone_missing', None, 'No buyzone exists', 'Add a func_buyzone over each spawn group.'))
    hostage = bool(classes & {'hostage_entity', 'info_hostage_spawn', 'func_hostage_rescue'})
    if not any(c == 'func_bomb_target' for c in classes) and not story and not hostage:
        problems.append(_problem('warning', 'bombsite_missing', None, 'No bombsite exists', 'Add at least one func_bomb_target for a defuse map.'))
    nav = [e for e in entities if _props(e).get('classname') == 'point_nav_walkable']
    if not nav:
        if story:
            problems.append(_problem('warning', 'nav_seed_missing', None, 'No point_nav_walkable: bots cannot move',
                                    'Only needed if the story uses bots (kills stages); add a seed and build full.'))
        else:
            problems.append(_problem('error', 'nav_seed_missing', None, 'No point_nav_walkable navigation seed exists',
                                    'Place seeds on walkable floor near every disconnected play area and rebuild full.'))
    for team, ent, point in ([] if story else spawns):
        if not any(_inside(point, _mesh_bounds(ch)) for e in entities if _props(e).get('classname') == 'func_buyzone'
                   and str(_props(e).get('TeamNum','0')) in ('0',str(team))
                   for ch in m.iter_nodes(e) if ch.type == 'CMapMesh' and _mesh_bounds(ch)):
            problems.append(_problem('warning', 'buyzone_not_over_spawn', ent.get('nodeID'),
                                    f'{_props(ent).get("classname")} is not covered by a buyzone',
                                    'Extend the team buyzone over the spawn origins.'))

    # Lighting, sky, probes and lightmap resolution.
    if not any(c == 'light_environment' for c in classes):
        problems.append(_problem('error', 'sun_missing', None, 'No light_environment exists', 'Add a sun and set its skytexture.'))
    if not any(c == 'env_sky' for c in classes):
        problems.append(_problem('error', 'sky_missing', None, 'No env_sky exists', 'Add env_sky with a material from asset_search.'))
    if playable:
        for ent in entities:
            if _props(ent).get('classname') == 'light_environment' and not _props(ent).get('skytexture'):
                problems.append(_problem('warning', 'sun_sky_unlinked', ent.get('nodeID'),
                                        'light_environment has no skytexture link', 'Set skytexture to the env_sky targetname.'))
        probes = [e for e in entities if _props(e).get('classname') in ('env_combined_light_probe_volume', 'env_light_probe_volume')]
        if not probes:
            problems.append(_problem('warning', 'probe_missing', None, 'No light probe volume covers the play area',
                                    'Add env_combined_light_probe_volume covering the geometry.'))
        else:
            # Coverage is needed where people play: outdoor backdrops, clip and tool volumes are left out.
            from . import visual
            lo, hi = visual.playable_bounds(m) or playable
            covered = False
            for ent in probes:
                p = _vec(ent.get('origin'))
                b0 = _vec(_props(ent).get('box_mins'), (-1, -1, -1))
                b1 = _vec(_props(ent).get('box_maxs'), (1, 1, 1))
                if all(p[i] + b0[i] <= lo[i] and p[i] + b1[i] >= hi[i] for i in range(3)):
                    covered = True
            if not covered:
                problems.append(_problem('warning', 'probe_outside', probes[0].get('nodeID'),
                                        'Light probe volumes do not cover the complete playable bounds',
                                        'Expand box_mins/box_maxs to cover all playable geometry.'))
        if not any('toolslightmapres.vmat' in mat
                   for node, _ in meshes
                   for mat in node.get('meshData', {}).get('materials', [])):
            problems.append(_problem('warning', 'lightmap_volume_missing', None, 'No toolslightmapres volume was found',
                                    'Add a toolslightmapres box over the playable area before a full build.'))

    # Route sampling is a heuristic, not pathfinding. Sample only short direct
    # segments between gameplay anchors, and report uncertainty as warnings.
    anchors=[(e,_vec(e.get('origin'))) for e in entities if _props(e).get('classname') in
             ('point_nav_walkable','info_player_terrorist','info_player_counterterrorist')]
    # A ramp is a surface a player could try to walk: bevels, rounded seats and small rocks are not (their steep
    # faces add up to less than a 32x32 footprint).
    for node,ts in tris_by_mesh:
        steep=0.0
        for tri in ts:
            normal=_cross(_sub(tri[1],tri[0]),_sub(tri[2],tri[0]));length=math.sqrt(_dot(normal,normal))
            if length and .05 < normal[2]/length < math.cos(math.radians(45)):
                if any(abs(p[2]-sum(v[2] for v in tri)/3)<128 for _,p in anchors):
                    steep+=normal[2]/2
        if steep>=1024:
            problems.append(_problem('warning','steep_ramp',node.get('nodeID'),
                'Upward face exceeds 45 degrees near the gameplay height','Flatten the ramp or verify intentional clipping in CS2.'))
    route_codes=set()
    from . import route as _route
    layout_rooms=_route._layout_rooms(m)[0]
    def in_rooms(p):        # a straight segment through a layout wall is not a direct route
        return not layout_rooms or any(r['interior']['mins'][0]-24<=p[0]<=r['interior']['maxs'][0]+24 and
                                       r['interior']['mins'][1]-24<=p[1]<=r['interior']['maxs'][1]+24
                                       for r in layout_rooms.values())
    for i,(ent,a) in enumerate(anchors):
        nearest=sorted(anchors[:i]+anchors[i+1:],key=lambda item:math.dist(a,item[1]))[:2]
        for _,b in nearest:
            distance=math.dist(a,b)
            if distance>2048:continue
            count=max(2,math.ceil(distance/24));previous=None;run=distance/count
            if not all(in_rooms(tuple(a[k]+(b[k]-a[k])*j/count for k in range(2))) for j in range(count+1)):continue
            for j in range(count+1):
                p=tuple(a[k]+(b[k]-a[k])*j/count for k in range(3))
                z=_floor((p[0],p[1],max(a[2],b[2])+32),triangles,256)
                # Stairs rise at most 18 per 16 of run; a sample spacing of ~24 may skip a tread.
                rise=abs(z-previous) if z is not None and previous is not None else 0
                code='floor_gap' if z is None else ('high_step' if 18<rise<128 and rise>run*18/16 else None)
                if code and code not in route_codes:
                    route_codes.add(code);problems.append(_problem('warning',code,ent.get('nodeID'),
                        f'Direct gameplay-anchor segment has {"missing floor" if code=="floor_gap" else "a rise/drop over 18 units"} near {tuple(round(x,1) for x in p)}',
                        'Inspect the route in CS2; bridge the gap, add rails, stairs or a walkable ramp.'))
                previous=z
    for check_name, fn in _EXTRA_CHECKS.items():
        try:
            problems += fn(project, m) or []
        except Exception as e:
            problems.append(_problem('warning', 'check_failed', None, f'check {check_name!r} failed: {e}',
                                     'The map may still be fine; report this to the check author.'))
    extra = {'mode': 'story'} if story else {}
    mode = _authored_mode(m)
    if mode:
        extra['mode'] = mode
        rules = DEFUSE_ONLY | (MOVEMENT_ONLY if mode in MOVEMENT_MODES else set())
        if mode in NON_DEFUSE_MODES:
            skipped = sorted({p['code'] for p in problems if p['code'] in rules})
            problems = [p for p in problems if p['code'] not in rules]
            if skipped:
                extra['inapplicable_core_checks'] = skipped
    from . import maptypes
    kind, how = maptypes.effective(m, story, mode)
    problems, skipped = maptypes.apply(problems, kind)
    if skipped:
        extra['inapplicable_core_checks'] = sorted(set(extra.get('inapplicable_core_checks', [])) | set(skipped))
    extra.update({'type': kind, **({'type_source': how} if how != 'set' else {}),
                  'tests_at_the_end': maptypes.TYPES[kind]['tests']})
    ok = not any(p['severity'] == 'error' for p in problems)
    return {'map': name, 'ok': ok, **extra, 'problems': problems}


NON_DEFUSE_MODES = {'deathmatch', 'armsrace', 'surf', 'bhop', 'kz', 'aim'}
DEFUSE_ONLY = {'buyzone_missing', 'buyzone_not_over_spawn', 'bombsite_missing'}
MOVEMENT_MODES = {'surf', 'bhop', 'kz'}
# Steep surf ramps, floating jump platforms and gaps between them are the course itself.
MOVEMENT_ONLY = {'steep_ramp', 'visual_floating_platform', 'floor_gap', 'high_step', 'spawn_count'}


def _authored_mode(m) -> str | None:
    """The game mode written by map_edit add_mode (one per map), if any."""
    found = []
    for node in m.iter_nodes():
        raw = node.get('cs2map_mode')
        if raw:
            try:
                found.append(json.loads(raw).get('mode'))
            except (ValueError, AttributeError):
                continue
    return found[0] if len(found) == 1 else None

