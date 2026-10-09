"""Short, bounded CS2 bot playtests and deterministic VMAP camera recipes."""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path

from . import checks, game


# CS2's own diagnostics for stock agent animation graphs: printed on any map, not a map problem.
ENGINE_NOISE = (re.compile(r'^Graph Warning: animation/graphs/'),
                re.compile(r'Failed to set animgraph param e_(?:face|body)_idle of type unknown'))


def _split_engine(errors: list[str]) -> tuple[list[str], list[str]]:
    engine = [e for e in errors if any(p.search(e) for p in ENGINE_NOISE)]
    return [e for e in errors if e not in engine], engine


def _errors(lines: list[str]) -> list[str]:
    out=[]
    for ln in lines:
        low=ln.casefold()
        # Tools-mode annotations (e.g. "Annotation: Error loading file : Missing file ''") are editor noise.
        if low.startswith('annotation:'):
            continue
        if ('error' in low or 'failed' in low or 'missing' in low) and 'terrorist' not in low:
            if not any(x in low for x in ('errors: 0','failed: 0','missing: 0','no errors')): out.append(ln)
    return list(dict.fromkeys(out))


def _nav_areas(project, map_name: str) -> int | None:
    """Read the last successful compiler nav count for *map_name*.

    The in-game ``status`` command does not expose nav areas consistently, so
    the build result is the authoritative local fact.  A missing build is
    represented as ``None`` instead of an invented zero.
    """
    best = None
    for path in sorted(project.s.logs.glob('build-*.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            title = str(record.get('title', ''))
            result = record.get('result') or {}
            if (f'build {map_name} ' in title or title.endswith(f' {map_name}')) and result.get('ok'):
                value = result.get('nav_areas')
                if isinstance(value, int):
                    return value
                best = value
        except (OSError, ValueError, TypeError):
            continue
    return best if isinstance(best, int) else None


def _team_counts(players: list[dict], key: str = 'alive') -> dict[str, int]:
    return {str(team): sum(1 for p in players if p.get('team') == team and
                            (key is None or bool(p.get(key)))) for team in (2, 3)}


def _compact_summary(map_name: str, bots: int, started: float, samples: list[dict],
                     setup: list[str], final: list[str], nav_areas: int | None,
                     sample_log: str, reloaded: bool) -> dict:
    """Turn verbose diagnostic samples into the small MCP playtest response."""
    states = [s for s in samples if s.get('players')]
    final_players = states[-1]['players'] if states else []
    alive_timeline = []
    deaths = {"2": 0, "3": 0}
    previous: dict[tuple[bool, int], bool] = {}
    pickup_values: list[int] = []
    for sample in states:
        players = sample.get('players') or []
        alive_timeline.append({'elapsed': sample.get('elapsed'),
                               'alive': _team_counts(players, 'alive')})
        for player in players:
            team = player.get('team')
            if team not in (2, 3):
                continue
            identity = (bool(player.get('bot')), int(player.get('slot', -1)))
            alive = bool(player.get('alive'))
            if previous.get(identity) and not alive:
                deaths[str(team)] += 1
            previous[identity] = alive
        if isinstance(sample.get('weapon_pickups'), int):
            pickup_values.append(sample['weapon_pickups'])
    # Keep several points for an AI to reason about trends without returning
    # every status line.  First, middle and last cover very short runs too.
    if len(alive_timeline) > 3:
        indices = (0, len(alive_timeline) // 2, len(alive_timeline) - 1)
        alive_timeline = [alive_timeline[i] for i in indices]
    all_lines = [*setup, *final, *[line for s in samples for line in s.get('errors', [])]]
    errors = _errors(all_lines)
    for sample in samples:
        if sample.get('diagnostic_error'):
            errors.append(sample['diagnostic_error'])
    errors, engine = _split_engine(list(dict.fromkeys(errors)))
    return {
        'map': map_name,
        'ok': bool(states) and not errors,
        **({'engine_warnings': [e[:240] for e in engine[:4]]} if engine else {}),
        'reloaded': reloaded,
        'bots_requested': bots,
        'duration_seconds': round(time.monotonic() - started, 1),
        'players': _team_counts(final_players, None),
        'teams': _team_counts(final_players, None),
        'bots_by_team': {str(team): sum(1 for p in final_players if p.get('team') == team and p.get('bot'))
                         for team in (2, 3)},
        'alive_by_team': alive_timeline,
        'deaths': deaths,
        'death_count_method': 'observed alive-to-dead transitions; deaths between samples may be missed',
        'weapon_pickups': max(pickup_values, default=0),
        'errors': [e[:240] for e in errors[:6]],
        'error_count': len(errors),
        'nav_areas': nav_areas,
        'samples_path': sample_log,
    }


def playtest_run(project, map_name: str, bots: int = 10, seconds: int = 60, spectate: bool = True) -> dict:
    if bots < 0 or bots > 32:
        raise ValueError('bots must be between 0 and 32')
    if seconds < 1 or seconds > 120:
        raise ValueError('seconds must be between 1 and 120 (the shared game lock is bounded)')
    with game.exclusive('C-playtest'):
        reloaded = game.ensure_map(project.s, map_name)
        setup = game.command(project.s,
                             'sv_cheats 1\nbot_kick\nmp_limitteams 0\nmp_autoteambalance 0\nbot_quota 0\nmp_warmup_end\nmp_freezetime 0\nmp_restartgame 1', wait=2.0)
        if spectate and bots:
            # The local player watches: teams stay bots-only (5 v 5, not 6 v 5). Leave the team before
            # bots join, otherwise CS2 fills the freed slot with one more bot.
            game.command(project.s, 'jointeam 1', wait=0.5)
        game.command(project.s, 'bot_quota 0', wait=.3)
        for i in range(bots):
            game.command(project.s, 'bot_add_t' if i < bots // 2 else 'bot_add_ct', wait=0.25)
        started = time.monotonic()
        samples = []
        while time.monotonic() - started < seconds:
            status = game.command(project.s, 'status', wait=0.6)
            state=None
            try: state=game.player_state(project.s)
            except game.GameError as exc: state={'diagnostic_error':str(exc)}
            samples.append({'elapsed': round(time.monotonic() - started, 1), 'status': status[-40:],
                            'errors': _errors(status), 'players': state.get('players',[]),
                            'diagnostic_error': state.get('diagnostic_error'),
                            'round': state.get('rounds'), 'weapon_pickups': state.get('pickups')})
            time.sleep(min(5.0, max(0.2, seconds - (time.monotonic() - started))))
        final = game.command(project.s, 'status', wait=0.8)
        # Full status/player samples belong in a log file.  The MCP response is
        # intentionally a compact summary so an AI can keep it in context.
        project.s.logs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
        sample_path = project.s.logs / f'playtest-{map_name}-{stamp}.json'
        payload = {'map': map_name, 'setup': setup, 'samples': samples, 'final_status': final}
        sample_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        out = _compact_summary(map_name, bots, started, samples, setup, final,
                               _nav_areas(project, map_name), str(sample_path), reloaded)
        out['human'] = 'spectator' if spectate and bots else 'player'
        return out


def _centre(bounds):
    return [(bounds[0][i] + bounds[1][i]) / 2.0 for i in range(3)]


def _look_at(pos, target):
    dx, dy = target[0] - pos[0], target[1] - pos[1]
    horizontal = max(math.hypot(dx, dy), 1e-6)
    # Source camera pitch is positive when looking down.
    pitch = math.degrees(math.atan2(pos[2] - target[2], horizontal))
    yaw = math.degrees(math.atan2(dy, dx)) % 360.0
    return [round(max(-89.0, min(89.0, pitch)), 2), round(yaw, 2), 0.0]


def _entity_bounds(m, entity):
    meshes = []
    for node, bounds in checks._all_meshes(m):
        parent = m.parent_of(node)
        while parent is not None:
            if parent is entity:
                meshes.append(bounds)
                break
            parent = m.parent_of(parent)
    if not meshes:
        origin = checks._vec(entity.get('origin'))
        return origin, origin
    return (tuple(min(b[0][i] for b in meshes) for i in range(3)),
            tuple(max(b[1][i] for b in meshes) for i in range(3)))


class CameraGeometry:
    """Triangle rays shared by indoor lighting and automatic cameras.

    Geometry comes from VMAP half-edges, with node rotation/scale applied by
    checks._triangles. Helper volumes and meshes under non-solid entities do
    not block cameras. Props have no decoded collision here.
    """
    def __init__(self, m, ids=None):
        self.meshes = []
        allowed = set(ids or [])
        for node, bounds in checks._all_meshes(m):
            if not checks._is_solid_mesh(node):
                continue
            parent = m.parent_of(node)
            excluded = False
            selected = not allowed or node['nodeID'] in allowed
            while parent is not None:
                selected |= parent.get('nodeID') in allowed
                if parent.type == 'CMapEntity' and (m.classname(parent).startswith('trigger_') or
                        m.classname(parent) in {'func_buyzone', 'func_bomb_target', 'post_processing_volume',
                                               'func_precipitation','env_combined_light_probe_volume',
                                               'env_light_probe_volume','env_cubemap_box'}):
                    excluded = True
                parent = m.parent_of(parent)
            if selected and not excluded:
                self.meshes.append((node, bounds))
        self.bounds = checks._map_bounds(self.meshes)
        self.triangles = []
        for node, _ in self.meshes:
            for tri in checks._triangles(node):
                lo = tuple(min(v[i] for v in tri) for i in range(3))
                hi = tuple(max(v[i] for v in tri) for i in range(3))
                normal = checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
                self.triangles.append((tri, lo, hi, normal))

    def hits(self, point, direction, limit=8192, floors=False):
        # Fast rejection, important on larger maps: a hit lies on the ray segment, so the triangle's box must
        # overlap the segment's box (1e-5 along an axis the ray keeps, 1e-3 elsewhere for the ray's tolerance).
        pad = [1e-5 if abs(direction[i]) < 1e-9 else 1e-3 for i in range(3)]
        ends = [(point[i], point[i] + direction[i]*limit) if abs(direction[i]) >= 1e-9 else (point[i], point[i])
                for i in range(3)]
        lx, ly, lz = (min(e) - p for e, p in zip(ends, pad))
        hx, hy, hz = (max(e) + p for e, p in zip(ends, pad))
        for tri, lo, hi, normal in self.triangles:
            if lo[0] > hx or hi[0] < lx or lo[1] > hy or hi[1] < ly or lo[2] > hz or hi[2] < lz:
                continue
            if floors and normal[2] <= 1e-6:
                continue
            t = checks._ray(point, direction, tri)
            if t is not None and t <= limit:
                yield t

    def nearest(self, point, direction, limit=8192, floors=False):
        return min(self.hits(point, direction, limit, floors), default=None)

    def visible(self, a, b, margin=8):
        delta = checks._sub(b, a)
        length = math.sqrt(checks._dot(delta, delta))
        if length < margin:
            return True
        direction = tuple(v/length for v in delta)
        return self.nearest(a, direction, length-margin) is None

    def clear(self, eye, radius=20):
        return all(self.nearest(eye, d, radius) is None for d in
                   [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)])

    def rotated(self,yaw):
        """Read-only geometry expressed in a wall-aligned coordinate frame."""
        if abs(yaw)<1e-6:
            return self
        other=object.__new__(CameraGeometry)
        other.meshes=self.meshes
        other.triangles=[]
        for tri,_,_,_ in self.triangles:
            points=tuple(_rotate_xy(p,-yaw) for p in tri)
            lo=tuple(min(p[i] for p in points) for i in range(3))
            hi=tuple(max(p[i] for p in points) for i in range(3))
            normal=checks._cross(checks._sub(points[1],points[0]),checks._sub(points[2],points[0]))
            other.triangles.append((points,lo,hi,normal))
        other.bounds=(tuple(min(t[1][i] for t in other.triangles) for i in range(3)),
                      tuple(max(t[2][i] for t in other.triangles) for i in range(3))) if other.triangles else None
        return other


def _rotate_xy(point,yaw):
    a=math.radians(yaw);c,s=math.cos(a),math.sin(a)
    return [point[0]*c-point[1]*s,point[0]*s+point[1]*c,point[2]]


def find_rooms(m, ids=None, _yaw=None) -> list[dict]:
    """Find roofed, enclosed spaces by floor/ceiling and four wall rays.

    Samples with the same enclosing wall intersections vote for a room.
    This separates rooms sharing one roof and ignores open courtyards. It is
    bounded, conservative sampling, not a full architectural reconstruction.
    ids restricts the floor candidates; blockers always include the whole map.
    """
    scene = CameraGeometry(m)
    candidate = CameraGeometry(m, ids) if ids else scene
    if _yaw is None:
        orientations={0.0:0}
        for _,_,_,normal in scene.triangles:
            if abs(normal[2])<1e-5 and math.hypot(normal[0],normal[1])>1:
                angle=round(math.degrees(math.atan2(normal[1],normal[0]))%90,1)
                if angle>89.9:angle=0.0
                orientations[angle]=orientations.get(angle,0)+1
        result=[]
        for yaw in sorted(orientations,key=orientations.get,reverse=True)[:4]:
            for room in find_rooms(m,ids,_yaw=yaw):
                if not any(math.dist(room['centre'],r['centre'])<96 for r in result):
                    result.append(room)
        return sorted(result,key=lambda r:(r['floor'],r['centre'][0],r['centre'][1]))[:24]
    scene=scene.rotated(_yaw)
    candidate=candidate.rotated(_yaw)
    if not candidate.bounds:
        return []
    lo, hi = candidate.bounds
    step = max(96.0, math.sqrt(max(1, (hi[0]-lo[0])*(hi[1]-lo[1]))/2500))
    votes = {}
    nx = max(1, math.ceil((hi[0]-lo[0])/step))
    ny = max(1, math.ceil((hi[1]-lo[1])/step))
    for ix in range(nx):
        x = lo[0]+(ix+.5)*(hi[0]-lo[0])/nx
        for iy in range(ny):
            y = lo[1]+(iy+.5)*(hi[1]-lo[1])/ny
            floors = sorted({round(hi[2]+1-t, 2) for t in
                             candidate.hits((x,y,hi[2]+1),(0,0,-1),floors=True)}, reverse=True)
            for floor in floors[:8]:
                eye = (x,y,floor+72)
                ceiling = scene.nearest(eye,(0,0,1),2048)
                if ceiling is None or ceiling < 56 or not scene.clear(eye):
                    continue
                ds = [scene.nearest(eye,d,2048) for d in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0)]]
                if any(d is None or d < 24 for d in ds):
                    continue
                rlo = [x-ds[1],y-ds[3],floor]
                rhi = [x+ds[0],y+ds[2],eye[2]+ceiling]
                if min(rhi[i]-rlo[i] for i in (0,1)) < 128:
                    continue
                key = tuple(round(v/24) for v in rlo+rhi)
                votes.setdefault(key, []).append((rlo,rhi,eye))
    rooms = []
    for samples in sorted(votes.values(), key=len, reverse=True):
        if len(samples) < 2:
            continue
        rlo,rhi,_ = samples[0]
        centre = _centre((rlo,rhi))
        centre[2] = rlo[2]+72
        if any(abs(r['floor']-rlo[2]) < 12 and
               checks._covers_xy(centre, r['bounds'], -24) for r in rooms):
            continue
        # Wall rays through a doorway can describe a much larger outside area;
        # a single roof hit is insufficient. Validate floor AND roof across the
        # proposed space. This also rejects rooms inferred from furniture tops.
        probes = [(rlo[0]+(rhi[0]-rlo[0])*fx,rlo[1]+(rhi[1]-rlo[1])*fy,rlo[2]+4)
                  for fx,fy in [(.2,.2),(.8,.2),(.2,.8),(.8,.8),(.5,.5)]]
        valid=0
        for point in probes:
            floor_hit=scene.nearest(point,(0,0,-1),24,floors=True)
            roof_hit=scene.nearest((point[0],point[1],rlo[2]+72),(0,0,1),2048)
            if floor_hit is not None and abs(floor_hit-4)<4 and roof_hit is not None and abs(rlo[2]+72+roof_hit-rhi[2])<16:
                valid+=1
        if valid < 4:
            continue
        # A near-wall position gives depth; fall back to an actually sampled
        # point when a pillar or irregular polygon blocks the proposed view.
        options = [(rlo[0]+48,rlo[1]+48,rlo[2]+72),
                   (rhi[0]-48,rlo[1]+48,rlo[2]+72), *[s[2] for s in samples]]
        camera = next((e for e in options if scene.clear(e) and scene.visible(e,centre) and
                       scene.nearest((e[0],e[1],rlo[2]+4),(0,0,-1),8,floors=True) is not None), None)
        if camera is None:
            continue
        rooms.append({'bounds': (rlo,rhi), 'floor':rlo[2], 'ceiling':rhi[2],
                      'centre':centre, 'eye':list(camera), 'samples':len(samples)})
    for room in rooms:
        room['local_bounds']=room['bounds']
        room['yaw']=_yaw
        if _yaw:
            lo,hi=room['bounds']
            corners=[_rotate_xy((x,y,z),_yaw) for x in [lo[0],hi[0]] for y in [lo[1],hi[1]] for z in [lo[2],hi[2]]]
            room['bounds']=(tuple(min(p[i] for p in corners) for i in range(3)),
                            tuple(max(p[i] for p in corners) for i in range(3)))
            room['eye']=_rotate_xy(room['eye'],_yaw)
            room['centre']=_rotate_xy(room['centre'],_yaw)
    return rooms


def _safe_anchor(scene, centre, desired):
    """Choose a clear eye with an unobstructed ray to the named anchor."""
    for eye in [desired, *[(centre[0]+dx,centre[1]+dy,centre[2]) for dx,dy in
                           [(-96,-96),(96,-96),(-96,96),(96,96),(-48,0),(48,0)]]]:
        if scene.clear(eye) and scene.visible(eye,centre):
            return list(eye)
    return None


def _auto_views(project, map_name: str) -> list[dict]:
    """Derive safe, named camera anchors from authored VMAP geometry/entities."""
    m = project.load(map_name)
    scene = CameraGeometry(m)
    meshes = scene.meshes
    if not meshes:
        raise game.GameError(f'cannot derive automatic views for {map_name!r}: map has no solid geometry')
    bounds = checks._map_bounds(meshes)
    lo, hi = bounds
    target = _centre(bounds)
    sx, sy = hi[0] - lo[0], hi[1] - lo[1]
    views: list[dict] = []

    # Four corners give useful coverage for elongated maps while staying a
    # bounded response.  Small maps use two diagonally opposite corners.
    inset_x, inset_y = max(64.0, sx * .12), max(64.0, sy * .12)
    corner_specs = [
        ('overview_nw', [lo[0] + inset_x, hi[1] - inset_y]),
        ('overview_ne', [hi[0] - inset_x, hi[1] - inset_y]),
        ('overview_se', [hi[0] - inset_x, lo[1] + inset_y]),
        ('overview_sw', [lo[0] + inset_x, lo[1] + inset_y]),
    ]
    count = 2 if max(sx, sy) < 1200 else (3 if max(sx, sy) < 2400 else 4)
    camera_z = hi[2] + max(256.0, min(1024.0, max(sx, sy) * .42))
    rooms = find_rooms(m)
    for suffix, xy in corner_specs[:count]:
        pos = [xy[0], xy[1], camera_z - 64.0]
        # A roof on the overview ray hides the playable floor. Replace such
        # views with the indoor cameras below instead of photographing roofs.
        ground = scene.nearest([*xy,hi[2]+1],(0,0,-1),floors=True)
        if ground is not None:
            floor = hi[2]+1-ground
            if any(checks._covers_xy(xy,r['bounds']) and floor >= r['ceiling']-1 for r in rooms):
                continue
            if rooms:
                # Mixed courtyard/house maps: an exterior view below the roof
                # shows the facade and yard, while room views cover indoors.
                eye=[xy[0],xy[1],floor+160]
                focus=[target[0],target[1],floor+72]
                delta=checks._sub(focus,eye)
                length=math.sqrt(checks._dot(delta,delta))
                direction=tuple(v/length for v in delta)
                hit=scene.nearest(eye,direction,length)
                if hit is not None:
                    focus=[eye[i]+direction[i]*max(32,hit*.8) for i in range(3)]
                if not scene.clear(eye):
                    continue
                views.append({'name':f'{map_name}_{suffix}','pos':[eye[0],eye[1],eye[2]-64],
                              'ang':_look_at(eye,focus)})
                continue
        views.append({'name': f'{map_name}_{suffix}', 'pos': pos,
                      'ang': _look_at([pos[0], pos[1], pos[2] + 64], target)})

    # Spawn groups are kept at eye-level and pointed toward the playable centre.
    spawn_classes = (('terrorist', 'info_player_terrorist'),
                     ('counterterrorist', 'info_player_counterterrorist'))
    for label, classname in spawn_classes:
        entities = m.entities(classname)
        if not entities:
            continue
        points = [checks._vec(e.get('origin')) for e in entities]
        # Actual spawn, rather than a centroid which can lie in a dividing wall.
        pos = list(points[0])
        eye = [pos[0],pos[1],pos[2]+64]
        focus = next((r['centre'] for r in rooms if checks._covers_xy(eye,r['bounds']) and
                      r['floor'] < eye[2] < r['ceiling']), [target[0],target[1],eye[2]])
        if not scene.visible(eye,focus) or math.dist(eye,focus)<32:
            yaw=math.radians(checks._vec(entities[0].get('angles'))[1])
            for dx,dy in [(math.cos(yaw),math.sin(yaw)),(1,0),(0,1),(-1,0),(0,-1)]:
                candidate=[eye[0]+dx*128,eye[1]+dy*128,eye[2]]
                if scene.visible(eye,candidate):
                    focus=candidate
                    break
        safe = eye if scene.clear(eye) and scene.visible(eye,focus) else None
        if safe is None:
            continue
        pos = [safe[0],safe[1],safe[2]-64]
        views.append({'name': f'{map_name}_spawns_{label}', 'pos': pos,
                      'ang': _look_at(safe, focus)})

    # Objective/zone entities get a camera just outside the volume, looking at
    # its centre.  This avoids placing the camera in a wall while retaining a
    # useful view of the named site or buy area.
    anchor_entities = []
    for entity in m.entities():
        classname = m.classname(entity)
        low = classname.casefold()
        if ('bomb_target' in low or 'buyzone' in low or 'hostage' in low or
                low in {'func_bomb_target_zone', 'func_buyzone'}):
            anchor_entities.append(entity)
    labels: dict[str, int] = {}
    for entity in anchor_entities:
        classname = m.classname(entity)
        props = entity.get('entity_properties')
        targetname = str(props.get('targetname') or '') if props else ''
        base = re.sub(r'[^a-z0-9_]+', '_', targetname.lower()).strip('_') or classname.lower()
        labels[base] = labels.get(base, 0) + 1
        suffix = base if labels[base] == 1 else f'{base}_{labels[base]}'
        eb = _entity_bounds(m, entity)
        centre = _centre(eb)
        floor_hit = scene.nearest((centre[0],centre[1],centre[2]+128),(0,0,-1),floors=True)
        if floor_hit is not None:
            centre[2] = centre[2]+128-floor_hit+72
        vx, vy = centre[0] - target[0], centre[1] - target[1]
        length = max(math.hypot(vx, vy), 1.0)
        radius = max(128.0, math.hypot(eb[1][0] - eb[0][0], eb[1][1] - eb[0][1]) * .6)
        # Stay on the playable side of the volume.  Placing the camera outside
        # the map made the surrounding wall hide the very objective being
        # documented on compact maps.
        pos = [centre[0] - vx / length * radius, centre[1] - vy / length * radius,
               centre[2] - 64.0]
        safe = _safe_anchor(scene,centre,[pos[0],pos[1],pos[2]+64])
        if safe:
            views.append({'name': f'{map_name}_{suffix}', 'pos': [safe[0],safe[1],safe[2]-64],
                          'ang': _look_at(safe, centre)})

    # A room made by the ``room`` shape has a thin ceiling and a floor with
    # enclosing wall boxes.  Find only clear, large candidates; missing a room
    # is preferable to returning a camera inside a random prop.
    for room_index, room in enumerate(rooms,1):
        eye = room['eye']
        views.append({'name': f'{map_name}_room_{room_index}', 'pos': [eye[0],eye[1],eye[2]-64],
                      'ang': _look_at(eye,room['centre'])})
    return views


def playtest_views(project, map_name: str, views='auto', offset: int = 0, limit: int | None = None) -> dict:
    """Capture automatic VMAP anchors or an explicit list of view dictionaries."""
    if views == 'auto' or views is None:
        requested = _auto_views(project, map_name)
        agent=re.sub(r'[^a-z0-9]','',os.environ.get('CS2MAP_AGENT','').lower())
        if agent:
            requested=[{**v,'name':v['name'] if v['name'].startswith(agent+'_') else agent+'_'+v['name']}
                       for v in requested]
    elif isinstance(views, list):
        requested = views
    else:
        raise ValueError('views must be "auto" or a list of {name,pos,ang} objects')
    if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:
        raise ValueError('offset must be a non-negative integer')
    if limit is not None and (not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=6):
        raise ValueError('limit must be between 1 and 6')
    total=len(requested)
    requested=requested[offset:None if limit is None else offset+limit]
    if not requested:
        return {'map':map_name,'reloaded':False,'shots':[],'total_views':total,'offset':offset}
    result = game.views(project.s, map_name, requested)
    compact = []
    for req, shot in zip(requested, result.get('shots', [])):
        compact.append({'name': shot.get('name'), 'path': shot.get('path'),
                        'pos': req.get('pos'), 'ang': req.get('ang'),
                        **({'console':shot['console']} if 'console' in shot else {})})
    return {'map': result.get('map'), 'reloaded': result.get('reloaded'), 'shots': compact,
            'total_views':total,'offset':offset}

