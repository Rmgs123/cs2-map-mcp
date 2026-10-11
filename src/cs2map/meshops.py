"""Geometry extension: bounded polygon modelling without changing the core Mesh."""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from . import mesh
from .config import PROJECT_ROOT
from .mesh import Mesh, MeshError, _add, _sub, _scale, _dot, _cross, _norm

EPS = 1e-4
BSP_EPS = .002  # above the core 0.001 vertex lookup grid
MAX_FACES = 8192


def number(v, label='number'):
    if isinstance(v, bool):
        raise MeshError(f'{label} must be finite')
    try:
        v = float(v)
    except (ValueError, TypeError):
        raise MeshError(f'{label} must be finite') from None
    if not math.isfinite(v) or abs(v) > 1e7:
        raise MeshError(f'{label} must be finite and within +/-10000000')
    return v


def vector(v, n=3):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise MeshError(f'expected {n} coordinates')
    return tuple(number(c) for c in v)


def integer(v, lo, hi, label):
    n = number(v, label)
    if n != int(n) or not lo <= n <= hi:
        raise MeshError(f'{label} must be an integer {lo}..{hi}')
    return int(n)


def keys(spec, allowed):
    extra = set(spec) - set(allowed) - {'shape', 'material', 'materials', 'solid'}
    if extra:
        raise MeshError(f'unknown geometry fields: {sorted(extra)}')
    if 'solid' in spec and not isinstance(spec['solid'], bool):
        raise MeshError('solid must be boolean')


def cross2(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def area(r):
    return sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(r, r[1:]+r[:1]))/2


def on_segment(p, a, b):
    return abs(cross2(a,b,p)) < EPS and all(min(a[i],b[i])-EPS <= p[i] <= max(a[i],b[i])+EPS for i in range(2))


def intersects(a,b,c,d):
    x,y,z,w = cross2(a,b,c),cross2(a,b,d),cross2(c,d,a),cross2(c,d,b)
    return (x*y < -EPS*EPS and z*w < -EPS*EPS) or any((abs(q)<EPS and on_segment(p,u,v)) for q,p,u,v in [(x,c,a,b),(y,d,a,b),(z,a,c,d),(w,b,c,d)])


def inside(p, ring):
    value = False
    for a,b in zip(ring, ring[1:]+ring[:1]):
        if on_segment(p,a,b):
            return False  # strictly inside: touching holes are invalid
        if (a[1]>p[1]) != (b[1]>p[1]) and p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:
            value = not value
    return value


def rings(outer, holes=()):
    if not isinstance(outer,(list,tuple)) or not isinstance(holes,(list,tuple)) or len(holes)>32:
        raise MeshError('profile and holes must be point lists (at most 32 holes)')
    result = []
    for i, raw in enumerate([outer, *holes]):
        r = [vector(p,2) for p in raw]
        if len(r)>1 and r[-1]==r[0]:
            r.pop()
        if not 3 <= len(r) <= 512 or len(set(r)) != len(r) or abs(area(r)) < EPS:
            raise MeshError('profile/ring needs 3..512 distinct points and nonzero area')
        for j,a in enumerate(r):
            b=r[(j+1)%len(r)]
            for k in range(j+1,len(r)):
                if k in (j+1,) or (j==0 and k==len(r)-1):
                    continue
                if intersects(a,b,r[k],r[(k+1)%len(r)]):
                    raise MeshError('self-intersecting polygon/profile')
        if (area(r)>0) != (i==0):
            r.reverse()
        result.append(r)
    for i,r in enumerate(result[1:],1):
        if not all(inside(p,result[0]) for p in r):
            raise MeshError('hole must lie strictly inside the outer ring')
        mine=_edges(r)
        for other in result[:i]:
            theirs=_edges(other)
            if any(intersects(a,b,c,d) for a,b,ax0,ax1,ay0,ay1 in mine for c,d,cx0,cx1,cy0,cy1 in theirs
                   if ax0<=cx1 and cx0<=ax1 and ay0<=cy1 and cy0<=ay1):
                raise MeshError('rings intersect or touch')
        for other in result[1:i]:
            if inside(r[0],other) or inside(other[0],r):
                raise MeshError('holes overlap or are nested')
    return result


def _edges(r):
    """Edges of a ring with their EPS-padded bounding boxes (a cheap reject before intersects)."""
    return [(c,d,min(c[0],d[0])-EPS,max(c[0],d[0])+EPS,min(c[1],d[1])-EPS,max(c[1],d[1])+EPS)
            for c,d in zip(r,r[1:]+r[:1])]


def triangulate(outer, holes=(), _rings=None):
    """Visible bridges followed by ear clipping; verify the resulting area."""
    rr = _rings or rings(outer,holes)
    polygon = rr[0][:]
    hole_edges=[e for r in rr[1:] for e in _edges(r)]
    # Rightmost holes first prevents bridges from cutting unprocessed holes.
    for hole in sorted(rr[1:], key=lambda r:max(p[0] for p in r), reverse=True):
        h = max(range(len(hole)),key=lambda i:(hole[i][0],-hole[i][1]))
        a = hole[h]
        obstacles=_edges(polygon)+hole_edges
        # The nearest visible vertex wins (ties: lowest index), so test candidates nearest first and
        # stop at the first visible one instead of testing every vertex of the polygon.
        chosen=None
        for _,i in sorted((sum((a[k]-b[k])**2 for k in range(2)),i) for i,b in enumerate(polygon) if a!=b):
            b=polygon[i]
            mid=((a[0]+b[0])/2,(a[1]+b[1])/2)
            if not inside(mid,rr[0]) or any(inside(mid,r) for r in rr[1:]):
                continue
            x0,x1,y0,y1=min(a[0],b[0]),max(a[0],b[0]),min(a[1],b[1]),max(a[1],b[1])
            if any(intersects(a,b,c,d) for c,d,cx0,cx1,cy0,cy1 in obstacles
                   if cx0<=x1 and x0<=cx1 and cy0<=y1 and y0<=cy1 and a not in (c,d) and b not in (c,d)):
                continue
            chosen=i
            break
        if chosen is None:
            raise MeshError('cannot triangulate hole: no visible bridge')
        i=chosen
        polygon=polygon[:i+1]+hole[h:]+hole[:h+1]+[polygon[i]]+polygon[i+1:]
    todo=polygon[:]; triangles=[]
    while len(todo)>3:
        for i,b in enumerate(todo):
            a,c=todo[i-1],todo[(i+1)%len(todo)]
            if cross2(a,b,c)<=EPS:
                continue
            if any(p not in (a,b,c) and min(cross2(a,b,p),cross2(b,c,p),cross2(c,a,p))>=-EPS for p in todo):
                continue
            triangles.append([a,b,c]);todo.pop(i)
            break
        else:
            raise MeshError('cannot triangulate polygon (degenerate or unsupported touching edges)')
    if len(todo)==3 and cross2(*todo)>EPS:
        triangles.append(todo)
    expected=sum(area(r) for r in rr)
    if abs(sum(area(t) for t in triangles)-expected)>max(EPS,abs(expected)*1e-6):
        raise MeshError('triangulation area mismatch')
    return triangles


