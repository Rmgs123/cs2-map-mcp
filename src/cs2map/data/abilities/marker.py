# Wallhack silhouette (abilities.py): a simple standing figure the size of a CS2 player (32 x 32 x 72),
# feet at z 0, facing +X. It is drawn fully transparent; only its glow outline shows, through walls.
import bpy

mat = bpy.data.materials.new("marker")
mat["source_vmat"] = "materials/dev/reflectivity_50.vmat"


def part(kind, name, size, at, rot=(0, 0, 0)):
    if kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=size[0], depth=size[1], location=at, rotation=rot)
    elif kind == "ball":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=size[0], location=at)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1, location=at)
        bpy.context.object.dimensions = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    return obj


part("cyl", "leg_l", (4.5, 34), (0, 5, 17))
part("cyl", "leg_r", (4.5, 34), (0, -5, 17))
part("box", "torso", (12, 20, 26), (0, 0, 46))
part("cyl", "arm_l", (3.5, 26), (0, 12.5, 44))
part("cyl", "arm_r", (3.5, 26), (0, -12.5, 44))
part("ball", "head", (6.5,), (0, 0, 64))
