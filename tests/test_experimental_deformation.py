"""Regression checks of bind-pose geometry using the bundled Blender math API."""
import os
import subprocess
import unittest
from cs2map.config import PROJECT_ROOT, load_settings


class CharacterDeformation(unittest.TestCase):
    def test_segment_volume_and_terminal_roll(self):
        blender = load_settings().blender_exe
        if not blender.is_file():
            self.skipTest('Bundled Blender is unavailable')
        code = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'src/cs2map/experimental/blender_chars'))
from deformation import segment, rigid_at
from mathutils import Vector
a,b=Vector((0,0,0)),Vector((0,10,0))
ta,tb=Vector((2,3,4)),Vector((2,3,6))
t=segment(a,b,ta,tb,Vector((1,0,0)),Vector((0,1,0)))
assert (t@a-ta).length < 1e-5
assert (t@b-tb).length < 1e-5
# A five-times shorter bone must not collapse the skirt / cross-section.
assert abs(((t@Vector((3,5,0)))-(t@Vector((0,5,0)))).length-3) < 1e-5
assert ((t@Vector((1,0,0)))-ta-Vector((0,1,0))).length < 1e-5
# Terminal fingers inherit anatomical roll but never the segment's scale.
tip=rigid_at(t,b,tb)
assert (tip@b-tb).length < 1e-5
assert abs((tip@(b+Vector((0,1,0)))-tb).length-1) < 1e-5
# Coincident L4D2 Spine/Spine1 pivots after FBX rounding must remain bounded.
near=segment(a,Vector((0,0.000004,0)),ta,tb)
assert (near@Vector((1,2,3))-Vector((3,5,7))).length < 1e-5
print('CHARACTER_GEOMETRY_OK')
'''
        env = {**os.environ, 'BLENDER_USER_CONFIG': str(PROJECT_ROOT/'.local/scratch/D/blender_config'),
               'BLENDER_USER_SCRIPTS': str(PROJECT_ROOT/'.local/scratch/D/blender_scripts')}
        result = subprocess.run([str(blender), '-b', '--factory-startup', '--python-expr', code],
                                cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('CHARACTER_GEOMETRY_OK', result.stdout, result.stdout+result.stderr)
