"""Reusable map structure: groups, prefab instances, and projected decals.

The helpers in this module deliberately stay outside the project core.  VMAP
nodes are regular DMX elements, so groups and prefab instances can be built
without changing the binary codec or the core map-edit dispatcher.
"""
from __future__ import annotations

import math
import random
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from . import dmx, system
from .dmx import Element
from .mesh import Mesh
from .project import OpError
from .config import check_name, ensure_inside
from .vmap import MapError, VMap

Vec3 = tuple[float, float, float]


def _vec(value: Sequence[float] | None, field: str, default: Vec3 = (0.0, 0.0, 0.0)) -> Vec3:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OpError(f'{field} must be [x, y, z]')
    try:
        out = tuple(float(x) for x in value)
    except (TypeError, ValueError):
        raise OpError(f'{field} must contain numbers') from None
    if not all(math.isfinite(x) for x in out):
        raise OpError(f'{field} must contain finite numbers')
    return out


def _positive_size(value: Any) -> tuple[float, float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        w = h = float(value)
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            w, h = (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            raise OpError('size must be a positive number or [width, height]') from None
    else:
        raise OpError('size must be a positive number or [width, height]')
    if not all(math.isfinite(x) and x > 0 and x <= 32768 for x in (w, h)):
        raise OpError('size must be between 0 and 32768 units')
    return w, h


def _unit(v: Sequence[float], field: str) -> Vec3:
    x, y, z = _vec(v, field)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-8:
        raise OpError(f'{field} must not be zero')
    return x / length, y / length, z / length


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _scale(v: Vec3, n: float) -> Vec3:
    return v[0] * n, v[1] * n, v[2] * n


def _add(a: Vec3, b: Vec3) -> Vec3:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _rotate(v: Vec3, angles: Vec3) -> Vec3:
    """Rotate a vector by Source-style pitch/yaw/roll degrees.

    Source qangles use pitch around Y, yaw around Z, and roll around X.  The
    order below is the conventional Rz(yaw) * Ry(pitch) * Rx(roll) order used
    for map transforms; it is sufficient for local prefab placement and the
    normal derived by ``add_decal``.
    """
    pitch, yaw, roll = (math.radians(a) for a in angles)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    # Rx(roll)
    x1, y1, z1 = v[0], cr * v[1] - sr * v[2], sr * v[1] + cr * v[2]
    # Ry(pitch)
    x2, y2, z2 = cp * x1 + sp * z1, y1, -sp * x1 + cp * z1
    # Rz(yaw)
    return cy * x2 - sy * y2, sy * x2 + cy * y2, z2


def _normal_from_angles(angles: Vec3) -> Vec3:
    return _unit(_rotate((0.0, 0.0, 1.0), angles), 'normal')


def _prefab_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise OpError('prefab name must be a-z0-9_')
    value = name.removesuffix('.vmap')
    if '/' in value or '\\' in value or ':' in value:
        raise OpError('prefab name must be a simple a-z0-9_ name')
    if value.startswith('e_'):
        value = value[2:]
    return 'e_' + check_name(value, 'prefab name')


def _prefab_path(project, name: str) -> Path:
    stem = _prefab_name(name)
    return ensure_inside(project.s.content_addon / 'maps' / 'prefabs' / f'{stem}.vmap', project.s.content_addon)


def _prefab_rel(project, path: Path) -> str:
    return path.resolve().relative_to(project.s.content_addon.resolve()).as_posix()


def _resolve(vmap: VMap, value: Any, refs: dict[str, int] | None = None) -> Element:
    if isinstance(value, str) and value.startswith('@'):
        if not refs or value[1:] not in refs:
            raise OpError(f'unknown reference {value}')
        value = refs[value[1:]]
    try:
        node = vmap.node(int(value))
    except (TypeError, ValueError, MapError):
        raise OpError(f'no map node with id {value!r}') from None
    return node


def _direct_nodes(vmap: VMap, ids: Iterable[Any], refs: dict[str, int] | None = None) -> list[Element]:
    if not isinstance(ids, (list, tuple)) or not ids:
        raise OpError('ids must be a non-empty list')
    out: list[Element] = []
    seen: set[int] = set()
    for value in ids:
        n = _resolve(vmap, value, refs)
        if n['nodeID'] in seen:
            raise OpError(f'duplicate node id {n["nodeID"]}')
        seen.add(n['nodeID'])
        if n.type == 'CMapWorld':
            raise OpError('the world node cannot be grouped or saved as a prefab')
        out.append(n)
    return out


def _set_group_defaults(group: Element) -> None:
    # These fields are present on current Valve maps and make a generated group
    # behave like one created in Hammer.  They are harmless on older versions.
    group.set('customVisGroup', 'string', '')
    group.set('randomSeed', 'int', random.randint(1, 2_147_483_647))
    group.set('deformationMode', 'int', 1)
    pin = Element('DmElement', 'transformPin')
    pin.set('referenceName', 'string', '')
    pin.set('targetReferenceID', 'uint64', 0)
    pin.set('offsetOrigin', 'vector3', (0.0, 0.0, 0.0))
    pin.set('offsetAngles', 'qangle', (0.0, 0.0, 0.0))
    pin.set('pinAngles', 'bool', True)
    pin.set('twoWay', 'bool', False)
    group.set('transformPin', 'element', pin)


def _bounds(nodes: Iterable[Element]) -> tuple[Vec3, Vec3] | None:
    points: list[Vec3] = []
    for node in nodes:
        stack = [node]
        while stack:
            n = stack.pop()
            if 'origin' in n:
                origin = tuple(float(x) for x in n['origin'])
                if n.type in ('CMapMesh', 'CMapStaticOverlay') and isinstance(n.get('meshData'), Element):
                    positions = n['meshData']['vertexData']['streams'][0]['data']
                    points.extend((_add(origin, tuple(float(x) for x in p)) for p in positions))
                else:
                    points.append(origin)
            stack.extend(x for x in n.get('children', []) if isinstance(x, Element))
    if not points:
        return None
    return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))


