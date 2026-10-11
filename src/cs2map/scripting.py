"""Write and attach CS2 ``point_script`` JavaScript sources."""
from __future__ import annotations

import re
import math
import time
import shutil
import os
from pathlib import Path

_NAME = re.compile(r'^[A-Za-z0-9_]{1,64}$')


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError('script name must contain only letters, digits and underscores (1-64 chars)')
    return name


def _api_path(project) -> Path:
    """Locate Valve's installed point_script declaration file."""
    candidates = (
        project.s.cs2_path / 'content' / 'csgo_addons' / 'cs_script_demo' / 'maps' / 'scripts' / 'point_script.d.ts',
        project.s.cs2_path / 'game' / 'csgo_addons' / 'cs_script_demo' / 'maps' / 'scripts' / 'point_script.d.ts',
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError('CS2 point_script.d.ts was not found in the installed cs_script_demo addon')


_DECLARATION = re.compile(
    r'^(?:(?:export|declare|readonly|public|private|static)\s+)*'
    r'([A-Za-z_$][\w$]*)\s*(?:<[^>\n]+>)?\s*(?=\(|:|=|\{)')


def _api_entries(text: str) -> list[dict[str, str]]:
    """Extract method/property signatures (including multiline) with nearest JSDoc."""
    lines = text.splitlines()
    entries: list[dict[str, str]] = []
    pending: list[str] = []
    in_comment = False
    comment: list[str] = []
    iterator = iter(lines)
    for line in iterator:
        stripped = line.strip()
        if in_comment:
            comment.append(line)
            if '*/' in line:
                in_comment = False
                pending = comment[:]
                comment = []
            continue
        if stripped.startswith('/**'):
            comment = [line]
            in_comment = '*/' not in stripped[3:]
            if not in_comment:
                pending = comment[:]
                comment = []
            continue
        if not stripped or stripped.startswith('*'):
            continue
        match = _DECLARATION.match(stripped)
        if not match:
            # Keep the comment through decorators/closing braces, but avoid
            # attaching a class-level comment to every following member.
            if stripped.startswith(('class ', 'interface ', 'type ', 'namespace ')):
                pending = []
            continue
        name = match.group(1)
        if name in {'if', 'for', 'while', 'return'}:
            continue
        clean_comment = re.sub(r'^\s*[/\*]+\s?', '', '\n'.join(pending), flags=re.M)
        clean_comment = re.sub(r'\s*\*/\s*$', '', clean_comment).strip()
        declaration = stripped
        depth = stripped.count('(') - stripped.count(')')
        while depth > 0:
            continuation = next(iterator, None)
            if continuation is None:
                break
            declaration += '\n' + continuation.rstrip()
            depth += continuation.count('(') - continuation.count(')')
        entries.append({'name': name, 'declaration': declaration,
                        'comment': clean_comment[:700]})
        pending = []
    return entries


def script_api(project, query: str = '', limit: int = 20) -> dict:
    """Search the installed ``point_script.d.ts`` API with compact JSDoc context."""
    if not isinstance(query, str):
        raise ValueError('query must be a string')
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise ValueError('limit must be between 1 and 100')
    path = _api_path(project)
    source = path.read_text(encoding='utf-8', errors='replace')
    entries = _api_entries(source)
    needle = query.strip().casefold()
    body = _api_type(source, query.strip()) if needle else None
    if body:
        return {'path': str(path), 'query': query, 'limit': limit,
                'matches': [body], 'count': 1, 'complete': True}
    if needle:
        matches = [entry for entry in entries if needle in ' '.join(
            (entry['name'], entry['declaration'], entry['comment'])).casefold()]
    else:
        common = {'Msg', 'SetThink', 'SetNextThink', 'EntFireAtName', 'ServerCommand',
                  'GiveNamedItem', 'GetAbsOrigin', 'GetHealth', 'IsAlive', 'IsValid'}
        # Put methods first, then events, so the default result remains useful
        # at the requested limit instead of being consumed by bomb callbacks.
        matches = ([entry for entry in entries if entry['name'] in common] +
                   [entry for entry in entries if entry['name'].startswith('On')])
    matches = matches[:limit]
    return {'path': str(path), 'query': query, 'limit': limit,
            'matches': matches, 'count': len(matches),
            'hint': 'Use the declaration and comments as an installed-version reference; APIs can change with CS2 updates.'}


def _api_type(source: str, name: str) -> dict | None:
    """Return an exact named type, preserving comments and nested signatures.

    Strip comments and strings only in the parallel brace-counting view; offsets
    still address the original installed declaration file.
    """
    masked = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), source)
    match = re.search(r'\b(?:class|interface|enum)\s+' + re.escape(name) + r'\b[^{}]*\{', masked)
    if not match:
        return None
    start = source.rfind('\n', 0, match.start()) + 1
    before = source[:start].rstrip()
    if before.endswith('*/'):
        start = before.rfind('/**') if before.rfind('/**') >= 0 else start
    depth, end = 1, match.end()
    while end < len(masked) and depth:
        depth += (masked[end] == '{') - (masked[end] == '}')
        end += 1
    if depth:
        return None
    return {'name': name, 'declaration': source[start:end].strip(), 'comment': '', 'complete': True}


