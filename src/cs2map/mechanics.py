"""Shared core of the mechanics: cs_script has no imports, so a script is ASSEMBLED from fragments.

A fragment is a JS file in ``data/mechanics/`` (or in the zone of a mechanic) whose first lines are a header::

    // @fragment seat
    // @requires core
    // @imports Instance, CSMoveType

``build_script`` orders the fragments by their dependencies, merges the imports into one line, inserts the
configuration as a JSON literal ``const config = ...`` and writes a marker line first (so ``check_map`` and the
re-use check of a script name can read the configuration back). This module also holds the schemas shared by
all mechanics (``camera``, ``health``, ``seat``, ``hud``), helpers that make their standard entities, and the
Node harness runner used by the fragment tests. See docs/specs/mechanics.md.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT, ensure_inside

DATA = Path(__file__).parent / 'data' / 'mechanics'
MARKER = '// CS2MAP_MECH '
# Everything `import { ... } from "cs_script/point_script"` can name (the installed point_script.d.ts, 05.10).
KNOWN_IMPORTS = frozenset('''Instance CSMoveType CSObserverMode CSRoundEndReason CSRadarIcon CSRadarColor CSTeamMoneyReason
CSWeaponType CSGrenadeType CSWeaponAttackType CSGearSlot CSLoadoutSlot CSDamageTypes CSDamageFlags CSHitGroup CSInputs
Entity BaseModelEntity CSWeaponBase C4 CSWeaponData CSGrenadeProjectileBase CSPlantedC4 CSRadarPoint CSObservablePoint
CSPlayerController CSObserverPawn CSPlayerPawn CustomHudLayout PointTemplate CustomCameraMode CustomPlayerCamera
CSDamageType'''.split())
_NAME = re.compile(r'[a-z][a-z0-9_]{0,39}')


@dataclass(frozen=True)
class Fragment:
    name: str
    path: Path
    requires: tuple
    imports: tuple


FRAGMENTS: dict[str, Fragment] = {}


def parse_header(text: str, path='') -> dict:
    """Read the leading ``// @fragment/@requires/@imports`` lines of a fragment."""
    head = {'fragment': None, 'requires': [], 'imports': []}
    for line in text.splitlines():
        m = re.match(r'//\s*@(fragment|requires|imports)\b\s*(.*)$', line)
        if not m:
            break
        key, value = m.group(1), m.group(2).strip()
        if key == 'fragment':
            head['fragment'] = value
        else:
            head[key] += [x.strip() for x in value.split(',') if x.strip()]
    if not head['fragment']:
        raise ValueError(f'{path}: a fragment file starts with "// @fragment <name>"')
    return head


def register_fragment(name: str, path) -> Fragment:
    """Add a fragment (a mechanic registers its own, e.g. a turret's ``heat``). Same name + same file is idempotent."""
    path = Path(path)
    if not _NAME.fullmatch(name):
        raise ValueError('fragment name: lowercase letter + up to 39 letters/digits/underscores')
    head = parse_header(path.read_text(encoding='utf-8'), path)
    if head['fragment'] != name:
        raise ValueError(f'{path.name}: header says "@fragment {head["fragment"]}", registered as {name!r}')
    bad = [i for i in head['imports'] if i not in KNOWN_IMPORTS]
    if bad:
        raise ValueError(f'{path.name}: unknown cs_script imports {bad}')
    old = FRAGMENTS.get(name)
    if old and old.path.resolve() != path.resolve():
        raise ValueError(f'fragment {name!r} is already registered from {old.path}')
    frag = FRAGMENTS[name] = Fragment(name, path, tuple(head['requires']), tuple(head['imports']))
    return frag


for _f in sorted(DATA.glob('*.js')):
    register_fragment(_f.stem, _f)


def resolve(names) -> list[Fragment]:
    """Fragments in build order: dependencies first, otherwise the order given; unknown names and cycles raise."""
    out: list[Fragment] = []
    state: dict[str, int] = {}

    def visit(name, chain):
        if name not in FRAGMENTS:
            raise ValueError(f'unknown fragment {name!r}; known: {sorted(FRAGMENTS)}')
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            raise ValueError('fragment dependency cycle: ' + ' -> '.join(chain + [name]))
        state[name] = 1
        for dep in FRAGMENTS[name].requires:
            visit(dep, chain + [name])
        state[name] = 2
        out.append(FRAGMENTS[name])

    for n in names:
        visit(n, [])
    return out