def _shift(node: Element, offset: Vec3) -> None:
    stack = [node]
    while stack:
        n = stack.pop()
        if 'origin' in n:
            n['origin'] = _add(tuple(n['origin']), offset)
        stack.extend(x for x in n.get('children', []) if isinstance(x, Element))


def group_nodes(vmap: VMap, ids: list[Any], name: str = '', refs: dict[str, int] | None = None) -> dict[str, Any]:
    nodes = _direct_nodes(vmap, ids, refs)
    parents = [vmap.parent_of(n) for n in nodes]
    if not parents or parents[0] is None or any(p is not parents[0] for p in parents):
        raise OpError('all grouped nodes must be direct children of the same parent')
    parent = parents[0]
    assert parent is not None
    children = list(parent.get('children', []))
    indices = [children.index(n) for n in nodes]
    b = _bounds(nodes)
    centre = tuple((a + z) / 2 for a, z in zip(*b)) if b else (0.0, 0.0, 0.0)
    group = vmap.new_group(name=name, origin=centre)
    _set_group_defaults(group)
    group['children'] = nodes
    selected = {id(n) for n in nodes}
    kept = [n for n in children if id(n) not in selected]
    insert_at = min(indices)
    kept.insert(insert_at, group)
    parent['children'] = kept
    return {'op': 'group', 'id': group['nodeID'], 'name': name, 'children': [n['nodeID'] for n in nodes]}


