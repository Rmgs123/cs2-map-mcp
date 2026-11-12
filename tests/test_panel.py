"""The status panel and what it rests on: the activity journal, the permits file (focus, heavy builds, deferred
tests), settings that follow the file, stopping a job from another process, the page's HTTP rules."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from cs2map import activity, config, game, jobs, panel, permits


def settings(root: Path, **kw):
    return config.Settings(cs2_path=root / 'cs2', addon='mcp_lab', **kw)


class Temp(unittest.TestCase):
    """Every test works on its own journal, permits file and settings file."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.settings_file = self.root / 'settings.json'
        self.settings_file.write_text(json.dumps({'cs2_path': str(self.root / 'cs2')}), encoding='utf-8')
        for target, name, value in ((activity, 'LOG', self.root / 'activity.jsonl'),
                                    (permits, 'STATE', self.root / 'panel_state.json'),
                                    (config, 'SETTINGS_FILE', self.settings_file)):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)


class Journal(Temp):
    def test_a_call_is_two_lines_and_reads_back_as_one(self):
        ok = activity.logged('map_edit', lambda **kw: {'ok': True, 'applied': 3}, who='test')
        ok(name='arena')
        with self.assertRaises(ValueError):
            activity.logged('map_info', lambda **kw: (_ for _ in ()).throw(ValueError('no such map')), who='test')(name='x')
        activity.note('focus', seconds=0.6, what='mouse')
        lines = [json.loads(x) for x in activity.LOG.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([x['ev'] for x in lines], ['start', 'end', 'start', 'end', 'focus'])
        got = activity.read()
        self.assertEqual([(c['tool'], c['state'], c['what']) for c in got['calls']],
                         [('map_info', 'bad', 'x'), ('map_edit', 'ok', 'arena')])
        self.assertEqual(got['calls'][1]['text'], '3 operations applied')
        self.assertEqual(got['calls'][0]['text'], 'no such map')
        self.assertEqual(got['focus'][0]['seconds'], 0.6)

    def test_a_call_of_a_dead_process_is_lost_and_a_refusal_is_short(self):
        activity.write({'ev': 'start', 'id': 'a', 't': time.time(), 'tool': 'playtest', 'what': 'm', 'who': 'x',
                        'pid': 2 ** 22 + 12345})
        self.assertEqual(activity.read()['calls'][0]['state'], 'lost')
        state, text = activity.summarize('map_build', {'refused': 'a heavy build (...)', 'estimate':
                                                       {'minutes': 7, 'memory_gb': 17}})
        self.assertEqual((state, text), ('warn', 'waits for the user: about 7 min, up to 17 GB'))
        state, text = activity.summarize('script_test', {'deferred': '1 step(s) need the real mouse (step 0: click '
                                                                     '[0.5, 0.5]) and settings game_focus is "never"'})
        self.assertEqual(text, 'put off: 1 step(s) need the real mouse (step 0: click [0.5, 0.5])')

    def test_the_journal_keeps_its_newest_lines(self):
        with patch.object(activity, 'MAX_BYTES', 2000), patch.object(activity, 'KEEP_LINES', 5):
            for i in range(60):
                activity.note('panel', text=f'line {i}')
        lines = activity.LOG.read_text(encoding='utf-8').splitlines()
        self.assertLess(len(lines), 50)
        self.assertIn('line 59', lines[-1])


class Permits(Temp):
    def big_map(self, s, mb=1):
        path = s.map_path('town')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'0' * int(mb * 2 ** 20))

    def past_build(self, s, seconds, title='build town (full)'):
        s.logs.mkdir(parents=True, exist_ok=True)
        (s.logs / f'{title.split()[0]}-20260101-{int(seconds):06d}-aaaaaa.json').write_text(json.dumps(
            {'id': 'x', 'kind': 'build', 'title': title, 'state': 'succeeded', 'seconds': seconds}), encoding='utf-8')

    def test_focus_for_a_while_turns_never_into_input_only(self):
        s = settings(self.root, game_focus='never')
        self.assertEqual(game.focus_policy(s), 'never')
        permits.allow_focus(15)
        self.assertEqual(game.focus_policy(s), 'input_only')
        self.assertIn('focus allowed until', permits.summary(s)['game_focus'])
        permits.allow_focus(None)
        self.assertEqual(game.focus_policy(s), 'never')
        permits.allow_focus(15)
        later = time.time() + 16 * 60
        with patch.object(permits.time, 'time', lambda: later):
            self.assertEqual(permits.focus_until(), 0)

    def test_a_heavy_build_waits_for_one_yes(self):
        with patch.object(config.Settings, 'logs', property(lambda self: self.cs2_path / 'logs')):
            s = settings(self.root, heavy_builds='ask')
            self.big_map(s)
            self.assertIsNone(permits.heavy_gate(s, 'town', 'full'))          # small and never built: not heavy
            self.assertIsNone(permits.heavy_gate(s, 'town', 'fast'))
            self.past_build(s, 400)
            self.assertIsNone(permits.heavy_gate(settings(self.root), 'town', 'full'))   # heavy_builds "allow"
            refused = permits.heavy_gate(s, 'town', 'full', who='Codex', panel='http://127.0.0.1:8717/')
            self.assertEqual(refused['estimate']['minutes'], 7)
            self.assertIn('http://127.0.0.1:8717/', refused['refused'])
            permits.heavy_gate(s, 'town', 'full')                             # asked again: still one question
            waiting = permits.waiting()['builds']
            self.assertEqual([(b['state'], b['who']) for b in waiting], [('waiting', 'Codex')])
            permits.answer_build(waiting[0]['id'], True)
            self.assertIsNone(permits.heavy_gate(s, 'town', 'full'))          # the yes lets one build through
            self.assertIn('refused', permits.heavy_gate(s, 'town', 'full'))   # and is used up
            ident = next(b['id'] for b in permits.waiting()['builds'] if b['state'] == 'waiting')
            permits.answer_build(ident, False)
            self.assertIn('not now', permits.heavy_gate(s, 'town', 'full')['refused'])
            self.assertEqual(len([b for b in permits.waiting()['builds'] if b['state'] == 'waiting']), 0)
            self.assertIn('town (full): declined', permits.summary(s)['heavy_build_requests'])

    def test_a_big_map_is_heavy_without_history_and_a_release_always(self):
        with patch.object(config.Settings, 'logs', property(lambda self: self.cs2_path / 'logs')):
            s = settings(self.root, heavy_builds='ask')
            self.big_map(s, 25)
            cost = permits.estimate(s, 'town', 'full')
            self.assertEqual(cost['basis'], 'the map size (no earlier build of this kind)')
            self.assertGreaterEqual(cost['memory_gb'], 3)
            self.assertIsNone(permits.estimate(s, 'town', 'nav'))
            self.assertIsNone(permits.estimate(s, 'nomap', 'full'))
            self.past_build(s, 900, 'release town -> addon town_ws')
            self.assertEqual(permits.estimate(s, 'town', 'release')['minutes'], 15)

    def test_a_click_test_waits_when_the_game_stays_behind(self):
        steps = [{'cmd': 'echo 1'}, {'click': [0.5, 0.5]}, {'focus': True}, {'focus': False}]
        self.assertEqual(len(permits.needs_focus(steps)), 2)
        self.assertEqual(permits.needs_focus('auto'), [])
        self.assertIsNone(permits.defer_test(settings(self.root), 'm', steps, 30))               # input_only
        never = settings(self.root, game_focus='never')
        self.assertIsNone(permits.defer_test(never, 'm', [{'mouse': {'dx': 40}}], 30))            # emulated
        held = permits.defer_test(never, 'm', steps, 30, who='Codex')
        self.assertIn('2 step(s) need the real mouse', held['deferred'])
        self.assertEqual(permits.defer_test(never, 'm', steps, 30)['id'], held['id'])             # the same test
        self.assertEqual(permits.summary(never)['deferred_tests'][0]['state'], 'waiting')
        taken = permits.take_test(held['id'])
        self.assertEqual(taken['steps'], steps)
        self.assertIsNone(permits.take_test(held['id']))
        permits.finish_test(held['id'], {'ok': True, 'passed': 2, 'check_count': 2, 'console': ['x'] * 100})
        done = permits.summary(never)['deferred_tests'][0]
        self.assertEqual((done['state'], done['ok'], done['passed']), ('done', True, 2))
        permits.allow_focus(15)
        self.assertIsNone(permits.defer_test(never, 'm', steps, 30))                              # allowed now

    def test_offline_runs_leave_the_users_file_alone(self):
        with patch.object(permits, 'STATE', permits._DEFAULT_STATE), patch.dict('os.environ', {'CS2MAP_OFFLINE': '1'}):
            self.assertFalse(permits.enabled())
            self.assertEqual(permits.load(), {})
            self.assertIsNone(permits.heavy_gate(settings(self.root, heavy_builds='ask'), 'town', 'full'))


