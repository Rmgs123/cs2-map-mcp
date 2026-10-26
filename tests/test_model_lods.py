"""model_build lods: levels are checked before Blender runs, the ModelDoc gets one LOD group per level with growing
switch distances (the layout an own SUV proved in CS2, 14.10), and a rebuild without lods keeps them."""
import json
import shutil
import unittest

from cs2map import kv3, models
from cs2map.models import ModelError
from tests.helpers import HAVE_CS2, agent_suffix

NAME = 't_lods' + agent_suffix()


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class ModelLods(unittest.TestCase):
    def setUp(self):
        from cs2map import server
        self.p = server.project
        self.dir = models._writable_model_dir(self.p.s, NAME)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_levels_are_checked(self):
        for bad in ([{'switch': 800, 'reduce': .5}, {'switch': 600, 'reduce': .2}],      # not growing
                    [{'switch': 800, 'reduce': 1.5}], [{'switch': 800}],
                    [{'switch': 800, 'reduce': .5, 'model': 'x'}], [{'switch': 800, 'model': 'no_such_model_x'}],
                    [{'switch': d, 'reduce': .5} for d in (100, 200, 300, 400, 500)]):
            with self.assertRaises((ModelError, ValueError)):
                models._lod_request(self.p, NAME, bad)
        self.assertEqual(models._lod_request(self.p, NAME, [{'switch': 800, 'reduce': .5}]),
                         [{'switch': 800.0, 'reduce': .5}])

    def test_modeldoc_groups_and_rebuild_keeps_them(self):
        (self.dir / 'model.json').write_text(json.dumps({'lod_request': [{'switch': 700.0, 'reduce': .3}]}),
                                             encoding='utf-8')
        self.assertEqual(models._lod_request(self.p, NAME, None), [{'switch': 700.0, 'reduce': .3}])   # kept
        self.assertEqual(models._lod_request(self.p, NAME, []), [])                                  # removed
        meta = {'render_file': f'models/mcp/{NAME}/{NAME}.fbx', 'materials': [],
                'lod_files': [{'level': 1, 'file': f'{NAME}_lod1.fbx', 'tris': 10}]}
        meta['lods'] = models._lod_parts(self.p, NAME, [{'switch': 700.0, 'reduce': .3}], meta)
        vmdl, _, _ = models._make_vmdl(self.p, NAME, meta, 'none', 'default')
        _, doc = kv3.loads(vmdl.read_text(encoding='utf-8'))
        children = doc['rootNode']['children']
        meshes = next(c for c in children if c['_class'] == 'RenderMeshList')['children']
        self.assertEqual([m['filename'] for m in meshes], [f'models/mcp/{NAME}/{NAME}.fbx',
                                                          f'models/mcp/{NAME}/{NAME}_lod1.fbx'])
        groups = next(c for c in children if c['_class'] == 'LODGroupList')['children']
        self.assertEqual([(g['switch_threshold'], g['mesh_references'][0]['mesh_name']) for g in groups],
                         [(0.0, NAME), (700.0, f'{NAME}_lod1')])


if __name__ == '__main__':
    unittest.main()