def build_script(name: str, fragments, config: dict, *, marker: str = MARKER, marker_config=None) -> str:
    """Assemble one cs_script module: marker line, merged import, ``const config``, fragments in order.

    ``marker_config`` (default: ``config``) is what the first line records; pass the user-facing spec when
    ``config`` carries derived values (entity names)."""
    frags = resolve(list(fragments))
    imports: list[str] = []
    for f in frags:
        imports += [i for i in f.imports if i not in imports]
    first = marker + json.dumps(config if marker_config is None else marker_config, sort_keys=True,
                                separators=(',', ':'), allow_nan=False)
    parts = [first, 'import { ' + ', '.join(imports) + ' } from "cs_script/point_script";' if imports else '',
             f'// ---- {name}: {", ".join(f.name for f in frags)} ----',
             'const config = ' + json.dumps(config, ensure_ascii=True, allow_nan=False, sort_keys=True) + ';']
    for f in frags:
        body = f.path.read_text(encoding='utf-8')
        parts.append(f'// ---- fragment {f.name} ----\n' + body.rstrip() + '\n')
    return '\n'.join(p for p in parts if p) + '\n'


# Own scripts may ask for the shared, tested pieces (docs/tools/scripting.md "Own mechanic on the shared pieces").
SHARED = ('core', 'hud', 'camera', 'seat', 'health', 'fire')
_REQUIRES = re.compile(r'^[ \t]*//[ \t]*@requires[ \t]+(.+?)[ \t]*$', re.M)
_FIRE = re.compile(r'^[ \t]*//[ \t]*@fire[ \t]+([a-z][a-z0-9_]{0,31})[ \t]+(\{.*\})[ \t]*$', re.M)
_IMPORT = re.compile(r'^[ \t]*import[ \t]*\{([^}]*)\}[ \t]*from[ \t]*["\']cs_script/point_script["\'][ \t]*;?[ \t]*$', re.M)


def fire_directives(code: str) -> dict:
    """`// @fire <prefix> {json}` lines of a script: the validated shot-engine config and its entity names."""
    from . import fire as fire_engine          # fire imports this module
    out = {}
    for prefix, raw in _FIRE.findall(code):
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f'@fire {prefix}: the config is not JSON ({exc})') from None
        f = fire_engine.validate(value)
        out[prefix] = {'fire': f, 'entities': fire_engine.names(prefix, f)}
    return out


def _head(code: str) -> str:
    """The leading comment block of a script (blank lines and `//` lines before the first code line)."""
    head = []
    for line in code.splitlines():
        if line.strip() and not line.lstrip().startswith('//'):
            break
        head.append(line)
    return '\n'.join(head)


def bundle(code: str) -> tuple[str, dict]:
    """A hand-written script whose leading comments say `// @requires core, hud, ...` gets those fragments in front of
    its code under one merged import; `// @fire <prefix> {json}` there adds `FIRE.<prefix> = {fire, entities}`
    (add_script makes the entities). Other scripts, and scripts assembled by build_script (their fragments' headers
    also say @requires), are returned unchanged."""
    if code.startswith(MARKER):
        return code, {}
    head = _head(code)
    req, fires = _REQUIRES.search(head), fire_directives(head)
    if not req and not fires:
        return code, {}
    names = [n.strip() for n in req.group(1).split(',') if n.strip()] if req else []
    if fires and 'fire' not in names:
        names.append('fire')
    bad = [n for n in names if n not in SHARED]
    if bad:
        raise ValueError(f'@requires: unknown pieces {bad}; shared pieces: {", ".join(SHARED)}')
    frags = resolve(names)
    imports: list[str] = []
    for f in frags:
        imports += [i for i in f.imports if i not in imports]
    for group in _IMPORT.findall(code):
        imports += [n for n in (x.strip() for x in group.split(',')) if n and n not in imports]
    unknown = [i for i in imports if i not in KNOWN_IMPORTS]
    if unknown:
        raise ValueError(f'unknown cs_script imports {unknown}')
    own = _IMPORT.sub('', _REQUIRES.sub('', code, count=1)).strip('\n')
    parts = ['import { ' + ', '.join(imports) + ' } from "cs_script/point_script";',
             f'// ---- own script on the shared pieces: {", ".join(f.name for f in frags)} ----']
    if fires:
        parts.append('const FIRE = ' + json.dumps(fires, ensure_ascii=True, allow_nan=False, sort_keys=True) + ';')
    for f in frags:
        parts.append(f'// ---- fragment {f.name} ----\n' + f.path.read_text(encoding='utf-8').rstrip() + '\n')
    parts.append('// ---- own code ----\n' + own + '\n')
    return '\n'.join(parts) + '\n', {'fragments': [f.name for f in frags], 'fire': sorted(fires)}


