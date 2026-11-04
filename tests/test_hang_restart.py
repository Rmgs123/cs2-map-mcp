"""Opt-in restart of a hung CS2 Workshop Tools session (settings restart_hung_cs2); processes are mocked."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cs2map import config, game


class Clock:
    """Fake time.monotonic/sleep for the game module: sleeping advances the clock."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def module(self):
        return SimpleNamespace(monotonic=self.monotonic, sleep=self.sleep, time=time.time)


def settings(on=True, seconds=180, addon='mcp_lab', user=False):
    return config.Settings(cs2_path=Path('cs2'), addon=addon, restart_hung_cs2=on, restart_hung_cs2_seconds=seconds,
                           restart_hung_cs2_user=user)


class Fixture(unittest.TestCase):
    """A CS2 session (pid 111) whose window is hung while `self.hung_until` is in the future."""

    def setUp(self):
        game._HANG.clear()
        self.clock = Clock()
        self.hung_until = float('inf')
        self.session = {'pid': 111, 'mode': 'tools', 'addon': 'mcp_lab', 'launched_by_tool': True}
        self.tmp = tempfile.TemporaryDirectory()
        self.loaded = Path(self.tmp.name) / 'loaded_map.json'
        self.loaded.write_text(json.dumps({'pid': 111, 'map': 'kr_test'}), encoding='utf-8')
        self.proc = MagicMock()
        self.launch = MagicMock(side_effect=self._launch)
        self.load_map = MagicMock(return_value=[])
        self.calls = 0
        self.install = MagicMock(return_value='F8')
        patches = [patch.object(game, 'time', self.clock.module()),
                   patch.object(game, 'session', side_effect=lambda s: self.session),
                   patch.object(game, 'hung', side_effect=lambda s: self.clock.now < self.hung_until),
                   patch.object(game, 'LOADED', self.loaded),
                   patch.object(game, 'launch', self.launch),
                   patch.object(game, 'load_map', self.load_map),
                   patch.object(game, 'exclusive', MagicMock()),
                   patch.object(game.vconsole, 'release'),
                   patch.object(game, 'console_ready', return_value=True),
                   patch.object(game.system, 'windows', return_value=[]),
                   patch.object(game.psutil, 'Process', return_value=self.proc),
                   patch('cs2map.marks.install', self.install)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _launch(self, s, *args, **kwargs):
        self.launched_addon = s.addon
        self.session = {'pid': 222, 'mode': 'tools', 'addon': s.addon, 'launched_by_tool': True}
        self.hung_until = 0.0               # the new process responds
        return {'state': 'started', 'addon': s.addon}

    def tool(self, s, swallow=False):
        """A game tool call: checks CS2 is alive (30 s grace like every command), then answers."""
        def call():
            self.calls += 1
            try:
                game.ensure_alive(s)
            except game.GameError as e:
                if swallow:                 # script_test/route_test turn errors into a result
                    return {'ok': False, 'reason': str(e)}
                raise
            return {'ok': True, 'pid': self.session['pid']}
        return call


class HangRestart(Fixture):
    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'settings.json'
            f.write_text(json.dumps({'cs2_path': tmp}), encoding='utf-8')
            with patch.dict('os.environ', {}, clear=False) as env:
                env.pop('CS2MAP_CS2_PATH', None)
                s = config.load_settings(f)
        self.assertEqual((s.restart_hung_cs2, s.restart_hung_cs2_seconds, s.restart_hung_cs2_user), (False, 180, False))
        with self.assertRaisesRegex(game.GameError, 'ask the user to restart.*restart_hung_cs2'):
            game.run_restartable(s, self.tool(s))
        self.proc.kill.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(self.calls, 1)

    def test_settings_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'settings.json'
            for bad in ({'restart_hung_cs2': 'yes'}, {'restart_hung_cs2_seconds': 10}, {'restart_hung_cs2_user': 1},
                        {'restart_hung_cs2_seconds': True}):
                f.write_text(json.dumps({'cs2_path': tmp, **bad}), encoding='utf-8')
                with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                    config.load_settings(f)

    def test_user_started_or_plain_game_is_never_killed(self):
        for session in ({'launched_by_tool': False}, {'mode': 'game'}):
            self.session = {'pid': 111, 'mode': 'tools', 'addon': 'mcp_lab', 'launched_by_tool': True, **session}
            self.calls = 0
            game._HANG.clear()
            with self.subTest(session=session):
                with self.assertRaisesRegex(game.GameError, 'ask the user to restart CS2 Workshop Tools'):
                    game.run_restartable(settings(), self.tool(settings()))
                self.assertEqual(self.calls, 1)
        self.proc.kill.assert_not_called()
        self.launch.assert_not_called()

    def test_user_started_tools_restart_only_with_restart_hung_cs2_user(self):
        self.session = {'pid': 111, 'mode': 'tools', 'addon': 'mcp_lab', 'launched_by_tool': False}
        with self.assertRaisesRegex(game.GameError, 'restart_hung_cs2_user'):
            game.run_restartable(settings(), self.tool(settings()))     # on, but not for user-started
        self.proc.kill.assert_not_called()
        game._HANG.clear()
        s = settings(user=True)
        out = game.run_restartable(s, self.tool(s))
        self.proc.kill.assert_called_once()
        self.assertEqual((self.launched_addon, out['pid']), ('mcp_lab', 222))
        self.load_map.assert_called_once_with(s, 'kr_test')
        self.assertIn('restarted it', out['cs2_restart'])
        # The killed session never saved its binds: the player-marks key is bound again in the new one.
        self.install.assert_called_once()
        self.assertEqual(self.install.call_args[0][0].addon, 'mcp_lab')
        self.assertNotIn('marks key', out['cs2_restart'])

    def test_marks_key_failure_does_not_fail_the_recovery(self):
        self.install.side_effect = RuntimeError('console busy')
        s = settings()
        out = game.run_restartable(s, self.tool(s))
        self.assertEqual(out['pid'], 222)
        self.assertIn('marks key not bound: console busy', out['cs2_restart'])

    def test_user_option_never_touches_a_plain_game_or_another_addon(self):
        for session in ({'mode': 'game'}, {'addon': 'their_addon'}):
            self.session = {'pid': 111, 'mode': 'tools', 'addon': 'mcp_lab', 'launched_by_tool': False, **session}
            game._HANG.clear()
            with self.subTest(session=session), self.assertRaises(game.GameError):
                game.run_restartable(settings(user=True), self.tool(settings(user=True)))
        self.proc.kill.assert_not_called()
        self.launch.assert_not_called()
        # The user option alone does nothing while restart_hung_cs2 is off.
        self.session = {'pid': 111, 'mode': 'tools', 'addon': 'mcp_lab', 'launched_by_tool': False}
        game._HANG.clear()
        with self.assertRaises(game.GameError):
            game.run_restartable(settings(on=False, user=True), self.tool(settings(on=False, user=True)))
        self.proc.kill.assert_not_called()

    def test_threshold_is_respected(self):
        s = settings(seconds=180)
        killed_at = []
        self.proc.kill.side_effect = lambda: killed_at.append(self.clock.now)
        out = game.run_restartable(s, self.tool(s))
        start = 1000.0                                      # the hang is seen at once and lasts
        self.assertEqual(len(killed_at), 1)
        self.assertGreaterEqual(killed_at[0], start + 180)
        self.assertLess(killed_at[0], start + 185)
        self.assertTrue(out['ok'])

    def test_recovery_before_the_threshold_kills_nothing(self):
        s = settings(seconds=180)
        self.hung_until = 1000.0 + 100                      # unfreezes after 100 s
        out = game.run_restartable(s, self.tool(s))
        self.proc.kill.assert_not_called()
        self.launch.assert_not_called()
        self.assertIn('recovered by itself', out['cs2_restart'])
        self.assertEqual(self.calls, 2)

    def test_relaunch_reloads_the_map_and_retries_once(self):
        s = settings()
        out = game.run_restartable(s, self.tool(s))
        self.proc.kill.assert_called_once()
        self.launch.assert_called_once()
        self.assertEqual(self.launched_addon, 'mcp_lab')
        self.load_map.assert_called_once_with(s, 'kr_test')
        self.assertEqual(self.calls, 2)
        self.assertEqual(out['pid'], 222)
        self.assertRegex(out['cs2_restart'], r'restarted it .*reloaded kr_test and retried the call once')
        self.assertNotIn('\n', out['cs2_restart'])

    def test_a_release_addon_relaunches_the_session_addon(self):
        s = settings(addon='my_release')
        game.run_restartable(s, self.tool(s))
        self.assertEqual(self.launched_addon, 'mcp_lab')

    def test_a_swallowed_hang_in_a_scenario_result_is_retried(self):
        s = settings()
        out = game.run_restartable(s, self.tool(s, swallow=True))
        self.assertEqual((out['ok'], self.calls), (True, 2))
        self.assertIn('cs2_restart', out)

    def test_hung_again_after_the_restart_gives_up(self):
        s = settings()
        self.launch.side_effect = lambda st, *a, **k: (self._launch(st), setattr(self, 'hung_until', float('inf')))[0]
        with self.assertRaisesRegex(game.GameError, 'restarted it.*The retry failed .*: CS2 is not responding'):
            game.run_restartable(s, self.tool(s))
        self.assertEqual(self.calls, 2)
        self.proc.kill.assert_called_once()
        # An outer call (lighting_build around its bake) does not restart a second time.
        with self.assertRaises(game.GameError):
            game.run_restartable(s, lambda: game.run_restartable(s, self.tool(s)))
        self.assertEqual(self.proc.kill.call_count, 2)       # one per tool call, never two in one call

    def test_unsaved_hammer_edits_block_the_restart(self):
        s = settings()
        hammer = SimpleNamespace(pid=111, title='Hammer - kr_test.vmap *')
        with patch.object(game.system, 'windows', return_value=[hammer]):
            with self.assertRaisesRegex(game.GameError, 'unsaved edits.*not restarted'):
                game.run_restartable(s, self.tool(s))
        self.proc.kill.assert_not_called()

    def test_calls_without_a_hang_are_untouched(self):
        s = settings()
        self.hung_until = 0.0
        self.assertEqual(game.run_restartable(s, self.tool(s)), {'ok': True, 'pid': 111})
        with self.assertRaisesRegex(ValueError, 'bad'):
            game.run_restartable(s, lambda: (_ for _ in ()).throw(ValueError('bad')))
        self.proc.kill.assert_not_called()


MAP_STATUS = ['loaded spawngroup(  1)  : SV:  [1: kr_test | main lump | mapload]']


class AfterRelaunch(Fixture):
    """Defects of the first live test (03.11): a fresh CS2 drops the console during its first map load."""

    def test_console_drop_during_the_reload_is_waited_out_and_the_state_written(self):
        s = settings()
        self.load_map.side_effect = [game.vconsole.VConsoleError('CS2 closed the console connection')]
        with patch.object(game, 'status', return_value=MAP_STATUS):
            out = game.run_restartable(s, self.tool(s))
        self.assertIn('reloaded kr_test', out['cs2_restart'])
        self.assertEqual(json.loads(self.loaded.read_text(encoding='utf-8'))['pid'], 222)
        self.assertEqual((out['ok'], self.calls), (True, 2))

    def test_a_console_error_in_the_retry_keeps_the_restart_note(self):
        s = settings()
        first = self.tool(s)

        def call():
            if self.calls == 0:
                return first()
            self.calls += 1
            raise game.vconsole.VConsoleError('CS2 closed the console connection (did the game crash?)')
        with self.assertRaisesRegex(game.GameError, r'restarted it.*retry failed .*: VConsoleError'):
            game.run_restartable(s, call)

    def test_a_console_error_during_the_restart_is_reported_with_the_hang(self):
        s = settings()
        self.launch.side_effect = game.vconsole.VConsoleError('console busy')
        with self.assertRaisesRegex(game.GameError, r'not responding.*\| CS2 hung for 180 s and was killed.*relaunch failed: console busy'):
            game.run_restartable(s, self.tool(s))

    def test_no_console_after_the_relaunch_is_a_clear_error(self):
        s = settings()
        with patch.object(game, 'console_ready', return_value=False):
            with self.assertRaisesRegex(game.GameError, 'restarted it.*did not answer'):
                game.run_restartable(s, self.tool(s))


class LoadedState(unittest.TestCase):
    def test_loaded_map_is_written_as_soon_as_status_confirms_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = Path(tmp) / 'loaded_map.json'
            loaded.write_text(json.dumps({'pid': 111, 'map': 'old'}), encoding='utf-8')
            s = settings()
            with patch.object(game, 'LOADED', loaded), patch.object(game, 'ensure_alive'), \
                    patch.object(game, 'session', return_value={'pid': 222}), \
                    patch.object(game.vconsole, 'run_until', return_value=(True, [])), \
                    patch.object(game, 'status', return_value=MAP_STATUS), \
                    patch.object(game, 'fast_start', side_effect=game.vconsole.VConsoleError('dropped')):
                with self.assertRaises(game.vconsole.VConsoleError):
                    game.load_map(s, 'kr_test')
            self.assertEqual(json.loads(loaded.read_text(encoding='utf-8'))['pid'], 222)
            self.assertEqual(json.loads(loaded.read_text(encoding='utf-8'))['map'], 'kr_test')


class FrozenDetection(unittest.TestCase):
    def setUp(self):
        game._HANG.clear()
        game._HWNDS.update(pid=None, at=0.0, list=[])
        self.addCleanup(game._HWNDS.update, pid=None, at=0.0, list=[])

    def test_a_window_that_does_not_answer_wm_null_is_hung(self):
        s = settings()
        win = SimpleNamespace(hwnd=5, pid=111, title='Counter-Strike 2')
        with patch.object(game, 'session', return_value={'pid': 111}), \
                patch.object(game.system, 'windows', return_value=[win]), \
                patch.object(game.system, 'is_hung', return_value=False), \
                patch.object(game.system.user32, 'IsWindow', return_value=True):
            with patch.object(game.system, 'responds', return_value=True):
                self.assertFalse(game.hung(s))
            with patch.object(game.system, 'responds', return_value=False):
                self.assertTrue(game.hung(s))

    def test_a_silent_console_starts_the_hang_path(self):
        s = settings()
        frozen = {'v': False}
        with patch.object(game, 'session', return_value={'pid': 111, 'mode': 'tools', 'launched_by_tool': True}), \
                patch.object(game, 'hung', side_effect=lambda st: frozen['v']), \
                patch.object(game.vconsole, 'run_ended', side_effect=lambda *a, **k: (frozen.update(v=True), (False, []))[1]):
            with self.assertRaisesRegex(game.GameError, 'not responding'):
                game.command(s, 'echo X')
        self.assertEqual(game._HANG.get('pid'), 111)
        # A console that stalls while the window answers (a map load) returns the partial output as before.
        game._HANG.clear()
        with patch.object(game, 'session', return_value={'pid': 111}), patch.object(game, 'hung', return_value=False), \
                patch.object(game.vconsole, 'run_ended', return_value=(False, ['partial'])):
            self.assertEqual(game.command(s, 'echo X'), ['partial'])
        self.assertFalse(game._HANG)


class SettingsUpdate(unittest.TestCase):
    def test_update_keeps_other_keys_and_reads_a_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'settings.json'
            f.write_text('﻿' + json.dumps({'cs2_path': tmp, 'addon': 'my_lab'}), encoding='utf-8')
            config.update_settings({'restart_hung_cs2': True, 'restart_hung_cs2_seconds': 180}, f)
            data = json.loads(f.read_text(encoding='utf-8'))
            self.assertEqual((data['addon'], data['restart_hung_cs2']), ('my_lab', True))
            with patch.dict('os.environ', {}, clear=False) as env:
                env.pop('CS2MAP_CS2_PATH', None); env.pop('CS2MAP_ADDON', None)
                self.assertTrue(config.load_settings(f).restart_hung_cs2)

    def test_example_documents_the_setting(self):
        data = json.loads((config.PROJECT_ROOT / 'config' / 'settings.example.json').read_text(encoding='utf-8'))
        self.assertIs(data['restart_hung_cs2'], False)
        self.assertIs(data['restart_hung_cs2_user'], False)
        self.assertIn('restart_hung_cs2', data['note'])


if __name__ == '__main__':
    unittest.main()
