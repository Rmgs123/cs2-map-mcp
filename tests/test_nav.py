"""Bots on big maps (1.2): nav estimate, the nav guard, play area and nav markup, release steps."""
import json
import subprocess
import sys
import textwrap
import unittest
import uuid

from cs2map import compiler, lockrun, navcost, releaserun
from tests.helpers import HAVE_CS2, ROOT, agent_suffix

MAP = 'test_nav' + agent_suffix()
TMP = ROOT / '.local' / 'tmp'

FAKE_NAVGEN = '''
import sys, time
print('Nav settings not found, using default settings.', flush=True)
print('>>NAVGEN: pass 1/1: orig tris 407719', flush=True)
print('>>NAVGEN: -- Start --', flush=True)
print('>>NAVGEN: stage 01 / 01: IslandRemoval took 1.556 sec, produced %s polys' % sys.argv[1], flush=True)
print('CSGO Compute AI Data', flush=True)
time.sleep(float(sys.argv[2]))
print('Nav mesh (v36/1) loaded with %s nav areas for 1 hulls.' % sys.argv[1], flush=True)
print(' OK: 3 compiled, 0 failed, 0 skipped, 0m:01s', flush=True)
'''


def _fake(tmp_name='fake_navgen.py'):
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f'{uuid.uuid4().hex[:6]}_{tmp_name}'
    path.write_text(FAKE_NAVGEN, encoding='utf-8')
    return path


def _lockrun(*args, timeout=60):
    env = {'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1', 'SYSTEMROOT': __import__('os').environ.get('SYSTEMROOT', '')}
    return subprocess.run([sys.executable, '-m', 'cs2map.lockrun', *args], capture_output=True, text=True,
                          timeout=timeout, env=env, cwd=str(ROOT))


class Cost(unittest.TestCase):
    def test_time_model_matches_the_logged_builds(self):
        # 2000 areas: 15-56 s in the logs; 66 000: not done after 3 h 40 min
        low, mid, high = navcost.ai_data_seconds(2000)
        self.assertTrue(low < 30 < high and low < 56 <= high)
        self.assertGreater(navcost.ai_data_seconds(66000)[1], 4 * 3600)
        self.assertEqual(navcost.verdict(1500, 20000)['level'], 'ok')
        self.assertEqual(navcost.verdict(12000, 20000)['level'], 'slow')
        v = navcost.verdict(66482, 20000)
        self.assertEqual(v['level'], 'too_slow')
        self.assertIn('h', v['ai_data'])

    def test_log_parsing_and_progress(self):
        text = ('>>NAVGEN: pass 1/1: orig tris 12\n>>NAVGEN: stage 01 / 01: IslandRemoval took 0.1 sec, produced 66482 '
                'polys\nCSGO Compute AI Data\n-------------------------------\n')
        self.assertEqual(navcost.from_log(text), 66482)
        prog = compiler.progress('== step 2/3: build the map\nVRAD3 - Distributed Lighting Tool\n' + text)
        self.assertIn('bot data', prog['stage'])
        self.assertEqual(prog['nav_areas'], 66482)
        self.assertTrue(prog['step'].startswith('2/3'))
        info = compiler.analyze_log(text + '>>NAVGEN: Skipped... no walkable seeds present\n')
        self.assertEqual(info['nav_areas'], 66482)
        info = compiler.analyze_log('>>NAVGEN: Skipped... no walkable seeds present\n')
        self.assertIn('no walkable seeds present', info['nav'])
        self.assertIn('>>NAVGEN: Skipped', info['hints'][0])

    def test_flags(self):
        self.assertNotIn('-nav', compiler.flags('full', bots=False))
        self.assertNotIn('-gridnav', compiler.flags('final', bots=False))
        self.assertNotIn('-vis', compiler.flags('final', vis=False))
        self.assertIn('-nav', compiler.flags('fast', bots=True))
        self.assertEqual(compiler.flags('full'), compiler.PRESETS['full'])