def read_marker(code: str, marker: str = MARKER):
    """The configuration recorded on the first line of an assembled script (None if it has no marker)."""
    first = code.split('\n', 1)[0]
    if not first.startswith(marker):
        return None
    return json.loads(first[len(marker):])


# --- shared schemas ----------------------------------------------------------------------------------------

def number(value, label, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{label} must be a finite number in {lo}..{hi}')
    return float(value)


def vector(value, label, lo=-100000, hi=100000):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f'{label} must be [x,y,z]')
    return [number(v, label, lo, hi) for v in value]


def keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'{label}: expected object with fields {sorted(allowed)}')


def validate_camera(camera, default=None):
    """``{forward, up, chase:{forward, up}|false, start?:"fpv"|"chase"}``: FPV view and the optional chase view (R switches)."""
    default = default or {'forward': 22, 'up': 10}
    camera = default if camera is None else camera
    keys(camera, {'forward', 'up', 'right', 'chase', 'start'}, 'camera')
    out = {'forward': number(camera.get('forward', default['forward']), 'camera.forward', -256, 128),
           'up': number(camera.get('up', default['up']), 'camera.up', 0, 128)}
    if camera.get('right', default.get('right', 0)):        # sideways from the body origin (a driver's seat)
        out['right'] = number(camera.get('right', default.get('right', 0)), 'camera.right', -128, 128)
    chase = camera.get('chase', default.get('chase', {'forward': -120, 'up': 36}))
    if chase is not False:
        keys(chase, {'forward', 'up'}, 'camera.chase')
        chase = {'forward': number(chase.get('forward', -120), 'camera.chase.forward', -512, 128),
                 'up': number(chase.get('up', 36), 'camera.chase.up', 0, 256)}
    out['chase'] = chase
    if 'start' in camera:                    # optional: which view a seated player gets first (default fpv)
        if camera['start'] not in ('fpv', 'chase') or (camera['start'] == 'chase' and chase is False):
            raise ValueError("camera.start: 'fpv', or 'chase' when a chase view exists")
        out['start'] = camera['start']
    return out


def validate_health(spec, default_hp=50, native=True):
    """In place: ``health`` (hit points, 0 = indestructible), ``respawn`` (seconds, 0 = next round, "never" = the
    wreck stays for good, also after round restarts; stored as -1), ``effects``."""
    spec['health'] = number(spec.get('health', default_hp if native else 0), 'health', 0, 10000)
    raw = spec.get('respawn', 10)
    spec['respawn'] = -1 if raw in ('never', -1) else number(raw, 'respawn', 0, 600)
    spec['effects'] = spec.get('effects', True)
    if not isinstance(spec['effects'], bool):
        raise ValueError('effects must be a boolean')
    return spec


def validate_seat(spec):
    """In place: optional ``reach`` — how far (units, default 96) the eye ray may be from the object at E.
    An absent field stays absent, so existing definitions keep their recorded configuration."""
    if 'reach' in spec:
        spec['reach'] = number(spec['reach'], 'reach', 16, 512)
    return spec


def validate_hud(hud):
    """``hud`` is optional: ``{"title": "text up to 24 chars"}`` prefixed to the telemetry line, plus the style keys
    of ui.py (theme, position, size, width, offset, animation, accent, background, text, font)."""
    if hud is None:
        return {}
    from . import ui
    hud = ui.validate(hud, extra={'title'})
    title = hud.pop('title', '')
    if not isinstance(title, str) or len(title) > 24 or any(c in title for c in '"\\\n\r<>'):
        raise ValueError('hud.title must be a plain string up to 24 characters')
    return {**hud, **({'title': title} if title else {})}


def model_path(value, label='model'):
    if not isinstance(value, str) or not re.fullmatch(r'models/[a-zA-Z0-9_./-]+\.vmdl', value) or '..' in value:
        raise ValueError(f'{label} must be a safe addon/stock models/.../*.vmdl path')
    return value


# --- standard entities -------------------------------------------------------------------------------------

EFFECTS = {'smoke': 'particles/burning_fx/env_fire_tiny_smoke.vpcf', 'fire': 'particles/burning_fx/env_fire_tiny.vpcf',
           'boom': 'particles/explosions_fx/explosion_hegrenade.vpcf', 'boom_sound': 'BaseGrenade.Explode'}
NATIVE_HEALTH = 100000
PILOT_MODEL = 'agents/models/tm_phoenix/tm_phoenix.vmdl'


def entity(cls, name, origin, kv=None, angles=None):
    """An ``add_entity`` operation (what ``Project._apply`` takes)."""
    return {'op': 'add_entity', 'classname': cls, 'origin': list(origin), 'angles': list(angles or [0, 0, 0]),
            'keyvalues': {'targetname': name, **(kv or {})}}