NODE_CANDIDATES = (Path(r'C:\Program Files\nodejs\node.exe'), Path(r'C:\Program Files (x86)\nodejs\node.exe'))


def _node() -> Path | None:
    found = shutil.which('node')
    if found:
        return Path(found)
    return next((p for p in NODE_CANDIDATES if p.exists()), None)


def syntax_check(code: str) -> dict:
    """Check JavaScript syntax with Node.js (`node --check` on an ES module copy).

    resourcecompiler packs even invalid JavaScript, and CS2 reports the error only
    when the map loads. Returns {checked, ok, error?, line?}."""
    import subprocess
    from .config import PROJECT_ROOT, ensure_inside
    node = _node()
    if node is None:
        return {'checked': False, 'ok': True, 'note': 'Node.js not found; syntax not checked'}
    agent = re.sub(r'[^A-Za-z0-9_]', '', os.environ.get('CS2MAP_AGENT', 'S'))[:16] or 'S'
    src = PROJECT_ROOT / '.local' / 'scratch' / agent / f'syntax-{time.time_ns()}.mjs'
    ensure_inside(src, PROJECT_ROOT)
    src.parent.mkdir(parents=True, exist_ok=True)
    # Keep diagnostics inside the project; no unmanaged TEMP writes or deletes.
    src.write_text(code, encoding='utf-8', newline='\n')
    try:
        proc = subprocess.run([str(node), '--check', str(src)], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=20,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'checked': False, 'ok': True, 'note': f'node --check failed to run: {e}'}
    if proc.returncode == 0:
        return {'checked': True, 'ok': True}
    text = (proc.stderr or proc.stdout or '').replace(str(src), 'script.js')
    m = re.search(r'script\.js:(\d+)', text)
    message = next((ln.strip() for ln in text.splitlines()
                    if re.match(r'\s*\w*Error\b', ln)), text.strip().splitlines()[-1] if text.strip() else 'syntax error')
    out = {'checked': True, 'ok': False, 'error': message}
    if m:
        line = int(m.group(1))
        out['line'] = line
        lines = code.splitlines()
        if 0 < line <= len(lines):
            out['source_line'] = lines[line - 1].strip()[:200]
    return out


