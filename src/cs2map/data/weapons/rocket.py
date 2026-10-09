"""Default projectile of the fire engine: a small rocket. Inches, front +X, origin at its middle, ~22 long."""
import math
import bpy


def material(name, path, color):
    mat = bpy.data.materials.new(name)
    mat['source_vmat'] = path
    mat.diffuse_color = (*color, 1)
    return mat


body = material('body', PARAMS['body'], (.27, .32, .23))
dark = material('dark', PARAMS['dark'], (.12, .12, .13))
flame = material('flame', PARAMS['flame'], (1, .6, .25))


def cylinder(name, x0, x1, r0, r1, mat, sides=20):
    """A cone frustum along X from x0 (radius r0) to x1 (radius r1)."""
    bpy.ops.mesh.primitive_cone_add(vertices=sides, radius1=r0, radius2=r1, depth=x1 - x0,
                                    location=((x0 + x1) / 2, 0, 0), rotation=(0, math.pi / 2, 0))
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


cylinder('body', -8, 6, 1.6, 1.6, body)
cylinder('nose', 6, 11, 1.6, .15, body)
cylinder('band', 3, 4, 1.7, 1.7, dark)
cylinder('nozzle', -11, -8, 1.1, 1.5, dark)
cylinder('flame', -11.6, -11, .9, .9, flame, 12)
for i in range(4):
    a = i * math.pi / 2
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-7, math.cos(a) * 2.6, math.sin(a) * 2.6),
                                    rotation=(a, 0, 0))
    fin = bpy.context.object
    fin.name = f'fin{i}'
    fin.dimensions = (4, 2.4, .25)
    fin.data.materials.append(dark)
