"""Transfer a humanoid mesh to the extracted CS2 bind skeleton in Blender."""
from mathutils import Matrix, Quaternion, Vector
from math import atan2
import bpy
from mapping import resolve, TARGETS
from deformation import segment, rigid_at


def _distortion_zone(names):
    """Return a stable body zone for an edge's strongest source influences."""
    text = " ".join(names).lower()
    if any(k in text for k in ("head", "neck", "eye", "hair", "ear")):
        return "head"
    if any(k in text for k in ("pelvis", "spine", "chest", "hip")):
        return "pelvis"
    if any(k in text for k in ("thigh", "calf", "leg", "foot", "ankle", "toe")):
        return "thighs"
    if any(k in text for k in ("upperarm", "upper_arm", "clavicle", "shoulder")):
        return "shoulders"
    if any(k in text for k in ("forearm", "lower_arm", "hand", "wrist", "finger")):
        return "forearms"
    return "other"


def _heat_color(value):
    """Blue/green/red colour for a length ratio around 1.0."""
    t = max(0.0, min(1.0, (float(value) - 0.70) / 0.60))
    if t < 0.5:
        return (0.0, t * 2.0, 1.0 - t * 2.0, 1.0)
    return ((t - 0.5) * 2.0, 1.0 - (t - 0.5) * 2.0, 0.0, 1.0)