def script_write(project, name: str, code: str) -> dict:
    """Write ``maps/scripts/mcp/<name>.js`` and compile it to ``.vjs_c``."""
    name = _safe_name(name)
    if not isinstance(code, str) or not code.strip():
        raise ValueError('script code must be a non-empty string')
    from . import mechanics
    code, pieces = mechanics.bundle(code)       # `// @requires core, hud, ...`, `// @fire <prefix> {...}`
    syntax = syntax_check(code)
    if not syntax['ok']:
        where = f" at line {syntax['line']}: {syntax.get('source_line', '')}" if syntax.get('line') else ''
        raise ValueError(f"JavaScript syntax error{where} -> {syntax['error']}; nothing was written")
    root = project.s.content_addon / 'maps' / 'scripts' / 'mcp'
    path = root / f'{name}.js'
    from .config import ensure_inside
    ensure_inside(path, project.s.content_addon)
    root.mkdir(parents=True, exist_ok=True)
    compiled = project.s.game_addon / 'maps' / 'scripts' / 'mcp' / f'{name}.vjs_c'
    try:
        same = (path.read_text(encoding='utf-8') == code and compiled.is_file()
                and compiled.stat().st_mtime >= path.stat().st_mtime)
    except OSError:
        same = False
    if same:
        # Unchanged source, compiled after it: nothing to do. A running CS2 that loaded the script keeps the
        # compiled file open, and rewriting it would fail (07.10, a playground rebuilt while CS2 ran).
        return {'name': name, 'ok': True, 'cs_script': f'maps/scripts/mcp/{name}.vjs', 'source': str(path),
                'syntax_checked': syntax['checked'], 'compile_state': 'unchanged', 'log': None}
    if path.exists():
        backup = project.s.backups / 'scripts' / f'{name}-{time.time_ns()}.js'
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    path.write_text(code, encoding='utf-8', newline='\n')
    from . import compiler
    job = compiler.compile_assets(project.s, project.jobs, [path], force=True)
    result = project.job_status(job.id, 50, 0)
    compiled = project.s.game_addon / 'maps' / 'scripts' / 'mcp' / f'{name}.vjs_c'
    res = result.get('result') or {}
    out = {'name': name, 'ok': result['state'] == 'succeeded' and res.get('ok', False) and compiled.exists(),
           'cs_script': f'maps/scripts/mcp/{name}.vjs', 'source': str(path),
           'syntax_checked': syntax['checked'], 'compile_state': result['state']}
    if res.get('errors'):
        out['errors'] = res['errors'][:10]
    if result.get('log'):
        out['log'] = result['log']
    if result['state'] in ('failed', 'timeout', 'lost', 'cancelled') or (result['state'] == 'succeeded' and not out['ok']):
        out['error'] = 'script compilation failed; see errors and log'
    if pieces:
        out['pieces'] = pieces
    return out


# A runtime-only point_script supplies authoritative player state on any map.
# ent_create never changes the VMAP. The script and console callback live only
# until the next map load; no user entity is killed or overwritten.
RUNTIME_CODE = '''import { Instance } from "cs_script/point_script";
let rounds=0, resets=0, pickups=0;
Instance.OnRoundStart(()=>{rounds++;Instance.Msg("C_MCP_EVENT round_start "+rounds);});
Instance.OnPlayerReset(()=>{resets++;});
Instance.OnWeaponPickup(()=>{pickups++;});
Instance.RegisterCheatCommand("c_mcp_quality_probe",(token)=>{
 const players=[];
 const r=(x)=>Math.round(x*100)/100;
 const xyz=(v)=>v?{x:r(v.x),y:r(v.y),z:r(v.z)}:undefined;
 for(const p of Instance.GetAllPlayerControllers()) {
  if(!p.IsConnected()) continue;
  const pawn=p.GetPlayerPawn();
  players.push({slot:p.GetPlayerSlot(),bot:p.IsBot(),team:p.GetTeamNumber(),observing:p.IsObserving(),alive:!!pawn&&pawn.IsValid()&&pawn.IsAlive(),health:pawn&&pawn.IsValid()?pawn.GetHealth():0,noclip:pawn&&pawn.IsValid()?pawn.IsNoclipping():false,pos:pawn&&pawn.IsValid()?xyz(pawn.GetAbsOrigin()):undefined});
 }
 // One line per player: a console line holds ~2 KB, 20+ players in one JSON line were cut off.
 for(const p of players) Instance.Msg("C_MCP_STATEP "+token.trim()+" "+JSON.stringify(p));
 Instance.Msg("C_MCP_STATE "+token.trim()+" "+JSON.stringify({map:Instance.GetMapName(),rounds,resets,pickups,freeze:Instance.IsFreezePeriod(),warmup:Instance.IsWarmupPeriod(),count:players.length}));
});
'''


def runtime_script(s) -> str:
    """Prepare the small diagnostics script; compile only when its source changed."""
    from .jobs import JobManager
    from . import compiler
    rel = 'maps/scripts/mcp/c_mcp_runtime'
    source, compiled = s.content_addon / (rel + '.js'), s.game_addon / (rel + '.vjs_c')
    same = source.exists() and source.read_text(encoding='utf-8') == RUNTIME_CODE
    if same and compiled.exists() and compiled.stat().st_mtime >= source.stat().st_mtime:
        return rel + '.vjs'
    source.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        backup = s.backups / 'scripts' / f'c_mcp_runtime-{time.time_ns()}.js'
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
    source.write_text(RUNTIME_CODE, encoding='utf-8', newline='\n')
    jobs = JobManager(s.logs)
    job = compiler.compile_assets(s, jobs, [source], force=True, timeout=40)
    state = jobs.wait(job.id, 45)
    if state.state != 'succeeded' or not state.result.get('ok') or not compiled.exists():
        raise RuntimeError(f'cannot compile player diagnostics: {state}')
    return rel + '.vjs'


