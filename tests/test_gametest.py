"""The test layer: minidump triage, crash report, hang detection, script_test input steps (no game needed)."""
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cs2map import crashdump, game, gametest, scripting, system
from cs2map.config import PROJECT_ROOT


def minidump(code=0xC00000FD, address=0x7FF000001234, base=0x7FF000000000, text=b''):
    """A tiny but valid minidump: one module (server.dll), one exception, extra bytes for the console tail."""
    name = 'C:\\game\\bin\\win64\\server.dll'.encode('utf-16-le')
    header_size, dir_size = 32, 2 * 12
    modules_rva = header_size + dir_size
    module_list = bytearray(struct.pack('<I', 1) + struct.pack('<QIIII', base, 0x2000, 0, 0, 0) + bytes(108 - 24))
    exception_rva = modules_rva + len(module_list)
    exception = struct.pack('<II', 77, 0) + struct.pack('<IIQQI', code, 0, 0, address, 0) + bytes(4 + 15 * 8)
    struct.pack_into('<I', module_list, 4 + 20, exception_rva + len(exception))      # the module name follows
    body = struct.pack('<4sIIIIIQ', b'MDMP', 0xA793, 2, header_size, 0, 0, 0)
    body += struct.pack('<III', 4, len(module_list), modules_rva) + struct.pack('<III', 6, len(exception), exception_rva)
    body += bytes(module_list) + exception + struct.pack('<I', len(name)) + name + b'\0\0' + text
    return bytes(body)


class CrashDump(unittest.TestCase):
    def setUp(self):
        root = PROJECT_ROOT / '.local' / 'tmp'
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='dump-', dir=root)
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_exception_module_and_console_tail(self):
        text = (b'\0 120(10.500000):  D_CONTROL drone enter {"slot":0}\0 130(11.250000):  X8_CUSTOM railgun shot {"hits":1}\0'
                b'Cause the engine to crash by Plat_FatalError on main thread\0 FATAL ERROR: Error loading models/x.vxml_c: bad folder\0')
        path = self.dir / 'cs2_2026_1005_000000_0_crash.mdmp'
        path.write_bytes(minidump(text=text))
        info = crashdump.parse(path)
        self.assertEqual(info['exception']['name'], 'STACK_OVERFLOW')
        self.assertEqual(info['module'], {'name': 'server.dll', 'offset': '0x1234'})
        tail = crashdump.console_tail(path)
        self.assertEqual(tail[0], '10.5s D_CONTROL drone enter {"slot":0}')       # stamped lines sorted by game time
        self.assertTrue(tail[1].startswith('11.2s X8_CUSTOM railgun shot'))
        self.assertTrue(tail[-1].startswith('FATAL ERROR'))
        self.assertFalse(any('Cause the engine' in x for x in tail))               # command help text is noise
        (self.dir / 'bad.mdmp').write_bytes(b'nope')
        with self.assertRaises(ValueError):
            crashdump.parse(self.dir / 'bad.mdmp')

    def test_crash_report_is_short_and_names_the_dump(self):
        old = self.dir / 'cs2_2026_1004_000000_0_error.mdmp'
        new = self.dir / 'cs2_2026_1005_000000_0_crash.mdmp'
        old.write_bytes(minidump(code=0x80000003))
        new.write_bytes(minidump())
        os.utime(old, (time.time() - 5000, time.time() - 5000))
        project = SimpleNamespace(s=SimpleNamespace(game_bin=self.dir, logs=self.dir / 'logs'))
        with patch.object(gametest, '_launch_time', return_value=time.time() - 60):
            report = gametest.crash_report(project)
            self.assertEqual((report['dump'], report['exception'].split()[0], report['crashed_since_launch']),
                             (new.name, 'STACK_OVERFLOW', True))
            self.assertIn('server.dll', report['module'])
            self.assertIn('hint', report)
            self.assertTrue(Path(report['log']).is_file())
            self.assertLess(len(str(report)), 1800)
            self.assertEqual(gametest.crash_report(project, old.name)['exception'].split()[0], 'BREAKPOINT')
            with self.assertRaises(ValueError):
                gametest.crash_report(project, 'nope.mdmp')
        with patch.object(gametest, '_launch_time', return_value=time.time() + 60):
            self.assertIn('older', gametest.crash_report(project)['note'])
        nothing = SimpleNamespace(s=SimpleNamespace(game_bin=self.dir / 'none', logs=self.dir))
        self.assertFalse(gametest.crash_report(nothing)['crash'])


