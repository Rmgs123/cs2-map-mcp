"""Tests for E: CMapGroup, CMapInstance prefabs, and CMapStaticOverlay decals."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.helpers import HAVE_CS2, agent_suffix, check_topology


class StructureGeometry(unittest.TestCase):
    def test_decal_frame_is_planar_and_wound(self):
        from cs2map.structure import _decal_mesh

        m = _decal_mesh((10, 20, 30), (0, 0, 1), (64, 32), 'materials/decals/sitea.vmat')
        self.assertEqual(len(m.positions), 4)
        self.assertEqual(len(m.faces), 1)
        self.assertAlmostEqual(m.normal(m.faces[0])[2], 1.0)
        lo, hi = m.bounds()
        self.assertAlmostEqual(hi[0] - lo[0], 64.0)
        self.assertAlmostEqual(hi[1] - lo[1], 32.0)
        self.assertAlmostEqual(hi[2] - lo[2], 0.0)

    def test_angle_to_normal(self):
        from cs2map.structure import _normal_from_angles

        self.assertEqual(_normal_from_angles((0, 0, 0)), (0.0, 0.0, 1.0))
        n = _normal_from_angles((90, 0, 0))
        self.assertAlmostEqual(n[0], 1.0, places=6)
        self.assertAlmostEqual(n[2], 0.0, places=6)

    def test_decal_uv_fits_image_at_arbitrary_position(self):
        from cs2map.structure import _decal_mesh

        for normal in ((0, 0, 1), (0, -1, 0), (1, 0, 0)):
            mesh = _decal_mesh((123, -456, 78), normal, (96, 64), 'material', (512, 256))
            data = mesh.to_dmx(texture_size=(512, 256))
            uv = data['faceVertexData']['streams'][0]['data']
            active = [uv[i] for i, face in enumerate(data['edgeFaceIndices']) if face >= 0]
            for axis in (0, 1):
                self.assertAlmostEqual(min(p[axis] for p in active), 0.0)
                self.assertAlmostEqual(max(p[axis] for p in active), 1.0)

    def test_prefab_names_reject_paths(self):
        from cs2map.structure import OpError, _prefab_name

        self.assertEqual(_prefab_name('e_corner.vmap'), 'e_corner')
        for value in ('../../corner', 'maps/prefabs/corner', 'C:\\corner'):
            with self.assertRaises((OpError, ValueError)):
                _prefab_name(value)


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class StructureOps(unittest.TestCase):
    MAP = 'e_structure_test' + agent_suffix()

    @classmethod
    def setUpClass(cls):
        from cs2map.ext.structure import register
        from cs2map.project import Project

        class DummyMCP:
            def tool(self):
                return lambda f: f

        cls.project = Project()
        register(DummyMCP(), cls.project)
        cls.project.map_create(cls.MAP, 'empty', overwrite=True)

    def test_group_move_duplicate_and_ungroup(self):
        p = self.project
        result = p.map_edit(self.MAP, [
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [64, 64, 64]},
             'material': 'materials/dev/dev_measuregeneric01.vmat', 'ref': 'a'},
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [96, 0, 0], 'maxs': [160, 64, 64]},
             'material': 'materials/dev/dev_measuregeneric01.vmat', 'ref': 'b'},
            {'op': 'group', 'ids': ['@a', '@b'], 'name': 'two_boxes', 'ref': 'g'},
            {'op': 'move', 'id': '@g', 'offset': [0, 128, 0]},
            {'op': 'duplicate', 'id': '@g', 'offset': [256, 0, 0], 'count': 1, 'ref': 'copy'},
        ], force=True)
        self.assertEqual(result['results'][2]['op'], 'group')
        self.assertEqual(result['results'][3]['ids'], [result['refs']['g']])
        groups = p.map_info(self.MAP, kind='group')['nodes']
        self.assertEqual(len(groups), 2)
        self.assertEqual([c['origin'][1] for c in groups[0]['children']], [160.0, 160.0])
        # A core delete on a group removes the whole subtree; ungroup restores
        # the children as direct map nodes.
        p.map_edit(self.MAP, [{'op': 'ungroup', 'id': result['refs']['copy']}], force=True)
        self.assertEqual(p.map_info(self.MAP, kind='group')['total_matching'], 1)
        p.map_edit(self.MAP, [{'op': 'delete', 'id': result['refs']['g']}], force=True)
        self.assertEqual(p.map_info(self.MAP, kind='group')['total_matching'], 0)

    def test_decal_has_overlay_schema_and_round_trips(self):
        from cs2map import dmx

        p = self.project
        result = p.map_edit(self.MAP, [{
            'op': 'add_decal', 'material': 'materials/decals/sitea.vmat',
            'origin': [32, 32, 65], 'normal': [0, 0, 1], 'size': [64, 32], 'ref': 'decal',
        }], force=True)
        node_id = result['refs']['decal']
        m = p.load(self.MAP)
        node = m.node(node_id)
        self.assertEqual(node.type, 'CMapStaticOverlay')
        self.assertEqual(node['projectionTargets'], [0])
        self.assertGreater(node['projectionFar'], 0)
        self.assertEqual(node['meshData']['materials'], ['materials/decals/sitea.vmat'])
        check_topology(self, node['meshData'], closed=False)
        # Ensure the binary codec can still parse the overlay after a save.
        self.assertIsNotNone(dmx.loads(m.to_bytes()))

        angled = p.map_edit(self.MAP, [{
            'op': 'add_decal', 'material': 'materials/decals/sitea.vmat',
            'origin': [128, 64, 96], 'angles': [90, 0, 0], 'size': 64,
        }], force=True)
        node = p.load(self.MAP).node(angled['results'][0]['id'])
        self.assertEqual(node['angles'], (0.0, 0.0, 0.0))
        normals = node['meshData']['faceVertexData']['streams'][1]['data']
        active = [normals[i] for i, face in enumerate(node['meshData']['edgeFaceIndices']) if face >= 0]
        self.assertTrue(all(abs(n[0] - 1.0) < 1e-6 for n in active))

    def test_prefab_save_and_three_instances(self):
        from cs2map import structure

        p = self.project
        result = p.map_edit(self.MAP, [{
            'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [64, 64, 64]},
            'material': 'materials/dev/dev_measuregeneric01.vmat', 'ref': 'piece',
        }], force=True)
        saved = structure.prefab_save(p, self.MAP, [result['refs']['piece']], 'crate_corner')
        self.assertEqual(saved['prefab'], 'maps/prefabs/e_crate_corner.vmap')
        info = structure.prefab_info(p, 'crate_corner')
        self.assertTrue(info['isprefab'])
        self.assertEqual(info['nodes'], 1)
        placed = p.map_edit(self.MAP, [
            {'op': 'add_prefab', 'prefab': 'crate_corner', 'origin': [0, 256, 0]},
            {'op': 'add_prefab', 'prefab': 'e_crate_corner.vmap', 'origin': [256, 256, 0]},
            {'op': 'add_prefab', 'prefab': 'crate_corner', 'origin': [512, 256, 0]},
        ], force=True)
        self.assertEqual(len([r for r in placed['results'] if r['op'] == 'add_prefab']), 3)
        m = p.load(self.MAP)
        instances = [n for n in m.iter_nodes() if n.type == 'CMapInstance']
        self.assertEqual(len(instances), 3)
        self.assertTrue(all(n.get('target') is not None for n in instances))
        with self.assertRaises(structure.OpError):
            structure.ungroup_node(m, instances[0]['target']['nodeID'])
        with self.assertRaises(structure.OpError):
            structure.add_prefab(p, m, {'prefab': 'crate_corner', 'inline': True,
                                      'angles': [0, 90, 0]}, {})
        with patch('cs2map.structure.system.hammer_state', return_value={'open': True, 'dirty': True}):
            with self.assertRaises(structure.MapError):
                structure.prefab_save(p, self.MAP, [result['refs']['piece']], 'crate_corner')


if __name__ == '__main__':
    unittest.main()
