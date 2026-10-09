"""Skeletal prop assets and conservative, floor-supported actor routes."""
from __future__ import annotations

from contextvars import ContextVar
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from . import blender_run, checks, kv3, layout, materials, models, route, scripting
from .config import PROJECT_ROOT, ensure_inside
from .project import OpError

DATA = Path(__file__).parent / 'data' / 'actors'
PREVIEW = ContextVar('actor_preview', default=False)
MARKER = 'CS2MAP_ACTOR '


def _name(value, label='name'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,55}', value):
        raise OpError(f'{label} must be an ASCII identifier, max 56 characters')
    return value


def _number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise OpError(f'{label} must be a finite number in {low}..{high}')
    return float(value)


def _point(m, value):
    if isinstance(value, dict):
        value = layout.point(m, value)[0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError('route points need [x,y,z] or {room,at:[u,v]}')
    return [_number(v, 'coordinate', -32768, 32768) for v in value]


def model_document(name, source, remaps, clips):
    return {'rootNode': {'_class': 'RootNode', 'model_archetype': '',
        'primary_associated_entity': 'prop_dynamic', 'anim_graph_name': '', 'children': [
        {'_class': 'RenderMeshList', 'children': [{'_class': 'RenderMeshFile', 'name': name,
            'filename': source + '/mesh.fbx', 'import_scale': .0254,
            'import_filter': {'exclude_by_default': False, 'exception_list': []}}]},
        {'_class': 'MaterialGroupList', 'children': [{'_class': 'DefaultMaterialGroup',
            'remaps': remaps, 'use_global_default': False, 'global_default_material': ''}]},
        {'_class': 'AnimationList', 'default_root_bone_name': 'root', 'children': [
            {'_class': 'AnimFile', 'name': clip, 'source_filename': source + '/' + clip + '.fbx',
             'start_frame': -1, 'end_frame': -1, 'frame_rate': meta['fps'],
             'looping': True, 'is_default_idle_anim': clip == 'idle',
             'fade_in_time': .12, 'fade_out_time': .12, 'import_bone_scales': False}
            for clip, meta in clips.items()]}
    ]}}


def actor_build(project, name='walker', script=None, walk_speed=62.0):
    name = _name(name, 'asset name')
    walk_speed = _number(walk_speed, 'walk_speed', 1, 400)
    custom = None
    if script is not None:
        if not isinstance(script, str):
            raise OpError('script must be a project-local Blender Python file')
        custom = ensure_inside(Path(script).resolve(), PROJECT_ROOT)
        if not custom.is_file() or custom.suffix != '.py':
            raise OpError('script must be an existing project-local .py file')
    root = ensure_inside(project.s.content_addon / 'models' / 'mcp_n' / name, project.s.content_addon)
    source = ensure_inside(root / 'sources' / uuid.uuid4().hex[:12], project.s.content_addon)
    job = blender_run.run(project.s, project.jobs, DATA / 'export.py',
                          [source, str(custom) if custom else ''], title=f'actor {name}', timeout=180)
    if job.state != 'succeeded' or not job.result.get('ok'):
        return {'ok': False, 'stage': 'blender', 'log': str(job.log), 'errors': job.result.get('errors', [])[:4]}
    meta = job.result
    remaps, vmats = [], []
    for material in meta['materials']:
        rel = material['vmat']
        if rel:
            from .ext.models import asset_path
            rel = asset_path(project, rel, '.vmat', 'materials')
        else:
            color = [round(255 * (12.92 * c if c <= .0031308 else 1.055 * c ** (1/2.4) - .055))
                     for c in material['color']]
            made = materials.create(project.s, name + '_' + material['token'],
                pattern={'pattern': 'solid', 'color': color, 'size': 64},
                roughness=.8, normal_strength=0, folder='mcp_n')
            rel = made['material']
            vmats.append(project.s.content_addon / rel)
        remaps.append({'from': material['token'] + '.vmat', 'to': rel})
    relsource = source.relative_to(project.s.content_addon).as_posix()
    vmdl = root / (name + '.vmdl')
    if vmdl.exists():
        history = ensure_inside(root / 'history', project.s.content_addon)
        history.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vmdl, history / (uuid.uuid4().hex[:12] + '.vmdl'))
    vmdl.write_text(kv3.dumps(model_document(name, relsource, remaps, meta['clips']),
                            header=kv3.HEADER_MODELDOC), encoding='utf-8')
    compiled = models._compile_model(project, vmdl, vmats)
    preview = PROJECT_ROOT / 'screenshots' / ('n8_' + name + '_preview-' + uuid.uuid4().hex[:12] + '.png')
    tmp_root = ensure_inside(PROJECT_ROOT / '.local/tmp', PROJECT_ROOT)
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='actor-preview-', dir=tmp_root) as temp:
        preview_job = blender_run.run(project.s, project.jobs, DATA / 'preview.py',
            [source / 'actor.blend', temp], timeout=90)
        if preview_job.state == 'succeeded':
            from PIL import Image, ImageDraw
            sheet = Image.new('RGB', (768, 832), '#eeeeee')
            draw = ImageDraw.Draw(sheet)
            labels = ['idle | +X red, +Y green', 'walk | phase 0.25', 'walk | phase 0.75', 'turn | phase 0.25']
            for i, file in enumerate(preview_job.result['files']):
                safe = ensure_inside(Path(file), Path(temp))
                x, y = (i % 2) * 384, (i // 2) * 416
                with Image.open(safe) as img:
                    sheet.paste(img.convert('RGB'), (x, y+32))
                draw.text((x+12, y+9), labels[i], fill='#171717')
            preview.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(preview)
    preview_ok = preview_job.state == 'succeeded' and preview.exists()
    meta.update(model=vmdl.relative_to(project.s.content_addon).as_posix(), walk_speed=walk_speed,
                source=str(source), blender_log=str(job.log), compile=compiled, ok=compiled['ok'],
                preview=str(preview) if preview_ok else None, preview_log=preview_job.log)
    (root / 'actor.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return {'ok': meta['ok'], 'model': meta['model'], 'bounds': meta['bounds'], 'bones': len(meta['bones']),
            'clips': list(meta['clips']), 'walk_speed': walk_speed, 'forward': '+X', 'source': str(source),
            'preview': meta['preview'],
            'compile': {k: compiled[k] for k in ('ok', 'exit_code', 'log', 'output', 'errors') if k in compiled}}


def _model(project, value):
    if not isinstance(value, str) or not re.fullmatch(r'models/mcp_n/[A-Za-z_][A-Za-z0-9_]{0,55}/[A-Za-z_][A-Za-z0-9_]{0,55}\.vmdl', value):
        raise OpError('model must be an actor_build models/mcp_n/<name>/<name>.vmdl path')
    source = ensure_inside(project.s.content_addon / value, project.s.content_addon)
    compiled = ensure_inside(project.s.game_addon / (value + '_c'), project.s.game_addon)
    metadata = ensure_inside(source.parent / 'actor.json', project.s.content_addon)
    if not source.is_file() or not compiled.is_file() or not metadata.is_file():
        raise OpError('build the skeletal model with actor_build first')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    if not meta.get('ok') or meta.get('model') != value or not {'idle', 'walk', 'turn'} <= meta.get('clips', {}).keys():
        raise OpError('actor model must have successfully compiled idle/walk/turn clips')
    bounds = meta['bounds']
    if (abs(bounds[0][2]) > .1 or bounds[1][2] > 72 or
            any(bounds[0][i] < -16 or bounds[1][i] > 16 for i in (0, 1))):
        raise OpError('actor must have feet at z=0 and fit the route hull [-16,-16,0]..[16,16,72]')
    return value, meta


def plan(m, points, loop):
    """Core opening graph and flat A*, with checked direct stairs/ramp legs."""
    solids = layout.world_solids(m)          # world-space, group transforms applied
    triangles = [tri for _, _, ts in solids for tri in ts]
    def floor(q):
        z = checks._floor([q[0], q[1], q[2] + 18.1], triangles, max_drop=36.2)
        if z is None:
            raise OpError(f'actor route has no supporting floor near {q}')
        p = [q[0], q[1], z]
        hull = {'mins': [p[0]-16, p[1]-16, z+.1], 'maxs': [p[0]+16, p[1]+16, z+72]}
        if layout.collisions(m, hull, [0, 0, 0], solids):
            # A step can occupy the lower 18 units; still check head/torso clearance.
            hull['mins'][2] = z+18.1
            if layout.collisions(m, hull, [0, 0, 0], solids):
                raise OpError(f'actor route hull is blocked near {p}')
        for dx, dy in ((-15, -15), (-15, 15), (15, -15), (15, 15)):
            support = checks._floor([p[0]+dx, p[1]+dy, z+18.1], triangles, max_drop=36.2)
            if support is None or abs(support-z) > 18:
                raise OpError('actor footprint has no floor support')
        return p
    goals = [floor(q) for q in points]
    path = [{'pos': goals[0], 'stop': True}]
    pairs = list(zip(goals, goals[1:]))
    if loop:
        pairs.append((goals[-1], goals[0]))
    for start, goal in pairs:
        cursor = start
        for target in [*route.layout_waypoints(m, start, goal), goal]:
            via = route._triangle_grid_waypoints(m, cursor, target)
            candidates = via or [target]
            for candidate in candidates:
                count = max(1, math.ceil(math.dist(cursor, candidate) / 12))
                previous = cursor
                for index in range(1, count+1):
                    q = [cursor[i] + (candidate[i]-cursor[i])*index/count for i in range(3)]
                    # Climb one real step at a time. Interpolating endpoint Z
                    # can put the ray under a tread before the actor reaches it.
                    q[2] = previous[2]
                    supported = floor(q)
                    if abs(supported[2]-previous[2]) > 18:
                        raise OpError('actor route step exceeds 18 units')
                    # A swept torso detects thin walls between sampled positions.
                    hull = {'mins': [previous[0]-16, previous[1]-16, max(previous[2], supported[2])+18.1],
                            'maxs': [previous[0]+16, previous[1]+16, max(previous[2], supported[2])+72]}
                    if layout.collisions(m, hull, [supported[0]-previous[0], supported[1]-previous[1], 0], solids):
                        raise OpError('actor route crosses blocking geometry; no safe A* path')
                    path.append({'pos': supported, 'stop': False})
                    previous = supported
                cursor = candidate
            path[-1]['stop'] = target == goal
    if len(path) > 4096:
        raise OpError('actor route exceeds 4096 sampled points')
    return path


def add_actor(project, m, op, refs):
    allowed = {'op', 'name', 'model', 'route', 'speed', 'loop', 'idle_at_points', 'ref', 'stop_near_player'}
    if set(op) - allowed:
        raise OpError('unknown add_actor fields: ' + ', '.join(sorted(set(op)-allowed)))
    name = _name(op.get('name'))
    model, meta = _model(project, op.get('model'))
    speed = _number(op.get('speed', 90), 'speed', 1, 300)
    if not .1 <= speed / meta['walk_speed'] <= 4:
        raise OpError('speed must keep walk playback rate in .1..4 relative to actor_build walk_speed')
    idle = _number(op.get('idle_at_points', 2), 'idle_at_points', 0, 60)
    near = _number(op.get('stop_near_player', 0), 'stop_near_player', 0, 256)
    loop = op.get('loop', True)
    if not isinstance(loop, bool):
        raise OpError('loop must be true or false')
    raw = op.get('route')
    if not isinstance(raw, list) or not 2 <= len(raw) <= 64:
        raise OpError('route must contain 2..64 points')
    points = [_point(m, p) for p in raw]
    if any(math.dist(a, b) < 1 for a, b in zip(points, points[1:])):
        raise OpError('consecutive route points must differ by at least one unit')
    names = {n['entity_properties'].get('targetname') for n in m.entities()}
    if names & {name, name + '_script'}:
        raise OpError('actor or script targetname already exists')
    path = plan(m, points, loop)
    script_name = 'n8_' + name
    spec = {'name': name, 'model': model, 'path': path, 'speed': speed, 'walk_speed': meta['walk_speed'],
            'loop': loop, 'idle_at_points': idle, 'stop_near_player': near}
    code = '// ' + MARKER + json.dumps(spec, separators=(',', ':')) + '\n'
    code += (DATA / 'runtime.js').read_text(encoding='utf-8').replace('__SPEC__', json.dumps(spec, separators=(',', ':')))
    # Names are immutable across different maps: never silently retarget another actor.
    script_path = ensure_inside(project.s.content_addon / 'maps/scripts/mcp' / (script_name + '.js'), project.s.content_addon)
    if script_path.exists() and script_path.read_text(encoding='utf-8') != code:
        for other in (project.s.content_addon / 'maps').glob('*.vmap'):
            if other.stem == m.path.stem:
                continue
            if any(e['entity_properties'].get('cs_script') == 'maps/scripts/mcp/' + script_name + '.vjs'
                   for e in project.load(other.stem).entities(classname='point_script')):
                raise OpError('actor name is used by another map with a different definition; choose a unique name')
    if not PREVIEW.get():
        written = scripting.script_write(project, script_name, code)
        if not written['ok']:
            raise OpError(f'actor script compilation failed; see {written.get("log")}')
    actor = m.new_entity('prop_dynamic', {'targetname': name, 'model': model, 'solid': 0,
        'use_animgraph': 0, 'IdleAnim': 'idle', 'IdleAnimationLoopMode': 'ANIM_LOOP_MODE_LOOPING',
        'AnimateOnServer': 1}, path[0]['pos'], defaults=project.catalog.defaults('prop_dynamic'))
    m.attach(actor)
    script = m.new_entity('point_script', {'targetname': name + '_script',
        'cs_script': 'maps/scripts/mcp/' + script_name + '.vjs'}, path[0]['pos'],
        defaults=project.catalog.defaults('point_script'))
    m.attach(script)
    actor.set('cs2map_actor', 'string', json.dumps(spec, separators=(',', ':')))
    if op.get('ref'):
        refs[op['ref']] = actor['nodeID']
    return {'op': 'add_actor', 'id': actor['nodeID'], 'script_id': script['nodeID'],
            'name': name, 'points': len(path), 'cs_script': 'maps/scripts/mcp/' + script_name + '.vjs'}


def map_edit_wrapper(project, original):
    def edit(name, operations, dry_run=False, force=False):
        if not isinstance(operations, list) or not any(isinstance(o, dict) and o.get('op') == 'add_actor' for o in operations):
            return original(name, operations, dry_run, force)
        with project._lock:
            if not dry_run:
                project._check_hammer(name, force)
            token = PREVIEW.set(True)
            try:
                preview = original(name, operations, True, force)
            finally:
                PREVIEW.reset(token)
            return preview if dry_run else original(name, operations, False, force)
    return edit


def check_actors(project, m):
    problems = []
    for actor in m.entities(classname='prop_dynamic'):
        raw = actor.get('cs2map_actor')
        if not raw:
            continue
        try:
            spec = json.loads(raw)
            name = _name(spec['name'])
            _model(project, spec['model'])
            matching = m.entities(targetname=name + '_script')
            rel = 'maps/scripts/mcp/n8_' + name + '.vjs'
            if len(matching) != 1 or matching[0]['entity_properties'].get('cs_script') != rel:
                raise OpError('actor script entity is missing or changed')
            src = ensure_inside(project.s.content_addon / Path(rel).with_suffix('.js'), project.s.content_addon)
            first = src.read_text(encoding='utf-8').split('\n', 1)[0]
            if first != '// ' + MARKER + json.dumps(spec, separators=(',', ':')):
                raise OpError('actor runtime source metadata differs from map; replay add_actor')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(checks.problem('error', 'actor_resource', actor['nodeID'], str(exc),
                'Rebuild the model, then recreate the map and replay add_actor.'))
    return problems