def basis(normal):
    helper=(0.,0.,1.) if abs(normal[2])<.9 else (0.,1.,0.)
    u=_norm(_cross(helper,normal))
    return u,_cross(normal,u)


def normal(points):
    n=(0.,0.,0.)
    for a,b in zip(points,points[1:]+points[:1]):
        n=_add(n,_cross(a,b))
    return _norm(n)


def planar_parts(points, holes=()):
    n=normal(points);u,v=basis(n);o=points[0]
    if any(abs(_dot(_sub(p,o),n))>BSP_EPS for r in [points,*holes] for p in r):
        raise MeshError('face is not planar; supply triangles for a warped surface')
    projected=[[(_dot(_sub(p,o),u),_dot(_sub(p,o),v)) for p in r] for r in [points,*holes]]
    rr=rings(projected[0],projected[1:])
    if not holes and all(cross2(rr[0][i-1],b,rr[0][(i+1)%len(rr[0])])>=-EPS for i,b in enumerate(rr[0])):
        return [points]
    lookup={q:p for r,rp in zip([points,*holes],projected) for p,q in zip(r,rp)}
    return [[lookup[q] for q in t] for t in triangulate(projected[0],projected[1:],rr)]


def uv_options(points, spec):
    if not spec:
        return {}
    keys(spec, {'mode','scale','rotate','offset','u_axis','v_axis'})
    mode=spec.get('mode','world')
    if mode not in ('world','box','planar'):
        raise MeshError('shape face UV mode must be world, box or planar; use texture_faces for cylinder')
    n=normal(points)
    u,v=mesh.world_axes(n) if mode in ('world','box') else basis(n)
    if 'u_axis' in spec or 'v_axis' in spec:
        u,v=_norm(vector(spec.get('u_axis'))),_norm(vector(spec.get('v_axis')))
    if abs(_dot(u,n))>.999 or abs(_dot(v,n))>.999 or abs(_dot(u,v))>.999:
        raise MeshError('UV axes must span the face')
    scale=vector(spec.get('scale',[.25,.25]),2)
    if min(scale)<=0:
        raise MeshError('UV scale must be positive units per texel')
    angle=math.radians(number(spec.get('rotate',0)));c,s=math.cos(angle),math.sin(angle)
    return {'u_axis':_add(_scale(u,c),_scale(v,s)), 'v_axis':_add(_scale(u,-s),_scale(v,c)),
            'scale':scale,'offset':vector(spec.get('offset',[0,0]),2)}


def volume(m):
    o=m.positions[0]
    return sum(_dot(_sub(m.positions[f.verts[0]],o),_cross(_sub(m.positions[b],o),_sub(m.positions[c],o)))/6
               for f in m.faces for b,c in zip(f.verts[1:],f.verts[2:]))


def check_intersections(m):
    triangles=[]
    for fi,f in enumerate(m.faces):
        # Convex format faces may have collinear corners after T-junction welding.
        for b,c in zip(f.verts[1:],f.verts[2:]):
            indices=(f.verts[0],b,c);p=[m.positions[i] for i in indices]
            if _dot(_cross(_sub(p[1],p[0]),_sub(p[2],p[0])),_cross(_sub(p[1],p[0]),_sub(p[2],p[0])))<EPS*EPS:continue
            lo=tuple(min(q[k] for q in p) for k in range(3));hi=tuple(max(q[k] for q in p) for k in range(3))
            triangles.append((lo,hi,fi,indices,p))
    # Sweep along the longest axis of the mesh (a long wall along y would overlap everything in x).
    k0=max(range(3),key=lambda k:max((t[1][k] for t in triangles),default=0)-min((t[0][k] for t in triangles),default=0))
    k1,k2=[k for k in range(3) if k!=k0]
    triangles.sort(key=lambda t:t[0][k0])
    # Plane of every triangle (unnormalised): an edge can only pierce a triangle when its ends lie on
    # opposite sides of that plane, and only a triangle near the plane can be coplanar with it, so most
    # pairs skip the exact tests below.
    planes=[]
    for lo,hi,fi,idx,p in triangles:
        e1,e2=_sub(p[1],p[0]),_sub(p[2],p[0]);n=_cross(e1,e2)
        planes.append((n[0],n[1],n[2],_dot(n,p[0]),math.sqrt(_dot(n,n)),e1,e2))
    def pierces(a,b,tri,e1,e2):
        direction=_sub(b,a)
        h=_cross(direction,e2);det=_dot(e1,h)
        if abs(det)<1e-9:return False
        s=_sub(a,tri[0]);u=_dot(s,h)/det;q=_cross(s,e1);v=_dot(direction,q)/det;t=_dot(e2,q)/det
        return EPS<t<1-EPS and u>EPS and v>EPS and u+v<1-EPS
    face_verts=[frozenset(f.verts) for f in m.faces]
    normals={}
    count=len(triangles)
    for i in range(count):
        lo,hi,fi,idx,p=triangles[i]
        hx,hy,hz,ly,lz=hi[k0]+EPS,hi[k1],hi[k2],lo[k1],lo[k2]
        verts=face_verts[fi]
        pa,pb,pc,pd,plen,pe1,pe2=planes[i]
        for j in range(i+1,count):
            lj,hj,fj,jdx,q=triangles[j]
            if lj[k0]>hx:break
            if fi==fj or hy<lj[k1]-EPS or hj[k1]<ly-EPS or hz<lj[k2]-EPS or hj[k2]<lz-EPS:continue
            # Triangulated CSG caps may share a boundary vertex while their
            # projections overlap. This is a shared edge/corner, not a shell
            # self-intersection; disconnected shells are checked below.
            if not verts.isdisjoint(face_verts[fj]):continue
            qa,qb,qc,qd,qlen,qe1,qe2=planes[j]
            sp=[qa*x+qb*y+qc*z-qd for x,y,z in p]   # p against the plane of q
            sq=[pa*x+pb*y+pc*z-pd for x,y,z in q]   # q against the plane of p
            if (not (min(sp)>0 or max(sp)<0) and any(pierces(a,b,q,qe1,qe2) for a,b in zip(p,p[1:]+p[:1]))) or (
                    not (min(sq)>0 or max(sq)<0) and any(pierces(a,b,p,pe1,pe2) for a,b in zip(q,q[1:]+q[:1]))):
                raise MeshError(f'self-intersecting surface between faces {fi} and {fj}; reduce profile size/path bends')
            if max(abs(x) for x in sq)>2*EPS*plen:continue   # q is clearly off the plane of p
            n=normals.get(i)
            if n is None:n=normals[i]=normal(p)
            if all(abs(_dot(_sub(v,p[0]),n))<EPS for v in q):
                u,v=basis(n);pp=[(_dot(x,u),_dot(x,v)) for x in p];qq=[(_dot(x,u),_dot(x,v)) for x in q]
                if any(inside(x,qq) for x in pp) or any(inside(x,pp) for x in qq) or any(
                    cross2(a,b,c)*cross2(a,b,d)<-EPS and cross2(c,d,a)*cross2(c,d,b)<-EPS
                    for a,b in zip(pp,pp[1:]+pp[:1]) for c,d in zip(qq,qq[1:]+qq[:1])):
                    raise MeshError(f'overlapping coplanar faces {fi} and {fj}')


