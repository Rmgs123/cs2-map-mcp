"""New shapes and modelling operations; loaded by the extension registry."""
import hashlib
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from .. import meshedit, meshops as g, shapes, checks
from .. import kv3
from ..config import PROJECT_ROOT
from ..project import OpError

_BLEND_CACHE = OrderedDict()
_BLEND_LOCK = threading.Lock()


def _blend_material(project, material):
    """Inspect the actual installed compiled shader; cache is bounded and content keyed."""
    addon=project.s.game_addon/(material+'_c')
    data=addon.read_bytes() if addon.is_file() else project.game.read(material+'_c')
    native=project.s.game_bin/'resourceinfo.exe'
    if not native.is_file():raise OpError('resourceinfo.exe is required to verify blend shader support')
    st=native.stat();key=(hashlib.sha256(data).digest(),str(native),st.st_size,st.st_mtime_ns)
    with _BLEND_LOCK:
        cached=_BLEND_CACHE.get(key)
        if cached is not None:
            _BLEND_CACHE.move_to_end(key)
            return cached
    temp_root=PROJECT_ROOT/'.local/tmp';temp_root.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='g8_blend_',dir=temp_root) as folder:
            path=Path(folder)/'material.vmat_c';path.write_bytes(data)
            proc=subprocess.run([str(native),'-game',str(project.s.cs2_path/'game/csgo'),
                                 '-i',str(path),'-b','DATA','-baremode'],capture_output=True,timeout=20,check=False)
            if proc.returncode:raise OpError(f'resourceinfo could not inspect blend material: exit {proc.returncode}')
            _,value=kv3.loads(proc.stdout.decode('utf-8-sig',errors='replace'))
    except (OSError,subprocess.SubprocessError,kv3.KV3Error) as exc:
        raise OpError(f'cannot verify blend shader: {exc}') from None
    params={v.get('m_name') for v in value.get('m_textureParams',[]) if isinstance(v,dict)}
    shader=value.get('m_shaderName')
    supported=shader=='csgo_environment_blend.vfx' and {'g_tColor1','g_tColor2'}<=params
    with _BLEND_LOCK:
        _BLEND_CACHE[key]=supported
        while len(_BLEND_CACHE)>32:_BLEND_CACHE.popitem(last=False)
    return supported


def _fields(op, allowed):
    extra=set(op)-set(allowed)-{'op','id','ref'}
    if extra:raise OpError(f'unknown geometry operation fields: {sorted(extra)}')


def _target(project, vmap, op, refs):
    target=project.resolve(vmap,op.get('id'),refs)
    g.decode_node(target)  # refuse unsupported input before any mutation
    return target


def _material(project, material):
    if not isinstance(material,str) or not material.startswith('materials/') or not material.endswith('.vmat') or '..' in material.split('/') or not checks._asset_exists(project,material):
        raise OpError(f'missing/invalid material {material!r}; use asset_search')
    return material


def _finish(node, op, refs, **extra):
    if op.get('ref'):refs[op['ref']]=node['nodeID']
    return {'op':op['op'],'id':node['nodeID'],**({'ref':op['ref']} if op.get('ref') else {}),**extra}


CUTTERS_MAX = 64


def cut_mesh(project, vmap, op, refs):
    """Subtract one cutter or a list of them (all windows of a wall in one operation)."""
    _fields(op,{'cutter','material'})
    node=_target(project,vmap,op,refs);specs=op.get('cutter')
    specs=specs if isinstance(specs,list) else [specs]
    if not 1<=len(specs)<=CUTTERS_MAX:raise OpError(f'cutter: one shape or a list of 1..{CUTTERS_MAX}')
    material=_material(project,op.get('material',node['meshData']['materials'][0]))
    cutters=[]
    for k,spec in enumerate(specs):
        if not isinstance(spec,dict) or spec.get('shape') not in ('box','prism'):
            raise OpError(f'cutter {k}: a box or convex prism shape in world coordinates')
        cutter=shapes.build(spec,material)[0];g.validate(cutter)
        for f in cutter.faces:
            n=cutter.normal(f);w=g._dot(n,cutter.positions[f.verts[0]])
            if any(g._dot(n,p)>w+g.EPS for p in cutter.positions):raise OpError(f'cutter {k}: the prism must be convex')
        cutters.append(cutter)
    original=g.decode_node(node)
    # Validate input closure even if collision is disabled.
    g.encode_polygons(node,original,project.texture_size,True)
    # All cutters in one subtraction: every separate cut_mesh rounds the result to the vertex grid, and a face
    # cut again and again drifted off its plane (10.10: 3-5 windows in one wall broke the mesh).
    result=g.subtract(original,g.union_all([g.mesh_polygons(c,project.texture_size) for c in cutters]))
    try:
        md=g.encode_polygons(node,result,project.texture_size,True)
    except (g.MeshError,ValueError) as e:
        raise OpError(f'{e}. A face cut many times: pass all its openings in one cut_mesh (cutter: [...]), or '
                      'build the wall as add_mesh shape mesh with faces[].holes') from None
    # The entire cut, including new interior surfaces, must remain a valid closed solid.
    node['meshData']=md
    return _finish(node,op,refs,faces=len(md['faceEdgeIndices']),vertices=len(md['vertexEdgeIndices']))


