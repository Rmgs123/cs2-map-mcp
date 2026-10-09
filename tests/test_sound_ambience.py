import unittest
from unittest.mock import patch
from cs2map import sound
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2


@unittest.skipUnless(HAVE_CS2,'installed FGD required')
class AmbienceZones(unittest.TestCase):
    def setUp(self):
        self.p=Project();self.m=VMap.from_template(self.p.s.cs2_path,'empty')

    def add(self,name,lo,hi,fade=1.5):
        tree={'mcp.test':{'base':'amb.looping.stereo.base','time_volume_mapping_curve':[[0,0],[fade,1]],
                         'fadetime_volume_mapping_curve':[[0,1],[fade,0]]}}
        with patch.object(sound,'_source_events',return_value=(None,'',tree)):
            return sound.add_ambience(self.p,self.m,{'event':'mcp.test','name':name,'mins':lo,'maxs':hi,'fade':fade},{})

    def test_disjoint_zones_players_only_cancel_stale_starts_and_wait_for_fade(self):
        a=self.add('m_a',[0,0,0],[256,256,128],.2)
        b=self.add('m_b',[256,0,0],[512,256,128],2)
        trigger=self.m.node(a['id'])
        self.assertEqual(trigger['entity_properties']['spawnflags'],'1')
        self.assertTrue(any(c['inputName']=='CancelPending' and c['outputName']=='OnEndTouch' for c in trigger['connectionsData']))
        for relay in self.m.entities('logic_relay'):
            starts=[c for c in relay['connectionsData'] if c['inputName']=='StartSound']
            self.assertAlmostEqual(starts[0]['delay'],2.1)
        for speaker in self.m.entities('point_soundevent'):
            self.assertEqual(speaker['entity_properties']['startOnSpawn'],'0')
            self.assertEqual(speaker['entity_properties']['toLocalPlayer'],'1')
        self.assertEqual(len(self.m.entities('trigger_multiple')),2)

    def test_overlap_bad_fade_and_duplicate_refuse_before_nodes(self):
        self.add('m_a',[0,0,0],[256,256,128])
        before=len(list(self.m.iter_nodes()))
        with self.assertRaisesRegex(sound.SoundError,'overlap'):
            self.add('m_overlap',[128,0,0],[300,256,128])
        with self.assertRaisesRegex(ValueError,'already exists'):
            self.add('m_a',[256,0,0],[512,256,128])
        self.assertEqual(before,len(list(self.m.iter_nodes())))
        with patch.object(sound,'_source_events',return_value=(None,'',{'mcp.x':{'base':'amb.looping.stereo.base'}})):
            with self.assertRaisesRegex(sound.SoundError,'fade'):
                sound.add_ambience(self.p,self.m,{'event':'mcp.x','mins':[0,0,0],'maxs':[1,1,1]}, {})

    def test_dry_run_has_no_asset_writes_and_later_failure_never_saves(self):
        self.p.register_op('add_ambience',sound.add_ambience)
        tree={'mcp.test':{'base':'amb.looping.stereo.base','time_volume_mapping_curve':[[0,0],[1.5,1]],
                         'fadetime_volume_mapping_curve':[[0,1],[1.5,0]]}}
        op={'op':'add_ambience','event':'mcp.test','name':'m_dry','mins':[0,0,0],'maxs':[256,256,128]}
        with patch.object(sound,'_source_events',return_value=(None,'',tree)), \
             patch.object(sound,'_compile') as compile_asset, patch.object(self.p,'load',return_value=self.m), \
             patch.object(self.p,'save') as save:
            result=self.p.map_edit('m_dry',[op],dry_run=True)
            self.assertTrue(result['dry_run']);compile_asset.assert_not_called();save.assert_not_called()
            op={**op,'name':'m_later','mins':[256,0,0],'maxs':[512,256,128]}
            with self.assertRaisesRegex(ValueError,'Nothing was saved'):
                self.p.map_edit('m_dry',[op,{'op':'no_such_operation'}])
            compile_asset.assert_not_called();save.assert_not_called()

