import unittest
from cs2map import materials
from tests.helpers import HAVE_CS2, agent_suffix


@unittest.skipUnless(HAVE_CS2,'CS2 install not found')
class SignOps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map.project import Project
        from cs2map.ext.structure import register
        class Dummy:
            def tool(self): return lambda fn: fn
        cls.p=Project(); register(Dummy(),cls.p)
        cls.map='b_sign_test'+agent_suffix()
        cls.p.map_create(cls.map,'empty',overwrite=True)
        materials.sign_create(cls.p.s,'b_test_sign','Склад 3','plate')

    def test_frame_group_and_live_text_roundtrip(self):
        result=self.p.map_edit(self.map,[{'op':'add_sign','name':'b_test_sign','origin':[200,100,80],
                            'normal':[0,-1,0],'size':[128,32],'ref':'sign'},
                            {'op':'move','id':'@sign','offset':[16,0,0]},
                            {'op':'add_world_text','text':'Склад 3','origin':[100,100,80],'angles':[0,-90,0],
                             'size':18,'color':'#55eeff','targetname':'b_live'}])
        self.assertEqual(result['applied'],3)
        m=self.p.load(self.map)
        group=m.node(result['results'][0]['id'])
        self.assertEqual(group.type,'CMapGroup')
        self.assertEqual(len(group['children']),2)
        entity=m.entities(targetname='b_live')[0]
        self.assertEqual(entity['entity_properties']['message'],'Склад 3')
        self.assertAlmostEqual(float(entity['entity_properties']['world_units_per_pixel']),18/64)
        with self.assertRaises(ValueError):
            self.p.map_edit(self.map,[{'op':'add_world_text','text':'test','size':float('nan')}])


if __name__=='__main__': unittest.main()
