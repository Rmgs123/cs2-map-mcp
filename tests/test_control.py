import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from cs2map import control
from cs2map.config import PROJECT_ROOT
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


def sample():
    return {'op': 'add_controllable', 'name': 'unit', 'kind': 'drone', 'model': 'models/mcp_d/drone/drone.vmdl',
            'controller': {'model': 'models/mcp_d/remote/remote.vmdl', 'at': [100, 100, 40]},
            'origin': [200, 200, 2], 'bounds': {'mins': [0, 0, 0], 'maxs': [1024, 1024, 384]}, 'rotors': []}


class ControlValidation(unittest.TestCase):
    def test_stock_drone_gets_its_four_rotors(self):
        op = sample(); del op['rotors']; del op['model']; del op['controller']['model']
        spec = control.validate(op)
        self.assertEqual(spec['model'], control.DRONE_MODEL)
        self.assertEqual([r['offset'] for r in spec['rotors']], [[-26, -26, 20], [-26, 26, 20], [26, -26, 20], [26, 26, 20]])
        self.assertEqual(control.validate(spec), spec)
        own = sample(); del own['rotors']; own['model'] = 'models/mcp/my_drone/my_drone.vmdl'
        self.assertEqual(control.validate(own)['rotors'], [], 'an own model brings its own rotors')

    def test_defaults_and_no_mutation(self):
        op = sample(); before = copy.deepcopy(op)
        spec = control.validate(op)
        self.assertEqual(op, before)
        self.assertEqual(spec['speed'], 400)
        self.assertEqual(spec['battery'], 0)
        self.assertEqual((spec['physics'], spec['health'], spec['on_exit'], spec['mass']), (True, 50, 'hover', 8))
        kinematic = control.validate({**sample(), 'physics': False})
        self.assertEqual((kinematic['health'], kinematic['on_exit']), (0, 'home'))

    def test_reject_ambiguous_and_unsafe_inputs(self):
        variants = []
        def change(fn):
            op = sample(); fn(op); variants.append(op)
        change(lambda x: x.update(name='../other'))
        change(lambda x: x.update(speed=float('nan')))
        change(lambda x: x.update(speed=True))
        change(lambda x: x.update(battery=-1))
        change(lambda x: x.update(physics='yes'))
        change(lambda x: x.update(physics=False, health=50))
        change(lambda x: x.update(on_exit='home'))
        change(lambda x: x.update(health=-5))
        change(lambda x: x.update(camera={'chase': {'forward': -600}}))
        change(lambda x: x.update(kind='car'))
        change(lambda x: x.update(origin=[1, 1, 0]))
        change(lambda x: x.update(bounds={'mins': [0, 0, 0], 'maxs': [50, 50, 50]}))
        change(lambda x: x.update(hull={'mins': [0, 0, 0], 'maxs': [0, 2, 2]}))
        change(lambda x: x.update(model='models/../../outside.vmdl'))
        change(lambda x: x.update(sound='event;quit'))
        change(lambda x: x['controller'].update(unknown=1))
        change(lambda x: x.update(rotors=[{'model': x['model'], 'offset': [0, 0]}]))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                control.validate(value)

    @unittest.skipUnless(HAVE_CS2, 'CS2 unavailable')
    def test_relative_coordinates_and_room_bounds(self):
        from cs2map import layout
        p = Project(); m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        layout.add_layout(p, m, {'op': 'add_layout', 'name': 'test', 'rooms': [
            {'id': 'yard', 'origin': [0, 0, 0], 'size': [1024, 1024, 384], 'ceiling': False}]}, {})
        op = sample(); op.pop('origin'); op['bounds'] = {'room': 'yard'}
        op['controller']['at'] = {'room': 'yard', 'at': [.3, .5], 'z': 40}
        spec = control.validate(op, m)
        self.assertAlmostEqual(spec['controller']['at'][0], 307.2)
        self.assertEqual(spec['bounds'], {'mins': [0, 0, 0], 'maxs': [1024, 1024, 384]})