def ungroup_node(vmap: VMap, value: Any, refs: dict[str, int] | None = None) -> dict[str, Any]:
    group = _resolve(vmap, value, refs)
    if group.type != 'CMapGroup':
        raise OpError('ungroup id must refer to CMapGroup')
    if any(n.type == 'CMapInstance' and n.get('target') is group for n in vmap.iter_nodes()):
        raise OpError('cannot ungroup a source group referenced by CMapInstance')
    parent = vmap.parent_of(group)
    if parent is None:
        raise OpError('group is not attached to the map')
    siblings = list(parent.get('children', []))
    index = siblings.index(group)
    kids = [x for x in group.get('children', []) if isinstance(x, Element)]
    siblings[index:index + 1] = kids
    group['children'] = []
    parent['children'] = siblings
    return {'op': 'ungroup', 'id': group['nodeID'], 'ids': [n['nodeID'] for n in kids]}


def _minimal_prefab(project, source: VMap, nodes: list[Element], name: str, centre: Vec3) -> VMap:
    for root in nodes:
        hierarchy = set()
        stack = [root]
        while stack:
            node = stack.pop()
            hierarchy.add(id(node))
            stack.extend(x for x in node.get('children', []) if isinstance(x, Element))
        if any('nodeID' in e and id(e) not in hierarchy for e in dmx.walk(root)):
            raise OpError('prefab selection contains a reference outside its child hierarchy')
    # Start with a valid Valve map header/world and remove all scene nodes.
    target = VMap.from_template(project.s.cs2_path, 'empty')
    target.world['children'] = []
    target.doc.root['isprefab'] = True
    if 'itemFile' in target.doc.root:
        target.doc.root['itemFile'] = ''
    for key in ('m_ReferencedMeshSnapshots', 'nodeInstanceData'):
        if key in target.doc.root:
            target.doc.root[key] = []
    group = target.new_group(name=name, origin=(0.0, 0.0, 0.0))
    _set_group_defaults(group)
    clones = [source.clone(n) for n in nodes]
    for clone in clones:
        _shift(clone, _scale(centre, -1.0))
    group['children'] = clones
    target._reassign_ids([group])
    target.attach(group)
    return target


def prefab_save(project, map_name: str, ids: list[Any], name: str) -> dict[str, Any]:
    source = project.load(map_name)
    nodes = _direct_nodes(source, ids)
    parents = [source.parent_of(n) for n in nodes]
    if any(p is None for p in parents) or any(p is not parents[0] for p in parents):
        raise OpError('prefab nodes must be direct children of the same parent')
    bounds = _bounds(nodes)
    centre = tuple((a + b) / 2 for a, b in zip(*bounds)) if bounds else (0.0, 0.0, 0.0)
    stem = _prefab_name(name)
    path = _prefab_path(project, stem)
    if system.hammer_state(path)['open']:
        raise MapError(f'prefab {stem} is open in Hammer; close it before saving')
    path.parent.mkdir(parents=True, exist_ok=True)
    target = _minimal_prefab(project, source, nodes, stem, centre)
    saved = target.save(path, backup_dir=project.s.backups / 'maps' / 'prefabs', settings=project.s)
    return {'ok': True, 'prefab': _prefab_rel(project, path), 'name': stem,
            'source_map': map_name, 'source_ids': [n['nodeID'] for n in nodes],
            'origin': [round(x, 4) for x in centre], 'nodes': len(nodes), **saved}


def _load_prefab(project, value: str) -> tuple[Path, VMap, Element]:
    if not isinstance(value, str) or not value.strip():
        raise OpError('prefab must be a name or maps/prefabs/e_name.vmap path')
    if '/' not in value and '\\' not in value:
        path = _prefab_path(project, value)
    else:
        rel = value.replace('\\', '/')
        rel_path = PurePosixPath(rel)
        if rel_path.is_absolute() or '..' in rel_path.parts or ':' in rel:
            raise OpError('prefab path must stay inside maps/prefabs/')
        try:
            path = ensure_inside(project.s.content_addon / rel, project.s.content_addon)
        except Exception as e:
            raise OpError(f'invalid prefab path: {e}') from None
    folder = (project.s.content_addon / 'maps' / 'prefabs').resolve()
    if path.suffix.casefold() != '.vmap' or path.parent.resolve() != folder or not path.stem.startswith('e_'):
        raise OpError('prefab path must be maps/prefabs/e_name.vmap')
    if not path.is_file():
        raise OpError(f'prefab does not exist: {_prefab_rel(project, path)}')
    try:
        pmap = VMap.load(path)
    except Exception as e:
        raise OpError(f'cannot read prefab: {e}') from None
    groups = [n for n in pmap.world.get('children', []) if isinstance(n, Element) and n.type == 'CMapGroup']
    if not groups:
        raise OpError('prefab has no CMapGroup root')
    return path, pmap, groups[0]


