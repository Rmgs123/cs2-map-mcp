"""The local layer my/: notes, recipes and own styles survive between chats and feed map_edit."""
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from cs2map import layout, mylayer, server
from cs2map.ext import my as my_ext
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, ROOT, SETTINGS

NORDIC = {'description': 'Nordic harbour: dark timber, white trim, slate roofs',
          'materials': {'wall': ['materials/dev/dev_measuregeneric01.vmat', 'materials/dev/reflectivity_30.vmat'],
                        'roof': 'materials/dev/reflectivity_50.vmat', 'floor': 'materials/dev/reflectivity_70.vmat',
                        'sidewalk': 'materials/dev/reflectivity_90.vmat'},
          'tints': {'wall': [[60, 40, 30], '#e0e0e0']},
          'sizes': {'floor_height': 128, 'house_width': 256},
          'light': {'preset': 'overcast'},
          'rules': ['steep roofs', 'no plaster']}


class Layer(unittest.TestCase):
    def setUp(self):
        self.dir = ROOT / '.local' / 'tmp' / f'my_test_{uuid.uuid4().hex[:8]}'
        self.old = os.environ.get('CS2MAP_MY')
        os.environ['CS2MAP_MY'] = str(self.dir)

    def tearDown(self):
        if self.old is None:
            os.environ.pop('CS2MAP_MY', None)
        else:
            os.environ['CS2MAP_MY'] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_notes_write_read_append_and_history(self):
        empty = server.guide_text()
        self.assertIn('Empty so far', empty)
        r = mylayer.write('note', 'lamps', '# Lamps\n- wall lamps need a bracket')
        self.assertEqual(r['files'], ['my/notes/lamps.md'])
        self.assertTrue((self.dir / 'README.md').is_file())
        mylayer.write('note', 'lamps', '- ceiling lamps hang 8 below', append=True)
        text = mylayer.read('note', 'lamps')['text']
        self.assertIn('bracket', text)
        self.assertIn('ceiling lamps', text)
        mylayer.write('note', 'lamps', '# Lamps v2')
        self.assertEqual(len(list((self.dir / '.history' / 'notes').glob('lamps.*.md'))), 2)
        listed = mylayer.list_all()
        self.assertEqual([e['name'] for e in listed['notes']], ['lamps'])
        self.assertEqual(listed['notes'][0]['title'], 'Lamps v2')
        core = server.guide_text()
        self.assertIn('`lamps` (Lamps v2)', core)
        self.assertIn('Lamps v2', server.guide_text('my'))
        self.assertIn('Lamps v2', server.guide_text('my:lamps'))

    def test_bad_names_kinds_and_sizes_are_refused(self):
        for bad in ('../x', 'A', '', 'x' * 65, 'a b'):
            with self.subTest(name=bad), self.assertRaises(mylayer.MyError):
                mylayer.write('note', bad, 'text')
        with self.assertRaisesRegex(mylayer.MyError, 'kind'):
            mylayer.write('inbox', 'x', 'text')
        with self.assertRaisesRegex(mylayer.MyError, 'limit'):
            mylayer.write('note', 'big', 'x' * (mylayer.TEXT_LIMIT + 1))
        with self.assertRaisesRegex(mylayer.MyError, 'recipe'):
            mylayer.write('note', 'json', data={'a': 1})

    def test_recipe_with_batch_data(self):
        batch = [{'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': [0, 0, 8]}]
        mylayer.write('recipe', 'spawn_pair', '# Spawn pair\nT and CT 512 apart', data=batch)
        r = mylayer.read('recipe', 'spawn_pair')
        self.assertEqual(r['data'], batch)
        self.assertIn('"info_player_terrorist"', server.guide_text('my:spawn_pair'))

    def test_style_validation(self):
        mylayer.write('style', 'nordic', data=NORDIC)
        info = mylayer.styles_info('nordic')
        self.assertEqual(info['tints']['wall'], [[60, 40, 30], [224, 224, 224]])
        self.assertEqual(mylayer.styles_info()['styles'][0]['name'], 'nordic')
        for bad, msg in (({'materials': {}}, 'description'),
                         ({**NORDIC, 'colour': 1}, 'unknown'),
                         ({**NORDIC, 'materials': {'wall': 'x.png'}}, 'vmat'),
                         ({**NORDIC, 'materials': {'wall': '../../x.vmat'}}, 'vmat'),
                         ({**NORDIC, 'tints': {'wall': [300, 0, 0]}}, 'colour'),
                         ({**NORDIC, 'light': {'sun': 1}}, 'preset')):
            with self.subTest(msg=msg), self.assertRaisesRegex(mylayer.MyError, msg):
                mylayer.write('style', 'bad', data=bad)

    def test_style_values_expand_in_any_operation(self):
        mylayer.write('style', 'nordic', data=NORDIC)
        op = my_ext.preprocess(None, None, {'op': 'add_mesh', 'material': '$nordic.materials.roof',
                                            'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [8, 8, 8],
                                                      'materials': {'top': '$nordic.materials.wall.1'}}}, {})
        self.assertEqual(op['material'], 'materials/dev/reflectivity_50.vmat')
        self.assertEqual(op['shape']['materials']['top'], 'materials/dev/reflectivity_30.vmat')
        self.assertEqual(my_ext.preprocess(None, None, {'op': 'x', 'k': '$money'}, {})['k'], '$money')
        with self.assertRaisesRegex(OpError, 'no materials.door'):
            my_ext.preprocess(None, None, {'op': 'add_mesh', 'material': '$nordic.materials.door'}, {})
        with self.assertRaisesRegex(OpError, 'no own style'):
            my_ext.preprocess(None, None, {'op': 'add_mesh', 'material': '$ghost.materials.wall'}, {})

    def test_quick_commands_take_own_styles(self):
        mylayer.write('style', 'nordic', data=NORDIC)
        f = my_ext.preprocess(None, None, {'op': 'add_facade', 'style': 'my:nordic', 'on': {}}, {})
        self.assertEqual(f['style'], 'building_inferno')
        self.assertEqual(f['materials'], [{'material': NORDIC['materials']['wall'][0], 'tint': [60, 40, 30]},
                                          {'material': NORDIC['materials']['wall'][1], 'tint': [224, 224, 224]}])
        self.assertEqual(f['roof'], {'material': 'materials/dev/reflectivity_50.vmat'})
        self.assertEqual((f['floor_height'], f['house_width']), (128, 256))
        self.assertEqual((f['balconies'], f['shops']), (False, 0))
        light = my_ext.preprocess(None, None, {'op': 'lighting_preset', 'preset': 'my:nordic'}, {})
        self.assertEqual(light['preset'], 'overcast')
        with self.assertRaisesRegex(OpError, 'does not take own styles'):
            my_ext.preprocess(None, None, {'op': 'dress_street', 'style': 'my:nordic'}, {})

    @unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
    def test_paving_with_own_style(self):
        from cs2map import checks, paving
        mylayer.write('style', 'nordic', data=NORDIC)
        p, m, refs = Project(), VMap.from_template(SETTINGS.cs2_path, 'empty'), {}
        layout.add_layout(p, m, {'op': 'add_layout', 'name': 'q', 'wall': 16,
                                 'rooms': [{'id': 'street', 'size': [1024, 384, 256], 'ceiling': False}]}, refs)
        op = my_ext.preprocess(p, m, {'op': 'add_paving', 'name': 'p', 'style': 'my:nordic'}, refs)
        r = paving.add_paving(p, m, op, refs)
        mats = set()
        for n in m.iter_nodes():
            if n.type == 'CMapMesh':
                mats.update(str(x) for x in n['meshData']['materials'])
        self.assertIn('materials/dev/reflectivity_70.vmat', mats)        # own floor
        self.assertIn('materials/dev/reflectivity_90.vmat', mats)        # own sidewalk
        self.assertNotIn(paving.STYLES['inferno']['surfaces']['street']['floor'], mats)
        self.assertTrue(r)
        self.assertTrue(checks)

    def test_inbox_lists_user_files_with_their_tool(self):
        mylayer.ensure()
        (self.dir / 'inbox' / 'wall_photo.jpg').write_bytes(b'\xff\xd8x')
        (self.dir / 'inbox' / 'bell.wav').write_bytes(b'RIFF')
        r = mylayer.inbox_search()
        self.assertEqual({f['kind'] for f in r['files']}, {'image', 'sound'})
        self.assertIn('material_from_reference', r['use']['image'])
        self.assertEqual(mylayer.inbox_search('bell')['total'], 1)

    def test_a_new_server_process_sees_what_an_old_one_wrote(self):
        """A new chat = a new MCP server: it reads what the previous one wrote (through the real stdio tool path)."""
        env = {**os.environ, 'CS2MAP_MY': str(self.dir), 'PYTHONIOENCODING': 'utf-8'}
        call = [sys.executable, str(ROOT / 'tools' / 'mcp_call.py')]
        w = subprocess.run(call + ['my_notes', json.dumps({'action': 'write', 'kind': 'note', 'name': 'taste',
                                                          'text': '# Taste\nThe user likes warm evening light'})],
                           capture_output=True, text=True, env=env, timeout=120, encoding='utf-8')
        self.assertEqual(w.returncode, 0, w.stderr[-2000:])
        r = subprocess.run(call + ['read_guide', '{}'], capture_output=True, text=True, env=env, timeout=120,
                           encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn('`taste` (Taste)', r.stdout)


if __name__ == '__main__':
    unittest.main()
