"""Sound contracts: actual Valve trees, bounded WAV conversion, synthesis and map checks."""
import json
import struct
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from tests.helpers import ROOT, HAVE_CS2
from cs2map import kv3, sound
from cs2map.project import Project
from cs2map.vmap import VMap


class SoundWave(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / '.local/scratch/G/tests' / uuid.uuid4().hex
        self.folder.mkdir(parents=True)

    def write(self, raw, name='input.wav'):
        p = self.folder / name; p.write_bytes(raw); return p

    def test_pcm_conversion_and_loop_marker_bounds(self):
        samples = np.array([0, -.5, .5, .1, 0])[:, None]
        data = sound.wav_bytes(samples, 44100, True)
        actual, rate = sound.read_wav(self.write(data))
        np.testing.assert_allclose(actual, samples, atol=1 / 32767)
        self.assertEqual(rate, 44100)
        pos = 12; chunks = {}
        while pos < len(data):
            kind, n = struct.unpack_from('<4sI', data, pos)
            chunks[kind] = data[pos + 8:pos + 8 + n]; pos += 8 + n + n % 2
        self.assertEqual(struct.unpack_from('<I', chunks[b'cue '], 48)[0], len(samples))
        self.assertEqual(struct.unpack_from('<II', chunks[b'smpl'], 44), (0, len(samples)-1))
        self.assertEqual(struct.unpack_from('<I', data, 4)[0], len(data)-8)

    def test_pcm24_sign_and_float_resample(self):
        values = np.array([-8388608, -4194304, 0, 4194304, 8388607], dtype=np.int32)
        pcm = np.column_stack((values&255, values>>8&255, values>>16&255)).astype('u1').tobytes()
        fmt = struct.pack('<HHIIHH', 1, 1, 44100, 132300, 3, 24)
        chunks = sound._chunk(b'fmt ', fmt)+sound._chunk(b'data', pcm)
        a, _ = sound.read_wav(self.write(b'RIFF'+struct.pack('<I', len(chunks)+4)+b'WAVE'+chunks))
        np.testing.assert_allclose(a[:, 0], values / 8388608 * .95)
        fmt = struct.pack('<HHIIHH', 3, 2, 22050, 176400, 8, 32)
        pcm = np.tile([-.25, .25], 100).astype('<f4').tobytes()
        chunks = sound._chunk(b'fmt ', fmt)+sound._chunk(b'data', pcm)
        a, rate = sound.read_wav(self.write(b'RIFF'+struct.pack('<I', len(chunks)+4)+b'WAVE'+chunks))
        self.assertEqual(rate, 48000); self.assertEqual(a.shape, (218, 2))
        self.assertLess(sound.metrics(a)['peak'], .26)

    def test_refuse_bad_wav_and_unsupported_extension(self):
        with self.assertRaisesRegex(sound.SoundError, 'only WAV'):
            sound.read_wav(self.write(b'file', 'test.ogg'))
        raw = sound.wav_bytes(np.zeros((20, 1)), 44100)
        with self.assertRaisesRegex(sound.SoundError, 'truncated'):
            sound.read_wav(self.write(raw[:-2]))
        bad = bytearray(raw); struct.pack_into('<H', bad, 32, 0)
        with self.assertRaisesRegex(sound.SoundError, 'frame size'):
            sound.read_wav(self.write(bad))

    def test_all_synthesis_presets_are_finite_bounded_and_seamless(self):
        for preset in ('beep','alarm','hum','wind','rain','drone','click','whoosh','rumble','charge','zap'):
            for channels in (1, 2):
                with self.subTest(preset=preset, channels=channels):
                    x, rate, loop = sound.synthesize(preset, .3, {'loop': True, 'channels': channels, 'gain': .4})
                    self.assertTrue(loop); self.assertEqual(rate, 44100)
                    self.assertEqual(x.shape, (13230, channels)); self.assertTrue(np.isfinite(x).all())
                    self.assertAlmostEqual(sound.metrics(x)['peak'], .4, places=5)
                    self.assertEqual(sound.metrics(x)['loop_seam'], 0)
                    self.assertGreater(sound.metrics(x)['rms'], 0)
                    self.assertLess(np.max(np.abs(x[1] - x[-2])), .001)
        a, _, _ = sound.synthesize('wind', .3, {'seed': 2})
        b, _, _ = sound.synthesize('wind', .3, {'seed': 2})
        np.testing.assert_array_equal(a, b)

    def test_invalid_synthesis_has_no_assets(self):
        for preset, seconds, params in [('unknown', 1, {}), ('hum', float('nan'), {}), ('wind', 1, {'seed': -1}),
                                        ('rain', 1, {'channels': 1.5}), ('hum', 1, {'frequency': 100000}),
                                        ('beep', 1, {'gain': 2}), ('hum', 1, {'foo': 3})]:
            with self.subTest(preset=preset, params=params), self.assertRaises(sound.SoundError):
                sound.synthesize(preset, seconds, params)

    def test_event_transaction_preserves_sources_and_restores_on_failed_compile(self):
        content = self.folder / 'content'; events = content / 'soundevents/soundevents_addon.vsndevts'
        events.parent.mkdir(parents=True)
        tree = {'amb.intermittent.atXYZ.base': {'type': 'csgo_mega'}, 'old.event': {'volume': .2, 'x': [1,2]}}
        events.write_text(kv3.dumps(tree),encoding='utf-8')
        game = self.folder / 'game'; game.mkdir(); compiled = game / 'mock.vsndevts_c'; compiled.write_bytes(b'mock')
        project = SimpleNamespace(s=SimpleNamespace(content_addon=content, game_addon=game, backups=self.folder/'backups'))
        with patch.object(sound,'_sound_path',return_value='sounds/stock.vsnd'), \
             patch.object(sound,'_compile',return_value={'ok':True,'compiled':str(compiled)}), \
             patch.object(sound,'decode_events',return_value={**tree,'mcp.new': {}}):
            result=sound.sound_event(project,'new','oneshot',sound='anything')
        written=kv3.loads(events.read_text())[1]
        self.assertEqual(written['old.event'],tree['old.event']); self.assertEqual(result['preserved_events'],2)
        before=events.read_bytes()
        with patch.object(sound,'_sound_path',return_value='sounds/stock.vsnd'), \
             patch.object(sound,'_compile',side_effect=[sound.SoundError('compiler failed'),{'ok':True}]):
            with self.assertRaisesRegex(sound.SoundError,'compiler failed'):
                sound.sound_event(project,'other','oneshot',sound='anything')
        self.assertEqual(events.read_bytes(),before)

    def test_conversion_uses_bundled_blender_and_reports_failure(self):
        source=self.write(b'ID3dummy','input.mp3'); calls=[]
        def run(s,jobs,script,args,**kw):
            calls.append((script,args,kw))
            Path(args[1]).write_bytes(sound.wav_bytes(np.zeros((100,1)),44100))
            return SimpleNamespace(state='succeeded',result={'ok':True},log='conversion.log')
        project=SimpleNamespace(s=None,jobs=None)
        with patch('cs2map.blender_run.run',side_effect=run):
            decoded,meta=sound._convert_audio(project,source)
        self.assertEqual(meta['converted_from'],'.mp3'); self.assertTrue(decoded.exists())
        self.assertEqual(calls[0][2]['timeout'],45)
        with patch('cs2map.blender_run.run',return_value=SimpleNamespace(state='failed',result={},log='bad.log')):
            with self.assertRaisesRegex(sound.SoundError,'conversion log: bad.log'):
                sound._convert_audio(project,source)


@unittest.skipUnless(HAVE_CS2, 'needs installed Valve fixtures')
class SoundValve(unittest.TestCase):
    def setUp(self):
        self.project = Project()

    def test_binary_stock_event_association(self):
        data = self.project.game.read('soundevents/ambience/game_sounds_dust2.vsndevts_c')
        tree = sound.decode_events(data)
        event = tree['dust2.indoors.interior_01']
        self.assertIn('sounds/ambient/dust2/interior_01.vsnd', event['vsnd_files_track_01'])
        self.assertEqual(tree['dust2.indoors']['dsp_preset'], 'reverb_6_largeRoom')
        with self.assertRaises(sound.SoundError): sound.decode_events(data[:100])

    def test_search_returns_usable_event_before_incidental_substring(self):
        result = sound.sound_search(self.project, 'door', 5)
        self.assertEqual(result['index_failed'], 0)
        self.assertGreater(result['stock_events'], 1000)
        self.assertTrue(all(m['kind']=='event' for m in result['matches']))
        self.assertTrue(all('door' in m['event'].casefold() and 'outdoors' not in m['event'].casefold() for m in result['matches']))
        self.assertTrue(result['matches'][0]['sounds'])

    def test_map_ops_parent_local_player_and_checker(self):
        p = self.project; m = VMap.from_template(p.s.cs2_path); refs = {}
        _, _, existing = sound._source_events(p)
        own = dict(existing); own['mcp.test_ui'] = {'mixgroup': 'UI'}
        parent = m.new_entity('info_target', {'targetname':'g_parent'}); m.attach(parent)
        with patch.object(sound, '_source_events', return_value=(None, '', own)):
            out = sound.add_sound(p, m, {'event': 'mcp.test_ui', 'name':'g_ui', 'parent':'g_parent', 'start':False, 'ref':'x'}, refs)
            kv = m.node(out['id'])['entity_properties']
            self.assertEqual(kv['toLocalPlayer'], '1'); self.assertEqual(kv['startOnSpawn'], '0')
            self.assertEqual(kv['sourceEntityName'], 'g_parent'); self.assertEqual(refs['x'], out['id'])
            with self.assertRaises(sound.SoundError): sound.add_soundscape(p,m,{'event':'mcp.test_ui','radius':0},refs)
            ent = m.new_entity('env_soundscape', {'soundevent':'missing.event','enablesoundevent':1,'radius':'nan'})
            m.attach(ent)
            codes = [problem['code'] for problem in sound.check_sound(p,m)]
            self.assertIn('soundscape_radius', codes); self.assertIn('sound_event_missing', codes)
            ent['entity_properties']['soundevent']=''
            self.assertIn('sound_event_missing',[problem['code'] for problem in sound.check_sound(p,m)])

    def test_event_invalid_inputs_do_not_write(self):
        path, _, _ = sound._source_events(self.project); before = path.read_bytes()
        cases = [dict(name='bad_name',kind='loop',sound='sounds/../other.vsnd'),
                 dict(name='bad_name',kind='soundscape',children=['mcp.bad_name']),
                 dict(name='bad_name',kind='soundscape',children=['missing.event']),
                 dict(name='bad_name',kind='ui',volume=2,sound='sounds/common/talk.vsnd'),
                 dict(name='bad_name',kind='soundscape',children=['ambient_example.indoors'],reverb='guess')]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(sound.SoundError): sound.sound_event(self.project,**args)
        self.assertEqual(before, path.read_bytes())

    def test_event_fade_and_reverb_preserve_old_definitions_and_reject_invalid_params(self):
        self.folder=ROOT/'.local/scratch/M/sound_tests'/uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        content=self.folder/'fade_content';events=content/'soundevents/soundevents_addon.vsndevts'
        events.parent.mkdir(parents=True)
        tree={'amb.intermittent.atXYZ.base':{'type':'csgo_mega'},'old.event':{'volume':.2}}
        events.write_text(kv3.dumps(tree),encoding='utf-8')
        game=self.folder/'fade_game';game.mkdir();compiled=game/'event.vsndevts_c';compiled.write_bytes(b'mock')
        p=SimpleNamespace(s=SimpleNamespace(content_addon=content,game_addon=game,backups=self.folder/'backups'))
        with patch.object(sound,'_sound_path',return_value='sounds/stock.vsnd'), \
             patch.object(sound,'_compile',return_value={'ok':True,'compiled':str(compiled)}), \
             patch.object(sound,'decode_events',return_value={**tree,'mcp.echo':{}}):
            sound.sound_event(p,'echo','oneshot',sound='stock',reverb='reverb_10_warehouse',fade=.4)
        written=kv3.loads(events.read_text(encoding='utf-8'))[1]
        self.assertEqual(written['old.event'],tree['old.event'])
        self.assertTrue(written['mcp.echo']['override_dsp_preset'])
        self.assertEqual(written['mcp.echo']['dsp_preset'],'reverb_10_warehouse')
        self.assertEqual(written['mcp.echo']['time_volume_mapping_curve'][-1][:2],[.4,1.])
        self.assertEqual(written['mcp.echo']['fadetime_volume_mapping_curve'][-1][:2],[.4,0.])
        before=events.read_bytes()
        for kw in ({'fade':float('nan')},{'fade':0},{'reverb':'guessed_dsp'}):
            with self.assertRaises(sound.SoundError):
                sound.sound_event(p,'echo','oneshot',sound='stock',**kw)
        self.assertEqual(events.read_bytes(),before)
