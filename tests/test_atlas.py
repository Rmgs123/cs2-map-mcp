import importlib.util
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import HAVE_CS2, ROOT

from cs2map import atlas


def _glb(path: Path, lo, hi, matrix=None):
    node = {'mesh': 0, **({'matrix': matrix} if matrix else {})}
    doc = {'asset': {'version': '2.0'}, 'scenes': [{'nodes': [0]}], 'nodes': [node],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
           'accessors': [{'min': lo, 'max': hi, 'count': 2, 'type': 'VEC3', 'componentType': 5126}]}
    js = json.dumps(doc).encode()
    js += b' ' * (-len(js) % 4)
    path.write_bytes(struct.pack('<III', 0x46546C67, 2, 20 + len(js)) + struct.pack('<II', len(js), 0x4E4F534A) + js)


def _rec(model, **kw):
    rec = {'model': model, 'category': 'furniture', 'tags': ['bench', 'wood', 'old'], 'style': 'inferno',
           'front': '-x', 'mount': 'floor', 'note': 'wooden park bench', 'by': 'test-model', 'source': 'offline'}
    rec.update(kw)
    return rec


class AtlasUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.patches = [patch.object(atlas, 'NOTES', t / 'notes.jsonl'), patch.object(atlas, 'CATALOG', t / 'catalog.jsonl'),
                        patch.object(atlas, 'IMAGES', t / 'img'), patch.object(atlas, 'SHEETS', t / 'sheets')]
        for p in self.patches:
            p.start()
        self.t = t

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_glb_bounds_are_source_inches(self):
        # Source (x, y, z) = glTF (z, x, y); metres -> inches.
        f = self.t / 'm.glb'
        _glb(f, [-0.5, 0.0, -0.25], [0.5, 1.0, 0.75])
        lo, hi = atlas.glb_bounds(f)
        self.assertEqual(lo, [round(-0.25 / 0.0254, 2), round(-0.5 / 0.0254, 2), 0.0])
        self.assertEqual(hi, [round(0.75 / 0.0254, 2), round(0.5 / 0.0254, 2), round(1 / 0.0254, 2)])
        # node transforms are applied (column-major translation by +1 m on glTF Y = Source Z)
        _glb(f, [0, 0, 0], [0.0254, 0.0254, 0.0254], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1])
        lo, hi = atlas.glb_bounds(f)
        self.assertAlmostEqual(lo[2], 1 / 0.0254, places=1)

    def test_note_schema(self):
        atlas.validate_note(_rec('models/a.vmdl'))
        for bad in ({'category': 'chairs'}, {'tags': ['a', 'b']}, {'tags': ['A', 'b', 'c']}, {'front': 'x'},
                    {'mount': 'roof'}, {'note': 'x' * 81}, {'by': ''}, {'style': 'Old Town'}, {'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(atlas.AtlasError):
                atlas.validate_note(_rec('models/a.vmdl', **bad))

    def test_note_appends_and_local_overrides_catalog(self):
        atlas.CATALOG.write_text(json.dumps({**_rec('models/a.vmdl', note='shared'), 'rev': 1, 'date': '2026-01-01',
                                             'crc': '1'}) + '\n', encoding='utf-8')
        fake = {'models/a.vmdl': {'crc': '1', 'mats': [], 'phys': False, 'vpk': 'csgo'}}
        with patch.object(atlas, 'index', return_value=fake):
            self.assertEqual(atlas.notes_all()['models/a.vmdl']['origin'], 'catalog')
            with self.assertRaisesRegex(atlas.AtlasError, 'detail=true'):   # front needs both sides seen
                atlas.note(None, 'models/a.vmdl', 'furniture', ['bench', 'wood', 'old'], 'inferno', '-x', 'floor',
                           'mine', 'test-model')
            atlas.IMAGES.mkdir()
            (atlas.IMAGES / f'{atlas._key("models/a.vmdl", "1", "detail")}.json').write_text('{"status": "ok"}')
            r = atlas.note(None, 'models/A.vmdl', 'furniture', ['bench', 'wood', 'old'], 'inferno', '-x', 'floor',
                           'mine', 'test-model')
            self.assertEqual(r['rev'], 2)
            r = atlas.note(None, 'models/a.vmdl', 'furniture', ['bench', 'wood', 'new'], 'inferno', '-x', 'floor',
                           'mine again', 'test-model')
            self.assertEqual(r['rev'], 3)
            with self.assertRaises(atlas.AtlasError):
                atlas.note(None, 'models/missing.vmdl', 'furniture', ['a', 'b', 'c'], 'x', '+x', 'floor', '', 'm')
        latest = atlas.notes_all()['models/a.vmdl']
        self.assertEqual((latest['origin'], latest['note']), ('local', 'mine again'))
        self.assertEqual(len(atlas.NOTES.read_text(encoding='utf-8').splitlines()), 2)  # journal keeps history

    def test_promote_sorted_one_line_per_model(self):
        spec = importlib.util.spec_from_file_location('prop_catalog', ROOT / 'tools' / 'prop_catalog.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        lines = [{**_rec(m), 'rev': 1, 'date': '2026-10-04', 'crc': '0'} for m in ('models/z.vmdl', 'models/b.vmdl')]
        lines.append({**_rec('models/bad.vmdl', category='nope'), 'rev': 1, 'date': '2026-10-04', 'crc': '0'})
        atlas.NOTES.write_text(''.join(json.dumps(r) + '\n' for r in lines), encoding='utf-8')
        out = tool.promote()
        self.assertEqual((out['added'], len(out['rejected'])), (2, 1))
        models = [json.loads(x)['model'] for x in atlas.CATALOG.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(models, ['models/b.vmdl', 'models/z.vmdl'])
        self.assertEqual(tool.promote()['added'], 0)

    def test_search_ranks_names_and_notes(self):
        fake = {'models/props/park/bench01.vmdl': {'crc': '1', 'mats': ['wood'], 'phys': True, 'vpk': 'csgo',
                                                   'lo': [0, 0, 0], 'hi': [30, 90, 40]},
                'models/props/x/seat_thing.vmdl': {'crc': '2', 'mats': ['metal'], 'phys': True, 'vpk': 'csgo'},
                'models/props/x/crate.vmdl': {'crc': '3', 'mats': ['wood'], 'phys': True, 'vpk': 'csgo'}}
        atlas.NOTES.write_text(json.dumps({**_rec('models/props/x/seat_thing.vmdl', tags=['bench', 'metal', 'urban']),
                                           'rev': 1, 'date': 'd', 'crc': 'old'}) + '\n', encoding='utf-8')
        with patch.object(atlas, 'index', return_value=fake), patch.object(atlas, 'measure', return_value=fake):
            out = atlas.search(None, 'bench', 5)
        self.assertEqual(out['found'], 2)
        # equal match: a described model ranks first; its note is stale (crc changed)
        self.assertTrue(out['results'][0].startswith('models/props/x/seat_thing.vmdl | ? | front -x, floor'))
        self.assertIn('(model changed since)', out['results'][0])
        self.assertTrue(out['results'][1].startswith('models/props/park/bench01.vmdl | 30x90x40 | not described'))
        with self.assertRaises(atlas.AtlasError):
            atlas.search(None, '  ')

    def test_evict_least_recent_first(self):
        atlas.IMAGES.mkdir()
        for i in range(4):
            f = atlas.IMAGES / f'{i}.webp'
            f.write_bytes(b'x' * 100)
            (atlas.IMAGES / f'{i}.json').write_text('{}')
            os.utime(f, (1000 + i, 1000 + i))
        with patch.object(atlas, 'IMAGE_LIMIT', 250):
            atlas._evict()
        self.assertEqual(sorted(p.name for p in atlas.IMAGES.glob('*.webp')), ['2.webp', '3.webp'])
        self.assertFalse((atlas.IMAGES / '0.json').exists())

    def test_lock_waits_for_other_holder(self):
        import threading, time
        lock = self.t / 'render.lock'
        held = threading.Event()

        def holder():
            with atlas._file_lock(lock):
                held.set()
                time.sleep(0.6)
        th = threading.Thread(target=holder)
        th.start()
        held.wait(5)
        start = time.monotonic()
        with atlas._file_lock(lock, timeout=10):
            waited = time.monotonic() - start
        th.join()
        self.assertGreater(waited, 0.3)
        with atlas._file_lock(lock):
            with self.assertRaises(atlas.AtlasError):
                with atlas._file_lock(lock, timeout=0.3):
                    pass

    def test_magenta_detection(self):
        from PIL import Image
        self.assertGreater(atlas._magenta(Image.new('RGB', (32, 32), (255, 0, 255))), 0.9)
        self.assertEqual(atlas._magenta(Image.new('RGB', (32, 32), (120, 130, 140))), 0)


@unittest.skipUnless(HAVE_CS2 and atlas.VRF.is_file(), 'CS2 or Source2Viewer-CLI not installed')
class AtlasReal(unittest.TestCase):
    def test_preview_real_stock_model(self):
        from cs2map.project import Project
        project = Project()
        model = 'models/props/de_inferno/bench_wood.vmdl'
        out = atlas.preview(project, [model])
        self.assertEqual(out['models'][0]['status'], 'ok', out)
        self.assertTrue(Path(out['sheet']).is_file())
        info = atlas.index(project)[model]
        size = [b - a for a, b in zip(info['lo'], info['hi'])]
        for got, want in zip(size, [37.7, 98.3, 41.7]):  # measured natively by resourceinfo (kit street_common)
            self.assertAlmostEqual(got, want, delta=0.6)
        again = atlas.preview(project, [model])
        self.assertEqual((again['rendered'], again['cached']), (0, 1))
        self.assertEqual(list(atlas.TMP.glob('*')), [])  # temp exports are always removed
        with self.assertRaises(atlas.AtlasError):
            atlas.preview(project, [model], game=True)  # offline preview is fine: no game


if __name__ == '__main__':
    unittest.main()