def _new_instance(vmap: VMap, target: Element, origin: Vec3, angles: Vec3, scales: Vec3) -> Element:
    inst = Element('CMapInstance', '')
    vmap._node_common(inst)
    plugs = Element('DmePlugList', '')
    for key, typ in (('names', 'string_array'), ('dataTypes', 'int_array'), ('plugTypes', 'int_array'),
                     ('descriptions', 'string_array')):
        plugs.set(key, typ, [])
    inst.set('relayPlugData', 'element', plugs)
    inst.set('connectionsData', 'element_array', [])
    inst.set('target', 'element', target)
    vmap._transform(inst, origin, angles, scales)
    inst.set('transformPin', 'element', _transform_pin())
    inst.set('customVisGroup', 'string', '')
    inst.set('randomSeed', 'int', random.randint(1, 2_147_483_647))
    inst.set('tintColor', 'color', (255, 255, 255, 255))
    inst.set('visexclude', 'bool', False)
    return inst


def _transform_pin() -> Element:
    pin = Element('DmElement', 'transformPin')
    pin.set('referenceName', 'string', '')
    pin.set('targetReferenceID', 'uint64', 0)
    pin.set('offsetOrigin', 'vector3', (0.0, 0.0, 0.0))
    pin.set('offsetAngles', 'qangle', (0.0, 0.0, 0.0))
    pin.set('pinAngles', 'bool', True)
    pin.set('twoWay', 'bool', False)
    return pin


def add_prefab(project, vmap: VMap, op: dict[str, Any], refs: dict[str, int]) -> dict[str, Any]:
    path, pmap, source = _load_prefab(project, op.get('prefab'))
    origin = _vec(op.get('origin'), 'origin')
    angles = _vec(op.get('angles'), 'angles')
    raw_scale = op.get('scale', 1.0)
    if isinstance(raw_scale, (list, tuple)):
        scales = _vec(raw_scale, 'scale')
    else:
        try:
            s = float(raw_scale)
        except (TypeError, ValueError):
            raise OpError('scale must be a positive number or [x, y, z]') from None
        scales = (s, s, s)
    if not all(math.isfinite(x) and x > 0 and x <= 100 for x in scales):
        raise OpError('scale must be between 0 and 100')
    inline = op.get('inline', False)
    if not isinstance(inline, bool):
        raise OpError('inline must be a boolean')
    if inline and (angles != (0.0, 0.0, 0.0) or scales != (1.0, 1.0, 1.0)):
        raise OpError('inline placement currently supports translation only; use angles=[0,0,0], scale=1')
    cloned = pmap.clone(source)
    # The prefab source group is local to its origin.  Give it fresh map IDs
    # before it is attached to the destination document.
    vmap._reassign_ids([cloned])
    if inline:
        _shift(cloned, origin)
        vmap.attach(cloned)
        out = {'op': 'add_prefab', 'id': cloned['nodeID'], 'prefab': _prefab_rel(project, path),
               'inline': True}
    else:
        # CMapInstance targets a local CMapGroup, exactly as Valve's maps do.
        # Keep the source group at local zero; the instance transform places it.
        cloned['origin'] = (0.0, 0.0, 0.0)
        cloned['angles'] = (0.0, 0.0, 0.0)
        vmap.attach(cloned)
        inst = _new_instance(vmap, cloned, origin, angles, scales)
        vmap.attach(inst)
        out = {'op': 'add_prefab', 'id': inst['nodeID'], 'source_id': cloned['nodeID'],
               'prefab': _prefab_rel(project, path), 'inline': False}
    if op.get('ref'):
        refs[op['ref']] = out['id']
        out['ref'] = op['ref']
    return out


