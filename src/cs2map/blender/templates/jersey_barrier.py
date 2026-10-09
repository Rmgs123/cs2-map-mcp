"""Extruded jersey profile: flared foot, sloping shoulders, narrow top."""
import bpy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import stock_base, finish


def build(params):
    mat = stock_base(params, "barrier", default=(0.42, 0.44, 0.43, 1))
    length = float(params.get("length", params.get("width", 128)))
    depth = float(params.get("depth", 30))
    height = float(params.get("height", 44))
    top = float(params.get("top_width", 8))
    if not 0 < top < depth or min(length, height) <= 0:
        raise ValueError("jersey needs positive length/height and 0 < top_width < depth")
    profile = [(-depth/2, 0), (depth/2, 0), (depth*.2, height*.24),
               (top/2, height), (-top/2, height), (-depth*.2, height*.24)]
    n = len(profile)
    verts = [(x, y, z) for x in (-length/2, length/2) for y, z in profile]
    faces = [(i, (i+1) % n, n+(i+1) % n, n+i) for i in range(n)]
    faces += [tuple(reversed(range(n))), tuple(range(n, 2*n))]
    mesh = bpy.data.meshes.new("jersey_barrier_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("jersey_barrier", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    bevel = obj.modifiers.new("soft_edges", "BEVEL")
    bevel.width = float(params.get("bevel", .75))
    bevel.segments = 2
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    obj.select_set(False)
    return finish([obj], params)
