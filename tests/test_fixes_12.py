"""Journal fixes of 1.2 (my/feedback.md, 26.10): agent model paths, operation limits that say what to do."""
import unittest

from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_fixes_12' + agent_suffix()


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class Fixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map import server
        cls.p = server.project
        cls.p.map_create(MAP, 'empty', overwrite=True)

    def edit(self, ops):
        return self.p.map_edit(MAP, ops, dry_run=True)

    def test_agent_models_from_any_stock_folder(self):
        for model in ('characters/models/tm_phoenix/tm_phoenix.vmdl', 'agents/models/ctm_sas/ctm_sas.vmdl'):
            res = self.edit([{'op': 'add_model', 'model': model, 'origin': [0, 0, 0], 'dynamic': True}])
            self.assertEqual(res['results'][0]['model'], model)
        with self.assertRaisesRegex(Exception, 'existing paths with this name: .*tm_phoenix/tm_phoenix.vmdl'):
            self.edit([{'op': 'add_model', 'model': 'models/tm_phoenix/tm_phoenix.vmdl', 'origin': [0, 0, 0]}])
        with self.assertRaises(Exception):
            self.edit([{'op': 'add_model', 'model': '../outside/x.vmdl', 'origin': [0, 0, 0]}])

    def test_stock_materials_without_textures_are_flagged(self):
        found = self.p.asset_search('leaves', 'material', 50)
        self.assertIn('materials/decals/oldleaves01.vmat', found.get('broken_stock', []))
        self.assertNotIn('materials/decals/oldleaves01.vmat', found['stock'])
        self.assertEqual(self.p.game.missing_textures('materials/dev/reflectivity_50.vmat'), [])

    def test_paved_lips(self):
        from cs2map import visual
        asphalt, slabs = 'materials/asphalt/hr_c/hr_asphalt_001.vmat', 'materials/concrete/concrete_floor_04.vmat'

        def lips(ops):
            self.p.map_create(MAP + '_lip', 'empty', overwrite=True)
            self.p.map_edit(MAP + '_lip', ops)
            m = self.p.load(MAP + '_lip')
            return [(p['code'], round(float(p['message'].split(' with a ')[1].split('-unit')[0]), 1))
                    for p in visual._lip_checks(m, visual._solid_meshes(m))]
        plaza = {'op': 'add_mesh', 'material': slabs, 'shape': {'shape': 'box', 'mins': [0, 0, -16], 'maxs': [1024, 1024, 0]}}

        def road(top, y0=-200, y1=1224):
            return {'op': 'add_mesh', 'material': asphalt,
                    'shape': {'shape': 'box', 'mins': [384, y0, top - 8], 'maxs': [640, y1, top]}}
        self.assertEqual(lips([plaza, road(3)]), [('visual_paved_lip', 3.0)])      # a road standing on a plaza
        self.assertEqual(lips([plaza, road(1)]), [])                               # sunk to 1 unit: the convention
        self.assertEqual(lips([plaza, road(6)]), [])                               # a kerb
        # side by side: a 1.5-unit seam is a flaw, 3 units is a low kerb
        self.assertEqual(lips([plaza, road(1.5, 1024, 1500)]), [('visual_paved_lip', 1.5)])
        self.assertEqual(lips([plaza, road(3, 1024, 1500)]), [])

    def test_limits_name_the_fix(self):
        with self.assertRaisesRegex(Exception, 'refs? .*carry over|"ref" names carry over'):
            self.p.map_edit(MAP, [{'op': 'delete', 'id': 1}] * 2001, dry_run=True)
        with self.assertRaisesRegex(Exception, '6..32'):
            self.edit([{'op': 'add_pipes', 'points': [[0, 0, 64], [64, 0, 64]], 'sides': 7.5}])
        with self.assertRaisesRegex(Exception, '2..33'):
            self.edit([{'op': 'add_pipes', 'points': [[0, 0, 64]]}])


if __name__ == '__main__':
    unittest.main()
