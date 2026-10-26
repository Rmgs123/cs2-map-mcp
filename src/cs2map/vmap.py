"""High-level VMAP document: entities, meshes, I/O connections, save with backup.

Every map node (entity, mesh, group) is identified by its Hammer `nodeID`,
which Hammer preserves, so ids returned by this module stay valid across
saves in Hammer as well.
"""
from __future__ import annotations

import os
import random
import uuid
from pathlib import Path
from typing import Any, Iterator, Sequence

from . import dmx
from .dmx import Element
from .mesh import Mesh

Vec3 = tuple[float, float, float]

ASSET_SUFFIXES = ('.vmat', '.vmdl', '.vsnd', '.vsndevts', '.vpcf', '.vsmart', '.vpost', '.vmap', '.vjs', '.vts',
                  '.vtex', '.vdata')
NODE_TYPES = ('CMapEntity', 'CMapMesh', 'CMapGroup', 'CMapPrefab', 'CMapSmartProp', 'CMapPath', 'CMapWorld',
              'CMapInstance')

DEFAULT_SKY = 'materials/skybox/sky_de_dust2.vmat'
TEMPLATES = {
    # name -> path relative to <cs2>/content ; 'empty' is derived from template_defuse
    'defuse': 'csgo/maps/templates/template_defuse.vmap',
    'hostage': 'csgo/maps/templates/template_hostage.vmap',
    'wingman': 'csgo/maps/templates/template_wingman.vmap',
}


class MapError(RuntimeError):
    pass


def _v3(v: Sequence[float] | None, default=(0.0, 0.0, 0.0)) -> Vec3:
    if v is None:
        return tuple(default)
    if len(v) != 3:
        raise MapError(f'expected 3 numbers, got {v!r}')
    return tuple(float(c) for c in v)


def _kv_string(value: Any) -> str:
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return f'{value:g}' if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return ' '.join(_kv_string(v) for v in value)
    return str(value)


