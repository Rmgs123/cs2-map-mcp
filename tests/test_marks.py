"""Player marks (marks.py): console stamps, parsing the mark block and chat notes, matching frames by name and
time, replays, cleanup, the key bind and the listing — all offline with recorded CS2 output (03.11)."""
import json
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cs2map import config, marks, vconsole
from cs2map.config import Settings

# Lines as CS2 Workshop Tools 1.41.9.5 printed them for `exec mcp_mark` on c_defuse (03.11).
BLOCK = [
    'execing mcp_mark',
    'MCP_MARK_BEGIN',
    'setpos 256.000000 128.000000 64.000000;setang 0.000000 90.000000 0.000000',
    'Server:  Running [192.0.2.10:27015]',
    '----- Status -----',
    'loaded spawngroup(  1)  : SV:  [1: c_defuse | main lump | mapload]',
    'Screenshot written to: screenshots\\mcp_mark_0001.jpg',
]
CHAT = ' [Alle] Tester‎: the gate floats'      # a localised channel name; U+200E after the name


def stamped(lines, start, step=0):
    return [(start + i * step, ln) for i, ln in enumerate(lines)]


def packet(ms: int, text: str) -> bytes:
    payload = struct.pack('>IIIII', 0x094098AD, 1, 2, 0, ms) + bytes(8) + text.encode() + b'\0'
    return b'PRNT' + struct.pack('>IHH', vconsole.VERSION, 12 + len(payload), 0) + payload


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.s = Settings(cs2_path=root / 'cs2', addon='mcp_lab')
        self.incoming = root / 'incoming'
        self.store = root / 'store'
        self.incoming.mkdir()
        patches = [mock.patch.object(marks, 'STATE', root / 'state' / 'marks.json'),
                   mock.patch.object(marks, '_incoming', lambda s: self.incoming),
                   mock.patch.object(marks, 'store', lambda s: self.store),
                   mock.patch.dict(marks._CONNS, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def frame(self, name, mtime):
        f = self.incoming / name
        f.write_bytes(b'\xff\xd8jpeg')
        os.utime(f, (mtime, mtime))
        return f

    def feed(self, lines, now, conn=None):
        conn = conn or type('Conn', (), {})()
        with mock.patch.object(marks.time, 'time', return_value=now):
            marks.tap(conn, lines)
        return conn


class Stamps(unittest.TestCase):
    def test_reader_keeps_the_millisecond_stamp_of_each_line(self):
        out: list = []
        reader = vconsole._Reader(out)
        data = packet(3522269, 'MCP_MARK_BEGIN\n') + packet(3522290, 'setpos 1 2 3;setang 0 0 0\n')
        self.assertEqual(reader.feed(data[:30]), [])           # a partial packet waits
        self.assertEqual(reader.feed(data[30:]), ['MCP_MARK_BEGIN', 'setpos 1 2 3;setang 0 0 0'])
        self.assertEqual(out, [(3522269, 'MCP_MARK_BEGIN'), (3522290, 'setpos 1 2 3;setang 0 0 0')])

    def test_plain_reader_and_taps(self):
        self.assertEqual(vconsole._prints(packet(5, 'x\n')), ['x'])
        self.assertIn(marks.tap, vconsole.TAPS)


class Parse(unittest.TestCase):
    def test_block_with_map_frame_and_note(self):
        lines = stamped(BLOCK, 1000) + [(30000, 'setpos 9 9 9;setang 0 0 0'), (40000, CHAT)]
        (m,) = marks.parse(lines, offset=1_700_000_000.0)
        self.assertEqual(m['eye'], [256.0, 128.0, 64.0])
        self.assertEqual(m['ang'], [0.0, 90.0, 0.0])
        self.assertEqual(m['map'], 'c_defuse')
        self.assertEqual(m['shot'], 'mcp_mark_0001.jpg')
        self.assertEqual(m['note'], 'the gate floats')
        self.assertAlmostEqual(m['wall'], 1_700_000_001.0)
        self.assertTrue(m['complete'])
        self.assertTrue(m['note_open'], 'more note lines may still come')

    def test_note_window_and_the_next_mark_bound_the_note(self):
        late = stamped(BLOCK, 0) + [(marks.NOTE_SECONDS * 1000 + 1, CHAT)]
        self.assertIsNone(marks.parse(late, None)[0]['note'])
        two = stamped(BLOCK, 0) + stamped(BLOCK, 5000) + [(6000, CHAT)]
        first, second = marks.parse(two, None)
        self.assertIsNone(first['note'])
        self.assertEqual(second['note'], 'the gate floats')

    def test_every_chat_line_until_the_next_mark_joins_the_note(self):
        def chat(text):
            return CHAT.replace('the gate floats', text)
        two = stamped(BLOCK, 0) + [(10_000, chat('odd spot')), (100_000, chat('the wall is thin'))]
        (m,) = marks.parse(two, None)
        self.assertEqual(m['notes'], ['odd spot', 'the wall is thin'])
        self.assertEqual(m['note'], 'odd spot / the wall is thin')
        split = (stamped(BLOCK, 0) + [(5_000, chat('one'))] + stamped(BLOCK, 20_000)
                 + [(30_000, chat('two')), (40_000, chat('three'))])
        first, second = marks.parse(split, None)
        self.assertEqual((first['notes'], second['notes']), (['one'], ['two', 'three']))

    def test_lines_after_the_window_are_not_notes(self):
        def chat(text):
            return CHAT.replace('the gate floats', text)
        gap = stamped(BLOCK, 0) + [(60_000, chat('a')), (60_000 + marks.NOTE_SECONDS * 1000 + 1, chat('later'))]
        self.assertEqual(marks.parse(gap, None)[0]['notes'], ['a'])
        chain = stamped(BLOCK, 0) + [(s * 1000, chat(str(s))) for s in (100, 210, 290, marks.NOTE_MAX + 10)]
        self.assertEqual(marks.parse(chain, None)[0]['notes'], ['100', '210', '290'])
        open_ = stamped(BLOCK, 0) + [(100_000, chat('x'))]
        self.assertTrue(marks.parse(open_, None)[0]['note_open'])

    def test_incomplete_until_the_screenshot_line_or_timeout(self):
        head = stamped(BLOCK[:3], 1000)
        self.assertFalse(marks.parse(head, None)[0]['complete'])
        self.assertTrue(marks.parse(head + [(1000 + marks.BLOCK_MS + 1, 'x setpos')], None)[0]['complete'])
        self.assertTrue(marks.parse(stamped(BLOCK, 1000), None)[0]['note_open'])

    def test_empty_map_and_unrelated_lines(self):
        lines = stamped(['MCP_MARK_BEGIN', 'setpos 0 0 0.015625;setang 0 0 0',
                         'loaded spawngroup(  1)  : SV:  [1: <empty> | main lump | mapload]',
                         '[SplitScreen] Saved user config file: type 1'], 0)
        (m,) = marks.parse(lines, None)
        self.assertIsNone(m['map'])
        self.assertIsNone(m['note'])
        self.assertEqual(marks.parse(stamped(['echo MCP_MARK_BEGIN x', 'setpos 1 1 1;setang 0 0 0'], 0), None), [])

    def test_chat_variants(self):
        for line, text in ((' [ALL] Tester‎: hello', 'hello'), ('*DEAD* [CT] Tester‎ @ Bombsite A: low wall',
                           'low wall'), (' [T] a: b: c‎: x', 'x')):
            self.assertEqual(marks._CHAT.match(line).group('text'), text)
        self.assertIsNone(marks._CHAT.match('[SplitScreen] Saved user config: type 1'))


class Harvest(Fixture):
    def test_frame_matched_by_name_and_time_is_moved(self):
        start = 1_800_000_000.0                       # wall clock of stamp 0
        self.feed(stamped(BLOCK, 1000) + [(5000, CHAT)], now=start + 6)
        self.frame('mcp_mark_0001.jpg', start + 1.2)
        out = marks.harvest(self.s, now=start + 30)
        self.assertEqual(out, {'new': 1, 'pending': 0})
        (rec,) = marks._load()['marks']
        self.assertEqual(rec['pos'], [256.0, 128.0, 0.0])          # feet, for game_screenshot views
        self.assertEqual(rec['ang'], [0.0, 90.0, 0.0])
        self.assertEqual(rec['map'], 'c_defuse')
        self.assertEqual(rec['note'], 'the gate floats')
        self.assertEqual(rec['epoch'], round(start + 1.2, 3))      # the frame's own time
        self.assertTrue(Path(rec['path']).is_file() and Path(rec['path']).parent == self.store)
        self.assertFalse((self.incoming / 'mcp_mark_0001.jpg').exists())
        # the backlog replays on the next connection: no duplicate
        self.feed(stamped(BLOCK, 1000), now=start + 40)
        self.assertEqual(marks.harvest(self.s, now=start + 41)['new'], 0)
        self.assertEqual(len(marks._load()['marks']), 1)

    def test_a_reused_number_from_another_session_is_not_taken(self):
        start = 1_800_000_000.0
        self.feed(stamped(BLOCK, 1000), now=start + 10)
        old = self.frame('mcp_mark_0001.jpg', start - 3600)
        marks.harvest(self.s, now=start + 20)
        (rec,) = marks._load()['marks']
        self.assertIsNone(rec['path'])
        self.assertTrue(old.exists())
        self.assertEqual(rec['time'][:4], time.strftime('%Y', time.localtime(start)))

    def test_note_typed_after_the_first_read_is_added(self):
        start = 1_800_000_000.0
        conn = self.feed(stamped(BLOCK, 1000), now=start + 2)
        self.frame('mcp_mark_0001.jpg', start + 1)
        marks.harvest(self.s, now=start + 10)
        self.feed([(20000, CHAT)], now=start + 20, conn=conn)
        marks.harvest(self.s, now=start + 21)
        self.assertEqual(marks._load()['marks'][0]['note'], 'the gate floats')
        rec = marks._load()['marks'][0]
        rec['seen'] = True
        marks._save({'install': None, 'marks': [rec]})
        self.feed([(60000, CHAT.replace('the gate floats', 'and the roof'))], now=start + 60, conn=conn)
        marks.harvest(self.s, now=start + 61)
        rec = marks._load()['marks'][0]
        self.assertEqual(rec['notes'], ['the gate floats', 'and the roof'])
        self.assertFalse(rec['seen'], 'new lines make the mark unread again')

    def test_orphan_frames_only_when_asked_and_not_fresh(self):
        now = 1_800_000_000.0
        self.frame('mcp_mark_0004.jpg', now - 600)
        fresh = self.frame('mcp_mark_0005.jpg', now - 2)
        self.assertEqual(marks.harvest(self.s, now=now), {'new': 0, 'pending': 2})
        self.assertEqual(marks.harvest(self.s, orphans=True, now=now), {'new': 1, 'pending': 1})
        (rec,) = marks._load()['marks']
        self.assertIsNone(rec['pos'])
        self.assertTrue(fresh.exists())

    def test_old_marks_are_cleaned_with_their_frames(self):
        now = 1_800_000_000.0
        self.feed(stamped(BLOCK, 1000), now=now - 20 * 86400)
        self.frame('mcp_mark_0001.jpg', now - 20 * 86400 + 1)
        marks.harvest(self.s, now=now - 20 * 86400 + 5)
        path = Path(marks._load()['marks'][0]['path'])
        self.assertTrue(path.is_file())
        marks._CONNS.clear()
        marks.harvest(self.s, now=now)
        self.assertEqual(marks._load()['marks'], [])
        self.assertFalse(path.exists())
        with mock.patch.object(Settings, 'limit', lambda self, k, d: 30 if k == 'marks_days' else d):
            self.assertEqual(marks.days(self.s), 30.0)


class Limits(Fixture):
    def add(self, n, now, size=1000, age_step=60):
        data = {'install': None, 'marks': []}
        self.store.mkdir(exist_ok=True)
        for i in range(n):
            when = now - (n - i) * age_step                          # mark 0 is the oldest
            f = self.store / f'mark-{i:03d}.jpg'
            f.write_bytes(b'x' * size)
            os.utime(f, (when, when))
            data['marks'].append({'id': f.stem, 'epoch': when, 'path': str(f), 'key': str(i), 'seen': True})
        marks._save(data)

    def limits(self, **values):
        return mock.patch.object(Settings, 'limit', lambda self, k, d: values.get(k, d))

    def test_count_and_size_caps_drop_the_oldest_with_their_frames(self):
        now = 1_800_000_000.0
        self.add(10, now, age_step=120)
        with self.limits(marks_max=4):
            self.assertEqual(marks.prune(self.s, now=now), {'marks': 4, 'dropped': 6, 'mb': 0.0})
        self.assertEqual([m['id'] for m in marks._load()['marks']], ['mark-006', 'mark-007', 'mark-008', 'mark-009'])
        self.assertEqual(sorted(f.name for f in self.store.iterdir()), [f'mark-00{i}.jpg' for i in range(6, 10)])
        with self.limits(marks_mb=0.0025):                           # floor 1 MB: all four 1 KB frames fit
            self.assertEqual(marks.prune(self.s, now=now)['marks'], 4)
        self.add(5, now, size=400_000)
        with self.limits(marks_mb=1):                                # 1 MB holds two 400 KB frames
            out = marks.prune(self.s, now=now)
        self.assertEqual((out['marks'], out['dropped']), (2, 3))
        self.assertEqual(len(list(self.store.iterdir())), 2)

    def test_stale_frames_in_both_folders_are_removed(self):
        now = 1_800_000_000.0
        self.add(1, now)
        stray = self.store / 'mark-stray.jpg'
        stray.write_bytes(b'x')
        os.utime(stray, (now - 3600, now - 3600))
        old = self.frame('mcp_mark_0007.jpg', now - 20 * 86400)       # never matched, past marks_days
        recent = self.frame('mcp_mark_0008.jpg', now - 3600)          # may still be matched: stays
        marks.prune(self.s, now=now)
        self.assertFalse(stray.exists())
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(len(marks._load()['marks']), 1)

    def test_housekeeping_skips_marks_and_calls_prune(self):
        from cs2map import housekeeping as hk
        self.assertIn('marks', hk.KEEP_DIRS)
        with tempfile.TemporaryDirectory() as d:
            shots = Path(d)
            (shots / 'marks').mkdir()
            (shots / 'marks' / 'mark-1.jpg').write_bytes(b'x' * 5000)
            (shots / 'view.png').write_bytes(b'x' * 5000)
            s = mock.MagicMock(screenshots=shots, logs=shots / 'nologs', backups=shots / 'nobackups')
            s.limit = lambda k, default: 0 if k == 'screenshots_mb' else default
            with mock.patch.object(hk, 'STAMP', shots / 'hk.json'),                     mock.patch.object(hk.backups, 'trim_all', return_value={}),                     mock.patch.object(marks, 'prune', return_value={'marks': 1}) as prune:
                out = hk.cleanup(s)
            self.assertTrue((shots / 'marks' / 'mark-1.jpg').exists())
            self.assertFalse((shots / 'view.png').exists())
            self.assertEqual(out['marks'], {'marks': 1})
            prune.assert_called_once()


class Listing(Fixture):
    def test_newest_first_paged_seen_and_hint(self):
        with mock.patch.object(marks, '_refresh', return_value='no session'):
            empty = marks.listing(self.s)
            self.assertEqual(empty['total'], 0)
            self.assertIn('F8', empty['hint'])
            start = time.time() - 100
            self.feed(stamped(BLOCK, 1000) + stamped(BLOCK, 9000) + [(9500, CHAT)], now=start + 10)
            out = marks.listing(self.s, limit=1)
        self.assertEqual(out['total'], 2)
        (shot,) = out['shots']
        self.assertEqual(shot['note'], 'the gate floats')            # the newer one first
        self.assertEqual(set(shot), {'name', 'path', 'map', 'pos', 'ang', 'time', 'note'})
        seen = [m['seen'] for m in sorted(marks._load()['marks'], key=lambda m: m['epoch'])]
        self.assertEqual(seen, [False, True])
        self.assertIn('1 new player marks', marks.summary(self.s))

    def test_console_trouble_is_reported(self):
        with mock.patch.object(marks, '_refresh', return_value='CS2 is busy'):
            out = marks.listing(self.s, map_name='c_defuse')
        self.assertIn('busy', out['warning'])


class Install(Fixture):
    def setUp(self):
        super().setUp()
        self.binds = {'F8': 'telemetry_server MEM_CAPTURE; telemetry_level 3', 'F9': 'my own thing', 'F1': ''}
        cfg = self.s.cs2_path / 'game' / 'csgo' / 'cfg'
        cfg.mkdir(parents=True)
        (cfg / 'user_keys_dev_default.vcfg').write_text(
            '"bindings"\n{\n\t\t"F8"\t\t\t"telemetry_server MEM_CAPTURE; telemetry_level 3"\n'
            '\t\t//"F9"\t"x"\n}\n', encoding='utf-8')
        from cs2map import game

        def query(s, cmd, pattern, timeout=5.0, retries=0):
            key = cmd.split()[1]
            if key not in self.binds:
                return [f'bind: "{key}" isn\'t a valid key']
            return [f'bind [player 0]: "{key}" = "{self.binds[key]}"']

        def command(s, cmd, wait=1.5):
            parts = cmd.split(' ', 2)
            if parts[0] == 'bind':
                self.binds[parts[1]] = parts[2].strip('"')
            elif parts[0] == 'unbind':
                self.binds[parts[1]] = ''
            return []
        for p in (mock.patch.object(game, 'query', query), mock.patch.object(game, 'command', command)):
            p.start()
            self.addCleanup(p.stop)

    def with_key(self, key):
        return Settings(cs2_path=self.s.cs2_path, addon='mcp_lab', marks_key=key)

    def test_binds_a_stock_key_writes_the_cfg_and_restores_on_change(self):
        self.assertEqual(marks.install(self.s), 'F8')
        self.assertEqual(self.binds['F8'], 'exec mcp_mark')
        cfg = (self.s.game_addon / 'cfg' / 'mcp_mark.cfg').read_text(encoding='utf-8')
        self.assertIn('echo MCP_MARK_BEGIN\ngetpos\nstatus\njpeg_screenshot mcp_mark', cfg)
        self.assertEqual(marks.install(self.s), 'F8')                    # idempotent
        self.assertEqual(marks.install(self.with_key('F1')), 'F1')       # moved: F8 gets its stock binding back
        self.assertEqual(self.binds['F8'], 'telemetry_server MEM_CAPTURE; telemetry_level 3')
        self.assertEqual(marks.install(self.with_key('')), 'off')
        self.assertEqual(self.binds['F1'], '')

    def test_never_takes_the_users_own_binding_or_a_bad_key(self):
        self.assertIn('bound to "my own thing"', marks.install(self.with_key('F9')))
        self.assertEqual(self.binds['F9'], 'my own thing')
        with self.assertRaisesRegex(ValueError, 'not a CS2 key'):
            marks.install(self.with_key('KP_X'))


class Settings_(unittest.TestCase):
    def test_marks_key_setting(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'settings.json'
            f.write_text(json.dumps({'cs2_path': d, 'marks_key': 'INS'}), encoding='utf-8')
            self.assertEqual(config.load_settings(f).marks_key, 'INS')
            f.write_text(json.dumps({'cs2_path': d}), encoding='utf-8')
            self.assertEqual(config.load_settings(f).marks_key, 'F8')
            f.write_text(json.dumps({'cs2_path': d, 'marks_key': 'bind F8; quit'}), encoding='utf-8')
            with self.assertRaises(config.ConfigError):
                config.load_settings(f)


if __name__ == '__main__':
    unittest.main()