class LiveSettings(Temp):
    def test_the_standing_choices_follow_the_file_and_paths_do_not(self):
        from cs2map.project import Project
        s = config.load_settings(self.settings_file)
        with patch.object(config.Settings, 'logs', property(lambda self: self.cs2_path / 'logs')):
            project = Project(s)
            self.assertEqual(project.s.heavy_builds, 'allow')
            config.update_settings({'heavy_builds': 'ask', 'build_priority': 'normal', 'cs2_path': 'D:/other'},
                                   self.settings_file)
            self.assertEqual(project.s.heavy_builds, 'allow')              # settings given by code stay as given
            project._live, project._settings_mtime = True, None
            self.assertEqual((project.s.heavy_builds, project.s.build_priority), ('ask', 'normal'))
            self.assertEqual(project.s.cs2_path, s.cs2_path)
            self.assertFalse(project.jobs.low_priority)
            self.settings_file.write_text('{broken', encoding='utf-8')      # keeps the last good values
            project._settings_checked = 0.0
            self.assertEqual(project.s.heavy_builds, 'ask')

    def test_new_settings_are_checked(self):
        for bad in ({'heavy_builds': 'yes'}, {'panel': 'on'}, {'panel_port': 80}, {'panel_port': True}):
            self.settings_file.write_text(json.dumps({'cs2_path': str(self.root), **bad}), encoding='utf-8')
            with self.assertRaises(config.ConfigError):
                config.load_settings(self.settings_file)
        self.settings_file.write_text(json.dumps({'cs2_path': str(self.root), 'panel': True, 'panel_port': 9000}),
                                      encoding='utf-8')
        s = config.load_settings(self.settings_file)
        self.assertEqual(config.panel_url(s), 'http://127.0.0.1:9000/')
        self.assertIsNone(config.panel_url(settings(self.root)))


