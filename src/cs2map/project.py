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
MAX_REPORTED_FAILURES = 20      # a map_edit names at most this many failed operations
# Settings for one call that works on another addon (a release addon: game tools with map="<addon>/<map>").
ADDON_SETTINGS = ContextVar('cs2map_addon_settings', default=None)


class OpError(ValueError):
    pass


def _memory_outlook(vmap: Path) -> str | None:
    """A warning when the light bake may not fit in free RAM. Measured 26.10 on a 225 MB vmap (a town on terrain):
    resourcecompiler 6.9 GB + vrad3 8.4 GB at 2048, while CS2 still held 16 GB of the same map loaded earlier
    (a map change keeps that cache; only a restart frees it): 0.6 GB of 32 GB were left."""
    import psutil
    need = 2.0 + vmap.stat().st_size / 2**20 * 0.06            # GB
    free = psutil.virtual_memory().available / 2**30
    if free >= need * 1.2:
        return None
    cs2 = 0.0
    for proc in psutil.process_iter(['name', 'memory_info']):
        if (proc.info['name'] or '').lower() == 'cs2.exe' and proc.info['memory_info']:
            cs2 += proc.info['memory_info'].rss / 2**30
    text = f'the light bake needs about {need:.0f} GB of RAM and {free:.1f} GB are free'
    if cs2 > 4:
        text += (f'; CS2 holds {cs2:.0f} GB (a big map loaded earlier stays cached after a map change): ask the user '
                 'to restart CS2 Workshop Tools before the build, or the PC may stall while it bakes')
    return text


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
        # The map last saved by us, parsed from the bytes we wrote: (path, mtime_ns, size, document).
        # Handed out once by load() while the file is unchanged, so a batch of map_edit calls on a big map
        # reads and parses the file once instead of every call.
        self._saved_map: tuple | None = None
        # map_edit operations added by extensions: name -> fn(project, vmap, op, refs) -> dict
        self.extra_ops: dict[str, Any] = {}
        self.preprocessors: list = []
        from . import meshops, uv
        meshops.UNITS_PER_TEXEL = lambda material: uv.units_per_texel(self, material)

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
        from . import backups, mylayer
        try:
            out['backups'] = backups.summary(self.s)
        except OSError as e:
            out['backups'] = {'folder': str(self.s.backups), 'error': str(e)}
        try:
            cards = system.gpus()
            rt = [system.ray_tracing(c) for c in cards]
            out['gpu'] = {'names': cards, 'ray_tracing': True if any(rt) else (False if rt and all(x is False for x in rt) else None)}
            if out['gpu']['ray_tracing'] is False:
                out['gpu']['note'] = ('no GPU with hardware ray tracing found: baked light (map_build full/final, '
                                      'lighting_build) will fail; build with preset nav (bots, no baked light) or fast')
        except (OSError, AttributeError):
            pass
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
        saved, self._saved_map = self._saved_map, None      # one use only: callers may change the map
        if saved is not None and saved[0] == p:
            try:
                st = p.stat()
                if (st.st_mtime_ns, st.st_size) == saved[1:3]:
                    return VMap(saved[3], p)
            except OSError:
                pass
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
        self._saved_map = None
        out = m.save(target, backup_dir=self.s.backups / 'maps', settings=self.s)   # trimmed by the worker
        written, m.written = getattr(m, 'written', None), None
        if written is not None:
            st = target.stat()
            self._saved_map = (target, st.st_mtime_ns, st.st_size, written)
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
            if target.exists() and system.hammer_state(target)['open']:
                # map_create has no force (25.10: the shared message sent callers to a parameter it lacks).
                raise MapError(f'map {name} is open in Hammer: close it there first, or build the new version '
                               'under another name')
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

    def map_backups(self, name: str, pin: str | None = None) -> dict:
        """Copies of a map, newest first, with the backups folder; pin=<label> keeps the map as it is now and
        the current sources as a milestone that is never trimmed."""
        from . import backups
        check_name(name, 'map name')
        if pin:
            path = self._map_file(name)
            if not path.is_file():
                raise MapError(f'map {name} does not exist')
            if not isinstance(pin, str) or not 0 < len(pin.strip()) <= 80:
                raise MapError('pin: a label of 1-80 characters, e.g. "market square done"')
            copy = backups.copy_live(self.s, path, self.s.backups / 'maps', kind='pin', pin=pin.strip())
            return {'pinned': copy.name.split('.')[0], 'label': pin.strip(), **backups.listing(self.s, name, 5)}
        return backups.listing(self.s, name)

    def map_restore(self, name: str, backup_id: str, force: bool = False, sources: bool = False) -> dict:
        """Put a copy back (the current file is backed up first); sources=True also writes the sources of that
        moment into restored/<id>/ (the live folders are not touched)."""
        from . import backups, dmx
        check_name(name, 'map name')
        try:
            entry = backups.find(self.s, name, backup_id)
        except backups.BackupError as e:
            raise MapError(str(e)) from None
        with self._lock:
            m = VMap(dmx.loads(backups.read_copy(entry)), None)
            out = {'restored': backup_id, **self.save(m, name, force)}
        if sources:
            stamp = entry['meta'].get('sources')
            if not stamp:
                out['sources'] = 'this copy has no sources snapshot (made by 1.0 or without settings)'
            else:
                out['sources'] = backups.restore_sources(self.s, stamp)
        return out

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
            raise OpError(f'at most 2000 operations per call (got {len(operations)}): split the batch into calls of '
                          '2000 or fewer ("ref" names carry over to later calls; tools/mcp_call.py splits a big '
                          'map_edit itself)')
        with self._lock:
            m = self.load(name)
            refs: dict[str, int] = {}
            results = []
            batch = self._replace_batch(m, operations[0]) if isinstance(operations[0], dict) and \
                operations[0].get('op') == 'replace_batch' else None
            if batch:
                results.append(batch['result'])
                operations = operations[1:]
            token = DRY_RUN.set(bool(dry_run))       # operations that write resources skip them in a dry run
            failed: list[str] = []
            try:
                # Every operation runs even after a failure, so one call names all the broken ones (25.10: a batch
                # of 400 meshes took 8 runs, one error each). Nothing is saved when any fails.
                for i, op in enumerate(operations):
                    try:
                        result = self._apply(m, op, refs)
                        self._keep_ref(m, op, result)
                        results.append(result)
                    except (OpError, MeshError, MapError, KeyError, ValueError, TypeError) as e:
                        kind = op.get('op') if isinstance(op, dict) else repr(op)
                        ref = f' ref {op["ref"]}' if isinstance(op, dict) and op.get('ref') else ''
                        failed.append((f'#{i} ({kind}{ref})', str(e)))
                        if len(failed) >= MAX_REPORTED_FAILURES:
                            break
            finally:
                DRY_RUN.reset(token)
            if len(failed) == 1:
                raise OpError(f'operation {failed[0][0]} failed: {failed[0][1]}. Nothing was saved.')
            if failed:
                more = f' (stopped after {MAX_REPORTED_FAILURES})' if len(failed) >= MAX_REPORTED_FAILURES else ''
                raise OpError(f'{len(failed)} operations failed{more}; a later one may fail only because of an '
                              'earlier one: ' + ' | '.join(f'{w}: {e}' for w, e in failed) + '. Nothing was saved.')
            if batch:
                batch['result']['tagged'] = self._tag_batch(m, batch['tag'], batch['before'])
            out: dict[str, Any] = {'map': name, 'applied': len(results), 'results': results, 'refs': refs}
            if dry_run:
                m.to_bytes()   # full encode + parse check
                out['dry_run'] = True
            else:
                out['saved'] = self.save(m, name, force)
            out['summary'] = m.summary()
            return out

    REF_KEY = 'cs2map_ref'
    BATCH_KEY = 'cs2map_batch'

    def _replace_batch(self, m: VMap, op: dict) -> dict:
        """{"op": "replace_batch", "tag": "market"} as the FIRST operation: the nodes an earlier call made with this
        tag are removed, and every node this call makes gets it. One generator's part of a big map is redone in
        place instead of rebuilding the map from all its batches (18/23.10: 4 min for a change in one zone)."""
        tag = op.get('tag')
        if set(op) - {'op', 'tag'} or not isinstance(tag, str) or not re.fullmatch(r'\w{1,64}', tag):
            raise OpError('replace_batch: {"op": "replace_batch", "tag": "<letters, digits, _>"} as the first operation')
        removed = 0

        def prune(parent: Element) -> None:
            nonlocal removed
            kids = parent.get('children', [])
            keep = []
            for c in kids:
                if isinstance(c, Element) and c.get(self.BATCH_KEY) == tag:
                    removed += 1 + sum(1 for _ in m.iter_nodes(c))
                    continue
                keep.append(c)
                if isinstance(c, Element):
                    prune(c)
            if len(keep) != len(kids):
                parent['children'] = keep
        prune(m.world)
        m._node_ids = m._ref_ids = None
        before = {n['nodeID'] for n in m.iter_nodes() if 'nodeID' in n}
        return {'tag': tag, 'before': before, 'result': {'op': 'replace_batch', 'tag': tag, 'removed': removed}}

    def _tag_batch(self, m: VMap, tag: str, before: set) -> int:
        """Tag the roots of the subtrees this batch made (their children go with them)."""
        tagged = 0

        def walk(parent: Element, parent_new: bool) -> None:
            nonlocal tagged
            for c in parent.get('children', []):
                if not isinstance(c, Element):
                    continue
                new = 'nodeID' in c and c['nodeID'] not in before
                if new and not parent_new:
                    c.set(self.BATCH_KEY, 'string', tag)
                    tagged += 1
                walk(c, new)
        walk(m.world, False)
        return tagged

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
    def map_build(self, name: str, preset: str = 'fast', bots: bool | None = None, vis: bool | None = None) -> dict:
        self._map_file(name)
        if preset == 'release':                      # the map alone in its own addon, for the Workshop
            from . import release
            return release.start(self, name, bots=bots, vis=vis)
        if preset not in compiler.PRESETS:
            raise MapError(f'preset must be one of {sorted([*compiler.PRESETS, "release"])}')
        plan = self.build_plan(name, preset, bots, vis)
        released = self._release_compiled_map(name)
        job = compiler.build_map(self.s, self.jobs, name, preset, timeout=plan['timeout'], map_flags=plan['flags'])
        out = {'job': job.id, 'state': job.state, 'log': job.log,
               'hint': 'poll job_status(job, wait_seconds=25) until state is not running',
               **{k: plan[k] for k in ('bots', 'vis', 'nav', 'memory') if k in plan}}
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

    def build_plan(self, name: str, preset: str, bots: bool | None = None, vis: bool | None = None,
                   s=None) -> dict:
        """Flags, time limit and the honest nav/VIS outlook of a build, decided BEFORE it starts.

        bots None (auto): the preset's nav, dropped (with the reason) when the geometry estimate is over the nav
        limit and the preset bakes light (a nav-only build is cheap: it runs and the guard stops it after NAVGEN with
        the true count). vis None (auto): the preset's VIS, dropped on big open maps."""
        from . import navcost
        s = s or self.s
        base = compiler.PRESETS[preset]
        out: dict = {}
        wants_nav = bots is True or (bots is None and '-nav' in base)
        wants_vis = vis is True or (vis is None and '-vis' in base)
        m = VMap.load(s.map_path(name)) if wants_nav or wants_vis else None
        timeout = 1800.0
        if wants_nav:
            est = navcost.estimate(m, navcost.max_areas(s))
            out['nav'] = {k: est[k] for k in ('areas', 'level', 'message', 'fix', 'ai_data') if k in est}
            if est['level'] == 'too_slow' and bots is None and '-bakelighting' in base:
                bots = False
                out['bots'] = ('off: the nav estimate is over the limit, so this build skips bots instead of '
                               'risking hours of bot data after the light bake. Limit the nav and measure it with '
                               'map_build(name, "nav") (stops right after NAVGEN with the true count), or pass '
                               'bots=true to try anyway (the build still stops after NAVGEN over the limit)')
            elif est['level'] == 'too_slow':
                timeout += min(navcost.ai_data_seconds(navcost.max_areas(s))[2], 4 * 3600)
                out['bots'] = ('on, as a measurement: over the limit the build stops right after NAVGEN (minutes) and '
                               'reports the true area count')
            elif est['level'] == 'slow':
                timeout += min(navcost.ai_data_seconds(est['areas'])[2], 4 * 3600)
                out['bots'] = f'on: allow for the bot data step ({est["ai_data"]})'
        if wants_vis and vis is None:
            o = compiler.open_map(m)
            if not o['vis']:
                vis = False
                out['vis'] = (f'off: an open map of {o["size_m"][0]} x {o["size_m"][1]} m; VIS would take hours and '
                              'add nothing (frustum culling keeps the FPS); vis=true forces it')
        out['flags'] = compiler.flags(preset, bots, vis)
        if '-bakelighting' in out['flags']:
            memory = _memory_outlook(s.map_path(name))
            if memory:
                out['memory'] = memory
        if '-vis' in out['flags']:
            timeout += 1800
        out['timeout'] = min(timeout, 6 * 3600)
        return out

    def _release_compiled_map(self, name: str) -> bool:
        """CS2 keeps the .vpk of a loaded map open, so the compiler cannot overwrite it.
        If that is the case, disconnect the game (under the CS2 lock) and wait for the file."""
        import time
        vpk_file = self.s.compiled_map(name)

        def locked() -> bool:
            # Opening for writing succeeds on a loaded map: CS2 shares write, not delete (25.10: three full builds
            # failed at their final "Write ...vpk Failed!"). file_in_use asks for what the replace needs.
            if not vpk_file.exists():
                return False
            try:
                return system.file_in_use(vpk_file)
            except OSError:
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
        if job.state == 'running' and job.kind in ('build', 'release'):
            import datetime as _dt
            try:
                text = Path(job.log).read_text(encoding='utf-8', errors='replace')
                out['progress'] = {**compiler.progress(text), 'minutes': round(
                    (_dt.datetime.now() - _dt.datetime.fromisoformat(job.started)).total_seconds() / 60, 1),
                    **({'limit_minutes': round(job.timeout / 60)} if job.timeout else {})}
            except OSError:
                pass
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
        broken = []
        if ext == 'vmat':                          # a stock material whose texture the game lacks shows magenta
            broken = [x for x in stock if self.game.missing_textures(x)]
            stock = [x for x in stock if x not in broken]
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
                **({'broken_stock': broken, 'broken_note': 'their textures are missing in the game (magenta '
                    'checker): do not use them'} if broken else {}),
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

    CONSOLE_FULL = 60       # longer console output is summarised; the whole text goes to logs/

    def game_command(self, command: str, wait: float = 1.5, focus: bool = False) -> dict:
        wait = min(max(wait, 0.2), 20)
        if focus:
            with game.exclusive(), game.focused():
                return self._console_answer(command, game.command(self.s, command, wait))
        return self._console_answer(command, game.command(self.s, command, wait))

    def _console_answer(self, command: str, lines: list[str]) -> dict:
        """Short console output as it is; a long one (a map load prints ~300 lines, 23.10) as a summary: errors,
        script messages, the map and the tail, with the whole text in a log file."""
        if len(lines) <= self.CONSOLE_FULL:
            return {'output': lines}
        import time as _time
        log = self.s.logs / f'console-{_time.strftime("%Y%m%d-%H%M%S")}-{_time.time_ns() % 10**6}.txt'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('\n'.join(lines), encoding='utf-8')
        err = re.compile(r'\berror\b|\bfailed\b|exception|missing|not found|cannot|can\'t', re.I)
        script = re.compile(r'cs_script|point_script|\.vjs|S_[A-Z]+ |MCP_|\[script\]', re.I)
        loaded = re.compile(r'map_workshop|changelevel|spawngroup|Loading map|loaded spawngroup|Host_NewGame', re.I)
        def pick(rx, n):
            return [ln for ln in lines if rx.search(ln)][:n]
        return {'summary': {'lines': len(lines), 'errors': pick(err, 15), 'script': pick(script, 15),
                            'map': pick(loaded, 4), 'tail': lines[-8:]},
                'log': str(log), 'hint': 'long output summarised; the whole text is in log'}

    def game_screenshot(self, map: str | None = None, views: list[dict] | str | None = None, name: str | None = None,
                        pos: list[float] | None = None, ang: list[float] | None = None, hide_hud: bool = True,
                        window: str = 'game', deadline: float | None = None) -> dict:
        from .config import check_name
        shots = views if isinstance(views, list) and views else [{'name': name or 'view', 'pos': pos, 'ang': ang}]
        for v in shots:
            # Names become file names: "Mid-View 2" -> "mid_view_2" instead of a refusal (10.10).
            if isinstance(v, dict) and v.get('name'):
                v['name'] = re.sub(r'[^a-z0-9_]+', '_', str(v['name']).lower()).strip('_')[:64] or 'view'
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
        if result.get('rejected'):
            out['rejected'] = result['rejected']
            out['hint'] = ('some frames were rejected (listed); the others are fine. Retake the rejected views; a '
                           'Steam notification or the user at the game can spoil a frame')
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
