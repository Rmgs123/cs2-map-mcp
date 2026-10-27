import json
import unittest
from unittest.mock import patch

from cs2map import outdoor, lighting, mesh
from cs2map.project import Project, OpError
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture template needed')
class OutdoorRecipes(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')

    def test_water_surface_bed_and_shore_are_render_only(self):
        with patch('cs2map.checks._asset_exists', return_value=True):
            result = outdoor.add_water_body(self.p, self.m, {
                'name': 'harbor_water', 'bounds': {'mins': [-512, -256, -64], 'maxs': [512, 256, 0]},
                'water_level': -32, 'depth': 256, 'shore_axis': 'x', 'shore_side': -1, 'ref': 'water'
            }, {})
        self.assertEqual(result['kind'], 'sea')
        self.assertEqual(len(result['ids']), 3)
        self.assertEqual(self.m.node(result['id']).type, 'CMapGroup')
        self.assertTrue(all(self.m.node(i)['physicsType'] == 'none' for i in result['ids']))
        from cs2map.playtest import CameraGeometry
        self.assertIsNone(CameraGeometry(self.m).nearest([0, 0, 0], [0, 0, -1], floors=True))
        bound = __import__('cs2map.checks', fromlist=['_mesh_bounds'])._mesh_bounds(self.m.node(result['ids'][1]))
        self.assertEqual(bound[1][2], -288)
        self.assertEqual(len(self.m.entities()), 0)  # empty fixture has no template entities

    def test_water_styles_select_stock_shader_without_changing_geometry(self):
        with patch('cs2map.checks._asset_exists', return_value=True):
            calm = outdoor.add_water_body(self.p, self.m, {'name': 'calm', 'bounds': [[0, 0, -64], [512, 512, 0]]}, {})
            coast = outdoor.add_water_body(self.p, self.m, {'name': 'coast', 'water_style': 'coast', 'bounds': [[1024, 0, -64], [1536, 512, 0]]}, {})
        self.assertEqual(calm['water_style'], 'calm')
        self.assertEqual(calm['material'], outdoor.WATER_MATERIAL)
        self.assertEqual(coast['material'], outdoor.COAST_WATER_MATERIAL)
        self.assertEqual(len(calm['ids']), len(coast['ids']))

    def test_far_water_is_an_opaque_own_material_made_once(self):
        with patch('cs2map.checks._asset_exists', side_effect=lambda p, rel: rel != outdoor.FAR_WATER_MATERIAL), \
                patch.object(self.p, 'material_create', return_value={'vmat': 'x'}, create=True) as make:
            far = outdoor.add_water_body(self.p, self.m, {'name': 'far', 'water_style': 'far',
                                                           'bounds': [[0, 2048, -64], [512, 2560, 0]]}, {})
        self.assertEqual(far['material'], outdoor.FAR_WATER_MATERIAL)
        make.assert_called_once()
        self.assertEqual(make.call_args.kwargs['surface'], 'water')
        with patch('cs2map.checks._asset_exists', return_value=True), \
                patch.object(self.p, 'material_create', create=True) as make:
            outdoor.add_water_body(self.p, self.m, {'name': 'far2', 'water_style': 'far',
                                                     'bounds': [[0, 3072, -64], [512, 3584, 0]]}, {})
        make.assert_not_called()

    def test_backdrop_accepts_non_solid_stock_silhouettes(self):
        with patch('cs2map.checks._asset_exists', return_value=True):
            out = outdoor.add_backdrop(self.p, self.m, {
                'name': 'stock', 'style': 'port', 'bounds': [[0, 0, 0], [1024, 1024, 512]],
                'clip': False, 'models': [{'model': 'models/props/de_overpass/crane.vmdl',
                                           'origin': [1400, 200, 0], 'scale': .5}]
            }, {})
        self.assertEqual(len(out['model_ids']), 1)
        model = self.m.node(out['model_ids'][0])
        self.assertEqual(model['entity_properties']['solid'], '0')

    def test_backdrop_sets_sky_and_supports_have_physics(self):
        with patch('cs2map.checks._asset_exists', return_value=True):
            backdrop = outdoor.add_backdrop(self.p, self.m, {
                'style': 'port', 'bounds': {'mins': [0, 0, 0], 'maxs': [1024, 1024, 512]},
                'segments': 3, 'clip': False, 'sky': 'overcast'
            }, {})
            supports = outdoor.add_supports(self.p, self.m, {
                'kind': 'piles', 'bounds': {'mins': [128, 128, -256], 'maxs': [640, 640, 256]},
                'base_z': -256, 'top_z': 192, 'count_x': 2, 'count_y': 2
            }, {})
        self.assertEqual(backdrop['segments'], 3)
        self.assertEqual(self.m.node(backdrop['sky_id'])['entity_properties']['classname'], 'env_sky')
        self.assertEqual(len(supports['ids']), 6)
        self.assertTrue(all(self.m.node(i)['physicsType'] == 'default' for i in supports['ids']))

    def test_supports_can_resolve_elevated_layout_room(self):
        refs = {}
        layout_op = {
            'op': 'add_layout', 'name': 'o_test', 'wall': 16,
            'materials': {'floor': 'materials/dev/dev_measuregeneric01.vmat',
                          'walls': 'materials/dev/dev_measuregeneric01.vmat',
                          'ceiling': 'materials/dev/dev_measuregeneric01.vmat'},
            'rooms': [{'id': 'base', 'size': [512, 512, 256]},
                      {'id': 'upper', 'size': [256, 256, 128], 'in': 'base', 'at': [0.25, 0.25],
                       'floor_z': 128, 'outdoor': True}]
        }
        # The layout operation itself is independent of asset presence in this fixture.
        from cs2map import layout
        layout.add_layout(self.p, self.m, layout_op, refs)
        with patch('cs2map.checks._asset_exists', return_value=True):
            result = outdoor.add_supports(self.p, self.m, {'room': 'upper', 'kind': 'columns'}, refs)
        self.assertEqual(result['top_z'], 112)
        self.assertEqual(result['base_z'], 0)
        self.assertGreaterEqual(len(result['ids']), 1)

    def test_invalid_bounds_are_rejected_before_nodes(self):
        before = len(list(self.m.iter_nodes()))
        with patch('cs2map.checks._asset_exists', return_value=True):
            with self.assertRaises((ValueError, OpError)):
                outdoor.add_water_body(self.p, self.m, {'bounds': {'mins': [0, 0, 0], 'maxs': [0, 4, 2]}}, {})
            with self.assertRaises((ValueError, OpError)):
                outdoor.add_backdrop(self.p, self.m, {'style': 'bad', 'bounds': [[0, 0, 0], [4, 4, 4]]}, {})
        self.assertEqual(before, len(list(self.m.iter_nodes())))

    def test_refs_group_and_bad_shore_request_is_atomic(self):
        op = {'bounds': [[0, 0, -32], [512, 512, 0]], 'name': 'o_sea', 'ref': 'sea'}
        refs = {}
        with patch('cs2map.checks._asset_exists', return_value=True):
            before = len(list(self.m.iter_nodes()))
            with self.assertRaises((ValueError, OpError)):
                outdoor.add_water_body(self.p, self.m, {**op, 'shore_axis': 'z'}, refs)
            self.assertEqual(before, len(list(self.m.iter_nodes())))
            self.assertEqual(refs, {})
            out = outdoor.add_water_body(self.p, self.m, op, refs)
            self.assertEqual(refs['sea'], out['id'])
            self.assertEqual(len(self.m.node(out['id'])['children']), len(out['ids']))
            before = len(list(self.m.iter_nodes()))
            with self.assertRaisesRegex(OpError, 'already exists'):
                outdoor.add_water_body(self.p, self.m, op, refs)
            self.assertEqual(before, len(list(self.m.iter_nodes())))

    def test_clip_four_edges_without_overwriting_sky(self):
        with patch('cs2map.checks._asset_exists', return_value=True):
            out = outdoor.add_backdrop(self.p, self.m, {'style': 'rocky',
                  'bounds': [[0, 0, -64], [1024, 1024, 512]]}, {})
        clips = [self.m.node(i) for i in out['ids'] if self.m.node(i)['physicsType'] == 'default']
        mats = [str(c['meshData']['materials'][0]) for c in clips]
        self.assertEqual(sum(x.endswith('toolsplayerclip.vmat') for x in mats), 4)
        self.assertEqual(sum(x.endswith('toolsnavclip.vmat') for x in mats), 4)     # player clips do not stop nav
        self.assertFalse(self.m.entities('env_sky'))

    def test_fractional_counts_and_short_girders(self):
        op = {'bounds': [[0, 0, 0], [512, 512, 16]], 'base_z': 0, 'top_z': 16}
        with patch('cs2map.checks._asset_exists', return_value=True):
            with self.assertRaisesRegex(OpError, 'integer'):
                outdoor.add_supports(self.p, self.m, {**op, 'count_x': 2.5}, {})
            out = outdoor.add_supports(self.p, self.m, op, {})
        from cs2map import checks
        bounds = [checks._mesh_bounds(self.m.node(i)) for i in out['ids']]
        self.assertEqual(max(b[1][2] for b in bounds), 16)
        self.assertTrue(all(b[1][2] > b[0][2] for b in bounds))

    def test_generated_shore_cannot_exceed_world_bounds(self):
        before = len(list(self.m.iter_nodes()))
        with patch('cs2map.checks._asset_exists', return_value=True):
            with self.assertRaisesRegex(OpError, 'exceeds'):
                outdoor.add_water_body(self.p, self.m, {'bounds': [[32000, 0, -16], [32760, 512, 0]],
                    'shore_axis': 'x', 'shore_side': 1}, {})
        self.assertEqual(before, len(list(self.m.iter_nodes())))

    def test_stock_silhouettes_stay_outside_bounds_and_preserve_mesh_mode(self):
        from cs2map import visual
        for style in outdoor.BACKDROP_MODELS:
            with self.subTest(style=style):
                out=outdoor.add_backdrop(self.p,self.m,{'name':'stock_'+style,'style':style,
                     'bounds':[[0,0,0],[1024,1024,512]],'segments':2,'clip':True},{})
                self.assertEqual(len(out['model_ids']),8)
                self.assertEqual(len(out['ids']),8)            # 4 player clips and their nav clips
                for i in out['model_ids']:
                    n=self.m.node(i)
                    b=visual._model_bounds(self.p,n['entity_properties']['model'],n['origin'],n['angles'],n['scales'])
                    self.assertTrue(any(b[1][k]<=0 or b[0][k]>=1024 for k in (0,1)),b)
        old=outdoor.add_backdrop(self.p,self.m,{'silhouettes':'mesh','bounds':[[0,0,0],[1024,1024,512]]},{})
        self.assertNotIn('model_ids',old)
        self.assertGreater(len(old['ids']),4)

    def test_bad_stock_models_fail_before_attachment(self):
        before=len(list(self.m.iter_nodes()))
        for spec in ({'model':'models/../bad.vmdl','origin':[1400,0,0]},
                     {'model':'models/props/de_overpass/crane.vmdl','origin':[512,512,0]},
                     {'model':'models/props/de_overpass/crane.vmdl','origin':[32760,0,0]}):
            with self.assertRaises(OpError):
                outdoor.add_backdrop(self.p,self.m,{'bounds':[[0,0,0],[1024,1024,512]],'models':[spec]}, {})
            self.assertEqual(before,len(list(self.m.iter_nodes())))

    def test_italy_skyline_is_deterministic_and_render_only(self):
        from cs2map import visual
        op = {'name': 'italy', 'style': 'italy', 'bounds': [[0, 0, 0], [1024, 1024, 512]],
              'ring': [320, 900], 'density': 2, 'trees': 1, 'seed': 19, 'clip': True,
              'ref': 'sky'}
        p2 = Project()
        m2 = VMap.from_template(SETTINGS.cs2_path, 'empty')
        with patch('cs2map.checks._asset_exists', return_value=True), \
             patch.object(visual, 'model_bounds', return_value=((-100, -60, 0), (100, 60, 300), 'test')):
            one = outdoor.add_backdrop(self.p, self.m, op, {})
            two = outdoor.add_backdrop(p2, m2, op, {})
        meta_one = json.loads(self.m.node(one['id'])['cs2map_outdoor'])
        meta_two = json.loads(m2.node(two['id'])['cs2map_outdoor'])
        self.assertEqual(meta_one['models'], meta_two['models'])
        self.assertEqual(one['model_roles']['bell_tower'], 1)
        self.assertGreater(one['model_roles']['building'], 0)
        self.assertGreater(one['model_roles']['cypress'], 0)
        self.assertEqual(one['model_count'], len(meta_one['models']))
        self.assertEqual(len([i for i in one['ids'] if self.m.node(i).get('physicsType') == 'default']), 8)
        models = [n for n in self.m.iter_nodes(self.m.node(one['id'])) if n.type == 'CMapEntity']
        self.assertTrue(all(n['entity_properties']['solid'] == '0' for n in models))
        self.assertTrue(all('modelscale' not in n['entity_properties'].attrs for n in models))
        bounds = [visual._transformed(((-100, -60, 0), (100, 60, 300)),
                  n['origin'], n['angles'], n['scales']) for n in models]
        for a in bounds:
            self.assertAlmostEqual(a[0][2], -1)
            self.assertTrue(any(a[1][k] <= -320 or a[0][k] >= 1344 for k in (0, 1)))
        for i, a in enumerate(bounds):
            for b in bounds[i+1:]:
                self.assertFalse(all(a[0][k] < b[1][k] and a[1][k] > b[0][k] for k in (0, 1)))
        self.assertEqual(meta_one['seed'], 19)
        self.assertEqual(meta_one['ground_z'], -1.0)
        self.assertEqual(meta_one['ring_min'], 320.0)

    def test_italy_skyline_rejects_small_ring_before_attachment(self):
        from cs2map import visual
        before = len(list(self.m.iter_nodes()))
        with patch('cs2map.checks._asset_exists', return_value=True), \
             patch.object(visual, 'model_bounds', return_value=((-1, -1, 0), (1, 1, 2), 'test')):
            with self.assertRaises((ValueError, OpError)):
                outdoor.add_backdrop(self.p, self.m, {'style': 'italy', 'bounds': [[0, 0, 0], [512, 512, 256]],
                                                       'ring': [128, 512]}, {})
            self.assertEqual(before, len(list(self.m.iter_nodes())))
            with self.assertRaises((ValueError, OpError)):
                outdoor.add_backdrop(self.p, self.m, {'style': 'italy', 'bounds': [[0, 0, 0], [512, 512, 256]],
                                                       'ring': [600, 500]}, {})
            self.assertEqual(before, len(list(self.m.iter_nodes())))

    def test_italy_far_row_is_smaller_and_sparser(self):
        from cs2map import visual
        from statistics import median
        with patch('cs2map.checks._asset_exists', return_value=True), \
             patch.object(visual, 'model_bounds', return_value=((-100, -60, 0), (100, 60, 300), 'test')):
            out = outdoor.add_backdrop(self.p, self.m, {'style': 'italy', 'height': 640,
                'bounds': [[0, 0, 0], [3000, 1600, 512]], 'seed': 76, 'trees': 0, 'bell_tower': False}, {})
        placements = json.loads(self.m.node(out['id'])['cs2map_outdoor'])['models']
        rows = [[s for s in placements if s['role'] == 'building' and s['row'] == r] for r in range(3)]
        self.assertGreater(len(rows[0]), len(rows[1]))
        self.assertGreater(len(rows[1]), len(rows[2]))
        self.assertGreater(median(s['scale'] for s in rows[0]), median(s['scale'] for s in rows[1]))
        self.assertGreater(median(s['scale'] for s in rows[1]), median(s['scale'] for s in rows[2]))

    def test_italy_uses_layout_and_deep_facade_envelope(self):
        from cs2map import layout
        layout.add_layout(self.p, self.m, {
            'name': 'quarter', 'wall': 16,
            'rooms': [{'id': 'street', 'origin': [100, 200, 64], 'size': [512, 256, 512],
                       'outdoor': True, 'ceiling': False}]
        }, {})
        row = self.m.new_group('facade_row:test')
        row.set('cs2map_facade', 'string', json.dumps({
            'schema': 2, 'axis': 1, 'plane': 200, 'normal': [0, 1, 0], 'z0': 70,
            'houses': [{'from': 100, 'to': 612, 'depth': 288, 'top': 650}]}))
        self.m.attach(row)
        lo, hi, z = outdoor._quarter_bounds(self.m, [[100, 200, 64], [612, 456, 576]], 'quarter')
        self.assertEqual(lo, [76, -112, 64])
        self.assertEqual(hi, [636, 472, 778])
        self.assertEqual(z, 64)
        with self.assertRaisesRegex(OpError, 'layout'):
            outdoor._quarter_bounds(self.m, layout_name='missing')

    def test_italy_ground_is_visible_to_foundation_audit_but_has_no_nav_floor(self):
        from cs2map import visual
        from cs2map.playtest import CameraGeometry
        with patch('cs2map.checks._asset_exists', return_value=True), \
             patch.object(visual, 'model_bounds', return_value=((-10, -10, -5), (10, 10, 60), 'test')):
            out = outdoor.add_backdrop(self.p, self.m, {
                'style': 'italy', 'bounds': [[0, 0, 0], [512, 512, 512]],
                'density': 1, 'trees': 0, 'clip': False, 'bell_tower': False}, {})
        ground = self.m.node(out['ids'][0])
        self.assertEqual(ground['physicsType'], 'none')
        self.assertNotIn(ground['nodeID'], visual.scenery_ids(self.m))
        self.assertIn(ground['nodeID'], [n['nodeID'] for n, _, _ in visual._visible_meshes(self.m)])
        self.assertIsNone(CameraGeometry(self.m).nearest([256, 256, 64], [0, 0, -1], floors=True))

    def test_italy_closes_an_exposed_layout_foundation_and_deletes_as_one_group(self):
        from cs2map import layout, visual
        layout.add_layout(self.p, self.m, {'name': 'quarter', 'wall': 16,
            'rooms': [{'id': 'street', 'size': [512, 512, 256], 'outdoor': True, 'ceiling': False}]}, {})
        self.m.attach(self.m.new_entity('info_player_terrorist', {}, [256, -200, 0]))
        def audit():
            return visual._foundation_checks(self.m, visual._visible_meshes(self.m), visual._solid_meshes(self.m))
        self.assertTrue(any(p['code'] == 'visual_exposed_foundation' for p in audit()))
        refs = {}
        with patch('cs2map.checks._asset_exists', return_value=True), \
             patch.object(visual, 'model_bounds', return_value=((-10, -10, -5), (10, 10, 60), 'test')):
            out = outdoor.add_backdrop(self.p, self.m, {'style': 'italy', 'layout': 'quarter',
                'ref': 'sky', 'density': 1, 'trees': 0, 'bell_tower': False, 'clip': False}, refs)
        self.assertFalse(audit())
        self.m.remove(self.p.resolve(self.m, '@sky', refs))
        self.assertFalse(any(n.get('cs2map_outdoor') or n.get('cs2map_scenery') for n in self.m.iter_nodes()))
        self.assertTrue(any(p['code'] == 'visual_exposed_foundation' for p in audit()))

    def test_italy_invalid_options_and_overflow_are_atomic(self):
        from cs2map import visual
        base = {'style': 'italy', 'bounds': [[0, 0, 0], [1024, 1024, 512]]}
        before = len(list(self.m.iter_nodes()))
        invalid = [{'seed': 1.5}, {'ground': False}, {'ground_z': 10}, {'trees': 4},
                   {'tower_side': 'bad'}, {'sides': []}, {'silhouettes': 'mesh'},
                   {'models': []}, {'material': outdoor.ITALY_GROUND_MATERIAL},
                   {'bounds': [[32000, 0, 0], [32500, 1024, 512]]}]
        with patch('cs2map.checks._asset_exists', return_value=True), \
             patch.object(visual, 'model_bounds', return_value=((-100, -60, 0), (100, 60, 300), 'test')):
            for fields in invalid:
                with self.subTest(fields=fields), self.assertRaises((ValueError, OpError)):
                    outdoor.add_backdrop(self.p, self.m, {**base, **fields}, {})
                self.assertEqual(before, len(list(self.m.iter_nodes())))
        with self.assertRaisesRegex(OpError, 'unknown outdoor fields'):
            outdoor.add_backdrop(self.p, self.m, {'style': 'port', 'bounds': base['bounds'], 'seed': 7}, {})

class FogBounds(unittest.TestCase):
    @unittest.skipUnless(HAVE_CS2, 'CS2 fixture template needed')
    def test_controller_bounds_are_relative_and_centered(self):
        p = Project()
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        m.attach(m.new_mesh(mesh.box([100, 200, 0], [900, 1000, 256])))
        lighting.apply_preset(p, m, 'overcast', {'mode': 'volumetric', 'mins': [100, 200, 0], 'maxs': [900, 1000, 256]})
        controller = m.entities('env_volumetric_fog_controller')[0]
        self.assertEqual(tuple(round(x, 3) for x in controller['origin']), (500.0, 600.0, 128.0))
        self.assertEqual(controller['entity_properties']['box_mins'], '-464 -464 -192')
        self.assertEqual(controller['entity_properties']['box_maxs'], '464 464 192')