class Guard(unittest.TestCase):
    def test_guard_stops_a_too_big_nav_right_after_navgen(self):
        fake = _fake()
        try:
            r = _lockrun('-', '60', '--nav-max', '20000', '--', sys.executable, str(fake), '66482', '30')
            self.assertEqual(r.returncode, lockrun.NAV_STOPPED, r.stdout + r.stderr)
            self.assertIn('[navguard] stopped', r.stdout)
            self.assertNotIn('loaded with', r.stdout)            # killed before the bot data ended
            info = compiler.analyze_log(r.stdout)
            self.assertTrue(info['nav_stopped'])
            self.assertIn('limit', info['nav'])
        finally:
            fake.unlink(missing_ok=True)

    def test_guard_lets_a_small_nav_finish_and_probe_stops_after_counting(self):
        fake = _fake()
        try:
            r = _lockrun('-', '60', '--nav-max', '20000', '--', sys.executable, str(fake), '1500', '0')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('[navguard] areas=1500 level=ok', r.stdout)
            self.assertIn('loaded with 1500', r.stdout)
            r = _lockrun('-', '60', '--nav-max', '20000', '--nav-probe', '--', sys.executable, str(fake), '9000', '30')
            self.assertEqual(r.returncode, 0)
            self.assertIn('probe done', r.stdout)
            self.assertNotIn('loaded with', r.stdout)
        finally:
            fake.unlink(missing_ok=True)