def validate(m, closed=True):
    if not m.faces or len(m.faces)>MAX_FACES:
        raise MeshError(f'geometry must contain 1..{MAX_FACES} faces')
    dest,fs,nxt,fe,ve=m.halfedges()
    if closed and -1 in fs:
        raise MeshError(f'solid mesh is open ({fs.count(-1)} boundary edges); close it or set solid:false')
    adjacent=[set() for f in m.faces]
    for h,f in enumerate(fs):
        if f>=0 and fs[h^1]>=0:adjacent[f].add(fs[h^1])
    incident=[set() for v in m.positions]
    for fi,f in enumerate(m.faces):
        for vi in f.verts:incident[vi].add(fi)
    for vi,faces in enumerate(incident):
        seen=set();stack=[next(iter(faces))]
        while stack:
            fi=stack.pop()
            if fi in seen:continue
            seen.add(fi);stack.extend((adjacent[fi]&faces)-seen)
        if seen!=faces:raise MeshError(f'non-manifold vertex {vi}: disconnected face fans')
    check_intersections(m)
    if closed:
        # Check every connected shell, so an inverted component cannot hide in a positive total.
        unseen=set(range(len(m.faces)))
        components=[]
        while unseen:
            component=set();stack=[next(iter(unseen))]
            while stack:
                f=stack.pop()
                if f in component:continue
                component.add(f);unseen.discard(f);stack.extend(adjacent[f]-component)
            part=Mesh(positions=m.positions,faces=[m.faces[i] for i in component])
            if volume(part)<=EPS:
                raise MeshError('solid shell has inward winding or zero volume; reverse its faces')
            components.append(component)
        # Two disconnected closed shells may not overlap in volume. This catches
        # intersecting boxes even when their planar faces meet only at an edge.
        bounds=[]
        for component in components:
            points=[m.positions[v] for fi in component for v in m.faces[fi].verts]
            bounds.append((tuple(min(p[k] for p in points) for k in range(3)),
                           tuple(max(p[k] for p in points) for k in range(3))))
        for i,(lo,hi) in enumerate(bounds):
            for lo2,hi2 in bounds[i+1:]:
                overlap=[min(hi[k],hi2[k])-max(lo[k],lo2[k]) for k in range(3)]
                if min(overlap)>EPS:
                    raise MeshError('self-intersecting closed shells overlap in volume')
    return m


# Validated meshes by content: a map script that rebuilds a town re-sends the same buildings, and their
# triangulation and self-intersection checks are the slow part of map_edit. Keyed by the spec, the material
# and the source of the geometry code (a code change starts a new cache). In memory and in one SQLite file
# under .local (one file: opening thousands of small files is slow on Windows).
_CACHE_FILE=PROJECT_ROOT/'.local'/'cache'/'meshes.sqlite'
_CACHE_MB=256
_CACHE_MEMORY=4000
_cache:'OrderedDict[str,bytes]'=OrderedDict()
_cache_db=None
_cache_writes=0
_CODE=hashlib.sha1(b''.join(Path(f).read_bytes() for f in (__file__,mesh.__file__))).hexdigest()[:12]


def _cache_key(spec, material):
    try:
        text=json.dumps([_CODE,spec,material],sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError):
        return None
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def _db():
    global _cache_db
    if _cache_db is None:
        try:
            _CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
            db=sqlite3.connect(str(_CACHE_FILE),timeout=5,check_same_thread=False,isolation_level=None)
            db.execute('create table if not exists mesh(key text primary key, code text, used real, data blob)')
            db.execute('delete from mesh where code<>?',(_CODE,))      # results of older geometry code
            _cache_db=db
        except sqlite3.Error:
            _cache_db=False
    return _cache_db or None