def _material_path(project, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpError('material is required')
    value = value.replace('\\', '/')
    p = PurePosixPath(value)
    # Valve keeps some kit materials beside their models (models/.../*.vmat); meshes can use them too.
    if p.is_absolute() or '..' in p.parts or ':' in value or not value.startswith(('materials/', 'models/'))             or p.suffix != '.vmat':
        raise OpError('material must be a relative materials/... .vmat path')
    src = ensure_inside(project.s.content_addon / value, project.s.content_addon)
    if src.is_file():
        return value
    try:
        if project.game.exists(value + '_c'):
            return value
    except Exception:
        pass
    raise OpError(f'material does not exist: {value}; use asset_search(kind="material")')


def _decal_mesh(origin: Vec3, normal: Vec3, size: tuple[float, float], material: str,
                texture_size: tuple[int, int] = (1, 1)) -> Mesh:
    # Choose a stable tangent frame.  ``u x v`` is the requested outward normal.
    ref = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(ref, normal), 'decal tangent')
    v = _unit(_cross(normal, u), 'decal bitangent')
    hw, hh = size[0] / 2, size[1] / 2
    pts = [_add(origin, _add(_scale(u, -hw), _scale(v, -hh))),
           _add(origin, _add(_scale(u, hw), _scale(v, -hh))),
           _add(origin, _add(_scale(u, hw), _scale(v, hh))),
           _add(origin, _add(_scale(u, -hw), _scale(v, hh)))]
    out = Mesh()
    # Fit the complete image to this quad, independently of its world position.
    image_v = _scale(v, -1.0)
    tw, th = texture_size
    scales = (size[0] / tw, size[1] / th)
    offsets = (tw * 0.5 - sum(a * b for a, b in zip(origin, u)) / scales[0],
               th * 0.5 - sum(a * b for a, b in zip(origin, image_v)) / scales[1])
    out.face_out(pts, normal, material, u_axis=u, v_axis=image_v, scale=scales, offset=offsets)
    return out


