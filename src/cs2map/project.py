"""Business operations behind the MCP tools.  No MCP imports here, so every
operation can be tested and scripted directly."""
from __future__ import annotations

from contextvars import ContextVar

import re
import threading
from functools import cached_property
from pathlib import Path
from typing import Any

from . import addon, compiler, fgd, game, materials, mesh, release, resource, shapes, system, vpk  # release: job recovery
from .config import Settings, check_name, ensure_inside, load_settings, writable_roots
from .dmx import Element
from .jobs import JobManager
from .mesh import MeshError
from .vmap import MapError, VMap

TRIGGER_CLASSES_PREFIX = ('trigger_',)
TRIGGER_CLASSES = {'func_buyzone', 'func_bomb_target', 'func_hostage_rescue', 'post_processing_volume',
                   'func_nav_blocker', 'func_nav_markup', 'env_cs_place', 'func_clip_vphysics', 'func_ladder',
                   'env_combined_light_probe_volume', 'env_light_probe_volume', 'env_cubemap_box',
                   'func_no_defuse', 'func_bomb_target_zone'}


DRY_RUN = ContextVar('cs2map_map_edit_dry_run', default=False)
# Settings for one call that works on another addon (a release addon: game tools with map="<addon>/<map>").
ADDON_SETTINGS = ContextVar('cs2map_addon_settings', default=None)


class OpError(ValueError):
    pass


IDLE_MAP = 'mcp_idle'
CORE_OPS = ('add_entity', 'add_brush_entity', 'add_mesh', 'update', 'move', 'delete', 'duplicate', 'connect',
            'set_material')



def _compact_shot(shot: dict) -> dict:
    """What a model needs from a capture: the PNG path (open it) and where the camera was."""
    out = {'name': shot.get('name'), 'path': shot.get('path')}
    pc = shot.get('player_check') or {}
    if pc.get('position'):
        out['eye'] = [round(c, 1) for c in pc['position']]
        out['ang'] = [round(c, 1) for c in pc.get('angles', [])]
    if shot.get('window'):
        out['window'] = shot['window']
    if shot.get('console') is not None:
        out['console'] = shot['console']
    return out