def add_script(project, m, op: dict, refs: dict) -> dict:
    name = _safe_name(str(op.get('name', '')))
    compiled = project.s.game_addon / 'maps' / 'scripts' / 'mcp' / f'{name}.vjs_c'
    if not compiled.exists():
        raise ValueError(f'script {name!r} is not compiled; call script_write first')
    kv = dict(op.get('keyvalues') or {})
    kv['cs_script'] = f'maps/scripts/mcp/{name}.vjs'
    if op.get('targetname'):
        kv['targetname'] = str(op['targetname'])
    # `// @fire <prefix> {...}` in the source: the shot engine's entities (HUD, beam rods, projectiles, sounds).
    source = project.s.content_addon / 'maps' / 'scripts' / 'mcp' / f'{name}.js'
    from . import fire as fire_engine, mechanics
    fires = mechanics.fire_directives(source.read_text(encoding='utf-8')) if source.is_file() else {}
    origin = list(op.get('origin') or [0, 0, 0])
    defs = [d for item in fires.values() for d in fire_engine.entity_defs(item['entities'], item['fire'], origin)]
    for _, _, ekv, _ in defs:
        if m.entities(targetname=ekv['targetname']):
            raise ValueError(f'entity {ekv["targetname"]!r} of @fire already exists in this map')
    if fires:
        from . import weapons
        weapons.ensure_hud(project)
        for item in fires.values():
            fire_engine.ensure_assets(project, item['fire'])
    ent = m.new_entity('point_script', kv, op.get('origin'), op.get('angles'), defaults=project.catalog.defaults('point_script'))
    m.attach(ent, project.resolve(m, op['parent'], refs) if op.get('parent') is not None else None)
    for cls, _, ekv, pos in defs:
        node = m.new_entity(cls, ekv, pos, [0, 0, 0], defaults=project.catalog.defaults(cls))
        m.attach(node)
    if op.get('ref'):
        refs[op['ref']] = ent['nodeID']
    return {'op': 'add_script', 'id': ent['nodeID'], 'name': name, 'cs_script': kv['cs_script'],
            **({'fire': {p: len(fire_engine.entity_defs(i['entities'], i['fire'], origin)) for p, i in fires.items()}}
               if fires else {}),
            **({'ref': op['ref']} if op.get('ref') else {})}


STEP_KINDS = {'cmd', 'say', 'fire', 'walk', 'go', 'press', 'wait', 'expect', 'shot', 'use', 'aim', 'hold', 'mouse', 'focus',
              'click'}
WAIT_AFTER = {'cmd', 'say', 'fire', 'press'}      # {"cmd": "...", "wait": 2}: seconds to wait after the action


def _step_kind(step) -> str:
    kinds = set(step) & STEP_KINDS if isinstance(step, dict) else set()
    if len(kinds) == 2 and 'wait' in kinds and kinds & WAIT_AFTER:
        kinds -= {'wait'}
    if len(kinds) != 1:
        raise ValueError(f'each step must contain exactly one action of {sorted(STEP_KINDS)} '
                         f'(cmd/say/fire/press may add "wait": seconds); got {step!r}'[:300])
    return next(iter(kinds))
# Console key commands of the keys a scenario may hold (the release list covers all of them).
INPUT_KEYS = {'attack': 'attack', 'attack2': 'attack2', 'use': 'use', 'forward': 'forward', 'back': 'back',
              'left': 'left', 'right': 'right', 'jump': 'jump', 'duck': 'duck', 'reload': 'reload',
              'walk': 'sprint', 'turnleft': 'turnleft', 'turnright': 'turnright'}      # CS2 names: +left is a strafe
RELEASE = ['-' + k for k in dict.fromkeys(INPUT_KEYS.values())]


