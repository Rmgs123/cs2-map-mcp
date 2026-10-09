"""Tiny Blender helpers shared by the stock environment templates."""
from __future__ import annotations

import bpy
import math
from mathutils import Vector


# These paths are returned by ``asset_search(kind="material")`` in the
# installed CS2 content.  Templates use them only when the caller did not
# provide a material, so a caller can always replace the stock surface.
STOCK_MATERIALS = {
    "crate": "materials/cs_italy/picrate1.vmat",
    "wood": "materials/wood/milbeams003.vmat",
    "metal": "materials/metal/metalwall031a.vmat",
    "concrete": "materials/concrete/concrete_ext_07.vmat",
    "plastic": "materials/pbr_defaults/default_white_plastic001.vmat",
    "rubber": "materials/vehicle/rubbertrainfloor001a.vmat",
    "fabric": "materials/pbr_defaults/default_cloth001.vmat",
    "barrel": "materials/models/props/de_train/barrel0003.vmat",
    "railing": "materials/metal/metalwall031a.vmat",
    "dumpster": "materials/metal/metalwall031a.vmat",
    "barrier": "materials/concrete/concrete_ext_07.vmat",
    "cement_bag": "materials/de_cache/misc/cement_bag_02.vmat",
}


def material(name, color=(0.55, 0.55, 0.55, 1.0), image=None, metallic=0.0, roughness=0.7):
    if isinstance(color, str):
        color = [int(color.lstrip('#')[i:i+2], 16) / 255 for i in (0, 2, 4)]
    else:
        color = list(color)
        if max(color[:3]) > 1:
            color[:3] = [v / 255 for v in color[:3]]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    # Blender truncates datablock names at 63 characters.  Preserve an
    # explicit Source path separately so long stock VMAT names still remap
    # correctly in ModelDoc.
    if isinstance(name, str) and name.replace('\\', '/').lower().endswith('.vmat'):
        mat["source_vmat"] = name.replace('\\', '/')
    mat.diffuse_color = (*color[:3], color[3] if len(color) > 3 else 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = mat.diffuse_color
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if image:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(image, check_existing=True)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def apply_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return obj


def project_uv(obj, tile_size=128.0, mode="tile"):
    """Dominant-normal box projection in author-space inches.

    ``face`` maps every major face over 0..1 (a crate texture). ``tile``
    divides positions by an explicit tile size, independent of prop size.
    Bevels use the same projection, rather than stretched smart-UV islands.
    """
    tile_size = float(tile_size)
    if not math.isfinite(tile_size) or tile_size <= 0:
        raise ValueError("uv_tile_size must be a finite positive number")
    mesh = obj.data
    layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    mesh.update()
    points = [obj.matrix_world @ v.co for v in mesh.vertices]
    lo = [min(p[i] for p in points) for i in range(3)]
    span = [max(p[i] for p in points)-lo[i] for i in range(3)]
    normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    for poly in mesh.polygons:
        n = normal_matrix @ poly.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        a, b = ((1, 2), (0, 2), (0, 1))[axis]
        flip = n[axis] < 0 if axis != 1 else n[axis] > 0
        for index in poly.loop_indices:
            p = points[mesh.loops[index].vertex_index]
            if mode == "face":
                u = (p[a]-lo[a])/max(span[a], 1e-9)
                v = (p[b]-lo[b])/max(span[b], 1e-9)
                u = 1-u if flip else u
            else:
                u, v = p[a]/tile_size, p[b]/tile_size
                u = -u if flip else u
            layer.data[index].uv = (u, v)


def cylinder_uv(obj, radius, depth, tile_size=128.0, mode="tile"):
    """Continuous cylinder sides (one circumference for an object texture)."""
    layer = obj.data.uv_layers.active or obj.data.uv_layers.new(name="UVMap")
    for poly in obj.data.polygons:
        ids = list(poly.loop_indices)
        points = [obj.data.vertices[obj.data.loops[i].vertex_index].co for i in ids]
        if abs(poly.normal.z) > .5:
            for i, p in zip(ids, points):
                divisor = 2*radius if mode == "face" else tile_size
                layer.data[i].uv = (p.x/divisor+.5, p.y/divisor+.5)
        else:
            angles = [(math.atan2(p.y, p.x)/(2*math.pi)) % 1 for p in points]
            if max(angles)-min(angles) > .5:
                angles = [u+1 if u < .5 else u for u in angles]
            for i, p, u in zip(ids, points, angles):
                if mode == "tile":
                    u *= 2*math.pi*radius/tile_size
                    v = (p.z+depth/2)/tile_size
                else:
                    v = (p.z+depth/2)/depth
                layer.data[i].uv = (u, v)


def box(name, size, location=(0, 0, 0), mat=None, bevel=0.5, uv_scale=1.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("soft_edges", "BEVEL")
        mod.width = bevel
        mod.segments = 2
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj["template_uv_scale"] = float(uv_scale)
    if mat:
        apply_material(obj, mat)
    return obj


def cylinder(name, radius, depth, location=(0, 0, 0), mat=None, vertices=32, uv_scale=1.0,
             uv_mode="tile"):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    for poly in obj.data.polygons:
        poly.use_smooth = len(poly.vertices) == 4
    if mat:
        apply_material(obj, mat)
    cylinder_uv(obj, radius, depth, 128.0/max(float(uv_scale), 1e-9), uv_mode)
    obj["template_uv_mode"] = "cylinder"
    obj["template_cylinder_uv_mode"] = uv_mode
    obj["template_uv_scale"] = float(uv_scale)
    return obj


def duplicate_collision(objects, name="model_col"):
    if not objects:
        return None
    pts = [obj.matrix_world @ Vector(c) for obj in objects for c in obj.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return box(name, hi - lo, (lo + hi) * 0.5, None)


def _material_value(params, stock, key=None):
    """Resolve a part material while retaining the global override contract.

    A stock path is used only for an untouched template.  ``material`` (or a
    part-specific ``<key>_material``) wins; passing a color/image also means
    the caller intentionally requested a generated Blender material.
    """
    if key and params.get(f"{key}_material") is not None:
        return params[f"{key}_material"]
    if params.get("material") is not None:
        return params["material"]
    if params.get("color") is not None or params.get("image") is not None:
        return "model"
    return STOCK_MATERIALS.get(stock, stock)


def stock_base(params, stock, key=None, default=(0.55, 0.55, 0.55, 1)):
    cfg = dict(params)
    cfg["material"] = _material_value(params, stock, key)
    return base(cfg, default)


def base(params, default=(0.55, 0.55, 0.55, 1)):
    return material(str(params.get("material", "model")), params.get("color", default),
                    params.get("image"), params.get("metallic", 0.0), params.get("roughness", 0.7))


def finish(objects, params, collision_objects=None, forward_degrees=0):
    """Apply a base offset and make convex collision copies.

    Templates may pass a smaller set of proxy objects for collision.  Proxy
    objects are removed after copying so they cannot accidentally become part
    of the render FBX.
    """
    objects = list(objects)
    extras = [] if collision_objects is None else [o for o in collision_objects if o not in objects]
    for obj in [*objects, *extras]:
        if forward_degrees:
            from mathutils import Matrix
            obj.matrix_world = Matrix.Rotation(math.radians(forward_degrees), 4, 'Z') @ obj.matrix_world
        obj.location.z += float(params.get("z", 0.0))
    bpy.context.view_layer.update()
    tile_size = float(params.get("uv_tile_size", 128))
    if not math.isfinite(tile_size) or tile_size <= 0:
        raise ValueError("uv_tile_size must be a finite positive number")
    for obj in objects:
        mode = obj.get("template_uv_mode", "tile")
        if mode != "cylinder":
            project_uv(obj, tile_size/max(obj.get("template_uv_scale", 1.0), 1e-9), mode)
        elif tile_size != 128 and obj.get("template_cylinder_uv_mode") == "tile":
            # Cylinder UVs are computed before its rotation, preserving a
            # continuous seam. Tile size remains a user override.
            for item in obj.data.uv_layers.active.data:
                item.uv *= 128/tile_size
    source = objects if collision_objects is None else list(collision_objects)
    # Separate convex pieces preserve gaps under tables/window frames.  A
    # template-provided proxy can instead collapse many pieces to one hull.
    for obj in source:
        copy = obj.copy()
        copy.data = obj.data.copy()
        copy.name = obj.name + '_col'
        bpy.context.collection.objects.link(copy)
    for obj in extras:
        bpy.data.objects.remove(obj, do_unlink=True)
    return objects
