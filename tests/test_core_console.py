"""Core: VConsole framing, CS2 queries, screenshot results and script syntax checks (no CS2 needed)."""
import struct
import threading
import unittest
from unittest import mock

from cs2map import game, scripting, vconsole
from cs2map.project import _compact_shot


def prnt(text: str) -> bytes:
    payload = b'\0' * 28 + text.encode() + b'\0'
    return b'PRNT' + struct.pack('>IHH', vconsole.VERSION, 12 + len(payload), 0) + payload


class FakeConnection:
    """Stands in for vconsole._Connection: lines are 'printed' by a timer thread."""

    def __init__(self):
        self.alive = True
        self.listeners = []
        self.cv = threading.Condition()
        self.port = vconsole.PORT

    def push(self, *lines):
        with self.cv:
            for listener in self.listeners:
                listener.extend(lines)
            self.cv.notify_all()


class Framing(unittest.TestCase):
    def test_reader_handles_split_packets_and_noise(self):
        data = b'junk' + prnt('first line') + b'CVRB\0\0' + prnt('a\nb')
        reader = vconsole._Reader()
        out = []
        for i in range(0, len(data), 7):          # feed in awkward 7-byte pieces
            out += reader.feed(data[i:i + 7])
        self.assertEqual(out, ['first line', 'a', 'b'])
        self.assertEqual(vconsole._prints(data), ['first line', 'a', 'b'])

    def test_collect_keeps_only_lines_after_begin_marker(self):
        conn = FakeConnection()
        listener = []
        conn.listeners.append(listener)
        conn.push('old backlog', '] echo mcp_begin_x')       # backlog and command echo are ignored
        timer = threading.Timer(0.05, conn.push, args=('mcp_begin_x', 'status line', 'mcp_end_x', 'late server line'))
        timer.start()
        ok, lines, _ = vconsole._collect(conn, listener, 'mcp_begin_x', 'mcp_end_x',
                                      lambda ls, ended: ended, timeout=3, linger=0.2)
        timer.join()
        self.assertTrue(ok)
        self.assertEqual(lines, ['status line', 'late server line'])

    def test_collect_waits_for_pattern_after_end_marker(self):
        # Server commands (status) can print a frame after the client-side end marker.
        conn = FakeConnection()
        listener = []
        conn.listeners.append(listener)
        threading.Timer(0.05, conn.push, args=('mcp_begin_y', 'mcp_end_y')).start()
        threading.Timer(0.2, conn.push, args=('Server: x', '#end')).start()
        ok, lines, _ = vconsole._collect(conn, listener, 'mcp_begin_y', 'mcp_end_y',
                                      lambda ls, ended: any('#end' in ln for ln in ls), timeout=3, linger=0)
        self.assertTrue(ok)
        self.assertEqual(lines[-1], '#end')

    def test_collect_times_out_without_hanging(self):
        conn = FakeConnection()
        listener = []
        conn.listeners.append(listener)
        ok, lines, _ = vconsole._collect(conn, listener, 'b', 'e', lambda ls, ended: ended, timeout=0.3, linger=0)
        self.assertFalse(ok)
        self.assertEqual(lines, [])


class Queries(unittest.TestCase):
    def test_query_spaces_repeated_commands_and_retries(self):
        calls = []

        def run_until(cmds, pattern, timeout):
            calls.append(cmds[0])
            return (len(calls) > 1, ['#end'] if len(calls) > 1 else [])

        with mock.patch.object(game, 'session', return_value={'pid': 1}), \
                mock.patch.object(game.vconsole, 'run_until', side_effect=run_until):
            game._LAST_QUERY.clear()
            lines = game.query(None, 'status', '#end', timeout=0.1, retries=1)
        self.assertEqual(calls, ['status', 'status'])
        self.assertEqual(lines, ['#end'])

    def test_hammer_capture_refuses_another_map(self):
        window = mock.Mock(title='Hammer - [e_prefabs.vmap]')
        with mock.patch.object(game, '_window', return_value=window), \
                mock.patch.object(game, '_capture', return_value={'name': 'h', 'path': 'x.png'}):
            with self.assertRaises(game.GameError):
                game.screenshot(None, 'h', window='hammer', map_name='mcp_accept')
            shot = game.screenshot(None, 'h', window='hammer', map_name='e_prefabs')
        self.assertEqual(shot['window'], 'Hammer - [e_prefabs.vmap]')

    def test_compact_shot_drops_frame_diagnostics(self):
        shot = {'name': 'v', 'path': 'p.png', 'size': [1, 1], 'frame_check': {'ok': True, 'signature': 'x'},
                'player_check': {'ok': True, 'position': [1.04, 2, 3], 'angles': [0, 90.04]}}
        self.assertEqual(_compact_shot(shot), {'name': 'v', 'path': 'p.png', 'eye': [1.0, 2, 3], 'ang': [0, 90.0]})


@unittest.skipUnless(scripting._node(), 'Node.js is not installed')
class VConsoleWindow(unittest.TestCase):
    """A VConsole2 window opened by CS2 silently takes the only console slot: it is closed."""

    def fake(self, pid, name, parent):
        proc = mock.Mock()
        proc.name.return_value = name
        proc.parent.return_value = parent
        return proc

    def test_closes_only_vconsole2_children_of_cs2(self):
        import psutil
        cs2 = self.fake(10, 'cs2.exe', None); cs2.pid = 10
        other = self.fake(11, 'explorer.exe', None); other.pid = 11
        procs = {20: self.fake(20, 'vconsole2.exe', cs2), 21: self.fake(21, 'vconsole2.exe', other),
                 22: self.fake(22, 'python.exe', cs2)}
        conns = [mock.Mock(raddr=mock.Mock(port=vconsole.PORT), status=psutil.CONN_ESTABLISHED, pid=pid) for pid in procs]
        from cs2map import system
        with mock.patch.object(psutil, 'net_connections', return_value=conns),              mock.patch.object(psutil, 'Process', side_effect=lambda pid: procs[pid]),              mock.patch.object(system, 'close_windows', return_value=1) as close,              mock.patch.object(vconsole.time, 'sleep'):
            closed = vconsole.evict_vconsole()
        self.assertEqual(len(closed), 1)
        close.assert_called_once_with(20)          # a polite WM_CLOSE, never a kill
        for proc in procs.values():
            proc.terminate.assert_not_called()
            proc.kill.assert_not_called()


class ScriptSyntax(unittest.TestCase):
    def test_valid_module_passes(self):
        code = 'import { Instance } from "cs_script/point_script";\nInstance.Msg("ok");\n'
        self.assertEqual(scripting.syntax_check(code), {'checked': True, 'ok': True})

    def test_error_reports_line(self):
        res = scripting.syntax_check('import { Instance } from "cs_script/point_script";\nconst broken = ;\n')
        self.assertFalse(res['ok'])
        self.assertEqual(res['line'], 2)
        self.assertIn('SyntaxError', res['error'])

    def test_script_write_refuses_before_writing(self):
        project = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'line 2'):
            scripting.script_write(project, 'bad_one', 'let a = 1;\nconst b = ;\n')
        self.assertEqual(project.mock_calls, [])     # refused before touching files or jobs


if __name__ == '__main__':
    unittest.main()
