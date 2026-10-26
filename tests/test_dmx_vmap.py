import tempfile
import unittest
from pathlib import Path

from tests.helpers import HAVE_CS2, SETTINGS, valve_map

from cs2map import dmx, mesh
from cs2map.config import ConfigError, ensure_inside
from cs2map.vmap import VMap

SMALL_VALVE_MAPS = [r'csgo\maps\templates\template_defuse.vmap', r'csgo\maps\templates\template_wingman.vmap',
                    r'csgo\maps\editor\toolscene_default.vmap',
                    r'csgo_addons\addon_template\maps\content_examples\lighting_info.vmap']


class DmxCodec(unittest.TestCase):
    def test_built_document_roundtrip(self):
        root = dmx.Element('CMapRootElement', '')
        child = dmx.Element('Thing', 'child')
        root.set('child', 'element', child)
        root.set('shared', 'element_array', [child, child])
        root.set('stub', 'element', dmx.Stub('0e2b7c3d-1111-2222-3333-444455556666'))
        for name, t, v in [('i', 'int', -5), ('f', 'float', 0.25), ('b', 'bool', True), ('s', 'string', 'héllo'),
                           ('bin', 'binary', b'\x00\x01'), ('t', 'time', 1234), ('c', 'color', (1, 2, 3, 4)),
                           ('v2', 'vector2', (1.0, 2.0)), ('v3', 'vector3', (1.0, 2.0, 3.0)),
                           ('q', 'qangle', (0.0, 90.0, 0.0)), ('m', 'matrix', tuple(float(i) for i in range(16))),
                           ('u64', 'uint64', 2**63 + 5), ('u8', 'uint8', 200), ('sa', 'string_array', ['a', 'b']),
                           ('fa', 'float_array', [0.5, 1.5])]:
            child.set(name, t, v)
        doc = dmx.Document(root=root, prefix=[{'x': dmx.Attr('string', 'y')}])
        back = dmx.loads(dmx.dumps(doc))
        c = back.root['child']
        self.assertIs(back.root['shared'][0], c)
        self.assertEqual(back.root['stub'], dmx.Stub('0e2b7c3d-1111-2222-3333-444455556666'))
        self.assertEqual(c['s'], 'héllo')
        self.assertEqual(c['u64'], 2**63 + 5)
        self.assertEqual(c['m'][15], 15.0)
        self.assertEqual(back.prefix[0]['x'].value, 'y')
        self.assertEqual(dmx.dumps(back), dmx.dumps(doc))

    def test_deleted_element_is_not_kept_as_orphan(self):
        root = dmx.Element('CMapRootElement', '')
        keep, gone = dmx.Element('Thing', 'keep'), dmx.Element('Thing', 'gone')
        gone.set('inner', 'element', dmx.Element('Inner', 'inner'))
        root.set('children', 'element_array', [keep, gone])
        orphan = dmx.Element('Orphan', 'valve')        # never reachable, like Valve's leftovers
        data = dmx.dumps(dmx.Document(root=root, element_order=[root, keep, gone, gone['inner'], orphan]))
        doc = dmx.loads(data)
        doc.root['children'] = [doc.root['children'][0]]
        back = dmx.loads(dmx.dumps(doc))
        names = [e.name for e in back.element_order]
        self.assertEqual(names, ['', 'keep', 'valve'])
        again = dmx.loads(dmx.dumps(back))            # a second save keeps the orphan, adds nothing
        self.assertEqual([e.name for e in again.element_order], names)

    def test_bad_values_rejected(self):
        e = dmx.Element('X')
        e.set('i', 'int', 2**40)
        with self.assertRaises(dmx.DmxError):
            dmx.dumps(dmx.Document(root=e))

    @unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
    def test_valve_maps_byte_exact(self):
        for rel in SMALL_VALVE_MAPS:
            with self.subTest(rel):
                data = valve_map(rel).read_bytes()
                self.assertEqual(dmx.dumps(dmx.loads(data)), data)


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class MapModel(unittest.TestCase):
    def test_new_map_edit_save_reload(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'basic')
        before = set(m.node_ids())
        box = m.attach(m.new_mesh(mesh.box((0, 0, 0), (64, 64, 64))))
        ent = m.attach(m.new_entity('info_player_terrorist', {'targetname': 't1'}, (10, 20, 30), (0, 90, 0)))
        self.assertNotIn(box['nodeID'], before)
        self.assertNotEqual(box['nodeID'], ent['nodeID'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'maps' / 't.vmap'
            m.save(path, backup_dir=Path(tmp) / 'bk')
            m2 = VMap.load(path)
            e2 = m2.entities('info_player_terrorist')[0]
            self.assertEqual(e2['origin'], (10.0, 20.0, 30.0))
            self.assertEqual(e2['entity_properties']['targetname'], 't1')
            self.assertEqual(m2.node(box['nodeID']).type, 'CMapMesh')
            m2.remove(m2.node(ent['nodeID']))
            m2.save(backup_dir=Path(tmp) / 'bk')
            from cs2map import backups
            self.assertTrue(backups.flush(20))
            self.assertEqual(len(list((Path(tmp) / 'bk' / 't').glob('*.vmap.zst'))), 1)
            self.assertEqual(VMap.load(path).entities('info_player_terrorist'), [])
            self.assertIn('materials/dev/reflectivity_50.vmat', VMap.load(path).asset_references())

    def test_ids_unique_after_clone(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'defuse')
        spawn = m.entities('info_player_terrorist')[0]
        dup = m.attach(m.clone(spawn))
        ids = [n['nodeID'] for n in m.iter_nodes()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotEqual(dup.id, spawn.id)
        refs = [n['referenceID'] for n in m.iter_nodes()]
        self.assertEqual(len(refs), len(set(refs)))


class Paths(unittest.TestCase):
    def test_outside_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'allowed'
            root.mkdir()
            self.assertEqual(ensure_inside(root / 'a' / 'b.txt', root), (root / 'a' / 'b.txt').resolve())
            for bad in (root / '..' / 'x.txt', Path(tmp) / 'other.txt'):
                with self.assertRaises(ConfigError):
                    ensure_inside(bad, root)


if __name__ == '__main__':
    unittest.main()