def _cache_get(key):
    blob=_cache.get(key)
    if blob is None:
        db=_db()
        try:
            row=db.execute('select data from mesh where key=?',(key,)).fetchone() if db else None
        except sqlite3.Error:
            row=None
        if row is None:
            return None
        blob=_cache[key]=row[0]
    _cache.move_to_end(key)
    while len(_cache)>_CACHE_MEMORY:_cache.popitem(last=False)
    try:
        return pickle.loads(blob)
    except Exception:
        _cache.pop(key,None)
        return None


def _cache_put(key, value):
    global _cache_writes
    blob=pickle.dumps(value,protocol=pickle.HIGHEST_PROTOCOL)
    _cache[key]=blob
    while len(_cache)>_CACHE_MEMORY:_cache.popitem(last=False)
    db=_db()
    if not db:
        return
    try:
        db.execute('insert or replace into mesh values(?,?,?,?)',(key,_CODE,time.time(),blob))
        _cache_writes+=1
        if _cache_writes%500==1:
            # Size cap: the least recently written meshes go first.
            total=db.execute('select coalesce(sum(length(data)),0) from mesh').fetchone()[0]
            if total>_CACHE_MB*2**20:
                db.execute('delete from mesh where key in (select key from mesh order by used limit '
                           '(select count(*)/3 from mesh))')
                db.execute('vacuum')
    except sqlite3.Error:
        pass


def mesh_shape(spec, material):
    key=_cache_key(spec,material)
    if key:
        cached=_cache_get(key)
        if cached is not None:
            return cached
    out=_mesh_shape(spec,material)
    if key:_cache_put(key,out)
    return out


def _mesh_shape(spec, material):
    keys(spec,{'vertices','faces'})
    vertices=[vector(p) for p in spec.get('vertices',[])]
    if not 3<=len(vertices)<=8192 or not spec.get('faces'):
        raise MeshError('mesh needs vertices (3..8192) and faces')
    if not isinstance(spec['faces'],list) or len(spec['faces'])>MAX_FACES:
        raise MeshError(f'faces must be a list with at most {MAX_FACES} entries')
    def points(indices):
        if not isinstance(indices,list) or any(type(i) is not int or not 0<=i<len(vertices) for i in indices):
            raise MeshError('face indices must be integers within vertices')
        return [vertices[i] for i in indices]
    m=Mesh()
    for face in spec['faces']:
        f=face if isinstance(face,dict) else {'vertices':face}
        keys(f,{'vertices','holes','uv'})
        p=points(f.get('vertices'));holes=[points(h) for h in f.get('holes',[])]
        uv=uv_options(p,f.get('uv'))
        for part in planar_parts(p,holes):
            m.face(part,f.get('material',material),**uv)
            if len(m.faces)>MAX_FACES:raise MeshError('triangulated mesh face budget exceeded')
    return [validate(m,spec.get('solid',True))]


def path_points(spec):
    raw=spec.get('path')
    if isinstance(raw,dict):
        keys(raw,{'center','radius','start','angle','segments','axis'})
        center=vector(raw.get('center'));r=number(raw.get('radius'))
        angle=number(raw.get('angle',180));start=number(raw.get('start',0))
        n=integer(raw.get('segments',16),2,256,'path segments')
        axis=vector(raw.get('axis',[0,0,1]));axis=_norm(axis);u,v=basis(axis)
        if r<=0 or not 0<abs(angle)<=360:raise MeshError('arc needs radius>0 and 0<abs(angle)<=360')
        raw=[_add(center,_add(_scale(u,r*math.cos(math.radians(start+angle*i/n))),_scale(v,r*math.sin(math.radians(start+angle*i/n))))) for i in range(n+1)]
    if raw is None:raw=[spec.get('start',[0,0,0]),spec.get('end')]
    path=[vector(p) for p in raw]
    closed=spec.get('closed',False)
    if type(closed) is not bool:raise MeshError('closed must be boolean')
    if closed and len(path)>1 and path[0]==path[-1]:path.pop()
    if not (3 if closed else 2)<=len(path)<=256:raise MeshError('path needs 2..256 points (3 for closed)')
    if any(_dot(_sub(b,a),_sub(b,a))<.01 for a,b in zip(path,path[1:]+(path[:1] if closed else []))):
        raise MeshError('path contains duplicate or too close points')
    return path,closed


def extrude_shape(spec, material):
    keys(spec,{'profile','holes','start','end','path','closed','caps','up'})
    rr=rings(spec.get('profile',[]),spec.get('holes',[]));path,closed=path_points(spec)
    caps=spec.get('caps',True)
    if type(caps) is not bool:raise MeshError('caps must be boolean')
    if spec.get('solid',True) and not (caps or closed):raise MeshError('uncapped extrude needs solid:false')
    expected=sum(len(r) for r in rr)*(len(path) if closed else len(path)-1)*2
    if expected>MAX_FACES:raise MeshError('extrude face budget exceeded; simplify profile/path')
    tangents=[]
    for i,p in enumerate(path):
        prev=_norm(_sub(p,path[i-1])) if i or closed else None
        after=_norm(_sub(path[(i+1)%len(path)],p)) if i+1<len(path) or closed else None
        tangents.append(_norm(_add(prev,after)) if prev and after else prev or after)
    up=_norm(vector(spec.get('up',[0,0,1])))
    # Project a fixed up vector at each station; fail explicitly at its singularity.
    if abs(_dot(up,tangents[0]))>.999 and 'up' not in spec:up=(0.,1.,0.)
    frames=[]
    for t in tangents:
        if abs(_dot(up,t))>.999:raise MeshError('path tangent parallel to up; choose another up vector')
        u=_norm(_cross(up,t));v=_cross(t,u);frames.append((u,v))
    stations=[[[ _add(p,_add(_scale(u,q[0]),_scale(v,q[1]))) for q in r] for r in rr] for p,(u,v) in zip(path,frames)]
    m=Mesh();mats=spec.get('materials',{})
    for i in range(len(path) if closed else len(path)-1):
        for ri,r in enumerate(rr):
            for j in range(len(r)):
                k=(j+1)%len(r);n=(i+1)%len(path)
                quad=[stations[i][ri][j],stations[i][ri][k],stations[n][ri][k],stations[n][ri][j]]
                # Helical paths create warped quads; store planar triangles.
                for tri in (quad[:3],[quad[0],quad[2],quad[3]]):m.face(tri,mats.get('sides',material))
    if caps and not closed:
        captris=triangulate(rr[0],rr[1:])
        for i in (0,len(path)-1):
            lookup={q:p for r,s in zip(rr,stations[i]) for q,p in zip(r,s)}
            for tri in captris:
                pts=[lookup[q] for q in tri]
                if i==0:pts.reverse()
                m.face(pts,mats.get('start' if i==0 else 'end',material))
    return [validate(m,spec.get('solid',True))]