def _test_steps(steps):
    """Validate all steps and regexes before acquiring the game or executing commands."""
    from .story import _number, _vector, entity_name
    if steps == 'auto':
        return steps
    if not isinstance(steps, list) or not 1 <= len(steps) <= 128:
        raise ValueError('steps must be auto or a list of 1..128 actions')
    for step in steps:
        kind = _step_kind(step)
        extra = {'within'} if kind == 'expect' else {'wait'} if kind in WAIT_AFTER else set()
        if set(step) - {kind, *extra}:
            raise ValueError(f'unknown fields in {kind} step')
        if kind in WAIT_AFTER and 'wait' in step:
            _number(step['wait'], 'wait', 0, 90)
        value = step[kind]
        if kind in ('walk', 'go'):
            _vector(value, 'walk')
        elif kind == 'wait':
            _number(value, 'wait', 0, 90)
        elif kind == 'expect':
            if not isinstance(value, str) or not 1 <= len(value) <= 256:
                raise ValueError('expect must be a regex with 1..256 characters')
            re.compile(value)
            _number(step.get('within', 5), 'within', 0, 90)
        elif kind in {'press', 'shot'}:
            entity_name(value)
        elif kind == 'use':
            if value is not True:
                raise ValueError('use must be true (E pressed for 0.15 s)')
        elif kind == 'aim':
            if isinstance(value, str):
                entity_name(value)
            else:
                _vector(value, 'aim')
        elif kind == 'hold':
            if not isinstance(value, dict) or set(value) - {'key', 'seconds'} or value.get('key') not in INPUT_KEYS:
                raise ValueError(f'hold must be {{"key": one of {sorted(INPUT_KEYS)}, "seconds": 0..30}}')
            _number(value.get('seconds', .2), 'hold.seconds', 0, 30)
        elif kind == 'mouse':
            if not isinstance(value, dict) or set(value) - {'dx', 'dy'}:
                raise ValueError('mouse must be {"dx": pixels, "dy": pixels}')
            for k in ('dx', 'dy'):
                _number(value.get(k, 0), 'mouse.' + k, -3000, 3000)
        elif kind == 'click':
            if (not isinstance(value, list) or len(value) != 2
                    or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1 for x in value)):
                raise ValueError('click is [x, y] in fractions of the game window (0..1; a HUD button in cursor mode)')
        elif kind == 'focus':
            if not isinstance(value, bool):
                raise ValueError('focus must be true (game window in front) or false (give focus back)')
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > 4096:
                raise ValueError(f'{kind} needs a non-empty short string')
            if kind == 'say' and any(c in value for c in '\n\r;"'):
                raise ValueError('say cannot contain newline/semicolon/quote')
            if kind == 'fire':
                parts = value.split()
                if len(parts) != 2:
                    raise ValueError('fire needs name input; use cmd for a parameter')
                for part in parts:
                    entity_name(part)
    return steps


def _script_errors(lines):
    out = []
    for i, ln in enumerate(lines):
        if 'annotation:' in ln.casefold():
            continue
        if 'Uncaught Exception' in ln:
            message = lines[i + 1] if i + 1 < len(lines) and not lines[i + 1].lstrip().startswith('at ') else ln
            if message not in out:
                out.append(message)
            continue
        if re.search(r'S_STORY_ERROR|(?:Type|Reference|Syntax|Range|Internal)Error|Invalid module specifier|Failed to (?:load|compile).*script', ln, re.I) or (
                re.search(r'\bError:', ln) and re.search(r'cs_script|\.v?js\b|script|Uncaught Exception',
                                                       '\n'.join(lines[max(0, i-2):i+4]), re.I)):
            if ln not in out:
                out.append(ln)
    return out


class _ConsoleTrace(list):
    # Core _session removes listeners by list equality. Two listeners receive
    # identical lines, so it can accidentally remove the long-lived test trace.
    # Identity equality keeps this local adapter safe without editing vconsole.
    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


