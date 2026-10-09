"""add_weapon: one weapon operation (stock base + own model + own shot); fields, entities and map safety."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cs2map import weapons
from cs2map.config import load_settings
from cs2map.project import OpError
from cs2map.vmap import VMap

PROJECT = SimpleNamespace(catalog=SimpleNamespace(defaults=lambda cls: {}))


def op(**kw):
    return {'op': 'add_weapon', 'name': 'zap', 'base': 'awp', 'origin': [0, 0, 40], **kw}


def build(m, spec, script='maps/scripts/mcp/w9_zap.vjs', ok=True):
    with patch('cs2map.weapons._check_assets'), patch('cs2map.weapons.ensure_hud'), \
         patch('cs2map.fire.ensure_assets'), patch('cs2map.ext.models.asset_path'), \
         patch('cs2map.weapons._rigged', return_value=(True, [])), \
         patch('cs2map.scripting.script_write', return_value={'ok': ok, 'cs_script': script}):
        return weapons.add_weapon(PROJECT, m, spec, {})


class Fields(unittest.TestCase):
    def test_bases_place_and_give(self):
        for base in ('awp', 'weapon_ak47', 'taser', 'knife', 'knife_t', 'nova', 'deagle'):
            with self.subTest(base=base):
                self.assertTrue(weapons.validate(op(base=base))['base'].startswith('weapon_'))
        self.assertEqual(weapons.validate(op(origin=None, give='ct'))['give'], 'ct')
        for bad in ({'base': 'hegrenade'}, {'base': 'c4'}, {'give': 'bots'}, {'origin': None},
                    {'damage': float('nan')}, {'damage': 0}, {'clip': 999}, {'color': [1, 2]}, {'model': '../x.vmdl'},
                    {'name': 'Bad'}, {'name': '../../foreign'}, {'speed': 3}, {'pickup': [0, 0, 0]}, {'impact': 'yes'},
                    {'sound': ['a'] * 5}, {'fire': {'projectile': {'speed': 1}}}):
            with self.subTest(bad=bad), self.assertRaises(OpError):
                weapons.validate(op(**bad))

    def test_names_and_scripted_fire(self):
        s = weapons.validate(op(fire={'charge': 1, 'charge_sound': 'mcp.c', 'beam': {}, 'sound': ['mcp.x', 'mcp.y']}))
        self.assertEqual(s['targetname'], 'w9_zap_weapon')
        self.assertEqual(s['entities']['glow'], 'w9_zap_glow')
        self.assertEqual(s['entities']['fire'], ['w9_zap_fire0', 'w9_zap_fire1'])
        code = weapons.render(s)
        self.assertIn('core, hud, fire, weapon', code)
        self.assertIn('core.command("w9_" + C.name + "_state"', code)
        self.assertNotIn('DestroyWeapons', code)

    def test_json_cannot_inject_code(self):
        code = weapons.render(weapons.validate(op(sound='"; throw Error(123); //')))
        self.assertIn('\\"; throw Error(123); //', code)
        self.assertNotIn('__CONFIG__', code)

    def test_former_operations_point_to_add_weapon(self):
        for name in ('add_custom_weapon', 'add_scripted_weapon'):
            with self.subTest(name=name), self.assertRaisesRegex(OpError, 'merged into add_weapon'):
                weapons.old_op(name)(PROJECT, None, {}, {})


class Entities(unittest.TestCase):
    def setUp(self):
        self.m = VMap.from_template(load_settings().cs2_path, 'empty')

    def classes(self):
        return sorted(self.m.classname(n) for n in self.m.entities())

    def test_lying_weapon_with_model_has_a_parked_copy_of_it(self):
        out = build(self.m, op(model='models/mcp/rail/rail.vmdl', impact=True, sound='mcp.zap'))
        self.assertEqual(self.classes(), ['info_particle_system', 'point_script', 'point_soundevent', 'prop_dynamic',
                                          'weapon_awp'])
        precache = self.m.entities(targetname='w9_zap_precache')[0]
        self.assertEqual(precache['origin'][2], 40 - 4096, 'the model is referenced by the map, out of sight')
        self.assertTrue(out['model_in_hands'])
        self.assertEqual(out['state'], 'w9_zap_state')
        with self.assertRaisesRegex(OpError, 'already exists'):
            build(self.m, op())

    def test_given_weapon_has_no_map_copy_and_rockets_have_pools(self):
        out = build(self.m, op(origin=None, give='all', fire={'magazine': 2, 'projectile': {'pool': 3}}))
        self.assertNotIn('weapon_awp', self.classes(), 'given at spawn: no weapon lies in the map')
        self.assertEqual(len(self.m.entities(targetname='w9_zap_proj_0')), 1)
        self.assertEqual(len(self.m.entities(targetname='w9_zap_proj_2')), 1)
        self.assertEqual(len(self.m.entities(targetname='w9_zap_boom_2')), 1)
        trail = self.m.entities(targetname='w9_zap_trail_1')[0]
        self.assertEqual(trail['entity_properties'].get('parentname'), 'w9_zap_proj_1', 'the trail rides on its rocket')
        self.assertEqual(out['id'], self.m.entities(targetname='w9_zap_script')[0]['nodeID'])

    def test_name_collision_and_failed_compile_leave_the_map_unchanged(self):
        node = self.m.new_entity('point_soundevent', {'targetname': 'w9_zap_fire1'}, [0, 0, 0], [0, 0, 0])
        self.m.attach(node)
        with self.assertRaisesRegex(OpError, 'duplicate weapon entity name'):
            build(self.m, op(sound=['mcp.first', 'mcp.second']))
        self.assertEqual(self.m.entities(), [node])
        with self.assertRaises(OpError):
            build(self.m, op(name='other'), ok=False)
        self.assertEqual(self.m.entities(), [node])

    def test_same_weapon_on_two_maps_shares_its_script_and_a_change_gets_a_new_one(self):
        scripts = []
        for damage in (25, 50, 25):
            m = VMap.from_template(load_settings().cs2_path, 'empty')
            with patch('cs2map.weapons._check_assets'), patch('cs2map.ext.models.asset_path'), \
                 patch('cs2map.scripting.script_write', side_effect=lambda p, n, c: {'ok': True, 'cs_script': n}):
                scripts.append(weapons.add_weapon(PROJECT, m, op(damage=damage), {})['script'])
        self.assertNotEqual(scripts[0], scripts[1])
        self.assertEqual(scripts[0], scripts[2])


if __name__ == '__main__':
    unittest.main()
