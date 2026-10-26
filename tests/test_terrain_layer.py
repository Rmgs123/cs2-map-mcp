"""add_terrain layer:true: the terrain meshes live in maps/prefabs/e_<map>_<terrain>.vmap behind a CMapPrefab, the map
file stays small, ground sampling still works, and the checks see the layered terrain as if it were inline."""
import unittest

from cs2map import terrain, visual
from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_terrain_layer' + agent_suffix()
LAND = {'op': 'add_terrain', 'name': 'land', 'bounds': [[-1024, -1024], [1024, 1024]], 'cell': 32, 'layer': True,
        'ops': [{'op': 'hill', 'center': [300, 0], 'radius': 600, 'height': 200}]}


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class TerrainLayer(unittest.TestCase):
    def setUp(self):
        from cs2map import server
        self.p = server.project
        self.p.map_create(MAP, 'basic', overwrite=True)

    def test_meshes_in_their_own_file(self):
        meshes = len([n for n in self.p.load(MAP).iter_nodes() if n.type == 'CMapMesh'])      # the template's
        out = self.p.map_edit(MAP, [LAND])['results'][0]
        rel = f'maps/prefabs/e_{MAP}_land.vmap'
        self.assertEqual(out['layer'], rel)
        self.assertEqual(out['ids'], [])
        layer = self.p.s.content_addon / rel
        self.assertTrue(layer.is_file())
        m = self.p.load(MAP)
        self.assertEqual(len([n for n in m.iter_nodes() if n.type == 'CMapMesh']), meshes)
        self.assertEqual([n['targetMapPath'] for n in m.iter_nodes() if n.type == 'CMapPrefab'], [rel])
        self.assertGreater(layer.stat().st_size, 10 * self.p.s.content_addon.joinpath(f'maps/{MAP}.vmap').stat().st_size)
        z = terrain.sample(terrain.select(m, 'land'), 300, 0)['z']
        self.assertAlmostEqual(z, 200, delta=1)                                    # plans still work
        # A slab whose top lies exactly on the flat ground: only the terrain's meshes (in the layer) show it.
        self.p.map_edit(MAP, [{'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-900, -900, -16], 'maxs': [-700, -700, 0]}}])
        codes = [x['code'] for x in visual.visual_check(self.p, MAP)['problems']]
        self.assertIn('visual_z_fighting', codes)

    def test_dry_run_writes_no_layer(self):
        rel = self.p.s.content_addon / f'maps/prefabs/e_{MAP}_dry.vmap'
        rel.unlink(missing_ok=True)
        self.p.map_edit(MAP, [dict(LAND, name='dry')], dry_run=True)
        self.assertFalse(rel.exists())


if __name__ == '__main__':
    unittest.main()