def script_test(project, map: str, steps='auto', timeout: float = 90) -> dict:
    """Collect console continuously, including during loading, movement and HUD captures.

    The core console exposes only command-scoped output. A temporary listener on
    its existing connection is the area-local adapter for asynchronous errors.
    """
    import json
    import uuid
    from . import game, route, story, system, vconsole
    from .route import focus_game
    story._number(timeout, 'timeout', 1, 90)
    project.s.map_path(map)
    steps = _test_steps(steps)
    specs = story.map_stories(project, project.load(map)) if steps == 'auto' else []
    if steps == 'auto' and len(specs) != 1:
        raise ValueError('auto needs exactly one add_story in the map; use explicit steps otherwise')
    logpath = project.s.logs / f'script-test-{map}-{time.time_ns()}.json'
    focus_state = {}
    lines, checks, shots, operations = _ConsoleTrace(), [], [], []
    started = time.monotonic()
    deadline = started + timeout
    reason = None
    conn = None
    cleanup_commands = None

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f'script_test exceeded {timeout:g} seconds')
        return left

    def pause(seconds):
        limit = min(deadline, time.monotonic() + seconds)
        while time.monotonic() < limit:
            time.sleep(min(0.1, limit - time.monotonic()))
        remaining()

    def command(cmd, wait=0.25):
        # A bounded marker query avoids the 20-second quiet/stall fallback of run().
        token = 's_test_' + uuid.uuid4().hex[:10]
        acknowledged, found = vconsole.run_until(cmd.splitlines()+['echo '+token], token,
                                                 timeout=min(3, remaining()))
        if not acknowledged:
            raise RuntimeError('CS2 console did not acknowledge the test command')
        pause(min(wait, remaining()))

    def expect(pattern, within=5, cursor=0):
        begin = time.monotonic()
        limit = min(deadline, begin + within)
        regex = re.compile(pattern)
        while True:
            match = next((i for i, x in enumerate(lines[cursor:], cursor) if regex.search(x)), None)
            if match is not None:
                ok = True
                break
            if time.monotonic() >= limit:
                ok = False
                break
            time.sleep(min(0.05, max(0, limit - time.monotonic())))
        checks.append({'expect': pattern, 'ok': ok, 'seconds': round(time.monotonic() - begin, 2)})
        if not ok:
            raise RuntimeError(f'expectation did not match: {pattern}')
        return match + 1

    def shot(name, target=None, ang=None):
        remaining()
        prefix=os.environ.get('CS2MAP_AGENT','').lower()
        # 'd8_drone' already carries agent D's prefix (letter + round digits).
        if prefix and not re.match(re.escape(prefix) + r'[0-9]*_', name):
            name=prefix+'_'+name
        if target is not None:
            from .route import angles
            current = game._getpos_values(game.getpos(project.s))
            if current is None:
                raise RuntimeError('cannot aim stage frame: getpos returned no eye position')
            ang = angles(current[:3], target)
        # A test frame shows what the player sees now: no noclip/setpos, and without a target
        # no setang either, so scripted cameras (vehicles, drones) and freezes are not disturbed.
        view = {'name': name, 'keep': True}
        if ang is not None:
            view['ang'] = ang
        captured = project.game_screenshot(map=map, views=[view], hide_hud=False,
                                           deadline=deadline)
        shots.extend(captured['shots'])
        remaining()

    def hold(name, seconds):
        # The key is always released, whatever happens in between.
        cmd = INPUT_KEYS[name]
        try:
            command('+' + cmd, 0)
            pause(seconds)
        finally:
            command('-' + cmd, 0.1)

    def aim(target):
        # The client takes a server setang into its user commands about a second later (and only while in front).
        if isinstance(target, str):
            target = route.target_position(project.load(map), target)
        current = game._getpos_values(game.getpos(project.s))
        if current is None:
            raise RuntimeError('cannot aim: getpos returned no eye position')
        command('setang {} {} 0'.format(*route.angles(current[:3], target)[:2]), 1.0)

    def mouse(dx, dy):
        system.mouse_move(int(dx), int(dy))
        pause(.3)

    def click(at):
        hwnd = game._window('Counter-Strike 2').hwnd
        system.focus(hwnd)
        for _ in range(20):
            if system.foreground() == hwnd:
                break
            time.sleep(.05)
        else:                                # never click into another program's window
            raise RuntimeError('click: the CS2 window is not in front (another window holds the focus); not clicked')
        system.click_window(hwnd, at[0], at[1])
        pause(.3)

    def focus_window(on):
        if on:
            focus_state.setdefault('previous', system.foreground())
            system.focus(game._window('Counter-Strike 2').hwnd)
        elif focus_state.get('previous'):
            system.focus(focus_state.pop('previous'))

    def walk(pos):
        command('noclip 0', 0.15)
        command('setpos ' + ' '.join(str(float(c)) for c in pos), 0.4)

    try:
        # Acquiring the shared lock consumes the same budget as the scenario.
        with game.exclusive(wait=min(timeout, 30)), focus_game(project.s):
            game.ensure_alive(project.s)
            conn = vconsole._connection(vconsole.PORT)
            # New VConsole clients receive megabytes of old buffered messages.
            # Drain them through a token marker before starting this test's log.
            command('echo S_TEST_BEGIN', 0)
            with conn.cv:
                conn.listeners.append(lines)
            try:
                # Always load through the screenshot path. A fresh frame also proves a live player.
                spawn = next(iter(project.load(map).entities(classname='info_player_terrorist')), None)
                shot(map + '_test_start', ang=list(spawn['angles']) if spawn is not None else None)
                command('sv_cheats 1\ncl_drawhud 1')
                if steps == 'auto':
                    spec = specs[0][1]
                    name = spec['name']
                    # Reset first (a finished chapter keeps its final fade and text), then start where a
                    # player starts, so the first stage frames show the first room.
                    cursor = len(lines)
                    command(f'ent_fire {name}_script RunScriptInput TestReset')
                    if spawn is not None:
                        walk([float(c) for c in spawn['origin']])
                        shot(map+'_spawn_hud',ang=list(spawn['angles']))
                    for stage in spec['stages']:
                        ident = stage['id']
                        expect(r'S_STORY ' + re.escape(name) + r' stage ' + re.escape(ident) + r' start\b', 5, cursor)
                        from .route import stage_target
                        shot(f'{name}_{ident}_hud', target=stage_target(project.load(map), stage))
                        cond = stage['until']
                        action_cursor = cursor
                        if 'button' in cond:
                            command(f'ent_fire {cond["button"]} Press')
                        elif 'use' in cond:
                            from .route import angles
                            target=stage_target(project.load(map),stage)
                            current=game._getpos_values(game.getpos(project.s))
                            if current is None:
                                raise RuntimeError('use test needs a live eye position')
                            distance=math.hypot(current[0]-target[0],current[1]-target[1]) or 1
                            approach=[target[j]+(current[j]-target[j])/distance*48 for j in range(2)]
                            walk([*approach, target[2]-64])
                            current=game._getpos_values(game.getpos(project.s))
                            ang=angles(current[:3],target)
                            command('setang '+' '.join(map(str,ang))+'\n+use',.3)
                            command('-use',.15)
                        elif 'zone' in cond:
                            lo, hi = cond['zone']['mins'], cond['zone']['maxs']
                            # Leave/reenter explicitly; setpos is feet, never the eye position.
                            walk([lo[0] - 40, (lo[1] + hi[1]) / 2, lo[2] + 8])
                            walk([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2] + 8])
                        elif 'chat' in cond:
                            command('say ' + cond['chat'])
                        elif 'input' in cond:
                            command(f'ent_fire {name}_script RunScriptInput {cond["input"]}')
                        elif 'kills' in cond:
                            if cleanup_commands is None:
                                before = len(lines)
                                command('bot_quota\nbot_stop')
                                settings_lines = '\n'.join(lines[before:])
                                quota = re.search(r'bot_quota\s*=\s*(\d+)', settings_lines)
                                stop = re.search(r'bot_stop\s*=\s*(true|false|0|1)', settings_lines, re.I)
                                if not quota or not stop:
                                    raise RuntimeError('cannot snapshot bot settings before auto kills')
                                cleanup_commands = ('bot_quota ' + quota[1] + '\nbot_stop ' +
                                                    str(int(stop[1].casefold() in {'true', '1'})))
                            command('bot_quota 0\nbot_stop 1')
                            for _ in range(cond['kills']):
                                command('bot_add_ct', 0.8)
                                command(name + '_test_kill', 0.4)
                        elif 'pickup' in cond:
                            command('noclip 0', 0.15)
                            command(name + '_test_pickup ' + cond['pickup'], 0.5)
                        # time uses the real engine timer; do not force completion.
                        duration = cond.get('time', 0) + sum(a.get('seconds', 1 if 'fade' in a else 2 if 'camera' in a else 0)
                                                          for a in stage['then'] if 'fade' in a or 'camera' in a)
                        expect(r'S_STORY ' + re.escape(name) + r' stage ' + re.escape(ident) + r' done\b',
                               min(remaining(), max(5, duration + 3)), action_cursor)
                        operations.append({'stage': ident, 'ok': True})
                        # The next start may have occurred in the same tick as done.
                        cursor = action_cursor
                    expect(r'S_STORY ' + re.escape(name) + r' complete\b', 5, cursor)
                    shot(name + '_complete_hud')
                else:
                    cursor = len(lines)
                    command('\n'.join(RELEASE), 0.1)     # keys a previous run (or a person at the keyboard) left held
                    for i, step in enumerate(steps):
                        remaining()
                        key = _step_kind(step)
                        value = step[key]
                        if key in {'cmd', 'say', 'fire', 'press', 'walk', 'use', 'hold', 'aim', 'mouse', 'click'}:
                            cursor = len(lines)
                        if key == 'cmd':
                            command(value)
                        elif key == 'say':
                            command('say ' + value)
                        elif key == 'fire':
                            command('ent_fire ' + value)
                        elif key == 'press':
                            command('ent_fire ' + value + ' Press')
                        elif key == 'walk':
                            walk(value)
                        elif key == 'go':
                            from .route import Driver, _probe_source
                            _probe_source(project)
                            driver=Driver(project,deadline,lines)
                            driver.timeout,driver.map=timeout,map
                            command('noclip 0')
                            driver.install()
                            issue=driver.go({'go':value})
                            if issue:
                                raise RuntimeError(f'physical go failed: {issue} at {driver.state.get("pos")}')
                        elif key == 'wait':
                            pause(value)
                        elif key == 'expect':
                            cursor = expect(value, step.get('within', 5), cursor)
                        elif key == 'shot':
                            shot(value)
                        elif key == 'use':
                            hold('use', .15)
                        elif key == 'hold':
                            hold(value['key'], value.get('seconds', .2))
                        elif key == 'aim':
                            aim(value)
                        elif key == 'mouse':
                            mouse(value.get('dx', 0), value.get('dy', 0))
                        elif key == 'focus':
                            focus_window(value)
                        elif key == 'click':
                            click(value)
                        if key in WAIT_AFTER and 'wait' in step:
                            pause(step['wait'])
                        operations.append({'step': i, 'action': key, 'ok': True})
            finally:
                try:
                    vconsole.run_until([*RELEASE,'echo S_INPUT_RELEASED'],'S_INPUT_RELEASED',timeout=2)
                    if cleanup_commands:
                        token = 's_cleanup_' + uuid.uuid4().hex[:10]
                        restored, output = vconsole.run_until(cleanup_commands.splitlines()+['echo '+token],token,timeout=2)
                        if not restored:
                            raise RuntimeError('could not restore bot quota/stop after auto kills')
                finally:
                    with conn.cv:
                        conn.listeners.remove(lines)
    except Exception as exc:
        reason = str(exc)
    errors = _script_errors(lines)
    ok = reason is None and not errors and all(c['ok'] for c in checks)
    details = {'map': map, 'ok': ok, 'seconds': round(time.monotonic() - started, 2),
               'reason': reason, 'checks': checks, 'errors': errors, 'shots': shots,
               'steps': operations, 'console': lines}
    logpath.parent.mkdir(parents=True, exist_ok=True)
    logpath.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    # Bound model-facing text independently of log volume or stage count.
    out = {k: details[k] for k in ('ok', 'map', 'seconds')}
    if reason:
        out['reason'] = reason[:220]
    out.update({'checks': [{**c, 'expect': c['expect'][:100]} for c in checks[:8]],
                'check_count': len(checks), 'passed': sum(c['ok'] for c in checks),
                'errors': [x[:180] for x in errors[:3]], 'error_count': len(errors),
                'shots': [{'name': s['name'], 'path': s['path']} for s in shots[:6]],
                'shot_count': len(shots), 'log': str(logpath)})
    while len(json.dumps(out, ensure_ascii=False).encode('utf-8')) > 2048:
        if out['shots']:
            out['shots'].pop()
        elif out['checks']:
            out['checks'].pop()
        else:
            break
    return out