def revolve_shape(spec, material):
    keys(spec,{'profile','origin','axis','segments','angle','start','caps','profile_closed'})
    profile=[vector(p,2) for p in spec.get('profile',[])]
    pc=spec.get('profile_closed',True);caps=spec.get('caps',True)
    if type(pc) is not bool or type(caps) is not bool:raise MeshError('caps/profile_closed must be boolean')
    if pc:profile=rings(profile)[0]
    elif not 2<=len(profile)<=512:raise MeshError('open revolve profile needs 2..512 points')
    if any(p[0]<0 for p in profile):raise MeshError('revolve radius must be nonnegative')
    angle=number(spec.get('angle',360));full=abs(angle-360)<EPS
    if not 0<angle<=360:raise MeshError('revolve angle must be >0 and <=360')
    n=integer(spec.get('segments',24),3,128,'segments');start=math.radians(number(spec.get('start',0)))
    if n*len(profile)>MAX_FACES:raise MeshError('revolve face budget exceeded; reduce segments/profile points')
    o=vector(spec.get('origin',[0,0,0]));axis=spec.get('axis','z')
    a={'x':(1.,0.,0.),'y':(0.,1.,0.),'z':(0.,0.,1.)}.get(axis) if isinstance(axis,str) else _norm(vector(axis))
    if a is None:raise MeshError('axis must be x, y, z or a vector')
    u,v=basis(a);m=Mesh();stations=[]
    for i in range(n if full else n+1):
        t=start+math.radians(angle)*i/n;radial=_add(_scale(u,math.cos(t)),_scale(v,math.sin(t)))
        stations.append([_add(o,_add(_scale(radial,r),_scale(a,z))) for r,z in profile])
    for i in range(n):
        for j in range(len(profile) if pc else len(profile)-1):
            k=(j+1)%len(profile);ni=(i+1)%len(stations)
            # profile CCW in (radius,height); outward sweep reverses profile-first quad.
            pts=[stations[i][j],stations[ni][j],stations[ni][k],stations[i][k]]
            unique=[]
            for p in pts:
                if not unique or _dot(_sub(p,unique[-1]),_sub(p,unique[-1]))>EPS*EPS:unique.append(p)
            if len(unique)>1 and unique[0]==unique[-1]:unique.pop()
            if len(unique)>=3:m.face(unique,material)
    if not full and caps and pc:
        for i in (0,n):
            for tri in triangulate(profile):
                pts=[stations[i][profile.index(p)] for p in tri]
                if i==n:pts.reverse()
                m.face(pts,material)
    if spec.get('solid',True) and (not pc or (not full and not caps)):
        # Open profiles ending on the axis can still form a closed surface.
        if not full or profile[0][0]>EPS or profile[-1][0]>EPS:raise MeshError('open revolve needs solid:false or a full sweep with both ends on the axis')
    return [validate(m,spec.get('solid',True))]


@dataclass
class Corner:
    p: tuple
    uv: tuple = (0.,0.)
    tint: tuple = (1.,1.,1.,1.)
    blend: tuple = (0.,0.,0.,0.)

    def lerp(self, other, t):
        return Corner(*(tuple(a+(b-a)*t for a,b in zip(x,y)) for x,y in
                        [(self.p,other.p),(self.uv,other.uv),(self.tint,other.tint),(self.blend,other.blend)]))


@dataclass
class Polygon:
    corners: list[Corner]
    material: str
    attributes: dict = field(default_factory=dict)

    @property
    def n(self):return normal([v.p for v in self.corners])

    def flipped(self):return Polygon(list(reversed(self.corners)),self.material,self.attributes.copy())

    def part(self, corners):
        clean=[]
        for v in corners:
            if not clean or _dot(_sub(v.p,clean[-1].p),_sub(v.p,clean[-1].p))>EPS*EPS:clean.append(v)
        if len(clean)>1 and _dot(_sub(clean[0].p,clean[-1].p),_sub(clean[0].p,clean[-1].p))<EPS*EPS:clean.pop()
        if len(clean)<3:return None
        try:normal([v.p for v in clean])
        except MeshError:return None
        return Polygon(clean,self.material,self.attributes.copy())


def stream_map(array):
    return {s.get('semanticName',s.get('standardAttributeName')):s for s in array['streams']}


def decode_node(node):
    if node.type!='CMapMesh':raise MeshError('target must be one CMapMesh')
    if any(abs(x)>EPS for x in node.get('angles',[0,0,0])) or any(abs(x-1)>EPS for x in node.get('scales',[1,1,1])):
        raise MeshError('transformed mesh is unsupported; bake rotation/scale into vertices first')
    md=node['meshData']
    if any(md['subdivisionData']['subdivisionLevels']):raise MeshError('subdivided Hammer mesh is unsupported; bake it first')
    vs=stream_map(md['vertexData']);fv=stream_map(md['faceVertexData']);fd=stream_map(md['faceData'])
    supported={'position','texcoord','normal','tangent','VertexPaintTintColor','VertexPaintBlendParams'}
    if (set(vs)|set(fv))-supported:raise MeshError('unsupported vertex streams; refusing to discard them')
    if any(md['edgeData']['streams'][0]['data']):raise MeshError('mesh has authored edge flags; refusing to discard them')
    pos=vs['position']['data'];origin=vector(node['origin']);out=[]
    def corner(h):
        vi=md['vertexDataIndices'][md['edgeVertexIndices'][h]];ci=md['edgeVertexDataIndices'][h]
        return Corner(_add(vector(pos[vi]),origin),tuple(fv['texcoord']['data'][ci]),
                      tuple(fv['VertexPaintTintColor']['data'][ci]) if 'VertexPaintTintColor' in fv else (1.,1.,1.,1.),
                      tuple(fv['VertexPaintBlendParams']['data'][ci]) if 'VertexPaintBlendParams' in fv else (0.,0.,0.,0.))
    for fi,h0 in enumerate(md['faceEdgeIndices']):
        loop=[];h=h0
        while True:
            loop.append(corner(h));h=md['edgeNextIndices'][h]
            if h==h0:break
            if len(loop)>len(md['edgeVertexIndices']):raise MeshError('corrupt face loop')
        di=md['faceDataIndices'][fi];attrs={k:s['data'][di] for k,s in fd.items()}
        out.append(Polygon(loop,md['materials'][int(attrs['materialindex'])],attrs))
    return out