def add_decal(project, vmap: VMap, op: dict[str, Any], refs: dict[str, int]) -> dict[str, Any]:
    if op.get('normal') is not None and op.get('angles') is not None:
        raise OpError('give either normal or angles, not both')
    material = _material_path(project, op.get('material'))
    origin = _vec(op.get('origin'), 'origin')
    size = _positive_size(op.get('size', 64.0))
    angles = _vec(op.get('angles'), 'angles') if op.get('angles') is not None else (0.0, 0.0, 0.0)
    normal = _unit(op['normal'], 'normal') if op.get('normal') is not None else _normal_from_angles(angles)
    try:
        projection_far = float(op.get('projection_far', max(size) * 0.5))
        decal_offset = float(op.get('offset', 0.05))
    except (TypeError, ValueError):
        raise OpError('offset and projection_far must be numbers') from None
    if not math.isfinite(projection_far) or projection_far <= 0 or projection_far > 32768:
        raise OpError('projection_far must be between 0 and 32768')
    if not math.isfinite(decal_offset) or abs(decal_offset) > 32768:
        raise OpError('offset must be finite and at most 32768 units')
    # A small lift prevents coplanar z-fighting while projectionFar controls the
    # depth range in which the overlay can find the receiving surface.
    plane_origin = _add(origin, _scale(normal, decal_offset))
    texture_size = project.texture_size(material)
    local = _decal_mesh(plane_origin, normal, size, material, texture_size)
    node = vmap.new_mesh(local, origin=plane_origin, texture_size=texture_size)
    node.type = 'CMapStaticOverlay'
    node.name = op.get('name', '') if isinstance(op.get('name', ''), str) else ''
    # Orientation is baked into world-space quad vertices exactly once.
    node['angles'] = (0.0, 0.0, 0.0)
    node.set('projectionTargets', 'int_array', [0])
    node.set('useAsOccluder', 'bool', False)
    node.set('renderOrder', 'int', int(op.get('render_order', 0)))
    node.set('disabledInLowQuality', 'bool', bool(op.get('disabled_in_low_quality', False)))
    node.set('projectionFar', 'float', projection_far)
    node.set('projectOnBackFaces', 'bool', bool(op.get('project_on_back_faces', False)))
    node.set('backFacingAngle', 'float', float(op.get('back_facing_angle', 90.0)))
    node.set('projectionMode', 'int', int(op.get('projection_mode', 0)))
    node.set('visexclude', 'bool', False)
    if op.get('material_adjustments') is not None:
        adj = op['material_adjustments']
        if not isinstance(adj, dict):
            raise OpError('material_adjustments must be an object')
        e = Element('DmElement', 'MaterialAdjustmentParamsStruct')
        defaults = {'ColorBrightness': 0.5, 'ColorContrast': 0.5, 'ColorAlpha': 1.0,
                    'RoughnessBrightness': 0.5, 'RoughnessContrast': 0.5, 'ShadingAlpha': 1.0,
                    'NormalIntensity': 0.75, 'RoughnessMetalnessOverride': False, 'NormalBlendOverride': True}
        for k, default in defaults.items():
            value = adj.get(k, default)
            if isinstance(default, bool):
                e.set(k, 'bool', bool(value))
            else:
                e.set(k, 'float', float(value))
        node.set('MaterialAdjustmentParamsStruct', 'element', e)
    ref = op.get('ref')
    out = {'op': 'add_decal', 'id': node['nodeID'], 'material': material,
           'origin': [round(x, 4) for x in origin], 'normal': [round(x, 5) for x in normal],
           'size': [round(x, 4) for x in size]}
    if ref:
        refs[ref] = node['nodeID']
        out['ref'] = ref
    vmap.attach(node, _resolve(vmap, op['parent'], refs) if op.get('parent') is not None else None)
    return out


def add_sign(project, vmap: VMap, op: dict, refs: dict) -> dict:
    """Place a generated sign: projected stencil or framed planar artwork."""
    name = check_name(op.get('name'), 'sign name')
    source = None
    for folder in ('mcp', 'mcp_decals'):
        path = ensure_inside(project.s.content_addon / f'materials/{folder}/{name}/{name}.json', project.s.content_addon)
        if path.is_file():
            meta = json.loads(path.read_text(encoding='utf-8'))
            if 'sign' in meta:
                source = (folder, meta['sign'])
                break
    if source is None:
        raise OpError(f'sign {name!r} not found; call sign_create first')
    folder, meta = source
    material = f'materials/{folder}/{name}/{name}.vmat'
    width = float(op.get('width', 128))
    size = _positive_size(op.get('size', [width, width / meta['aspect']]))
    if op.get('normal') is not None and op.get('angles') is not None:
        raise OpError('give either normal or angles, not both')
    normal = _unit(op['normal'], 'normal') if op.get('normal') is not None else _normal_from_angles(_vec(op.get('angles'), 'angles'))
    origin = _vec(op.get('origin'), 'origin')
    if meta['style'] == 'stencil':
        result = add_decal(project, vmap, {**op, 'material': material, 'normal': normal,
                                          'angles': None, 'size': size}, refs)
        result['op'] = 'add_sign'
        return result
    parent = project.resolve(vmap, op['parent'], refs) if op.get('parent') is not None else None
    texsize = project.texture_size(material)
    node = vmap.new_mesh(_decal_mesh(_add(origin, _scale(normal, 0.5)), normal, size, material, texsize),
                         texture_size=texsize, options={'physicsType': 'none'})
    vmap.attach(node, parent)
    ids = [node['nodeID']]
    if meta['style'] in ('plate', 'neon'):
        frame = float(op.get('frame', 2))
        if not math.isfinite(frame) or not 0 <= frame <= min(size) / 4:
            raise OpError('frame must be 0..one quarter of the smaller sign dimension')
        frame_material = _material_path(project, op.get('frame_material', 'materials/dev/reflectivity_30.vmat'))
        refaxis = (0, 0, 1) if abs(normal[2]) < .9 else (0, 1, 0)
        u = _unit(_cross(refaxis, normal), 'tangent'); v = _cross(normal, u)
        w, h = size[0] / 2 + frame, size[1] / 2 + frame
        coords = [(-w,-h,-1), (w,-h,-1), (w,h,-1), (-w,h,-1),
                  (-w,-h,0), (w,-h,0), (w,h,0), (-w,h,0)]
        pts = [_add(origin, _add(_scale(u,x), _add(_scale(v,y), _scale(normal,z)))) for x,y,z in coords]
        box = Mesh()
        for face, outward in [([0,3,2,1],_scale(normal,-1)), ([4,5,6,7],normal),
                              ([0,1,5,4],_scale(v,-1)), ([1,2,6,5],u),
                              ([2,3,7,6],v), ([3,0,4,7],_scale(u,-1))]:
            box.face_out([pts[i] for i in face], outward, frame_material)
        back = vmap.new_mesh(box, texture_size=project.texture_size(frame_material), options={'physicsType': 'none'})
        vmap.attach(back, parent); ids.append(back['nodeID'])
    result = group_nodes(vmap, ids, f'sign_{name}', refs)
    if op.get('ref'):
        refs[op['ref']] = result['id']
    return {'op': 'add_sign', 'id': result['id'], 'mesh_ids': ids, 'material': material,
            'size': list(size), 'style': meta['style']}


