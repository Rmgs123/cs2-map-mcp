import json
import math
import unittest
from pathlib import Path

from cs2map import kits
from cs2map.project import OpError, Project


class KitContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = Project()
        cls.data = kits.load(cls.project)

    def test_required_catalogues_and_counts(self):
        required = {'street_common','vegetation_med','rocks','vehicles','market','harbor','interior_common'}
        self.assertTrue(required.issubset(self.data))
        for name, data in self.data.items():
            if name in required:
                self.assertGreaterEqual(len(data['parts']), 15)
                self.assertLessEqual(len(data['parts']), 40)
            self.assertEqual(len({p['key'] for p in data['parts']}), len(data['parts']))

    def test_paths_bounds_and_native_models(self):
        for data in self.data.values():
            for p in data['parts']:
                self.assertTrue(self.project.game.exists(p['model']+'_c'), p['model'])
                self.assertEqual(len(p['bounds']), 2)
                self.assertTrue(all(b > a for a,b in zip(p['bounds'][0],p['bounds'][1])))
                self.assertTrue(all(abs(s-(b-a)) < .16 for s,a,b in zip(p['size'],p['bounds'][0],p['bounds'][1])))
                self.assertIn(p['verified'], {'bounds','game'})

    def test_owned_catalogue_matches_native_render_and_physics(self):
        for name,data in self.data.items():
            if name.startswith('building_'):continue
            for part in data['parts']:
                measured=kits.measure(self.project,part['model'])
                self.assertGreater(max(measured['size']),1,part['key'])
                self.assertEqual(measured['bounds'],part['bounds'],part['key'])
                self.assertEqual(measured['solid'],part['solid'],part['key'])

    def test_zero_native_bounds_never_become_point_one_cube(self):
        with self.assertRaisesRegex(OpError,'no native render bounds'):
            kits.measure(self.project,'models/generic/streetlamps_01/streetlamp_01_single_base.vmdl')

    def test_find_and_part(self):
        found = kits.find(self.project, category='street', tags=['bench'])
        self.assertGreaterEqual(len(found), 3)
        self.assertEqual(kits.part(self.project, 'street_common/bench_urban')['category'], 'street')
        with self.assertRaises(OpError): kits.part(self.project, 'unknown/nope')

    def test_invalid_catalogue_is_closed(self):
        bad = json.loads(json.dumps(self.data['street_common']))
        bad['parts'][0]['size'][0] = -1
        with self.assertRaises(OpError): kits.validate(self.project, bad)

    def test_contract_rejects_duplicate_front_nan_and_traversal(self):
        for field,value in [('front','+z'),('solid',1),('verified','compiler'),
                            ('model','models/../outside.vmdl'),('thumb','screenshots/kits/../escape.png')]:
            bad=json.loads(json.dumps(self.data['street_common']))
            bad['parts'][0][field]=value
            with self.assertRaises((OpError,RuntimeError),msg=field):kits.validate(self.project,bad)
        for update in ['duplicate','nan']:
            bad=json.loads(json.dumps(self.data['street_common']))
            if update=='duplicate':bad['parts'].append(bad['parts'][0].copy())
            else:bad['parts'][0]['bounds'][0][0]=math.nan
            with self.assertRaises(OpError):kits.validate(self.project,bad)

    def test_filter_types_and_empty_thumbnail_sentinel(self):
        for kwargs in [{'category':'not_a_category'}, {'mount':'flying'}, {'style':''}, {'tags':False}]:
            with self.assertRaises(OpError):kits.find(self.project,**kwargs)
        bad=json.loads(json.dumps(self.data['street_common']))
        bad['parts'][0].update(thumb='',verified='bounds')
        kits.validate(self.project,bad)

    def test_gallery_selectors_pagination_and_bad_keys(self):
        for kwargs in [{},{'kit':'rocks','category':'rock'}, {'kit':'rocks','limit':13},
                       {'kit':'rocks','offset':-1},{'kit':'rocks','offset':999},
                       {'keys':[]},{'keys':[{}]},{'keys':['rocks/rock_collection']*2}]:
            with self.assertRaises(OpError):kits.gallery(self.project,**kwargs)


if __name__ == '__main__':
    unittest.main()