def seat_entities(n: dict, origin, remote_at=None) -> dict:
    """Damage filter (refuses the renamed pilot) and the parked decoy of the pilot. ``n``: names pilot/filter/pawn."""
    at = remote_at or origin
    return {'filter': entity('filter_activator_name', n['filter'], origin, {'filtername': n['pawn'], 'Negated': 1}),
            'decoy': entity('prop_dynamic', n['pilot'], [at[0], at[1], at[2] - 4096],
                            {'model': PILOT_MODEL, 'solid': 0, 'StartDisabled': 1})}


def effect_entities(n: dict, origin, parent: str) -> list:
    """Smoke, fire (on the body), explosion particle and sound, when the names exist in ``n``."""
    if not n.get('smoke'):
        return []
    above = [origin[0], origin[1], origin[2] + 14]
    ops = [entity('info_particle_system', n[k], above, {'effect_name': EFFECTS[k], 'start_active': '0', 'parentname': parent})
           for k in ('smoke', 'fire')]
    ops.append(entity('info_particle_system', n['boom'], origin, {'effect_name': EFFECTS['boom'], 'start_active': '0'}))
    ops.append(entity('point_soundevent', n['boom_sound'], origin,
                      {'soundName': EFFECTS['boom_sound'], 'sourceEntityName': parent, 'startOnSpawn': 0}))
    return ops


def standard_names(prefix: str, name: str, *, script_prefix: str, hud_prefix: str, with_effects: bool) -> dict:
    """Targetnames every controllable kind shares: script, hud, sound, pilot decoy, damage filter, effects."""
    n = {'script': f'{script_prefix}_{name}', 'hud': f'{hud_prefix}_{name}', 'sound': f'{prefix}_{name}_motor',
         'pilot': f'{prefix}_{name}_pilot', 'filter': f'{prefix}_{name}_no_pilot', 'pawn': f'{prefix}_{name}_pilot_pawn'}
    for key in EFFECTS:
        n[key] = f'{prefix}_{name}_{key}' if with_effects else None
    return n


# --- assets and tests --------------------------------------------------------------------------------------

def write_asset(project, path, text):
    """Write a text asset inside the addon, keeping a backup of the previous version."""
    ensure_inside(path, project.s.content_addon)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    if path.exists():
        backup = project.s.backups / 'control' / str(time.time_ns()) / path.relative_to(project.s.content_addon)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    path.write_text(text, encoding='utf-8', newline='\n')


def ensure_hud(project, layout: str, xml: str, css: str, write=None) -> str:
    """Write and compile a HUD layout (panorama/layout/custom_game is the only folder CS2 accepts); returns the log."""
    from . import compiler
    xml_path = project.s.content_addon / f'panorama/layout/custom_game/{layout}.xml'
    css_path = project.s.content_addon / f'panorama/styles/custom_game/{layout}.css'
    write = write or write_asset
    write(project, xml_path, xml)
    write(project, css_path, css)
    job = compiler.compile_assets(project.s, project.jobs, [xml_path, css_path], force=True, timeout=40)
    state = project.job_status(job.id, 45, 0)
    if state['state'] != 'succeeded' or not state.get('result', {}).get('ok'):
        raise ValueError(f'HUD compilation failed: {state["log"]}')
    return state['log']


def node_path():
    from .scripting import _node
    return _node()


def run_harness(code: str, test: str, timeout: float = 20) -> str:
    """Run `test` (JS) against the assembled script in a Node engine double; returns stdout, raises on failure.

    The double (``data/mechanics/test_double.cjs``) provides Instance, entities, players and a tick loop; `code`
    has its ``import`` line removed. Temporary files live in .local/tmp and are removed here."""
    node = node_path()
    if node is None:
        raise RuntimeError('Node.js unavailable')
    code = '\n'.join(line for line in code.splitlines() if not line.startswith('import '))
    double = (DATA / 'test_double.cjs').read_text(encoding='utf-8')
    root = PROJECT_ROOT / '.local' / 'tmp'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mech-test-', dir=root) as tmp:
        path = Path(tmp) / 'script.cjs'
        # `config` must exist before the double's helpers use it; the script's own `const config` replaces nothing:
        # the harness runs the script in the same scope and the test body after it.
        path.write_text(double + '\n' + code + '\n' + test + '\n', encoding='utf-8')
        done = subprocess.run([str(node), str(path)], capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode != 0:
        raise AssertionError((done.stderr or done.stdout)[-2500:])
    return done.stdout