class StopFromOutside(Temp):
    def test_another_process_stops_a_job_and_its_owner_says_cancelled(self):
        owner = jobs.JobManager(self.root / 'logs')
        job = owner.start('compile', 'sleep', [sys.executable, '-c', 'import time; time.sleep(60)'], self.root, 120)
        other = jobs.JobManager(self.root / 'logs')                          # as the panel: it did not start the job
        self.assertEqual(other.cancel(job.id).state, 'cancelled')
        done = owner.wait(job.id, 15)
        self.assertEqual(done.state, 'cancelled')
        self.assertEqual(jobs.JobManager(self.root / 'logs').get(job.id).state, 'cancelled')
        time.sleep(0.2)


class Page(Temp):
    def setUp(self):
        super().setUp()
        p = patch.object(config.Settings, 'logs', property(lambda self: self.cs2_path / 'logs'))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(config.Settings, 'screenshots', property(lambda self: self.cs2_path / 'shots'))
        p.start()
        self.addCleanup(p.stop)
        self.s = settings(self.root)
        self.s.logs.mkdir(parents=True)
        self.s.screenshots.mkdir(parents=True)

    def test_gpu_shares_take_the_busiest_engine(self):
        sample = {10: {'e3d': 30.0, 'copy': 5.0}, 11: {'e3d': 10.0}, 12: {'e3d': 30.0}, 13: {'e3d': 20.0}}
        shares = panel.gpu_shares(sample, {10: 'cs2', 11: 'cs2', 12: 'compiler'})
        self.assertEqual((shares['cs2'], shares['compiler'], shares['blender']), (40.0, 30.0, 0.0))
        self.assertEqual((shares['total'], shares['other']), (90.0, 20.0))     # the busiest engine is the PC's share
        apart = panel.gpu_shares({1: {'a': 50.0}, 2: {'b': 30.0}}, {1: 'cs2', 2: 'compiler'})
        self.assertEqual((apart['cs2'], apart['compiler'], apart['total'], apart['other']), (50.0, 30.0, 80.0, 0.0))
        capped = panel.gpu_shares({1: {'a': 90.0}, 2: {'b': 60.0}}, {1: 'cs2', 2: 'compiler'})
        self.assertAlmostEqual(capped['cs2'] + capped['compiler'], 100.0)
        self.assertEqual(capped['total'], 100.0)
        self.assertEqual(panel.gpu_shares({}, {}), {'compiler': 0.0, 'cs2': 0.0, 'blender': 0.0, 'other': 0.0, 'total': 0.0})

    def test_a_running_build_shows_stage_and_percent_from_the_last_one(self):
        import datetime as dt
        (self.s.logs / 'build-20260101-000000-aaaaaa.json').write_text(json.dumps(
            {'id': 'old', 'kind': 'build', 'title': 'build town (full)', 'state': 'succeeded', 'seconds': 400,
             'argv': [], 'log': '', 'started': '2026-01-01T00:00:00'}), encoding='utf-8')
        log = self.s.logs / 'build-x.log'
        log.write_text('$ resourcecompiler ...\n\n==== Baking 2048 x 2048 lightmap ====\n'
                       '    Round 12 cumulative runtime: 1m 05s\n', encoding='utf-8')
        started = (dt.datetime.now() - dt.timedelta(seconds=100)).isoformat(timespec='seconds')
        job = jobs.Job(id='build-x', kind='build', title='build town (full)', log=str(log), started=started, cwd='',
                       argv=['resourcecompiler.exe', '-world', '-phys', '-nav', '-gridnav', '-bakelighting'])
        view = panel.JobView(self.s).view(job, 'Codex')
        self.assertEqual((view['name'], view['map'], view['preset'], view['by']), ('Light bake', 'town', 'full', 'Codex'))
        self.assertEqual([s[0] for s in view['stages']], ['Prepare', 'Light', 'World', 'Navigation', 'Bot data', 'Write'])
        self.assertEqual(view['stage'][0], 1)
        self.assertEqual(view['detail'], 'VRAD3 · round 12, 1m 05s')
        self.assertTrue(24 <= view['pct'] <= 27, view['pct'])
        self.assertEqual(view['left_min'], 5)
        first = panel.JobView(self.s).view(jobs.Job(id='build-y', kind='build', title='build other (fast)', log=str(log),
                                                    started=started, cwd='', argv=['-world']), None)
        self.assertIsNone(first['pct'])                             # no earlier build of this kind: no guess

    def test_frames_and_marks_newest_first_and_no_path_outside(self):
        from PIL import Image
        shots = self.s.screenshots
        (shots / 'marks').mkdir()
        Image.new('RGB', (64, 36), (200, 10, 10)).save(shots / 'view_a-20261109-221500-123.png')
        Image.new('RGB', (64, 36), (10, 10, 200)).save(shots / 'marks' / 'mark-1.jpg')
        state_dir = self.root / '.local' / 'state'
        state_dir.mkdir(parents=True)
        (state_dir / 'marks.json').write_text(json.dumps({'marks': [
            {'path': str(shots / 'marks' / 'mark-1.jpg'), 'map': 'town', 'epoch': time.time() + 5, 'notes': ['door stuck']}]}),
            encoding='utf-8')
        (self.root / 'secret.png').write_bytes(b'x')
        with patch.object(panel, 'PROJECT_ROOT', self.root), patch.object(panel, 'THUMBS', self.root / 'thumbs'):
            got = panel.frames(self.s)
            self.assertEqual([(f['name'], f.get('mark', False)) for f in got['items']], [('mark 1', True), ('view_a', False)])
            self.assertEqual(got['items'][0]['note'], 'door stuck')
            self.assertIsNone(panel._inside(shots, '../secret.png'))
            self.assertIsNone(panel._inside(shots, 'nothing.png'))
            small = panel.thumb(panel._inside(shots, 'view_a-20261109-221500-123.png'))
            self.assertTrue(small.is_file() and small.suffix == '.jpg')

    def serve(self):
        self.panel = panel.Panel(self.s)
        self.panel.settings = lambda: self.panel.s
        httpd = panel._Server(('127.0.0.1', 0), panel.make_handler(self.panel))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def ask(self, port, path, body=None, headers=None):
        request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers=headers or {},
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_the_page_is_local_and_actions_need_its_token(self):
        port = self.serve()
        code, body = self.ask(port, '/api/state')
        state = json.loads(body)
        self.assertEqual((code, state['now']['kind'], state['perms']['heavy']), (200, 'idle', True))
        self.assertEqual(self.ask(port, '/api/state', headers={'Host': 'evil.example:80'})[0], 403)
        code, page = self.ask(port, '/')
        self.assertIn(self.panel.token.encode(), page)
        self.assertNotIn(b'__TOKEN__', page)
        toggle = {'do': 'toggle', 'key': 'heavy', 'on': False}
        self.assertEqual(self.ask(port, '/api/action', toggle)[0], 403)
        token = {'X-Panel-Token': self.panel.token, 'Content-Type': 'application/json'}
        self.assertEqual(self.ask(port, '/api/action', toggle, {**token, 'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.ask(port, '/api/action', toggle, token)[0], 200)
        self.assertEqual(json.loads(self.settings_file.read_text(encoding='utf-8'))['heavy_builds'], 'ask')
        self.assertEqual(self.ask(port, '/api/action', {'do': 'toggle', 'key': 'focus', 'on': False}, token)[0], 200)
        self.assertEqual(json.loads(self.settings_file.read_text(encoding='utf-8'))['game_focus'], 'never')
        self.assertEqual(self.ask(port, '/api/action', {'do': 'focus15'}, token)[0], 200)
        self.assertGreater(permits.focus_until(), time.time() + 14 * 60)
        self.assertEqual(self.ask(port, '/api/action', {'do': 'nonsense'}, token)[0], 400)
        self.assertEqual(self.ask(port, '/frame?p=../../settings.json')[0], 404)
        self.assertTrue(panel.running(port))
        notes = [c for c in activity.read()['calls'] if c['who'] == 'panel']
        self.assertIn('heavy_builds = "ask"', [c['text'] for c in notes])

    def test_answers_reach_the_permits_file(self):
        port = self.serve()
        token = {'X-Panel-Token': self.panel.token}
        s = settings(self.root, heavy_builds='ask')
        path = s.map_path('town')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'0' * (21 * 2 ** 20))
        permits.heavy_gate(s, 'town', 'full', who='Codex')
        code, body = self.ask(port, '/api/state')
        asked = json.loads(body)['asks']['builds']
        self.assertEqual([(b['map'], b['who']) for b in asked], [('town', 'Codex')])
        self.assertEqual(self.ask(port, '/api/action', {'do': 'build', 'id': asked[0]['id'], 'allow': True}, token)[0], 200)
        self.assertIsNone(permits.heavy_gate(s, 'town', 'full'))
        self.assertEqual(self.ask(port, '/api/action', {'do': 'build', 'id': 'gone', 'allow': True}, token)[0], 400)
        self.assertEqual(self.ask(port, '/api/action', {'do': 'run_tests'}, token)[0], 400)      # nothing waits


if __name__ == '__main__':
    unittest.main()