def run(meshes, source, manifest, reference):
    mapping = resolve([b.name for b in source.data.bones], manifest['bone_map'],
                      manifest.get('bone_map_overrides'))
    roles = mapping['roles']
    # Keep complete bind matrices, not only bone endpoints. Endpoint-only
    # alignment loses roll around the limb axis and is especially visible in
    # wrists, palms and fingers once the CS2 animation graph poses them.
    source_worlds = {b.name: source.matrix_world @ b.matrix_local for b in source.data.bones}
    points = {name: matrix.translation for name, matrix in source_worlds.items()}
    # Left-right anatomy defines forward without relying on FBX axis metadata.
    left = points[roles['upper_arm_l']] - points[roles['upper_arm_r']]
    up = points[roles['head']] - points[roles['pelvis']]
    forward = left.cross(up).normalized()
    explicit = manifest.get('forward','auto')
    if explicit != 'auto':
        forward = Vector({'+x':(1,0,0),'-x':(-1,0,0),'+y':(0,1,0),'-y':(0,-1,0)}[explicit])
    rotation = Matrix.Rotation(-atan2(forward.y, forward.x),4,'Z')
    vertices = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    lo, hi = min(v.z for v in vertices), max(v.z for v in vertices)
    scale = manifest['height'] / (hi-lo)
    normalize = Matrix.Scale(scale,4) @ rotation
    center = normalize @ points[roles['pelvis']]
    normalize = Matrix.Translation(Vector((-center.x,-center.y,-lo*scale))) @ normalize
    source_worlds = {k: normalize @ matrix for k, matrix in source_worlds.items()}
    points = {k: matrix.translation for k, matrix in source_worlds.items()}
    worlds = {}
    for b in reference['bones']:
        x,y,z,w = b['rotation_xyzw']
        local = Matrix.LocRotScale(Vector(b['position']),Quaternion((w,x,y,z)),Vector((b['scale'],)*3))
        worlds[b['name']] = worlds[b['parent']] @ local if b['parent'] else local

    # Convert every source bind-space vertex into the corresponding target
    # bind-space vertex. This preserves full rest-pose rotation (roll) from
    # both rigs; aligning only segment direction twists forearms and fingers.
    chain = {'pelvis':'spine_0','spine_0':'spine_1','spine_1':'spine_2',
             'spine_2':'neck','neck':'head'}
    for s in ('l','r'):
        for a,b in [('clavicle','upper_arm'),('upper_arm','lower_arm'),('lower_arm','hand'),
                    ('thigh','calf'),('calf','foot'),('foot','toe')]: chain[f'{a}_{s}']=f'{b}_{s}'
        for f in ('thumb','index','middle','ring','pinky'):
            for i in (0,1): chain[f'{f}_{i}_{s}']=f'{f}_{i+1}_{s}'
    transforms={}
    # A pelvis's short first spine segment must not shrink the whole skirt.
    # Torso roll uses body left-right; palms use index-to-pinky, not FBX axes.
    source_across = points[roles['upper_arm_l']]-points[roles['upper_arm_r']]
    target_across = worlds['arm_upper_L'].translation-worlds['arm_upper_R'].translation
    hand_across = {}
    for s in ('l','r'):
        if all(f'{f}_0_{s}' in roles for f in ('index','middle','pinky')):
            hand_across[s] = (
                points[roles[f'index_0_{s}']]-points[roles[f'pinky_0_{s}']],
                worlds[TARGETS[f'index_0_{s}']].translation-worlds[TARGETS[f'pinky_0_{s}']].translation)
            chain[f'hand_{s}']=f'middle_0_{s}'
    for role, bone in roles.items():
        a, ta = points[bone], worlds[TARGETS[role]].translation
        end = chain.get(role)
        across = target_side = None
        if role in ('spine_0','spine_1','spine_2','neck'):
            across, target_side = source_across, target_across
        if role.startswith(('hand_', 'thumb_', 'index_', 'middle_', 'ring_', 'pinky_')):
            across, target_side = hand_across.get(role[-1], (None,None))
        if end in roles and role != 'pelvis':
            transforms[bone]=segment(a, points[roles[end]], ta, worlds[TARGETS[end]].translation,
                                      across, target_side)
        else:
            parent = source.data.bones[bone].parent
            while parent and parent.name not in transforms:
                parent = parent.parent
            transforms[bone]=rigid_at(transforms[parent.name], a, ta) if parent else Matrix.Translation(ta-a)

    # Extras inherit the nearest mapped ANCESTOR, keeping face/hair on head
    # and skirt/tail on pelvis; spatial nearest can cross left/right limbs.
    transfer = {}; fallback=[]
    for b in source.data.bones:
        ancestor=b
        while ancestor and ancestor.name not in mapping['source_to_target']:
            ancestor=ancestor.parent
        transfer[b.name]=ancestor.name if ancestor else roles['pelvis']
        if ancestor is None: fallback.append(b.name)
    # Source 1 eye rigs may be independent roots even though their vertices
    # belong to the face. Explicit roles avoid silently moving them to pelvis.
    for bone, role in manifest.get('extra_bone_roles', {}).items():
        if bone not in transfer or role not in roles:
            raise ValueError(f'Invalid extra bone role: {bone} -> {role}')
        transfer[bone] = roles[role]
        if bone in fallback: fallback.remove(bone)

    data=bpy.data.armatures.new('cs2_bind'); target=bpy.data.objects.new('cs2_player',data)
    bpy.context.collection.objects.link(target)
    bpy.ops.object.select_all(action='DESELECT'); target.select_set(True); bpy.context.view_layer.objects.active=target
    bpy.ops.object.mode_set(mode='EDIT')
    axis=Matrix.Rotation(-1.5707963267948966,4,'Z')
    for b in reference['bones']:
        eb=data.edit_bones.new(b['name']); eb.head=(0,0,0); eb.tail=(0,2,0)
        eb.matrix=worlds[b['name']] @ axis
        if b['parent']: eb.parent=data.edit_bones[b['parent']]
    bpy.ops.object.mode_set(mode='OBJECT')

    total=extra=0.0; unweighted=0
    distortion_edges=[]
    distortion_zone_values={}
    for obj in meshes:
        # FBX supplies custom split normals. Moving positions alone leaves
        # their directions in the original T pose and creates metallic shards.
        obj.data.update()
        source_normals = [n.vector.copy() for n in obj.data.corner_normals]
        normal_world = (normalize @ obj.matrix_world).to_3x3().inverted().transposed()
        positions=[]; weights=[]; groups={g.index:g.name for g in obj.vertex_groups}
        source_positions=[]
        normal_transforms=[]
        for v in obj.data.vertices:
            p=normalize @ obj.matrix_world @ v.co
            source_positions.append(p.copy())
            src_weights=[(groups[g.group],g.weight) for g in v.groups if groups[g.group] in transfer and g.weight>0]
            if not src_weights:
                src_weights=[(roles['pelvis'],1.0)]; unweighted+=1
            denom=sum(w for _,w in src_weights); position=Vector((0,0,0)); merged={}
            normal_matrix = Matrix(((0,0,0),(0,0,0),(0,0,0)))
            for n,w in src_weights:
                w/=denom; mapped=transfer[n]; t=mapping['source_to_target'][mapped]
                position += (transforms[mapped] @ p)*w
                normal_matrix += transforms[mapped].to_3x3().inverted().transposed() * w
                merged[t]=merged.get(t,0)+w; total+=w
                if n!=mapped: extra+=w
            # CS2 skinning supports 4 strongest influences.
            strongest=sorted(merged.items(),key=lambda item:item[1],reverse=True)[:4]
            weight_sum=sum(w for _,w in strongest)
            positions.append(position); weights.append([(n,w/weight_sum) for n,w in strongest])
            normal_transforms.append(normal_matrix @ normal_world)
        # Measure each original mesh edge after the same global normalization.
        # The ratio is independent of polygon tessellation and is retained in
        # a sidecar JSON so build_report.json stays compact for MCP callers.
        vertex_ratios=[[] for _ in obj.data.vertices]
        for edge_index, edge in enumerate(obj.data.edges):
            a, b = edge.vertices
            before=(source_positions[a]-source_positions[b]).length
            after=(positions[a]-positions[b]).length
            if before < 1e-6:
                continue
            ratio=after/before
            influence_names=[]
            for vi in (a,b):
                for g in obj.data.vertices[vi].groups:
                    source_name=groups.get(g.group)
                    if source_name in transfer:
                        mapped=transfer[source_name]
                        influence_names.append(mapping['source_to_target'].get(mapped,mapped))
            zone=_distortion_zone(influence_names)
            distortion_edges.append({'mesh':obj.name,'edge':edge_index,'v0':a,'v1':b,
                                     'ratio':round(float(ratio),6),'zone':zone})
            vertex_ratios[a].append(ratio); vertex_ratios[b].append(ratio)
            distortion_zone_values.setdefault(zone,[]).append(float(ratio))
        # Keep ratios in a colour attribute for the optional Blender heatmap.
        color_attr=obj.data.color_attributes.get('distortion_heatmap')
        if color_attr is None:
            color_attr=obj.data.color_attributes.new(name='distortion_heatmap',type='BYTE_COLOR',domain='CORNER')
        for loop in obj.data.loops:
            values=vertex_ratios[loop.vertex_index]
            value=sum(values)/len(values) if values else 1.0
            color_attr.data[loop.index].color=_heat_color(value)
        if obj.data.shape_keys:
            obj.shape_key_clear()
        obj.parent=None; obj.matrix_world=Matrix.Identity(4)
        for v,p in zip(obj.data.vertices,positions): v.co=p
        obj.data.update()
        obj.data.normals_split_custom_set([
            (normal_transforms[loop.vertex_index] @ normal).normalized()
            for loop,normal in zip(obj.data.loops,source_normals)])
        obj.vertex_groups.clear()
        for n in worlds: obj.vertex_groups.new(name=n)
        for i,ws in enumerate(weights):
            for n,w in ws: obj.vertex_groups[n].add([i],w,'REPLACE')
        for mod in list(obj.modifiers):
            if mod.type=='ARMATURE': obj.modifiers.remove(mod)
        mod=obj.modifiers.new('cs2_skin','ARMATURE'); mod.object=target
        obj.parent=target
    def _summary(values):
        values=sorted(values)
        if not values: return {'count':0}
        def q(p): return values[min(len(values)-1,int(round((len(values)-1)*p)))]
        return {'count':len(values),'min':round(values[0],6),'max':round(values[-1],6),
                'mean':round(sum(values)/len(values),6),'p05':round(q(.05),6),'p95':round(q(.95),6)}
    all_ratios=[e['ratio'] for e in distortion_edges]
    distortion={'edge_count':len(distortion_edges),'summary':_summary(all_ratios),
                'zones':{zone:_summary(vals) for zone,vals in sorted(distortion_zone_values.items())},
                'edges':distortion_edges}
    bpy.context.view_layer.update()
    return target, {**mapping,'status':'retargeted','source_bones':len(source.data.bones),
                    'target_bones':len(reference['bones']),'extra_weight_fraction':extra/max(total,1),
                    'fallback_bones':fallback,'unweighted_vertices':unweighted,
                    'forward_vector':list(forward),'scale_factor':scale,
                    'distortion':distortion,
                    'limitations':['Hair, skirt, tail and face weights merged to mapped ancestors.',
                                   'Limb proportions adapted to stock bind pose; not a physics rig.']}
