"""Anatomical frames independent of FBX bone-axis conventions (Blender)."""
from mathutils import Matrix, Vector


def frame(direction, across):
    x = direction.normalized()
    y = across - x * across.dot(x)
    if y.length < 1e-6:
        raise ValueError('Degenerate anatomical frame')
    y.normalize()
    z = x.cross(y).normalized()
    return Matrix((x, y, z)).transposed()


def segment(a, b, target_a, target_b, across=None, target_across=None,
            radial_scale=1.0):
    """Match endpoints without scaling the whole cross-section by bone length."""
    v, tv = b-a, target_b-target_a
    # Source1 coincident pivots acquire several micro-units of float noise
    # through FBX. Dividing by that residual sends torso vertices to infinity.
    if min(v.length, tv.length) < 1e-4:
        return Matrix.Translation(target_a-a)
    if across is not None and target_across is not None:
        rotation = frame(tv, target_across) @ frame(v, across).transposed()
    else:
        rotation = v.rotation_difference(tv).to_matrix()
    axis = v.normalized()
    stretch = Matrix.Identity(3) * radial_scale
    for i in range(3):
        for j in range(3):
            stretch[i][j] += (tv.length/v.length-radial_scale)*axis[i]*axis[j]
    return Matrix.Translation(target_a) @ (rotation @ stretch).to_4x4() @ Matrix.Translation(-a)


def rigid_at(transform, source_pivot, target_pivot):
    """Carry terminal bone roll without inheriting a parent's length scale."""
    rotation = transform.to_quaternion().to_matrix().to_4x4()
    return Matrix.Translation(target_pivot) @ rotation @ Matrix.Translation(-source_pivot)
