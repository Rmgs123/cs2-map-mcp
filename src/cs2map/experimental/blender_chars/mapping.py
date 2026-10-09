"""Pure-Python humanoid mapping; also importable by headless Blender."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
TARGETS = {'pelvis':'pelvis', 'spine_0':'spine_0', 'spine_1':'spine_1',
           'spine_2':'spine_3', 'neck':'neck_0', 'head':'head_0'}
for side in ('l', 'r'):
    for role, target in [('clavicle','clavicle'), ('upper_arm','arm_upper'),
                         ('lower_arm','arm_lower'), ('hand','hand'), ('thigh','leg_upper'),
                         ('calf','leg_lower'), ('foot','ankle'), ('toe','ball')]:
        TARGETS[f'{role}_{side}'] = f'{target}_{side.upper()}'
    for finger in ('thumb','index','middle','ring','pinky'):
        for i in range(3):
            TARGETS[f'{finger}_{i}_{side}'] = f'finger_{finger}_{i}_{side.upper()}'

REQUIRED = ['pelvis','head','neck'] + [f'{r}_{s}' for r in
            ('upper_arm','lower_arm','hand','thigh','calf','foot') for s in ('l','r')]

def normalized(name):
    return name.rsplit(':',1)[-1].casefold()

def presets():
    return {p.stem:json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'bonemaps').glob('*.json')}

def resolve(bones, preset='auto', overrides=None):
    names = {normalized(n):n for n in bones}
    choices = presets()
    def matches(data):
        result = {}
        for role, source in data['roles'].items():
            for candidate in [source, *data.get('aliases',{}).get(source,[])]:
                if normalized(candidate) in names:
                    result[role] = names[normalized(candidate)]
                    break
        return result
    scores = {k:len(matches(v)) for k,v in choices.items()}
    if preset == 'auto':
        preset = max(scores,key=scores.get)
        if scores[preset] < 5:
            raise ValueError('No humanoid preset matches; supply bone_map and role overrides')
    if preset not in choices:
        raise ValueError(f'Unknown bone map: {preset}')
    result = matches(choices[preset])
    for role, source in (overrides or {}).items():
        if role not in TARGETS:
            raise ValueError(f'Unknown humanoid role: {role}')
        if not isinstance(source,str) or normalized(source) not in names:
            raise ValueError(f'Override bone not found: {source}')
        result[role] = names[normalized(source)]
    missing = [r for r in REQUIRED if r not in result]
    if missing:
        raise ValueError(f'Missing required humanoid roles: {missing}; use overrides')
    if len(set(result.values())) != len(result):
        raise ValueError('A source bone cannot map to multiple target roles')
    return {'preset':preset,'roles':result,'scores':scores,
            'source_to_target':{b:TARGETS[r] for r,b in result.items()},
            'unmapped_bones':[n for n in bones if n not in result.values()]}
