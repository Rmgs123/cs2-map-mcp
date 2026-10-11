"""Batch editing through Project (the layer the MCP tools call). Writes a scratch map in the addon."""
import unittest

from tests.helpers import HAVE_CS2, agent_suffix

MAP = 'test_project_ops' + agent_suffix()


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class BatchOps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map.project import Project
        cls.p = Project()
        cls.p.map_create(MAP, 'empty', overwrite=True)

    def test_batch_is_atomic(self):
        from cs2map.project import OpError
        before = self.p.map_info(MAP)['nodes']
        with self.assertRaises(OpError) as ctx:
            self.p.map_edit(MAP, [
                {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, 0, 0]},
                {'op': 'add_entity', 'classname': 'no_such_entity_class', 'origin': [0, 0, 0]},
            ])
        self.assertIn('#1', str(ctx.exception))
        self.assertEqual(self.p.map_info(MAP)['nodes'], before)

    def test_refs_connect_duplicate_move_delete(self):
        r = self.p.map_edit(MAP, [
            {'op': 'add_brush_entity', 'classname': 'func_button',
             'shape': {'shape': 'box', 'mins': [0, 0, 48], 'maxs': [8, 32, 80]}, 'ref': 'b'},
            {'op': 'add_brush_entity', 'classname': 'func_door', 'keyvalues': {'targetname': 'door1'},
             'shape': {'shape': 'box', 'mins': [64, 0, 0], 'maxs': [72, 64, 112]}, 'ref': 'door'},
            {'op': 'connect', 'from': '@b', 'output': 'OnPressed', 'target': 'door1', 'input': 'Open'},
            {'op': 'add_entity', 'classname': 'prop_static', 'origin': [0, 0, 0],
             'keyvalues': {'model': 'models/props/de_nuke/crate_large.vmdl'}, 'ref': 'crate'},
            {'op': 'duplicate', 'id': '@crate', 'offset': [100, 0, 0], 'count': 3, 'ref': 'copies'},
            {'op': 'move', 'id': '@door', 'offset': [0, 0, 8]},
            {'op': 'update', 'id': 'door1', 'keyvalues': {'speed': 200}},
        ])
        info = self.p.map_info(MAP, ids=[r['refs']['b']], full=True)['nodes'][0]
        self.assertEqual(info['connections'][0]['target'], 'door1')
        crates = self.p.map_info(MAP, classname='prop_static')['nodes']
        xs = sorted(n['origin'][0] for n in crates)
        self.assertEqual(xs[-4:], [0.0, 100.0, 200.0, 300.0])
        door = self.p.map_info(MAP, ids=[r['refs']['door']], full=True)['nodes'][0]
        self.assertEqual(door['keyvalues']['speed'], '200')
        self.assertAlmostEqual(door['children'][0]['bounds'][0][2], 8.0)
        self.p.map_edit(MAP, [{'op': 'delete', 'ids': [n['id'] for n in crates]}])
        self.assertEqual(self.p.map_info(MAP, classname='prop_static')['total_matching'], 0)

    def test_saved_map_reused_until_the_file_changes(self):
        import os
        self.p.map_edit(MAP, [{'op': 'add_entity', 'classname': 'info_target', 'origin': [1, 2, 3],
                               'keyvalues': {'targetname': 'cache_probe'}}])
        cached = self.p._saved_map
        self.assertIsNotNone(cached, 'map_edit keeps the parsed copy of what it wrote')
        m = self.p.load(MAP)
        self.assertIs(m.doc, cached[3])
        self.assertIsNone(self.p._saved_map, 'handed out once: a caller may change it')
        self.assertEqual(len(m.entities(targetname='cache_probe')), 1)
        self.p.map_edit(MAP, [{'op': 'update', 'id': 'cache_probe', 'keyvalues': {'spawnflags': 1}}])
        path = self.p._map_file(MAP)
        stale = self.p._saved_map[3]
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))   # changed outside
        fresh = self.p.load(MAP)
        self.assertIsNot(fresh.doc, stale, 'a file changed by someone else is read again')
        self.assertEqual(fresh.entities(targetname='cache_probe')[0]['entity_properties']['spawnflags'], '1')

    def test_point_vs_brush_entity_checks(self):
        from cs2map.project import OpError
        with self.assertRaises(OpError):
            self.p.map_edit(MAP, [{'op': 'add_entity', 'classname': 'func_buyzone'}], dry_run=True)
        with self.assertRaises(OpError):
            self.p.map_edit(MAP, [{'op': 'add_brush_entity', 'classname': 'light_omni2',
                                   'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [1, 1, 1]}}], dry_run=True)

    def test_trigger_gets_trigger_material(self):
        r = self.p.map_edit(MAP, [{'op': 'add_brush_entity', 'classname': 'trigger_multiple',
                                   'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [64, 64, 64]}}], dry_run=True)
        self.assertTrue(r['dry_run'])


class CompactShot(unittest.TestCase):
    def test_console_lines_of_a_view_command_are_kept(self):
        from cs2map.project import _compact_shot
        shot = {'name': 'v', 'path': 'p.png', 'console': ['DONE'],
                'player_check': {'position': [1.04, 2, 3], 'angles': [5, 0, 0]}}
        self.assertEqual(_compact_shot(shot), {'name': 'v', 'path': 'p.png', 'eye': [1.0, 2, 3],
                                               'ang': [5, 0, 0], 'console': ['DONE']})
        self.assertNotIn('console', _compact_shot({'name': 'v', 'path': 'p.png'}))


if __name__ == '__main__':
    unittest.main()