def texture_faces(project, vmap, op, refs):
    _fields(op,{'faces','material','uv','tint','mesh_tint'})
    if not any(k in op for k in ('material','uv','tint','mesh_tint')):raise OpError('texture_faces needs material, uv, tint or mesh_tint')
    node=_target(project,vmap,op,refs);polys=g.decode_node(node);selected=g.select_faces(polys,op.get('faces','all'))
    if 'material' in op:
        mat=_material(project,op['material'])
        for i in selected:polys[i].material=mat
    if 'tint' in op:
        tint=g.tint_value(op['tint'])
        for i in selected:
            for v in polys[i].corners:v.tint=tint
    if 'uv' in op:g.project_uv(polys,selected,op['uv'],project.texture_size)
    md=g.encode_polygons(node,polys,project.texture_size,node.get('physicsType')!='none')
    mesh_tint=g.tint_value(op['mesh_tint']) if 'mesh_tint' in op else None
    node['meshData']=md
    if mesh_tint:node.set('tintColor','color',tuple(round(x*255) for x in mesh_tint))
    return _finish(node,op,refs,faces=len(selected),total_faces=len(polys))


def paint_blend(project, vmap, op, refs):
    _fields(op,{'faces','material','rule','cell','radius','seed','strength','base_z'})
    node=_target(project,vmap,op,refs);polys=g.decode_node(node);selected=g.select_faces(polys,op.get('faces','sides'))
    if 'material' in op:
        mat=_material(project,op['material'])
        for i in selected:polys[i].material=mat
    for mat in {polys[i].material for i in selected}:
        if not _blend_material(project,mat):
            raise OpError(f'material has no verified second color layer on csgo_environment_blend.vfx: {mat}')
    result=g.paint(polys,selected,op)
    node['meshData']=g.encode_polygons(node,result,project.texture_size,node.get('physicsType')!='none')
    weights=[v.blend[0] for p in result for v in p.corners]
    return _finish(node,op,refs,faces=len(node['meshData']['faceEdgeIndices']),weight_range=[round(min(weights),4),round(max(weights),4)])


def edit_mesh(project, vmap, op, refs):
    _fields(op,{'steps','uv'})
    node=_target(project,vmap,op,refs);polys=g.decode_node(node)
    uv=op.get('uv','world')
    if uv not in ('world','keep'):raise OpError('uv: world (re-project changed faces like Hammer) or keep (texture follows the vertices)')
    closed=-1 not in node['meshData']['edgeFaceIndices']
    result,reports=meshedit.run(polys,op.get('steps'),project.texture_size,lambda mat:_material(project,mat),uv=='keep')
    try:md=g.encode_polygons(node,result,project.texture_size,closed)
    except ValueError as e:
        raise OpError(f'the edited mesh is not a valid solid: {e}. Typical causes: a face pushed through another '
                      '(inset before pushing a whole face in), a move or deform that folds faces, a too wide bevel') from None
    node['meshData']=md
    return _finish(node,op,refs,steps=reports,faces=len(md['faceEdgeIndices']),vertices=len(md['vertexEdgeIndices']))


def preprocess(project, vmap, op, refs):
    if op.get('op') in ('add_mesh','add_brush_entity'):
        spec=op.get('shape') or {}
        if spec.get('shape') in ('mesh','extrude','revolve'):
            solid=spec.get('solid',True)
            if not solid:
                if op['op']=='add_brush_entity':raise OpError('brush entities require a closed solid')
                options=dict(op.get('options') or {})
                if options.get('physicsType','none')!='none':raise OpError('solid:false requires physicsType:none')
                options['physicsType']='none'
                options.setdefault('bakelighting',False)
                return {**op,'options':options}
    return op


def register(mcp, project):
    for name,fn in [('mesh',g.mesh_shape),('extrude',g.extrude_shape),('revolve',g.revolve_shape)]:
        if name not in shapes.EXTRA_SHAPES:shapes.register_shape(name,fn)
        elif shapes.EXTRA_SHAPES[name] is not fn:raise ValueError(f'geometry shape {name} already registered by another extension')
    for name,fn in [('cut_mesh',cut_mesh),('texture_faces',texture_faces),('paint_blend',paint_blend),('edit_mesh',edit_mesh)]:project.register_op(name,fn)
    if preprocess not in project.preprocessors:project.register_preprocessor(preprocess)

    @mcp.tool()
    def geometry_info(name: str, id: int | str) -> dict:
        """Inspect one mesh by node ID or @ref: face counts, closure, winding and bounds.

        Read geometry guide for add_mesh mesh/extrude/revolve and map_edit
        cut_mesh/texture_faces/paint_blend. Geometry coordinates are CS2 inches.
        """
        vmap=project.load(name);node=project.resolve(vmap,id,{})
        polys=g.decode_node(node)
        points=[v.p for p in polys for v in p.corners]
        closed=-1 not in node['meshData']['edgeFaceIndices']
        result={'id':node['nodeID'],'faces':len(polys),'vertices':len(node['meshData']['vertexEdgeIndices']),
                'closed':closed,'physics':node.get('physicsType'),
                'bounds':[[min(p[k] for p in points) for k in range(3)],[max(p[k] for p in points) for k in range(3)]]}
        try:g.encode_polygons(node,polys,project.texture_size,closed);result['valid']=True
        except ValueError as e:result.update(valid=False,error=str(e))
        return result