class ReleaseSteps(unittest.TestCase):
    def test_probe_over_the_limit_builds_without_bots_and_drops_the_bake_files(self):
        fake = _fake()
        game = TMP / f'rel_game_{uuid.uuid4().hex[:6]}'
        (game / '_vrad3').mkdir(parents=True)
        (game / '_vrad3' / 'lpv.exr').write_bytes(b'x' * 4096)
        (game / 'maps').mkdir()
        (game / 'maps' / 'x.vpk').write_bytes(b'y' * 1024)
        say = [sys.executable, '-c']
        guard = [sys.executable, '-m', 'cs2map.lockrun', '-', '60', '--nav-max', '20000', '--nav-probe', '--',
                 sys.executable, str(fake), '66482', '30']
        plan = {'compile': say + ['print("compiled")'], 'probe': guard, 'nav_max': 20000, 'game_addon': str(game),
                'build': say + ['print("BUILD WITH BOTS")'], 'build_without_bots': say + ['print("BUILD NO BOTS")']}
        plan_file = TMP / f'rel_plan_{uuid.uuid4().hex[:6]}.json'
        plan_file.write_text(json.dumps(plan), encoding='utf-8')
        try:
            env = {'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1',
                   'SYSTEMROOT': __import__('os').environ.get('SYSTEMROOT', '')}
            r = subprocess.run([sys.executable, '-m', 'cs2map.releaserun', str(plan_file)], capture_output=True,
                               text=True, timeout=120, env=env, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('BUILD NO BOTS', r.stdout)
            self.assertNotIn('BUILD WITH BOTS', r.stdout)
            self.assertIn('[release] bots off', r.stdout)
            self.assertFalse((game / '_vrad3').exists())
            self.assertIn('[release] upload size:', r.stdout)
            self.assertIn('== step 3/3', r.stdout)
        finally:
            import shutil
            shutil.rmtree(game, ignore_errors=True)
            plan_file.unlink(missing_ok=True)
            fake.unlink(missing_ok=True)

    def test_drop_bake_files_only_touches_vrad3(self):
        game = TMP / f'rel_game_{uuid.uuid4().hex[:6]}'
        (game / 'maps').mkdir(parents=True)
        try:
            self.assertEqual(releaserun.drop_bake_files(game), 0)
            self.assertTrue((game / 'maps').is_dir())
        finally:
            import shutil
            shutil.rmtree(game, ignore_errors=True)


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class PlayArea(unittest.TestCase):
    def setUp(self):
        from cs2map import server
        self.p = server.project
        self.p.map_create(MAP, 'empty', overwrite=True)
        # a 4096 x 4096 floor, a bumpy 'terrain' strip and a seed
        self.p.map_edit(MAP, [
            {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-2048, -2048, -16], 'maxs': [2048, 2048, 0]}},
            {'op': 'add_entity', 'classname': 'point_nav_walkable', 'origin': [0, 0, 4]}])

    def test_estimate_and_play_area_markup(self):
        from cs2map.vmap import VMap
        m = VMap.load(self.p.s.map_path(MAP))
        before = navcost.estimate(m)
        self.assertEqual(before['level'], 'ok')
        self.assertGreater(before['walkable_m2'], 10000)
        res = self.p.map_edit(MAP, [{'op': 'set_play_area', 'outline': [[-512, -512], [512, -512], [512, 512],
                                                                          [-512, 512]]}])
        r = res['results'][0]
        self.assertGreater(r['nonav_boxes'], 0)
        self.assertLess(r['nonav_boxes'], 12)                 # a square hole needs few rectangles
        m = VMap.load(self.p.s.map_path(MAP))
        self.assertEqual(navcost.play_area(m)['nav'], 'inside')
        ents = [e for e in m.iter_nodes() if e.type == 'CMapEntity' and m.classname(e) == 'func_nav_markup']
        self.assertEqual(len(ents), r['nonav_boxes'])                # one mesh per entity: the compiler reads one
        self.assertTrue(ents)
        self.assertEqual(ents[0]['entity_properties']['navProperty_NavGen'], 'NONAV')
        self.assertNotIn('navproperty_navgen', ents[0]['entity_properties'].attrs)
        after = navcost.estimate(m)
        self.assertLess(after['walkable_m2'], before['walkable_m2'] / 4)
        self.assertTrue(navcost.outside_play_area(m, (1500, 0)))
        self.assertFalse(navcost.outside_play_area(m, (0, 0)))
        # again: replaces its own markup instead of adding more
        res = self.p.map_edit(MAP, [{'op': 'set_play_area', 'outline': [[-1024, -1024], [1024, -1024], [1024, 1024],
                                                                          [-1024, 1024]], 'nav': 'routes',
                                     'routes': [{'points': [[-900, 0], [900, 0]], 'width': 256}]}])
        self.assertEqual(res['results'][0]['replaced_markup'], len(ents))
        self.assertLess(res['results'][0]['nav_m2'], 2048 * 384 * 0.0254 ** 2 * 1.5)

    def test_phantom_floors_outside_the_play_area_are_background(self):
        from cs2map import visual
        slab = {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [1400, -128, 8], 'maxs': [1656, 128, 12]},
                'options': {'physicsType': 'none'}}
        inner = {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-128, -128, 8], 'maxs': [128, 128, 12]},
                 'options': {'physicsType': 'none'}}
        pillar = {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-64, 400, 0], 'maxs': [64, 448, 256]}}
        ids = [r['ids'][0] for r in self.p.map_edit(MAP, [slab, inner, pillar])['results']][:2]

        def phantoms():
            found = visual.visual_check(self.p, MAP)['problems']
            return {p['id'] for p in found if p['code'] == 'visual_phantom_floor'}
        self.assertEqual(phantoms(), set(ids))
        self.p.map_edit(MAP, [{'op': 'set_play_area', 'outline': [[-512, -512], [512, -512], [512, 512], [-512, 512]],
                               'nav': 'all'}])
        self.assertEqual(phantoms(), {ids[1]})

    def test_add_nav_markup_and_check(self):
        res = self.p.map_edit(MAP, [{'op': 'add_nav_markup', 'kind': 'nonav', 'box': [[-100, -100, -32], [100, 100, 128]]},
                                    {'op': 'add_nav_markup', 'kind': 'seed', 'box': [[600, 600, 0], [700, 700, 72]]}])
        self.assertEqual([r['tag'] for r in res['results']], ['NONAV', 'WALKABLESEED'])
        from cs2map import checks
        problems = checks.check_map(self.p, MAP)['problems']
        self.assertIn('nav_seed_in_nonav', {p['code'] for p in problems})
        with self.assertRaises(Exception):
            self.p.map_edit(MAP, [{'op': 'add_nav_markup', 'kind': 'nope', 'box': [[0, 0, 0], [1, 1, 1]]}])

    def test_build_plan_drops_bots_over_the_limit_and_vis_on_open_maps(self):
        s = __import__('dataclasses').replace(self.p.s, limits=(('nav_max_areas', 100),))
        plan = self.p.build_plan(MAP, 'full', s=s)
        self.assertNotIn('-nav', plan['flags'])
        self.assertIn('off', plan['bots'])
        plan = self.p.build_plan(MAP, 'nav', s=s)                # a nav-only build is cheap: the guard measures
        self.assertIn('-nav', plan['flags'])
        plan = self.p.build_plan(MAP, 'final', bots=False)
        self.assertIn('-vis', plan['flags'])                     # 100 m wide: VIS stays
        self.p.map_edit(MAP, [{'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [-16000, -16000, -64],
                                                           'maxs': [16000, 16000, -48]}}])
        plan = self.p.build_plan(MAP, 'final', bots=False)
        self.assertNotIn('-vis', plan['flags'])
        self.assertIn('open map', plan['vis'])
        self.assertIn('-vis', self.p.build_plan(MAP, 'final', bots=False, vis=True)['flags'])


if __name__ == '__main__':
    unittest.main()