class Hang(unittest.TestCase):
    settings = SimpleNamespace(addon='mcp_lab')

    def test_no_session_gives_a_hint(self):
        with patch.object(game, 'session', return_value=None), patch.object(gametest, 'recent_dump', return_value=None):
            with self.assertRaisesRegex(game.GameError, 'not running: start it with game_launch'):
                game.ensure_alive(self.settings)
        with patch.object(game, 'session', return_value=None), \
                patch.object(gametest, 'recent_dump', return_value={'file': 'cs2_x_crash.mdmp', 'age_minutes': 3.0}):
            with self.assertRaisesRegex(game.GameError, 'crashed 3.0 min ago.*game_crash_report'):
                game.ensure_alive(self.settings)

    def test_hung_window_waits_then_asks_for_a_restart_and_kills_nothing(self):
        with patch.object(game, 'session', return_value={'pid': 1}), patch.object(game, 'hung', return_value=True), \
                patch('time.sleep'), patch('psutil.Process') as proc:
            with self.assertRaisesRegex(game.GameError, 'not responding.*restart CS2 Workshop Tools'):
                game.ensure_alive(self.settings, wait=0)
            proc.assert_not_called()
        states = iter([True, True, False])
        with patch.object(game, 'session', return_value={'pid': 1}), patch.object(game, 'hung', side_effect=lambda s: next(states)), \
                patch('time.sleep'):
            self.assertEqual(game.ensure_alive(self.settings, wait=30), {'pid': 1})     # recovered while it was loading


class InputSteps(unittest.TestCase):
    def test_new_steps_are_validated_before_anything_runs(self):
        good = [{'use': True}, {'aim': [1, 2, 3]}, {'aim': 'my_bot'}, {'hold': {'key': 'attack', 'seconds': .9}},
                {'mouse': {'dx': 40, 'dy': -5}}, {'focus': True}, {'focus': False},
                {'spread': {'shots': 5, 'interval': .4, 'max_deg': 1}}]
        self.assertEqual(scripting._test_steps(good), good)
        for bad in ({'hold': {'key': 'x'}}, {'hold': {'key': 'use', 'seconds': 99}}, {'use': 1}, {'mouse': {'dx': 9999}},
                    {'mouse': {'dz': 1}}, {'focus': 'yes'}, {'aim': [1, 2]}, {'use': True, 'aim': [1, 2, 3]},
                    {'spread': {'shots': 99}}, {'spread': {'target': [1, 2, 3]}}, {'spread': 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scripting._test_steps([bad])
        self.assertIn('-attack', scripting.RELEASE)                               # every held key has its release

    def test_script_test_examples_of_the_guide_are_valid(self):
        """Every scenario example in the guides (a list of steps or {"map", "steps"}) passes the step validation."""
        import json
        import re
        fence = '`' * 3
        checked = 0
        for path in sorted((PROJECT_ROOT / 'docs' / 'tools').glob('*.md')):
            for raw in re.findall(fence + 'json' + chr(10) + '(.*?)' + fence, path.read_text(encoding='utf-8'), re.S):
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                steps = data.get('steps') if isinstance(data, dict) and 'map' in data else data
                if not (isinstance(steps, list) and steps and all(isinstance(s, dict) and s and "op" not in s and
                        set(s) & scripting.STEP_KINDS for s in steps)):
                    continue
                with self.subTest(guide=path.name, first=steps[0]):
                    self.assertTrue(scripting._test_steps(steps))
                checked += 1
        self.assertGreaterEqual(checked, 5)

    def test_mouse_moves_in_small_real_steps(self):
        sent = []

        def fake(n, pointer, size):
            sent.append((pointer._obj.mi.dx, pointer._obj.mi.dy))
            return 1

        with patch.object(system.user32, 'SendInput', side_effect=fake), patch('time.sleep'):
            count = system.mouse_move(100, -45, step=20)
        self.assertEqual(count, 5)
        self.assertEqual((sum(x for x, _ in sent), sum(y for _, y in sent)), (100, -45))
        self.assertTrue(all(abs(x) <= 20 and abs(y) <= 20 for x, y in sent))


if __name__ == '__main__':
    unittest.main()
