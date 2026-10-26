"""add_model tint and skin: a material group is checked against the compiled model and the error lists the real ones."""
import unittest

from cs2map import checks, visual
from cs2map.project import OpError
from tests.helpers import HAVE_CS2, agent_suffix

TREE = 'models/cs_italy/foliage/tree/generic_tree_large_1.vmdl'
MAP = 'test_model_looks' + agent_suffix()


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class ModelLooks(unittest.TestCase):
    def setUp(self):
        from cs2map import server
        self.p = server.project
        self.p.map_create(MAP, 'empty', overwrite=True)

    def test_groups_are_read_from_the_compiled_model(self):
        self.assertEqual(visual.model_material_groups(self.p, TREE), ['default', 'no_motion'])

    def test_skin_and_tint(self):
        out = self.p.map_edit(MAP, [{'op': 'add_model', 'model': TREE, 'origin': [0, 0, 0], 'skin': 'no_motion',
                                     'tint': [255, 170, 90]}])
        ent = self.p.load(MAP).node(out['results'][0]['id'])
        props = checks._props(ent)
        kv = {k: props.get(k) for k in ('skin', 'rendercolor')}
        self.assertEqual(kv, {'skin': 'no_motion', 'rendercolor': '255 170 90'})
        with self.assertRaisesRegex(OpError, 'it has: default, no_motion'):
            self.p.map_edit(MAP, [{'op': 'add_model', 'model': TREE, 'origin': [0, 0, 0], 'skin': 'autumn'}])
        with self.assertRaisesRegex(OpError, 'tint is'):
            self.p.map_edit(MAP, [{'op': 'add_model', 'model': TREE, 'origin': [0, 0, 0], 'tint': [300, 0, 0]}])


if __name__ == '__main__':
    unittest.main()