class Project:
    @property
    def s(self) -> Settings:
        return ADDON_SETTINGS.get() or self._s

    @s.setter
    def s(self, value: Settings) -> None:
        self._s = value

    def released(self, map: str | None):
        """For map="<addon>/<map>" (a map built with map_build(..., "release")): (settings token, map name)."""
        if not isinstance(map, str) or '/' not in map:
            return None, map
        import dataclasses
        from . import release
        addon_name, name = map.split('/', 1)
        check_name(addon_name, 'release addon')
        check_name(name, 'map name')
        rs = dataclasses.replace(self._s, addon=addon_name)
        if not (rs.content_addon / release.MARKER).is_file() or not rs.compiled_map(name).is_file():
            raise MapError(f'{map!r}: no release build of {name} in addon {addon_name}; run map_build({name!r}, "release")')
        return ADDON_SETTINGS.set(rs), name

    def __init__(self, settings: Settings | None = None):
        self.s = settings or load_settings()
        self.jobs = JobManager(self.s.logs)
        self._lock = threading.RLock()
        self._tex_cache: dict[str, tuple[int, int]] = {}
        # map_edit operations added by extensions: name -> fn(project, vmap, op, refs) -> dict
        self.extra_ops: dict[str, Any] = {}
        self.preprocessors: list = []

    def register_preprocessor(self, fn) -> None:
        """Rewrite every map_edit operation before it runs: fn(project, vmap, op, refs) -> op.
        For notations shared by all operations (e.g. positions relative to named rooms). Return the op
        unchanged when it has nothing to resolve; raise OpError with a clear message otherwise."""
        self.preprocessors.append(fn)

    def register_op(self, name: str, fn) -> None:
        """Add a map_edit operation.  fn(project, vmap, op: dict, refs: dict[str, int]) -> result dict.
        Use project.resolve(vmap, id_or_ref, refs) to find nodes; raise OpError on bad input.
        Set refs[op['ref']] = nodeID yourself if the op supports "ref"."""
        if name in self.extra_ops or name in CORE_OPS:
            raise ValueError(f'map_edit op {name!r} already exists')
        self.extra_ops[name] = fn

    def resolve(self, m: VMap, ref: Any, refs: dict[str, int]) -> Element:
        return self._resolve(m, ref, refs)

    # -- lazily loaded game data ----------------------------------------------------
    @cached_property
    def catalog(self) -> fgd.Catalog:
        return fgd.Catalog.load(self.s.cs2_path)

    @cached_property
    def game(self) -> vpk.GameFiles:
        return vpk.game_files(str(self.s.cs2_path))

    # -- status -------------------------------------------------------------------------
    def status(self) -> dict:
        """Environment check, compact: CS2 (+ Workshop Tools), addon, Blender, Node, Source2Viewer, jobs, crash."""
        from .config import BLENDER_SUPPORTED, blender_version
        from .scripting import _node
        st = addon.status(self.s)
        maps = st.pop('maps')
        folder = self.s.content_addon / 'maps'
        newest = sorted(maps, key=lambda m: (folder / f'{m}.vmap').stat().st_mtime)[-5:][::-1] if maps else []
        running = system.cs2_processes()
        for proc in running:
            proc.pop('cmdline', None)
        blender = self.s.blender_exe
        version = blender_version(blender) if blender.exists() else None
        lo, hi = BLENDER_SUPPORTED
        node = _node()
        from .atlas import VRF
        from . import __version__
        out = {'version': __version__,
               'cs2': {'path': str(self.s.cs2_path), 'found': self.s.cs2_exe.exists(),
                       'workshop_tools': self.s.resourcecompiler.exists(), 'running': running},
               'addon': {**st, 'maps': len(maps), 'newest': newest},
               'blender': {'path': str(blender), 'found': blender.exists(),
                           'version': '.'.join(map(str, version)) if version else None,
                           'supported': bool(version and lo <= version <= hi)},
               'node': str(node) if node else None,
               'source2viewer': VRF.is_file(),
               'jobs_running': self.jobs.running(), 'recent_jobs': self.jobs.recent(3)}
        from . import mylayer
        if mylayer.feedback_count():
            out['feedback'] = f'{mylayer.feedback_count()} entries in my/feedback.md (problems for the MCP author)'
        if running:
            try:
                out['cs2']['responding'] = not game.hung(self.s)
            except Exception:                      # a window listing failure must not break the status
                pass
            if out['cs2'].get('responding') is False:
                out['hint'] = 'CS2 is not responding: ask the user to restart CS2 Workshop Tools (never kill it)'
        if not any(p['mode'] == 'tools' for p in running):
            out['hint'] = ('start CS2 Workshop Tools with addon ' + self.s.addon + ' (Steam: CS2 -> Workshop Tools) '
                           'or call game_launch; game tools need it')
        return out

    # -- maps ------------------------------------------------------------------------------
    def _map_file(self, name: str) -> Path:
        return self.s.map_path(name)

    def load(self, name: str) -> VMap:
        p = self._map_file(name)
        if not p.exists():
            raise MapError(f'map {name!r} does not exist in addon {self.s.addon}; create it with map_create')
        return VMap.load(p)

    def _check_hammer(self, name: str, force: bool) -> dict:
        state = system.hammer_state(self._map_file(name))
        # Unsaved Hammer edits are never overwritten, not even with force=true.
        if state['open'] and state['dirty']:
            raise MapError(f'map {name} is open in Hammer with UNSAVED changes ({state["titles"][0]}). '
                           'Save or close it in Hammer first; writing now would lose those edits.')
        if state['open'] and not force:
            raise MapError(f'map {name} is open in Hammer. Close it there first (or pass force=true and then '
                           'reload it in Hammer without saving over the new file).')
        return state

    def save(self, m: VMap, name: str, force: bool = False) -> dict:
        target = self._map_file(name)
        ensure_inside(target, *writable_roots(self.s))
        hammer = self._check_hammer(name, force)
        out = m.save(target, backup_dir=self.s.backups / 'maps')
        if hammer['open']:
            out['warning'] = 'map is open in Hammer: reload it there before editing further'
        return out

    def map_create(self, name: str, template: str = 'basic', overwrite: bool = False, type: str | None = None) -> dict:
        from . import maptypes
        kind = type or ('competitive' if template in ('defuse', 'hostage', 'wingman') else 'custom')
        maptypes.check_type(kind)
        with self._lock:
            addon.ensure(self.s)
            target = self._map_file(name)
            if target.exists() and not overwrite:
                raise MapError(f'map {name} already exists (use overwrite=true to replace it; a backup is kept)')
            m = VMap.from_template(self.s.cs2_path, template)
            maptypes.store(m, kind)
            saved = self.save(m, name)
            return {'map': name, 'template': template, 'type': kind, **saved, **m.summary()}

    def map_list(self) -> dict:
        maps = []
        folder = self.s.content_addon / 'maps'
        for p in sorted(folder.glob('*.vmap')) if folder.is_dir() else []:
            vpk_file = self.s.compiled_map(p.stem)
            maps.append({'name': p.stem, 'bytes': p.stat().st_size,
                         'compiled': vpk_file.exists() and vpk_file.stat().st_mtime >= p.stat().st_mtime})
        return {'addon': self.s.addon, 'maps': maps}

    def map_info(self, name: str, classname: str | None = None, kind: str | None = None,
                 bounds: list | None = None, ids: list[int] | None = None, full: bool = False,
                 offset: int = 0, limit: int = 200) -> dict:
        m = self.load(name)
        nodes = []
        for n in m.world['children']:
            if not isinstance(n, Element):
                continue
            d = m.describe(n, full)
            if ids and d['id'] not in ids:
                continue
            if classname and d.get('classname') != classname:
                continue
            if kind and d['type'] != kind:
                continue
            if bounds and 'origin' in d:
                lo, hi = bounds
                if not all(lo[i] <= d['origin'][i] <= hi[i] for i in range(3)):
                    continue
            nodes.append(d)
        from . import maptypes
        return {**m.summary(), 'type': maptypes.stored(m), 'total_matching': len(nodes), 'offset': offset,
                'nodes': nodes[offset:offset + limit]}

    def map_backups(self, name: str) -> dict:
        folder = self.s.backups / 'maps' / name
        files = sorted(folder.glob('*.vmap'), reverse=True) if folder.is_dir() else []
        return {'map': name, 'backups': [{'id': f.stem, 'bytes': f.stat().st_size} for f in files[:50]]}

    def map_restore(self, name: str, backup_id: str, force: bool = False) -> dict:
        src = self.s.backups / 'maps' / name / f'{backup_id}.vmap'
        if not re.fullmatch(r'[\d-]+', backup_id) or not src.exists():
            raise MapError(f'no backup {backup_id} for map {name}; see map_backups')
        with self._lock:
            m = VMap.load(src)
            return {'restored': backup_id, **self.save(m, name, force)}

    # -- texture sizes (for correct UV scale) --------------------------------------------------
    def texture_size(self, material: str) -> tuple[int, int]:
        if material in self._tex_cache:
            return self._tex_cache[material]
        size = (512, 512)
        try:
            src = self.s.content_addon / material
            if src.exists():
                size = self._source_material_size(src) or size
            elif self.game.exists(material + '_c'):
                data = self.game.read(material + '_c')
                tex = resource.color_texture(resource.external_references(data))
                if tex and self.game.exists(tex + '_c'):
                    size = resource.texture_size(self.game.read(tex + '_c'))
        except Exception:
            pass
        self._tex_cache[material] = size
        return size

    def _source_material_size(self, vmat: Path) -> tuple[int, int] | None:
        from PIL import Image
        text = vmat.read_text(encoding='utf-8', errors='replace')
        m = re.search(r'TextureColor"\s+"([^"]+)"', text)
        if not m:
            return None
        img = self.s.content_addon / m.group(1)
        if img.exists():
            with Image.open(img) as im:
                return im.size
        return None

    # -- editing ---------------------------------------------------------------------------------
    def map_edit(self, name: str, operations: list[dict], dry_run: bool = False, force: bool = False) -> dict:
        """Apply a batch of operations atomically: all succeed and the map is saved once, or nothing changes."""
        if not isinstance(operations, list) or not operations:
            raise OpError('operations must be a non-empty list')
        if len(operations) > 2000:
            raise OpError('at most 2000 operations per call')
        with self._lock:
            m = self.load(name)
            refs: dict[str, int] = {}
            results = []
            token = DRY_RUN.set(bool(dry_run))       # operations that write resources skip them in a dry run
            try:
                for i, op in enumerate(operations):
                    try:
                        result = self._apply(m, op, refs)
                        self._keep_ref(m, op, result)
                        results.append(result)
                    except (OpError, MeshError, MapError, KeyError, ValueError, TypeError) as e:
                        raise OpError(f'operation #{i} ({op.get("op") if isinstance(op, dict) else op!r}) failed: '
                                      f'{e}. Nothing was saved.') from None
            finally:
                DRY_RUN.reset(token)
            out: dict[str, Any] = {'map': name, 'applied': len(results), 'results': results, 'refs': refs}
            if dry_run:
                m.to_bytes()   # full encode + parse check
                out['dry_run'] = True
            else:
                out['saved'] = self.save(m, name, force)
            out['summary'] = m.summary()
            return out

    REF_KEY = 'cs2map_ref'

    @classmethod
    def _keep_ref(cls, m: VMap, op: Any, result: Any) -> None:
        """A "ref" names its node in the map, so later map_edit calls can use "@name" too."""
        ref = op.get('ref') if isinstance(op, dict) else None
        if not ref or not isinstance(result, dict) or not isinstance(result.get('id'), int):
            return
        for node in m.iter_nodes():
            if node.get(cls.REF_KEY) == ref and node['nodeID'] != result['id']:
                raise OpError(f'ref {ref!r} is already used by node {node["nodeID"]} in this map; choose another name')
        m.node(result['id']).set(cls.REF_KEY, 'string', ref)

    def _resolve(self, m: VMap, ref: Any, refs: dict[str, int]) -> Element:
        if isinstance(ref, str) and ref.startswith('@'):
            if ref[1:] not in refs:
                named = [n for n in m.iter_nodes() if n.get(self.REF_KEY) == ref[1:]]
                if len(named) != 1:
                    raise OpError(f'unknown reference {ref}; set "ref" on an earlier operation')
                refs[ref[1:]] = named[0]['nodeID']
            ref = refs[ref[1:]]
        if isinstance(ref, str) and not ref.isdigit():
            ents = m.entities(targetname=ref)
            if len(ents) != 1:
                raise OpError(f'{len(ents)} entities named {ref!r}; use a numeric id')
            return ents[0]
        return m.node(int(ref))

    def _new_meshes(self, m: VMap, spec: dict, default_material: str, options: dict | None) -> list[Element]:
        els = []
        for piece in shapes.build(spec, default_material):
            els.append(m.new_mesh(piece, texture_size=self.texture_size, options=options))
        return els

    def _apply(self, m: VMap, op: dict, refs: dict[str, int]) -> dict:
        if not isinstance(op, dict) or 'op' not in op:
            raise OpError('each operation is an object with an "op" field')
        for fn in self.preprocessors:
            op = fn(self, m, op, refs)
        kind = op['op']
        ref = op.get('ref')
        if ref is not None and (not isinstance(ref, str) or not re.fullmatch(r'\w{1,64}', ref)):
            raise OpError('"ref" must be a short name of letters, digits, _')

        def remember(node: Element) -> dict:
            if ref:
                refs[ref] = node['nodeID']
            return {'op': kind, 'id': node['nodeID'], **({'ref': ref} if ref else {})}

        parent = self._resolve(m, op['parent'], refs) if op.get('parent') is not None else None

        if kind == 'add_entity':
            cls = op.get('classname')
            info = self.catalog.get(cls)
            if info.kind == 'SolidClass':
                raise OpError(f'{cls} is a brush entity; use add_brush_entity with a shape')
            kv = dict(op.get('keyvalues') or {})
            unknown = sorted(k for k in kv if k not in self.catalog.props(cls) and k not in ('origin', 'angles',
                                                                                              'scales'))
            ent = m.new_entity(cls, kv, op.get('origin'), op.get('angles'), op.get('scales'),
                               defaults=self.catalog.defaults(cls))
            m.attach(ent, parent)
            res = remember(ent)
            if unknown:
                res['warning'] = f'keys not in the FGD for {cls} (kept anyway): {unknown}'
            return res

        if kind == 'add_mesh':
            spec = op.get('shape') or {}
            if not isinstance(spec, dict):
                raise OpError('"shape" must be an object, e.g. {"shape":"box","mins":[..],"maxs":[..]}')
            extra = set(op) - {'op', 'ref', 'parent', 'shape', 'material', 'materials', 'options'}
            if extra:
                raise OpError(f'unknown add_mesh field(s) {sorted(extra)}; shape parameters go inside "shape"')
            if op.get('materials') and 'materials' not in spec:
                spec = {**spec, 'materials': op['materials']}   # per-face materials given next to "material"
            els = self._new_meshes(m, spec, op.get('material') or mesh.DEFAULT_MATERIAL, op.get('options'))
            for el in els:
                m.attach(el, parent)
            if ref:
                refs[ref] = els[0]['nodeID']
            return {'op': kind, 'ids': [e['nodeID'] for e in els], **({'ref': ref} if ref else {})}

        if kind == 'add_brush_entity':
            cls = op.get('classname')
            info = self.catalog.get(cls)
            if info.kind != 'SolidClass':
                raise OpError(f'{cls} is a point entity; use add_entity')
            spec = op.get('shape') or {}
            default_mat = (mesh.TOOL_MATERIALS['trigger'] if cls in TRIGGER_CLASSES or cls.startswith(
                TRIGGER_CLASSES_PREFIX) else mesh.DEFAULT_MATERIAL)
            els = self._new_meshes(m, spec, op.get('material') or default_mat, op.get('options'))
            lo = [min(e['origin'][i] for e in els) for i in range(3)]
            hi = [max(e['origin'][i] for e in els) for i in range(3)]
            centre = op.get('origin') or [(a + b) / 2 for a, b in zip(lo, hi)]
            kv = dict(op.get('keyvalues') or {})
            ent = m.new_entity(cls, kv, centre, op.get('angles'), None, defaults=self.catalog.defaults(cls))
            for el in els:
                m.attach(el, ent)
            m.attach(ent, parent)
            res = remember(ent)
            res['mesh_ids'] = [e['nodeID'] for e in els]
            return res

        if kind == 'update':
            node = self._resolve(m, op.get('id'), refs)
            if node.type != 'CMapEntity' and op.get('keyvalues'):
                raise OpError('keyvalues can only be set on entities')
            if op.get('keyvalues'):
                m.set_keyvalues(node, op['keyvalues'])
            for key, t in (('origin', 'vector3'), ('angles', 'qangle'), ('scales', 'vector3')):
                if op.get(key) is not None:
                    if node.type == 'CMapMesh' and key != 'origin':
                        raise OpError('meshes can only be moved (origin); rebuild them to change shape')
                    node.set(key, t, tuple(float(c) for c in op[key]))
            return remember(node)

        if kind == 'move':
            delta = [float(c) for c in op.get('offset') or []]
            if len(delta) != 3:
                raise OpError('move needs "offset": [dx, dy, dz]')
            ids = op.get('ids') or [op.get('id')]
            moved = []
            for i in ids:
                node = self._resolve(m, i, refs)
                for n in [node, *m.iter_nodes(node)]:
                    if 'origin' in n:
                        n['origin'] = tuple(a + b for a, b in zip(n['origin'], delta))
                moved.append(node['nodeID'])
            return {'op': kind, 'ids': moved}

        if kind == 'delete':
            ids = op.get('ids') or [op.get('id')]
            deleted = []
            for i in ids:
                node = self._resolve(m, i, refs)
                deleted.append(node['nodeID'])
                m.remove(node)
            return {'op': kind, 'ids': deleted}

        if kind == 'duplicate':
            node = self._resolve(m, op.get('id'), refs)
            delta = [float(c) for c in op.get('offset') or [0, 0, 0]]
            copies = []
            for _ in range(int(op.get('count', 1))):
                dup = m.clone(node)
                for n in [dup, *m.iter_nodes(dup)]:
                    if 'origin' in n:
                        base = n['origin']
                        n['origin'] = tuple(a + b * (len(copies) + 1) for a, b in zip(base, delta))
                m.attach(dup, m.parent_of(node))
                copies.append(dup['nodeID'])
            if ref:
                refs[ref] = copies[0]
            return {'op': kind, 'ids': copies, **({'ref': ref} if ref else {})}

        if kind == 'connect':
            src = self._resolve(m, op.get('from'), refs)
            if src.type != 'CMapEntity':
                raise OpError('"from" must be an entity')
            for key in ('output', 'target', 'input'):
                if not op.get(key):
                    raise OpError(f'connect needs "{key}"')
            outs = self.catalog.io(m.classname(src))[1]
            m.add_connection(src, op['output'], op['target'], op['input'], str(op.get('param', '')),
                             float(op.get('delay', 0)), int(op.get('times', -1)))
            res = {'op': kind, 'id': src['nodeID']}
            if op['output'] not in outs:
                res['warning'] = f'{op["output"]} is not a known output of {m.classname(src)}'
            return res

        if kind == 'set_material':
            node = self._resolve(m, op.get('id'), refs)
            targets = [n for n in [node, *m.iter_nodes(node)] if n.type == 'CMapMesh']
            if not targets:
                raise OpError('no mesh under this id')
            new = op.get('material')
            old = op.get('replace')
            for t in targets:
                md = t['meshData']
                md['materials'] = [new if (old is None or mt == old) else mt for mt in md['materials']]
            return {'op': kind, 'ids': [t['nodeID'] for t in targets]}

        if kind in self.extra_ops:
            return self.extra_ops[kind](self, m, op, refs)
        raise OpError(f'unknown op {kind!r}; ops: {", ".join((*CORE_OPS, *self.extra_ops))}')

    # -- building ----------------------------------------------------------------------------------
    def map_build(self, name: str, preset: str = 'fast') -> dict:
        self._map_file(name)
        if preset == 'release':                      # the map alone in its own addon, for the Workshop
            from . import release
            return release.start(self, name)
        released = self._release_compiled_map(name)
        job = compiler.build_map(self.s, self.jobs, name, preset)
        out = {'job': job.id, 'state': job.state, 'log': job.log,
               'hint': 'poll job_status(job, wait_seconds=25) until state is not running'}
        if released:
            out['note'] = f'the map was loaded in CS2; the game was switched to {IDLE_MAP} so it can be rebuilt'
        if preset in ('full', 'final'):
            from . import radar
            try:                                     # the radar is a bonus: it never fails a build
                made = radar.write(self, name)
                out['radar'] = made.get('radar') or made.get('skipped')
            except Exception as e:                    # noqa: BLE001
                out['radar'] = f'not made: {e}'
        return out

    def _release_compiled_map(self, name: str) -> bool:
        """CS2 keeps the .vpk of a loaded map open, so the compiler cannot overwrite it.
        If that is the case, disconnect the game (under the CS2 lock) and wait for the file."""
        import time
        vpk_file = self.s.compiled_map(name)

        def locked() -> bool:
            if not vpk_file.exists():
                return False
            try:
                with open(vpk_file, 'r+b'):
                    return False
            except PermissionError:
                return True

        if not locked():
            return False
        if game.session(self.s) is None:
            raise MapError(f'{vpk_file.name} is in use by another program; close it and rebuild')
        if name == IDLE_MAP:
            raise MapError(f'{IDLE_MAP} is loaded in CS2 and cannot be rebuilt now')
        # `disconnect` is refused from VConsole ("missing required FCVAR flag"): load a tiny idle map instead
        self._ensure_idle_map()
        with game.exclusive():
            game.load_map(self.s, IDLE_MAP)
        for _ in range(20):
            if not locked():
                return True
            time.sleep(0.5)
        raise MapError(f'{vpk_file.name} is still in use by CS2; load another map and retry')

    def _ensure_idle_map(self, preset: str = 'full') -> dict:
        """mcp_idle: the small cozy lobby (a rooftop terrace at sunset over a town) that CS2 shows when it starts
        without a map and switches to while another map is rebuilt. Built from data/idle/idle.json; rebuilt when
        that batch changes (its digest is kept in the map)."""
        import hashlib
        import json as _json
        import time
        batch = Path(__file__).parent / 'data' / 'idle' / 'idle.json'
        version = hashlib.sha1(batch.read_bytes()).hexdigest()[:12]
        made = False
        path = self._map_file(IDLE_MAP)
        current = self.load(IDLE_MAP).world.get('cs2map_idle') if path.exists() else None
        if current != version:
            self.map_create(IDLE_MAP, 'basic', overwrite=path.exists(), type='sandbox')
            self.map_edit(IDLE_MAP, _json.loads(batch.read_text(encoding='utf-8')))
            m = self.load(IDLE_MAP)
            m.world.set('cs2map_idle', 'string', version)
            self.save(m, IDLE_MAP)
            made = True
        vpk_file = self.s.compiled_map(IDLE_MAP)
        if made or not vpk_file.exists() or vpk_file.stat().st_mtime < path.stat().st_mtime:
            self._unload_idle(vpk_file)
            from . import preview, radar
            try:                                      # radar and loading-screen picture: bonuses, never failures
                radar.write(self, IDLE_MAP)
                if not (self.s.game_addon / f'panorama/images/map_icons/screenshots/1080p/{IDLE_MAP}_png.vtex_c').is_file():
                    preview.install(self, IDLE_MAP, str(batch.with_name('preview.jpg')))
            except Exception:                          # noqa: BLE001
                pass
            job = compiler.build_map(self.s, self.jobs, IDLE_MAP, preset)
            while self.jobs.get(job.id).state == 'running':
                time.sleep(0.5)
            return {'idle': IDLE_MAP, 'built': self.jobs.get(job.id).state}
        return {'idle': IDLE_MAP, 'built': 'up to date'}

    def _unload_idle(self, vpk_file: Path) -> None:
        """mcp_idle itself is loaded in CS2 (its .vpk is locked): switch to a blank service map first."""
        import time
        try:
            with open(vpk_file, 'r+b'):
                return
        except FileNotFoundError:
            return
        except PermissionError:
            pass
        blank = 'mcp_blank'
        if not self.s.compiled_map(blank).exists():
            if not self._map_file(blank).exists():
                self.map_create(blank, 'basic', type='sandbox')
                self.map_edit(blank, [
                    {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-256, -256, -16], 'maxs': [256, 256, 0]},
                     'material': 'materials/dev/dev_measuregeneric01.vmat'},
                    {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [-128, 0, 8]},
                    {'op': 'add_entity', 'classname': 'info_player_counterterrorist', 'origin': [128, 0, 8],
                     'angles': [0, 180, 0]}])
            job = compiler.build_map(self.s, self.jobs, blank, 'fast')
            while self.jobs.get(job.id).state == 'running':
                time.sleep(0.5)
        with game.exclusive():
            game.load_map(self.s, blank)
        for _ in range(20):
            try:
                with open(vpk_file, 'r+b'):
                    return
            except PermissionError:
                time.sleep(0.5)

    def job_status(self, job_id: str, wait_seconds: float = 0, log_lines: int = 15) -> dict:
        job = self.jobs.wait(job_id, min(max(wait_seconds, 0), 50)) if wait_seconds else self.jobs.get(job_id)
        out = {'job': job.id, 'title': job.title, 'state': job.state, 'exit_code': job.exit_code,
               'seconds': job.seconds, 'log': job.log, 'result': job.result}
        if log_lines:
            out['log_tail'] = self.jobs.tail(job_id, log_lines)
        return out

    def job_cancel(self, job_id: str) -> dict:
        job = self.jobs.cancel(job_id)
        return {'job': job.id, 'state': job.state}

    # -- assets ------------------------------------------------------------------------------------------
    def asset_search(self, query: str = '', kind: str = 'material', limit: int = 50) -> dict:
        ext = {'material': 'vmat', 'model': 'vmdl', 'texture': 'vtex', 'sound': 'vsnd', 'particle': 'vpcf',
               'smartprop': 'vsmart', 'map': 'vmap', 'any': None}.get(kind, kind)
        stock = self.game.search(query, ext, limit)
        own = []
        if self.s.content_addon.is_dir():
            words = query.lower().split()
            pattern = f'*.{ext}' if ext else '*'
            for p in self.s.content_addon.rglob(pattern):
                rel = p.relative_to(self.s.content_addon).as_posix()
                if '/history/' in rel:      # earlier versions kept by model_build, not for maps
                    continue
                if all(w in rel.lower() for w in words):
                    own.append(rel)
        return {'kind': kind, 'addon': sorted(own)[:limit], 'stock': stock,
                'note': 'use paths exactly as listed (e.g. materials/x.vmat) in maps'}

    def material_create(self, name: str, image: str | None = None, pattern: dict | None = None,
                        roughness: float = 0.7, metalness: float = 0.0, surface: str = 'concrete',
                        kind: str = 'opaque', options: dict | None = None) -> dict:
        """Create textures + .vmat in the addon and compile them (waits up to ~50 s)."""
        addon.ensure(self.s)
        options = dict(options or {})
        unknown = set(options) - {'tint', 'opacity', 'dirt', 'color', 'glow', 'mask_image', 'murk', 'flow'}
        if unknown:
            raise MapError(f'unknown material options {sorted(unknown)}; '
                           'use tint/opacity/dirt (glass), color/glow/mask_image (emissive), color/murk/flow (water)')
        made = materials.create(self.s, name, image, pattern, roughness, metalness, surface, kind=kind,
                                folder='mcp_decals' if kind == 'decal' else 'mcp', **options)
        self._tex_cache.pop(made['material'], None)
        job = compiler.compile_assets(self.s, self.jobs, [Path(made['vmat'])], force=True)
        st = self.job_status(job.id, 50, 0)
        made.update({'compile_job': job.id, 'compile_state': st['state'], 'compile_result': st['result']})
        if st['state'] == 'failed':
            made['error'] = 'material compile failed; see compile_result.errors and the job log'
        return made

    # -- game ---------------------------------------------------------------------------------------------
    def game_launch(self, name: str | None = None, width: int = 1600, height: int = 900,
                    open_in_hammer: str | None = None) -> dict:
        if open_in_hammer and not self._map_file(open_in_hammer).exists():
            raise MapError(f'map {open_in_hammer} does not exist')
        if name:
            vpk_file = self.s.compiled_map(name)
            if not vpk_file.exists():
                raise MapError(f'map {name} is not compiled yet; run map_build first')
            if vpk_file.stat().st_mtime < self._map_file(name).stat().st_mtime:
                note = 'the .vmap is newer than the compiled map; rebuild to see the latest changes'
            else:
                note = None
        else:
            note = None
        if not name and not open_in_hammer:
            self._ensure_idle_map()                  # a cozy first view instead of the main menu
            name = IDLE_MAP
        with game.exclusive():
            out = game.launch(self.s, name, width, height, hammer_map=open_in_hammer)
        if note:
            out['warning'] = note
        return out

    def game_command(self, command: str, wait: float = 1.5, focus: bool = False) -> dict:
        wait = min(max(wait, 0.2), 20)
        if focus:
            with game.exclusive(), game.focused():
                return {'output': game.command(self.s, command, wait)}
        return {'output': game.command(self.s, command, wait)}

    def game_screenshot(self, map: str | None = None, views: list[dict] | str | None = None, name: str | None = None,
                        pos: list[float] | None = None, ang: list[float] | None = None, hide_hud: bool = True,
                        window: str = 'game', deadline: float | None = None) -> dict:
        from .config import check_name
        shots = views if isinstance(views, list) and views else [{'name': name or 'view', 'pos': pos, 'ang': ang}]
        for v in shots:
            check_name(str(v.get('name') or 'view'), 'view name')
            if v.get('grep'):
                try:
                    re.compile(str(v['grep']))
                except re.error as e:
                    raise MapError(f'view {v.get("name")!r}: bad grep regex: {e}') from None
        if window == 'hammer':
            shot = game.screenshot(self.s, name or 'hammer', window='hammer', map_name=map)
            return {'shots': [_compact_shot(shot)]}
        if map:
            vpk_file = self.s.map_vpk(map)
            if not vpk_file.exists():
                raise MapError(f'map {map} is not compiled yet; run map_build first')
        if views == 'auto':
            if not map:
                raise MapError('views="auto" needs map')
            from .playtest import _auto_views
            shots = _auto_views(self, map)
        wants_preview = [str(v.get('name') or 'view') for v in shots if isinstance(v, dict) and v.pop('preview', False)]
        result = game.views(self.s, map, shots, hide_hud, deadline=deadline)
        out = {'map': result.get('map'), 'reloaded': result.get('reloaded'),
               'shots': [_compact_shot(x) for x in result.get('shots', [])]}
        if wants_preview:
            target = map or result.get('map')
            frame = next((x.get('path') for x in result.get('shots', []) if x.get('name') == wants_preview[0]), None)
            if target and frame:
                from . import preview
                out['preview'] = preview.install(self, target, frame)
        return out

    def hammer_open(self, name: str) -> dict:
        self._map_file(name)
        if not self._map_file(name).exists():
            raise MapError(f'map {name} does not exist')
        return {'output': game.hammer_open(self.s, name),
                'note': 'the map is now open in Hammer; map_edit refuses to write it until it is closed there '
                        '(or use force=true and reopen it in Hammer afterwards)'}

    def game_stop(self) -> dict:
        return game.stop(self.s)

    def entity_info(self, classname: str | None = None, search: str | None = None) -> dict:
        if classname:
            return self.catalog.describe(classname)
        hits = self.catalog.search(search or '', 60)
        return {'matches': [{'classname': n, 'kind': k, 'description': d[:160]} for n, k, d in hits]}
