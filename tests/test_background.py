"""CS2 works behind the user's windows (settings game_focus): the console says "foreground" on every connection,
operations hold it, the window comes to the front only for real mouse input. Sockets and windows are faked."""
import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from cs2map import config, game, jobs, vconsole


def settings(focus='input_only'):
    return config.Settings(cs2_path=Path('cs2'), addon='mcp_lab', game_focus=focus)


class FakeConsole:
    """A TCP server that records what one console client sends."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.data = b''
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        conn.settimeout(0.2)
        with conn:
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                self.data += chunk

    def close(self):
        self.server.close()


class Awake(unittest.TestCase):
    def test_every_connection_says_foreground(self):
        fake = FakeConsole()
        try:
            with patch.object(vconsole, 'evict_vconsole', lambda port: []):
                conn = vconsole._Connection(fake.port)
            time.sleep(0.2)
            conn.wake(False)
            time.sleep(0.2)
            conn.close()
        finally:
            fake.close()
        on = vconsole._packet(b'VFCS', b'\x01')
        off = vconsole._packet(b'VFCS', b'\x00')
        self.assertTrue(fake.data.startswith(on), fake.data[:32])
        self.assertIn(off, fake.data)
        self.assertEqual(len(on), 13)

    def test_hold_counts_and_survives_a_missing_cs2(self):
        def refuse(port):
            raise vconsole.VConsoleError('no CS2')
        with patch.object(vconsole, '_connection', refuse):
            before = vconsole._HOLDS
            with vconsole.hold():
                self.assertEqual(vconsole._HOLDS, before + 1)
                with vconsole.hold():
                    self.assertEqual(vconsole._HOLDS, before + 2)
            self.assertEqual(vconsole._HOLDS, before)
            with self.assertRaises(ValueError):
                with vconsole.hold():
                    raise ValueError('inside')
            self.assertEqual(vconsole._HOLDS, before)


    def test_the_cs2_lock_keeps_the_connection_but_opens_none(self):
        @contextmanager
        def lock_file(owner, wait):
            yield

        def connect(port):
            self.fail('the lock itself must not connect to CS2')
        with patch.object(game, '_exclusive_file', lock_file), patch.object(vconsole, '_connection', connect):
            before = vconsole._HOLDS
            with game.exclusive('test'):
                self.assertEqual(vconsole._HOLDS, before + 1)
                with game.exclusive('test'):                 # nested: the same lock, the same hold
                    self.assertEqual(vconsole._HOLDS, before + 1)
            self.assertEqual(vconsole._HOLDS, before)


class Policy(unittest.TestCase):
    def test_settings_default_and_values(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'settings.json'
            f.write_text(json.dumps({'cs2_path': d}), encoding='utf-8')
            s = config.load_settings(f)
            self.assertEqual((s.game_focus, s.build_priority), ('input_only', 'low'))
            f.write_text(json.dumps({'cs2_path': d, 'game_focus': 'never', 'build_priority': 'normal'}), encoding='utf-8')
            s = config.load_settings(f)
            self.assertEqual((s.game_focus, s.build_priority), ('never', 'normal'))
            for bad in ({'game_focus': 'sometimes'}, {'game_focus': True}, {'build_priority': 'high'}):
                f.write_text(json.dumps({'cs2_path': d, **bad}), encoding='utf-8')
                with self.assertRaises(config.ConfigError):
                    config.load_settings(f)

    def test_awake_holds_the_console_and_takes_no_focus(self):
        calls = []

        @contextmanager
        def hold():
            calls.append('hold')
            yield

        @contextmanager
        def front(what=None):
            calls.append('front')
            yield 1

        with patch.object(vconsole, 'hold', hold), patch.object(game, '_front', front):
            for focus, expected in (('input_only', 'hold'), ('never', 'hold'), ('always', 'front')):
                calls.clear()
                with game.awake(settings(focus)):
                    pass
                self.assertEqual(calls, [expected], focus)

    def test_input_focus_refused_when_never_and_checked_otherwise(self):
        with self.assertRaisesRegex(game.GameError, 'click needs the CS2 window in front.*"never"'):
            with game.input_focus(settings('never'), 'click'):
                self.fail('must not run')

        @contextmanager
        def front(what=None):
            yield 77

        with patch.object(game, '_front', front), patch.object(game.time, 'sleep', lambda x: None):
            with patch.object(game.system, 'foreground', lambda: 77):
                with game.input_focus(settings(), 'mouse') as hwnd:
                    self.assertEqual(hwnd, 77)
            with patch.object(game.system, 'foreground', lambda: 5):       # another program kept the focus
                with self.assertRaisesRegex(game.GameError, 'did not come to the front'):
                    with game.input_focus(settings(), 'mouse'):
                        self.fail('must not send input')

    def test_minimized_game_window_is_left_minimized(self):
        seen = []

        def window(title, restore=True):
            seen.append(restore)
        with patch.object(game, '_window', window):
            game._game_window(settings())
            game._game_window(settings('always'))
        self.assertEqual(seen, [False, True])


class PlayerState(unittest.TestCase):
    def test_two_diagnostics_answers_are_one_player_list(self):
        def run_when(commands, done, timeout=5.0):
            token = commands[0].split()[1]
            p0 = json.dumps({'slot': 0, 'bot': False, 'team': 2, 'alive': True})
            p1 = json.dumps({'slot': 1, 'bot': True, 'team': 3, 'alive': True})
            head = json.dumps({'map': 'm', 'count': 2})
            lines = [f'C_MCP_STATEP {token} {p0}', f'C_MCP_STATEP {token} {p1}', f'C_MCP_STATE {token} {head}'] * 2
            self.assertFalse(done(lines[:1]))
            self.assertTrue(done(lines[:3]))
            return True, lines
        with patch.object(game, 'ensure_alive', lambda s, wait=0: {}), patch.object(vconsole, 'run_when', run_when):
            state = game.player_state(settings())
        self.assertEqual([p['slot'] for p in state['players']], [0, 1])

    def test_a_missing_player_line_is_still_an_error(self):
        def run_when(commands, done, timeout=5.0):
            token = commands[0].split()[1]
            return False, [f'C_MCP_STATE {token} ' + json.dumps({'map': 'm', 'count': 2})]
        with patch.object(game, 'ensure_alive', lambda s, wait=0: {}), patch.object(vconsole, 'run_when', run_when):
            with self.assertRaisesRegex(game.GameError, '0 of 2 player lines'):
                game.player_state(settings())


class Priority(unittest.TestCase):
    def test_jobs_start_below_normal_only_when_asked(self):
        flags = []

        class Proc:
            pid = 1

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        def popen(argv, **kw):
            flags.append(kw['creationflags'])
            return Proc()
        with tempfile.TemporaryDirectory() as d, patch.object(jobs.subprocess, 'Popen', popen), \
                patch.object(jobs.JobManager, '_watch', lambda self, job, proc, fh, on_finish: fh.close()):
            manager = jobs.JobManager(Path(d))
            manager.start('compile', 't', ['x'], Path(d))
            manager.low_priority = True
            manager.start('compile', 't', ['x'], Path(d))
            time.sleep(0.2)                     # the watcher threads close the log files
        self.assertFalse(flags[0] & jobs.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertTrue(flags[1] & jobs.BELOW_NORMAL_PRIORITY_CLASS)


if __name__ == '__main__':
    unittest.main()