class VMap:
    def __init__(self, doc: dmx.Document, path: Path | None = None):
        if doc.root.type != 'CMapRootElement':
            raise MapError(f'not a map: root is {doc.root.type}')
        self.doc = doc
        self.path = Path(path) if path else None
        self._node_ids: set[int] | None = None
        self._ref_ids: set[int] | None = None
        self.written: dmx.Document | None = None     # set by save(): the parsed copy of the bytes written

    # -- loading / creating ---------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> 'VMap':
        return cls(dmx.load(path), path)

    @classmethod
    def from_template(cls, cs2_path: Path, template: str = 'basic') -> 'VMap':
        """New map from a Valve template.

        empty   - nothing in the world (no light: the map will be dark)
        basic   - sky, sun, fog/cubemap and post-processing only
        defuse / hostage / wingman - Valve's gameplay templates (dev geometry, spawns, zones)
        Valve's templates reference a procedural sky that is not shipped; it is
        replaced by DEFAULT_SKY.
        """
        src = TEMPLATES.get(template, TEMPLATES['defuse'] if template in ('empty', 'basic') else None)
        if src is None:
            raise MapError(f'unknown template {template!r}; use empty, basic, ' + ', '.join(TEMPLATES))
        m = cls.load(Path(cs2_path) / 'content' / src)
        m.path = None
        if template in ('empty', 'basic'):
            keep = {'env_sky', 'light_environment', 'env_cubemap_fog', 'post_processing_volume'} \
                if template == 'basic' else set()
            m.world['children'] = [c for c in m.world['children'] if m.classname(c) in keep]
            m.doc.root['m_ReferencedMeshSnapshots'] = []
            m.doc.root['nodeInstanceData'] = []
            m._node_ids = m._ref_ids = None
        for sky in m.entities('env_sky'):
            sky['entity_properties'].set('skyname', 'string', DEFAULT_SKY)
        return m

    def _reassign_ids(self, nodes: list[Element]) -> None:
        """Give copied nodes fresh GUIDs/nodeIDs/referenceIDs."""
        inside = {id(e) for n in nodes for e in dmx.walk(n)}
        rest = [e for e in dmx.walk(self.doc.root) if id(e) not in inside]
        self._node_ids = {e['nodeID'] for e in rest if 'nodeID' in e and e.attrs['nodeID'].type == 'int'}
        self._ref_ids = {e['referenceID'] for e in rest if 'referenceID' in e}
        for n in nodes:
            for e in dmx.walk(n):
                e.id = uuid.uuid4()
                if 'nodeID' in e and e.attrs['nodeID'].type == 'int':
                    e['nodeID'] = self.next_node_id()
                if 'referenceID' in e:
                    e['referenceID'] = self.new_reference_id()

    def clone(self, node: Element) -> Element:
        """Deep copy of a node subtree with fresh ids (not attached)."""
        memo: dict[int, Element] = {}

        def copy(e: Element) -> Element:
            if id(e) in memo:
                return memo[id(e)]
            c = Element(e.type, e.name)
            memo[id(e)] = c
            for k, a in e.attrs.items():
                v = a.value
                if a.base == 'element':
                    v = [copy(x) if isinstance(x, Element) else x for x in v] if a.is_array else (
                        copy(v) if isinstance(v, Element) else v)
                elif a.is_array:
                    v = list(v)
                c.attrs[k] = dmx.Attr(a.type, v)
            return c

        dup = copy(node)
        for e in dmx.walk(dup):
            if 'nodeID' in e and e.attrs['nodeID'].type == 'int':
                e['nodeID'] = self.next_node_id()
            if 'referenceID' in e:
                e['referenceID'] = self.new_reference_id()
        return dup

    # -- structure --------------------------------------------------------------
    @property
    def world(self) -> Element:
        return self.doc.root['world']

    def iter_nodes(self, parent: Element | None = None) -> Iterator[Element]:
        """All map nodes (entities, meshes, groups...) below parent (default: world), depth-first."""
        stack = list(reversed((parent or self.world).get('children', [])))
        while stack:
            n = stack.pop()
            if not isinstance(n, Element):
                continue
            yield n
            stack.extend(reversed(n.get('children', [])))

    def parent_of(self, node: Element) -> Element | None:
        for n in [self.world, *self.iter_nodes()]:
            if any(c is node for c in n.get('children', [])):
                return n
        return None

    @staticmethod
    def classname(node: Element) -> str:
        p = node.get('entity_properties')
        return p.get('classname', '') if isinstance(p, Element) else ''

    def node(self, node_id: int) -> Element:
        for n in self.iter_nodes():
            if n.get('nodeID') == node_id:
                return n
        raise MapError(f'no node with id {node_id}')

    def entities(self, classname: str | None = None, targetname: str | None = None) -> list[Element]:
        out = []
        for n in self.iter_nodes():
            if n.type != 'CMapEntity':
                continue
            if classname and self.classname(n) != classname:
                continue
            if targetname and n['entity_properties'].get('targetname') != targetname:
                continue
            out.append(n)
        return out

    # -- ids ---------------------------------------------------------------------
    def node_ids(self) -> set[int]:
        if self._node_ids is None:
            self._node_ids = {e['nodeID'] for e in dmx.walk(self.doc.root) if 'nodeID' in e
                              and e.attrs['nodeID'].type == 'int'}
        return self._node_ids

    def next_node_id(self) -> int:
        ids = self.node_ids()
        nid = max(ids, default=0) + 1
        ids.add(nid)
        return nid

    def new_reference_id(self) -> int:
        if self._ref_ids is None:
            self._ref_ids = {e['referenceID'] for e in dmx.walk(self.doc.root) if 'referenceID' in e}
        while True:
            r = random.getrandbits(63) | (1 << 62)
            if r not in self._ref_ids:
                self._ref_ids.add(r)
                return r

    # -- creation ------------------------------------------------------------------
    def _node_common(self, el: Element) -> None:
        el.set('nodeID', 'int', self.next_node_id())
        el.set('referenceID', 'uint64', self.new_reference_id())
        el.set('children', 'element_array', [])
        el.set('variableTargetKeys', 'string_array', [])
        el.set('variableNames', 'string_array', [])

    @staticmethod
    def _transform(el: Element, origin: Vec3, angles: Vec3, scales: Vec3) -> None:
        el.set('origin', 'vector3', origin)
        el.set('angles', 'qangle', angles)
        el.set('scales', 'vector3', scales)
        el.set('transformLocked', 'bool', False)
        el.set('force_hidden', 'bool', False)
        el.set('editorOnly', 'bool', False)

    def new_entity(self, classname: str, keyvalues: dict[str, Any] | None = None, origin=None, angles=None,
                   scales=None, defaults: dict[str, str] | None = None) -> Element:
        """Create (but do not attach) a CMapEntity.  `defaults` are the FGD keyvalues."""
        kv = dict(keyvalues or {})
        origin = _v3(kv.pop('origin', None) if origin is None else origin)
        angles = _v3(kv.pop('angles', None) if angles is None else angles)
        scales = _v3(kv.pop('scales', None) if scales is None else scales, (1.0, 1.0, 1.0))
        kv.pop('origin', None), kv.pop('angles', None), kv.pop('scales', None)
        ent = Element('CMapEntity', '')
        self._node_common(ent)
        plugs = Element('DmePlugList', '')
        for name, t in (('names', 'string_array'), ('dataTypes', 'int_array'), ('plugTypes', 'int_array'),
                        ('descriptions', 'string_array')):
            plugs.set(name, t, [])
        ent.set('relayPlugData', 'element', plugs)
        ent.set('connectionsData', 'element_array', [])
        props = Element('EditGameClassProps', '')
        props.set('classname', 'string', classname)
        for k, v in (defaults or {}).items():
            props.set(k, 'string', _kv_string(v))
        for k, v in kv.items():
            props.set(k, 'string', _kv_string(v))
        ent.set('entity_properties', 'element', props)
        ent.set('hitNormal', 'vector3', (0.0, 0.0, 1.0))
        ent.set('isProceduralEntity', 'bool', False)
        self._transform(ent, origin, angles, scales)
        return ent

    def new_mesh(self, mesh: Mesh, origin: Vec3 | None = None, texture_size=(512, 512),
                 options: dict[str, Any] | None = None) -> Element:
        """Create a CMapMesh.  Mesh vertices are in world space; the node origin
        defaults to the bounding-box centre (like Hammer)."""
        local, centre = mesh.centered()
        if origin is not None:
            origin = _v3(origin)
            local = Mesh().extend(mesh, (-origin[0], -origin[1], -origin[2]))
        else:
            origin = centre
        el = Element('CMapMesh', '')
        self._node_common(el)
        o = {'cubeMapName': '', 'visexclude': False, 'disablemerging': False, 'renderwithdynamic': False,
             'disableHeightDisplacement': False, 'fademindist': -1.0, 'fademaxdist': 0.0, 'bakelighting': True,
             'renderToCubemaps': True, 'emissiveLightingEnabled': True, 'emissiveLightingBoost': 1.0,
             'disableShadows': 0, 'lightingDummy': False, 'keep_vertices': False, 'smoothingAngle': 40.0,
             'tintColor': (255, 255, 255, 255), 'renderAmt': 255, 'physicsType': 'default', 'physicsGroup': '',
             'physicsInteractsAs': '', 'physicsInteractsWith': '', 'physicsInteractsExclude': ''}
        types = {bool: 'bool', float: 'float', int: 'int', str: 'string', tuple: 'color'}
        unknown = set(options or {}) - set(o)
        if unknown:
            raise MapError(f'unknown mesh options {sorted(unknown)}; allowed: {sorted(o)}')
        o.update(options or {})
        for k, v in o.items():
            t = types[type(v)] if k != 'tintColor' else 'color'
            if t == 'float':
                v = float(v)
            el.set(k, t, tuple(v) if t == 'color' else v)
        el.set('meshData', 'element', local.to_dmx(origin=origin, texture_size=texture_size,
                                                   smoothing=float(o['smoothingAngle'])))
        el.set('physicsSimplificationOverride', 'bool', False)
        el.set('physicsSimplificationError', 'float', 0.0)
        el.set('lightGroup', 'string', '')
        el.set('precomputelightprobes', 'bool', True)
        self._transform(el, origin, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        return el

    def new_group(self, name: str = '', origin=(0, 0, 0)) -> Element:
        g = Element('CMapGroup', name)
        self._node_common(g)
        self._transform(g, _v3(origin), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        return g

    def attach(self, node: Element, parent: Element | None = None) -> Element:
        parent = parent or self.world
        parent['children'] = list(parent['children']) + [node]
        return node

    def remove(self, node: Element) -> None:
        parent = self.parent_of(node)
        if parent is None:
            raise MapError('node is not in this map')
        parent['children'] = [c for c in parent['children'] if c is not node]
        self._node_ids = None
        self._ref_ids = None

    # -- entity editing --------------------------------------------------------------
    def set_keyvalues(self, ent: Element, keyvalues: dict[str, Any]) -> None:
        props = ent['entity_properties']
        for k, v in keyvalues.items():
            if k in ('origin', 'angles', 'scales'):
                ent.set(k, 'qangle' if k == 'angles' else 'vector3', _v3(v))
            elif k == 'classname':
                props['classname'] = str(v)
            elif v is None:
                props.attrs.pop(k, None)
            else:
                props.set(k, 'string', _kv_string(v))

    def add_connection(self, ent: Element, output: str, target: str, input: str, param: str = '',
                       delay: float = 0.0, times: int = -1) -> Element:
        c = Element('DmeConnectionData', '')
        c.set('outputName', 'string', output)
        c.set('targetType', 'int', 7)   # 7 = by entity name (what Hammer writes)
        c.set('targetName', 'string', target)
        c.set('inputName', 'string', input)
        c.set('overrideParam', 'string', param)
        c.set('delay', 'float', float(delay))
        c.set('timesToFire', 'int', int(times))
        ent['connectionsData'] = list(ent['connectionsData']) + [c]
        return c

    # -- summaries -------------------------------------------------------------------
    def describe(self, node: Element, full: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {'id': node.get('nodeID'), 'type': node.type.removeprefix('CMap').lower()}
        if 'origin' in node:
            d['origin'] = [round(c, 2) for c in node['origin']]
        if node.type == 'CMapEntity':
            props = node['entity_properties']
            d['classname'] = props.get('classname')
            if props.get('targetname'):
                d['targetname'] = props['targetname']
            if any(c != 0 for c in node['angles']):
                d['angles'] = [round(c, 2) for c in node['angles']]
            if full:
                d['keyvalues'] = {k: a.value for k, a in props.attrs.items() if k != 'classname'}
                d['connections'] = [{'output': c['outputName'], 'target': c['targetName'], 'input': c['inputName'],
                                     'param': c['overrideParam'], 'delay': c['delay'], 'times': c['timesToFire']}
                                    for c in node['connectionsData']]
            else:
                for k in ('model', 'skyname', 'cs_script'):
                    if props.get(k):
                        d[k] = props[k]
        if node.type == 'CMapMesh':
            md = node['meshData']
            pos = md['vertexData']['streams'][0]['data']
            o = node['origin']
            if pos:
                lo = [round(min(p[i] for p in pos) + o[i], 2) for i in range(3)]
                hi = [round(max(p[i] for p in pos) + o[i], 2) for i in range(3)]
                d['bounds'] = [lo, hi]
            d['faces'] = len(md['faceEdgeIndices'])
            d['materials'] = list(md['materials'])
        if node.type == 'CMapGroup' and node.name:
            d['name'] = node.name
        kids = [c for c in node.get('children', []) if isinstance(c, Element)]
        if kids:
            d['children'] = [self.describe(c, full) for c in kids]
        return d

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for n in self.iter_nodes():
            key = self.classname(n) or n.type.removeprefix('CMap').lower()
            counts[key] = counts.get(key, 0) + 1
        return {'path': str(self.path) if self.path else None, 'format': f'vmap {self.doc.format_version}',
                'nodes': sum(counts.values()), 'counts': dict(sorted(counts.items()))}

    # -- asset references ---------------------------------------------------------------
    def asset_references(self) -> list[str]:
        refs: set[str] = set()
        for n in self.iter_nodes():
            if n.type == 'CMapMesh':
                refs.update(n['meshData']['materials'])
            props = n.get('entity_properties')
            if isinstance(props, Element):
                for a in props.attrs.values():
                    v = a.value
                    if isinstance(v, str) and v.lower().endswith(ASSET_SUFFIXES):
                        refs.add(v.replace('\\', '/'))
        return sorted(refs)

    def _update_prefix(self) -> None:
        if not self.doc.prefix:
            return
        p = self.doc.prefix[0]
        if 'map_asset_references' in p:
            p['map_asset_references'].value = self.asset_references()

    # -- saving ---------------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        return self._encode()[0]

    def _encode(self) -> tuple[bytes, dmx.Document]:
        self._update_prefix()
        data = dmx.dumps(self.doc)
        return data, dmx.loads(data)   # self-check: the file we write must parse

    def save(self, path: Path | None = None, backup_dir: Path | None = None, settings=None) -> dict[str, Any]:
        """Write the map (atomically).  An existing file is first backed up into backup_dir/<map>/ (compressed
        in the background, with a sources snapshot when `settings` is given: backups.py)."""
        from . import backups
        target = Path(path or self.path or '')
        if not target.name:
            raise MapError('no path to save to')
        data, written = self._encode()
        target.parent.mkdir(parents=True, exist_ok=True)
        job = backups.stage(target, Path(backup_dir)) if backup_dir is not None else None
        tmp = target.with_name(target.name + '.tmp')
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        backups.submit(job, settings)
        self.path = target
        # The self-check parse is exactly what loading the new file gives: keep it, so the next load of an
        # unchanged file can skip reading and parsing it again (Project.load).
        self.written = written
        return {'path': str(target), 'bytes': len(data), 'backup': job['stamp'] if job else None}
