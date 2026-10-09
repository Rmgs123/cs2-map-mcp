import json
import unittest
from pathlib import Path
from unittest.mock import patch

from cs2map import buildings
from cs2map.project import OpError, Project
from cs2map.vmap import VMap
from cs2map import checks, dmx, mesh
from tests.helpers import HAVE_CS2, check_topology, signed_volume


ROOT = Path(__file__).resolve().parents[1]


class BuildingContract(unittest.TestCase):
    def test_kits_are_complete_and_have_materials(self):
        for path in sorted((ROOT / 'src/cs2map/data/kits').glob('building_*.json')):
            data=json.loads(path.read_text(encoding='utf8'))
            self.assertEqual(data['schema'],1)
            self.assertGreaterEqual(len(data['parts']),25)
            self.assertTrue(all(p['model'].startswith('models/') and p['model'].endswith('.vmdl') for p in data['parts']))
            self.assertIn('roof',data['materials']); self.assertIn('walls',data['materials'])
            self.assertEqual(len({p['key'] for p in data['parts']}),len(data['parts']))

    def test_roofs_are_closed(self):
        for size in ((512,384),(384,512),(256,256)):
            for ridge in ('x','y'):
                for kind in ('flat','parapet','gable','hip','shed'):
                    with self.subTest(size=size,ridge=ridge,kind=kind):
                        pieces,_=buildings.roof_meshes([0,0,0],[*size,256],256,
                            {'type':kind,'pitch':25,'ridge':ridge},'materials/x.vmat','materials/y.vmat')
                        for piece in pieces:
                            el=piece.to_dmx();check_topology(self,el)
                            self.assertGreater(signed_volume(el),0)

    @unittest.skipUnless(HAVE_CS2,'Valve template required')
    def test_bad_opening_rejected_before_attach(self):
        p=Project(); m=VMap.from_template(p.s.cs2_path,'basic')
        op={'op':'add_building','name':'bad','footprint':{'mins':[0,0,0],'maxs':[256,256,0]},'floors':2,
            'floor_height':144,'style':'building_inferno','facades':{'-y':{'windows':{'part':'window_stone_40','per_floor':2,'floors':[0]},'door':{'part':'door_wood_double','at':.333}}}}
        with self.assertRaises(OpError): buildings.add_building(p,m,op,{})
        self.assertFalse(any(n.get('cs2map_building') for n in m.iter_nodes()))

    def test_mount_four_sides_and_noncentred_origins(self):
        part={'front':'+x','bounds':[[-8,4,-20],[4,60,80]]}
        for normal in buildings.SIDES.values():
            origin,angles=buildings.mount_transform(part,[100,200,40],normal)
            corners=[buildings.structure._rotate((x,y,z),angles) for x in (-8,4) for y in (4,60) for z in (-20,80)]
            world=[[c[i]+origin[i] for i in range(3)] for c in corners]
            self.assertAlmostEqual(min(sum(c[i]*normal[i] for i in range(3)) for c in world),100*normal[0]+200*normal[1])
            self.assertAlmostEqual(min(c[2] for c in world),40)
            axis=1 if normal[0] else 0
            self.assertAlmostEqual((min(c[axis] for c in world)+max(c[axis] for c in world))/2,[100,200][axis])


@unittest.skipUnless(HAVE_CS2,'Valve template and FGD required')
class BuildingMap(unittest.TestCase):
    def setUp(self):
        self.p=Project();self.m=VMap.from_template(self.p.s.cs2_path,'basic');self.refs={}
        self.p.register_op('add_building',buildings.add_building)
        from cs2map.ext.structure import register
        class Stub:
            def tool(self):return lambda fn:fn
        register(Stub(),self.p)

    def spec(self,**kw):
        return {'op':'add_building','name':'test','footprint':{'mins':[0,0,0],'maxs':[512,384,0]},
                'floors':3,'floor_height':144,'facades':{'-y':{'windows':{'per_floor':3,'floors':[1,2]}}},**kw}

    def test_stacked_openings_are_real_and_solids_closed(self):
        r=self.p._apply(self.m,self.spec(),self.refs)
        wall=self.m.node(r['facades']['-y'])['children'][0]
        ts=list(checks._triangles(wall))
        for a,b,c,d in r['openings']['-y']:
            # A ray crosses the wall aperture without hitting wall triangles.
            at=((a+b)/2,-8,(c+d)/2)
            self.assertFalse(any(checks._ray(at,(0,1,0),tri) is not None for tri in ts))
        self.assertTrue(any(checks._ray((32,-8,80),(0,1,0),tri) is not None for tri in ts))
        for nid in r['mesh_ids']:check_topology(self,self.m.node(nid)['meshData'])
        self.assertEqual(len(r['facades']),4)
        roundtrip=VMap(dmx.loads(self.m.to_bytes()))
        self.assertEqual(json.loads(roundtrip.node(r['id'])['cs2map_building'])['floors'],3)

    def test_group_move_translates_all_facades(self):
        r=self.p._apply(self.m,self.spec(ref='house'),self.refs)
        wall=self.m.node(r['facades']['-y'])['children'][0]
        before=checks._mesh_bounds(wall)
        self.p._apply(self.m,{'op':'move','id':'@house','offset':[64,128,16]},self.refs)
        after=checks._mesh_bounds(wall)
        self.assertEqual([after[0][i]-before[0][i] for i in range(3)],[64,128,16])

    def test_duplicate_name_and_roof_overlap_refused(self):
        self.p._apply(self.m,self.spec(),self.refs)
        with self.assertRaisesRegex(OpError,'already exists'):self.p._apply(self.m,self.spec(),self.refs)
        near=self.spec(name='other',footprint={'mins':[516,0,0],'maxs':[1028,384,0]})
        with self.assertRaisesRegex(OpError,'roof overhang'):self.p._apply(self.m,near,self.refs)

    def test_unknown_fields_finite_flags_and_model_budget(self):
        for change in ({'floors':1.5},{'enterable':'false'},{'bands':1},{'floor_height':float('nan')},{'typo':True},
                       {'facades':{'+z':{}}}, {'roof':{'type':'unknown'}},
                       {'facades':{s:{'windows':{'per_floor':8,'floors':[0,1,2]}} for s in buildings.SIDES}}):
            with self.subTest(change=change),self.assertRaises(OpError):self.p._apply(self.m,self.spec(**change),self.refs)
            self.assertFalse(any(n.get('cs2map_building') for n in self.m.iter_nodes()))

    def test_enterable_doorway_reaches_floor_and_has_light(self):
        spec=self.spec(style='building_mirage',enterable=True,facades={'-y':{'door':{'part':'door_wood'}}})
        r=self.p._apply(self.m,spec,self.refs)
        self.assertEqual(r['openings']['-y'][0][2],0)
        self.assertTrue(self.m.entities('light_omni2'))
        self.assertEqual(r['models'],0)