@unittest.skipUnless(HAVE_CS2, 'CS2 unavailable')
class ControlBatch(unittest.TestCase):
    def make_project(self):
        p = Project()
        p.load = lambda _: VMap.from_template(SETTINGS.cs2_path, 'empty')
        p.register_op('add_controllable', control.add_controllable)
        p.map_edit = control.map_edit_wrapper(p, p.map_edit)
        p.save = Mock()
        return p

    def test_dry_run_and_late_failure_never_write_assets(self):
        p = self.make_project()
        with patch.object(control, 'asset_path'), patch.object(control, 'check_hull'), patch.object(p.game, 'exists', return_value=True), \
             patch.object(control, 'write_asset') as write, patch.object(control.scripting, 'script_write') as script:
            result = p.map_edit('d8_unit', [sample()], dry_run=True)
            self.assertTrue(result['dry_run']); write.assert_not_called(); script.assert_not_called()
            with self.assertRaises(ValueError):
                p.map_edit('d8_unit', [sample(), {'op': 'not_an_op'}])
            write.assert_not_called(); script.assert_not_called(); p.save.assert_not_called()

    def test_hammer_refusal_precedes_asset_writes(self):
        p = self.make_project()
        p._check_hammer = Mock(side_effect=ValueError('unsaved Hammer'))
        with patch.object(control, 'write_asset') as write, self.assertRaisesRegex(ValueError, 'Hammer'):
            p.map_edit('d8_unit', [sample()])
        write.assert_not_called()

    def test_duplicate_names_and_persistent_ref(self):
        p = self.make_project()
        op = sample(); op['ref'] = 'vehicle'
        with patch.object(control, 'asset_path'), patch.object(control, 'check_hull'), patch.object(p.game, 'exists', return_value=True):
            result = p.map_edit('d8_unit', [op, {'op': 'update', 'id': '@vehicle', 'origin': [200, 200, 2]}], True)
            self.assertEqual(result['results'][0]['id'], result['results'][1]['id'])
            with self.assertRaisesRegex(ValueError, 'already attached'):
                p.map_edit('d8_unit', [op, op], True)


class ControlRuntime(unittest.TestCase):
    def test_collision_hull_covers_rotating_geometry(self):
        spec = control.validate(sample())
        with patch.object(control.visual, 'model_bounds', return_value=([-30, -30, 0], [30, 30, 20], 'MDAT')):
            control.check_hull(None, spec)
            spec['hull']['maxs'][0] = 30
            with self.assertRaisesRegex(ValueError, 'all rotations'):
                control.check_hull(None, spec)

    def run_harness(self, op, entry):
        code = control.generate(control.validate(op))[0]
        code = '\n'.join(line for line in code.splitlines() if not line.startswith('import '))
        harness = (control.DATA / 'test_runtime.cjs').read_text(encoding='utf-8')
        tmp_root = PROJECT_ROOT / '.local/tmp'; tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='d8-test-', dir=tmp_root) as tmp:
            path = Path(tmp) / 'runtime.cjs'
            path.write_text(harness + '\n' + code + f'\n{entry}();', encoding='utf-8')
            result = subprocess.run([shutil.which('node'), str(path)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_swept_wall_input_normalization_and_lifecycle(self):
        self.assertIn('CONTROL_RUNTIME_OK', self.run_harness({**sample(), 'physics': False}, 'runAcceptance'))

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_physics_inertia_hover_damage_destroy_respawn(self):
        self.assertIn('CONTROL_PHYSICS_OK', self.run_harness(sample(), 'runPhysics'))

    def test_physics_entities_and_effects(self):
        _, _, _, ops = control.generate(control.validate(sample()))
        body = next(o for o in ops if o['classname'] == 'prop_physics_override')
        self.assertEqual(body['keyvalues']['damagefilter'], 'd8_unit_no_pilot')
        self.assertEqual(body['classname'], 'prop_physics_override')
        self.assertEqual(body['keyvalues']['health'], control.NATIVE_HEALTH)
        effects = {o['keyvalues']['targetname']: o for o in ops if o['classname'] == 'info_particle_system'}
        self.assertEqual(set(effects), {'d8_unit_smoke', 'd8_unit_fire', 'd8_unit_boom'})
        self.assertEqual(effects['d8_unit_smoke']['keyvalues']['parentname'], 'd8_unit_body')
        quiet = control.generate(control.validate({**sample(), 'effects': False}))[3]
        self.assertFalse([o for o in quiet if o['classname'] == 'info_particle_system'])