def mesh_polygons(m, texture_size=(512,512)):
    from .dmx import Element
    node=Element('CMapMesh');node.set('origin','vector3',(0.,0.,0.));node.set('meshData','element',m.to_dmx(texture_size=texture_size))
    return decode_node(node)


def weld_polygons(polys):
    """Split T junctions with interpolation of every corner attribute."""
    positions=list({tuple(round(x,4) for x in v.p):v.p for p in polys for v in p.corners}.values())
    for p in polys:
        out=[]
        for a,b in zip(p.corners,p.corners[1:]+p.corners[:1]):
            out.append(a);d=_sub(b.p,a.p);l2=_dot(d,d)
            if l2<EPS*EPS:continue
            candidates=[]
            for q in positions:
                t=_dot(_sub(q,a.p),d)/l2
                if (tuple(round(x,3) for x in q) not in (tuple(round(x,3) for x in a.p),tuple(round(x,3) for x in b.p))
                    and EPS<t<1-EPS and _dot(_sub(q,_add(a.p,_scale(d,t))),_sub(q,_add(a.p,_scale(d,t))))<1e-6):
                    candidates.append(t)
            for t in sorted(set(round(t,8) for t in candidates)):out.append(a.lerp(b,t))
        p.corners=out
    return polys


def encode_polygons(node, polys, texture_size, closed=True):
    if not polys or len(polys)>MAX_FACES:raise MeshError('empty result or geometry budget exceeded')
    origin=vector(node['origin']);m=Mesh()
    # Adjacent rounding bins can still describe the same sub-grid intersection.
    # Weld by distance using nearby bins, so the collapse happens on all faces.
    buckets={};tolerance=.001
    for p in polys:
        for v in p.corners:
            key=tuple(math.floor(x/tolerance) for x in v.p);found=None
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for dz in (-1,0,1):
                        for q in buckets.get((key[0]+dx,key[1]+dy,key[2]+dz),[]):
                            if _dot(_sub(q,v.p),_sub(q,v.p))<tolerance*tolerance:found=q;break
            if found is not None:v.p=found
            else:buckets.setdefault(key,[]).append(v.p)
    # Remove BSP slivers below the core's 0.001 vertex-lookup grid. Preserve
    # the 0.0001 stored coordinates, needed for planar curved surfaces.
    cleaned=[]
    for p in polys:
        out=[];seen=set()
        for v in p.corners:
            local=tuple(round(x-y,3) for x,y in zip(v.p,origin))
            if local in seen:continue
            seen.add(local);out.append(v)
        part=p.part(out)
        if part:cleaned.append(part)
    polys=cleaned
    if not polys:raise MeshError('cut result collapsed below the 0.001 unit vertex grid')
    weld_polygons(polys)
    cleaned=[]
    for p in polys:
        corners=[];seen=set()
        for v in p.corners:
            v.p=tuple(round(x,4) for x in v.p)
            key=tuple(round(x-y,3) for x,y in zip(v.p,origin))
            if key not in seen:seen.add(key);corners.append(v)
        part=p.part(corners)
        if part:cleaned.append(part)
    polys=cleaned
    for p in polys:
        opts={}
        if p.attributes:
            opts={'u_axis':p.attributes['textureAxisU'][:3], 'v_axis':p.attributes['textureAxisV'][:3],
                  'scale':tuple(p.attributes['textureScale']),
                  'offset':(p.attributes['textureAxisU'][3],p.attributes['textureAxisV'][3])}
        m.face([_sub(v.p,origin) for v in p.corners],p.material,**opts)
    validate(m,closed)
    md=m.to_dmx(origin=origin,texture_size=texture_size,smoothing=float(node.get('smoothingAngle',40.0) or 0))
    fv=stream_map(md['faceVertexData']);fd=stream_map(md['faceData'])
    dest=md['edgeVertexIndices'];fs=md['edgeFaceIndices']
    tint=[];blend=[]
    lookups=[{i:v for i,v in zip(f.verts,p.corners)} for f,p in zip(m.faces,polys)]
    for h,fi in enumerate(fs):
        v=lookups[fi][dest[h]] if fi>=0 else Corner((0,0,0))
        fv['texcoord']['data'][h]=v.uv;tint.append(v.tint);blend.append(v.blend)
    for fi,p in enumerate(polys):
        for key,s in fd.items():
            if key in p.attributes and key!='materialindex':s['data'][fi]=p.attributes[key]
    md['faceVertexData']['streams'] += [mesh._paint_stream('VertexPaintTintColor',tint),mesh._paint_stream('VertexPaintBlendParams',blend)]
    return md


def split_polygon(poly, n, w):
    """BSP plane split, preserving UV and both paint streams at intersections."""
    distances=[_dot(n,v.p)-w for v in poly.corners]
    front=any(d>BSP_EPS for d in distances);back=any(d<-BSP_EPS for d in distances)
    if not front and not back:return 'coplanar',poly,None
    if not back:return 'front',poly,None
    if not front:return 'back',None,poly
    f=[];b=[]
    for i,a in enumerate(poly.corners):
        j=(i+1)%len(poly.corners);v=poly.corners[j];da,db=distances[i],distances[j]
        if da>=-BSP_EPS:f.append(a)
        if da<=BSP_EPS:b.append(a)
        if (da>BSP_EPS and db<-BSP_EPS) or (da<-BSP_EPS and db>BSP_EPS):
            c=a.lerp(v,da/(da-db));f.append(c);b.append(c)
    return 'split',poly.part(f),poly.part(b)