def add_world_text(project, vmap: VMap, op: dict, refs: dict) -> dict:
    text = op.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 512:
        raise OpError('text must contain 1..512 characters')
    size = float(op.get('size', 16))
    if not math.isfinite(size) or not 0 < size <= 512:
        raise OpError('size must be glyph height in world units, >0..512')
    from .materials import _rgb
    rgb = [int(x) for x in _rgb(op.get('color'), (255, 255, 255))]
    rgba = rgb + [255]
    kv = {'message': text, 'font_size': 64, 'font_name': op.get('font', 'Arial'),
          'world_units_per_pixel': size / 64, 'color': ' '.join(map(str, rgba)),
          'justify_horizontal': 1, 'justify_vertical': 1, 'enabled': 1,
          'fullbright': int(bool(op.get('fullbright', True)))}
    if op.get('targetname'):
        kv['targetname'] = op['targetname']
    result = project._apply(vmap, {'op': 'add_entity', 'classname': 'point_worldtext',
                           'origin': _vec(op.get('origin'), 'origin'), 'angles': _vec(op.get('angles'), 'angles'),
                           'keyvalues': kv, **({'parent': op['parent']} if 'parent' in op else {}),
                           **({'ref': op['ref']} if 'ref' in op else {})}, refs)
    return {**result, 'op': 'add_world_text', 'text': text, 'size': size}


def prefab_list(project) -> dict[str, Any]:
    folder = ensure_inside(project.s.content_addon / 'maps' / 'prefabs', project.s.content_addon)
    files = sorted(folder.glob('e_*.vmap')) if folder.is_dir() else []
    return {'prefabs': [{'name': f.stem, 'prefab': _prefab_rel(project, f), 'bytes': f.stat().st_size} for f in files]}


def prefab_info(project, name: str) -> dict[str, Any]:
    path, m, group = _load_prefab(project, name)
    return {'ok': True, 'name': path.stem, 'prefab': _prefab_rel(project, path),
            'isprefab': bool(m.doc.root.get('isprefab', False)), 'nodes': len(group.get('children', [])),
            'summary': m.summary(), 'group': m.describe(group, full=False)}