class BSP:
    def __init__(self, polys=(), depth=0):
        self.n=None;self.w=0.;self.polys=[];self.front=None;self.back=None;self.depth=depth
        if polys:self.build(list(polys))

    def build(self, polys):
        if not polys:return
        if self.depth>180:raise MeshError('cut BSP depth limit exceeded; simplify the mesh')
        if self.n is None:
            # The middle polygon avoids worst-case ordering for many stair/curved faces.
            p=polys[len(polys)//2];self.n=p.n;self.w=_dot(self.n,p.corners[0].p)
        front=[];back=[]
        for p in polys:
            kind,f,b=split_polygon(p,self.n,self.w)
            if kind=='coplanar':self.polys.append(p)
            else:
                if f:front.append(f)
                if b:back.append(b)
        for key,part in [('front',front),('back',back)]:
            if part:
                child=getattr(self,key)
                if child is None:child=BSP(depth=self.depth+1);setattr(self,key,child)
                child.build(part)

    def invert(self):
        self.polys=[p.flipped() for p in self.polys]
        if self.n is not None:self.n=_scale(self.n,-1);self.w=-self.w
        if self.front:self.front.invert()
        if self.back:self.back.invert()
        self.front,self.back=self.back,self.front

    def clip(self, polys):
        if self.n is None:return polys[:]
        front=[];back=[]
        for p in polys:
            kind,f,b=split_polygon(p,self.n,self.w)
            if kind=='coplanar':(front if _dot(self.n,p.n)>0 else back).append(p)
            else:
                if f:front.append(f)
                if b:back.append(b)
        if self.front:front=self.front.clip(front)
        back=self.back.clip(back) if self.back else []
        if len(front)+len(back)>MAX_FACES:raise MeshError('cut face budget exceeded')
        return front+back

    def clip_to(self, other):
        self.polys=other.clip(self.polys)
        if self.front:self.front.clip_to(other)
        if self.back:self.back.clip_to(other)

    def all(self):
        return self.polys+(self.front.all() if self.front else [])+(self.back.all() if self.back else [])


def subtract(target, cutter):
    a,b=BSP(target),BSP(cutter)
    a.invert();a.clip_to(b);b.clip_to(a);b.invert();b.clip_to(a);b.invert();a.build(b.all());a.invert()
    return a.all()


def select_faces(polys, selection='all'):
    if selection is None or selection=='all':return set(range(len(polys)))
    if isinstance(selection,list):
        if any(type(i) is not int or not 0<=i<len(polys) for i in selection):raise MeshError('invalid face index')
        selected=set(selection)
    elif isinstance(selection,dict):
        if set(selection)!={'mins','maxs'}:raise MeshError('face region requires mins and maxs')
        lo,hi=vector(selection['mins']),vector(selection['maxs'])
        if any(a>b for a,b in zip(lo,hi)):raise MeshError('region maxs must be >= mins')
        selected={i for i,p in enumerate(polys) if all(lo[k]-EPS<=sum(v.p[k] for v in p.corners)/len(p.corners)<=hi[k]+EPS for k in range(3))}
    elif isinstance(selection,str):
        directions={'+x':(1,0,0),'-x':(-1,0,0),'+y':(0,1,0),'-y':(0,-1,0),'+z':(0,0,1),'-z':(0,0,-1),'top':(0,0,1),'bottom':(0,0,-1)}
        if selection=='sides':selected={i for i,p in enumerate(polys) if abs(p.n[2])<=.707}
        elif selection in directions:selected={i for i,p in enumerate(polys) if _dot(p.n,directions[selection])>.707}
        else:raise MeshError('faces: all/top/bottom/sides/+x/-x/+y/-y/+z/-z, indices or {mins,maxs}')
    else:raise MeshError('invalid face selection')
    if not selected:raise MeshError('face selection is empty')
    return selected


def tint_value(value):
    tint=vector(value,4) if len(value)==4 else (*vector(value),1.)
    if not all(0<=x<=1 for x in tint):raise MeshError('tint must be RGB(A) in 0..1')
    return tint


def project_uv(polys, selected, spec, texture_size):
    keys(spec,{'mode','scale','rotate','offset','u_axis','v_axis','axis','origin','repeat'})
    mode=spec.get('mode','world')
    if mode not in ('world','box','planar','cylinder','fit'):raise MeshError('UV mode must be world/box/planar/cylinder/fit')
    if mode=='fit':return fit_uv(polys,selected,spec,texture_size)
    scale=vector(spec.get('scale',[.25,.25]),2);off=vector(spec.get('offset',[0,0]),2)
    if min(scale)<=0:raise MeshError('UV scale must be positive')
    theta=math.radians(number(spec.get('rotate',0)));c,s=math.cos(theta),math.sin(theta)
    origin=vector(spec.get('origin',[0,0,0]));axis=_norm(vector(spec.get('axis',[0,0,1])))
    # Planar shares ONE basis across all selected faces; box/world chooses per-face axes.
    first=polys[min(selected)].n
    pu,pv=basis(first)
    if 'u_axis' in spec or 'v_axis' in spec:pu,pv=_norm(vector(spec.get('u_axis'))),_norm(vector(spec.get('v_axis')))
    cu,cv=basis(axis)
    for fi in selected:
        p=polys[fi];tw,th=texture_size(p.material)
        u,v=mesh.world_axes(p.n) if mode in ('world','box') else (pu,pv)
        u,v=_add(_scale(u,c),_scale(v,s)),_add(_scale(u,-s),_scale(v,c))
        if mode!='cylinder' and (abs(_dot(u,p.n))>.999 or abs(_dot(v,p.n))>.999):raise MeshError('projection axis perpendicular to selected face')
        angles=[];radii=[]
        if mode=='cylinder':
            for q in p.corners:
                d=_sub(q.p,origin);x,y=_dot(d,cu),_dot(d,cv);angles.append(math.atan2(y,x));radii.append(math.hypot(x,y))
            if min(radii)<EPS:raise MeshError('cylinder UV meets axis; select sides only')
            angles=[angles[0]+((a-angles[0]+math.pi)%(2*math.pi)-math.pi) for a in angles]
        for j,q in enumerate(p.corners):
            if mode=='cylinder':
                x=angles[j]*sum(radii)/len(radii);y=-_dot(_sub(q.p,origin),axis)
                x,y=c*x+s*y,-s*x+c*y
            else:x,y=_dot(q.p,u),_dot(q.p,v)
            q.uv=((x/scale[0]+off[0])/tw,(y/scale[1]+off[1])/th)
        p.attributes.update(textureScale=scale,textureAxisU=(*u,off[0]),textureAxisV=(*v,off[1]))


def fit_uv(polys, selected, spec, texture_size):
    """Stretch the texture over each selected face exactly `repeat` times (posters, signs, plank ends): the face's
    extent along its world-aligned axes (rotated by `rotate`) maps to 0..repeat; offset is a fraction of the image."""
    rep=vector(spec.get('repeat',[1,1]),2);off=vector(spec.get('offset',[0,0]),2)
    if min(rep)<=0:raise MeshError('repeat must be positive')
    theta=math.radians(number(spec.get('rotate',0)));c,s=math.cos(theta),math.sin(theta)
    for fi in selected:
        p=polys[fi];tw,th=texture_size(p.material)
        u,v=mesh.world_axes(p.n)
        if 'u_axis' in spec or 'v_axis' in spec:u,v=_norm(vector(spec.get('u_axis'))),_norm(vector(spec.get('v_axis')))
        u,v=_add(_scale(u,c),_scale(v,s)),_add(_scale(u,-s),_scale(v,c))
        xs=[_dot(q.p,u) for q in p.corners];ys=[_dot(q.p,v) for q in p.corners]
        sx,sy=max(xs)-min(xs),max(ys)-min(ys)
        if sx<EPS or sy<EPS:raise MeshError('fit needs a face with area across its texture axes')
        for q,x,y in zip(p.corners,xs,ys):
            q.uv=((x-min(xs))/sx*rep[0]+off[0],(y-min(ys))/sy*rep[1]+off[1])
        # the same mapping in Hammer's terms: units per texel and a texel offset
        su,sv=sx/(rep[0]*tw),sy/(rep[1]*th)
        p.attributes.update(textureScale=(su,sv),textureAxisU=(*u,-min(xs)/su+off[0]*tw),textureAxisV=(*v,-min(ys)/sv+off[1]*th))


def polygon_area(points):
    total=(0.,0.,0.)
    for a,b in zip(points,points[1:]+points[:1]):total=_add(total,_cross(a,b))
    return math.sqrt(_dot(total,total))/2


def subdivide(poly, cell):
    """Split by the world grid of planes x/y/z = k*cell (the same BSP split as cut_mesh).

    Neighbouring faces are cut at identical edge points, so no slivers or T junctions
    appear between painted faces (fan triangles of cut faces produced zero-area slivers)."""
    if polygon_area([v.p for v in poly.corners])<1e-4:return [poly]
    pieces=[poly]
    for axis in range(3):
        values=[v.p[axis] for v in poly.corners];n=tuple(1. if k==axis else 0. for k in range(3))
        for k in range(math.floor(min(values)/cell)+1,math.ceil(max(values)/cell)):
            nxt=[]
            for piece in pieces:
                kind,front,back=split_polygon(piece,n,k*cell)
                nxt.extend(x for x in (front,back) if x is not None)
            pieces=nxt
            if len(pieces)>MAX_FACES:raise MeshError('paint grid face budget exceeded; increase cell')
    return pieces


def noise(p, seed, radius):
    coords=[x/radius for x in p];base=[math.floor(x) for x in coords];f=[x-math.floor(x) for x in coords]
    f=[t*t*(3-2*t) for t in f];out=0.
    for i in (0,1):
        for j in (0,1):
            for k in (0,1):
                h=((base[0]+i)*73856093 ^ (base[1]+j)*19349663 ^ (base[2]+k)*83492791 ^ seed*2654435761)&0xffffffff
                h=((h^(h>>16))*2246822519)&0xffffffff;h^=h>>13
                out+=(h/0xffffffff)*(f[0] if i else 1-f[0])*(f[1] if j else 1-f[1])*(f[2] if k else 1-f[2])
    return out


def paint(polys, selected, spec):
    cell=number(spec.get('cell',64));radius=number(spec.get('radius',96));strength=number(spec.get('strength',1))
    if not 4<=cell<=1024 or radius<=0 or not 0<=strength<=1:raise MeshError('paint cell 4..1024, radius>0, strength 0..1')
    seed=integer(spec.get('seed',1),0,2147483647,'seed')
    rules=spec.get('rule','bottom');rules=[rules] if isinstance(rules,str) else rules
    if not isinstance(rules,list) or not rules or set(rules)-{'bottom','corners','noise'}:raise MeshError('rule must be bottom/corners/noise or a list')
    points=[v.p for fi in selected for v in polys[fi].corners]
    lo=[min(p[k] for p in points) for k in range(3)];hi=[max(p[k] for p in points) for k in range(3)]
    horizontal=[k for k in (0,1) if hi[k]-lo[k]>EPS]
    base=number(spec.get('base_z',lo[2]));out=[]
    for fi,p in enumerate(polys):
        parts=subdivide(p,cell) if fi in selected else [p]
        if len(out)+len(parts)>MAX_FACES:raise MeshError('paint face budget exceeded; increase cell or select fewer faces')
        for part in parts:
            if fi in selected:
                for v in part.corners:
                    values=[]
                    if 'bottom' in rules:values.append(max(0.,min(1.,1-(v.p[2]-base)/radius)))
                    if 'corners' in rules:values.append(max(0.,1-min((min(abs(v.p[k]-lo[k]),abs(v.p[k]-hi[k])) for k in horizontal),default=radius)/radius))
                    if 'noise' in rules:values.append(noise(v.p,seed,radius))
                    weight=max(values)*strength
                    # Separate corners per polygon ensure painting one face never leaks onto another.
                    v.blend=(weight,*v.blend[1:])
            out.append(part)
    return out
