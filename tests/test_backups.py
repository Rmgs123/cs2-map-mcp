"""Backups: a save keeps a compressed copy with a sources snapshot, tiers keep finished sessions, sources are
stored once, a dead process's held file is finished later, 1.0 copies are compressed, restores never touch the
live source folders."""
import datetime as dt
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from compression import zstd

from cs2map import backups as bk
from cs2map.config import ConfigError, Settings, check_backups_dir


def settings(root: Path, **kw) -> Settings:
    cs2 = root / 'cs2'
    (cs2 / 'content' / 'csgo_addons' / 't' / 'maps').mkdir(parents=True, exist_ok=True)
    return Settings(cs2_path=cs2, addon='t', backups_dir=str(root / 'store'), **kw)


def stamp(now: dt.datetime, hours: float = 0, minutes: float = 0) -> str:
    return (now - dt.timedelta(hours=hours, minutes=minutes)).strftime(bk.STAMP_FORMAT)


def replace(target: Path, data: bytes) -> None:
    """How every writer saves a map: a new file put in place (the held hard link keeps the old content)."""
    tmp = target.with_name(target.name + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, target)


def fake_copy(folder: Path, st: str, kind: str = 'save', pin: str | None = None, size: int = 100) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{st}.vmap.zst').write_bytes(b'z' * size)
    meta = {'kind': kind}
    if pin:
        meta['pin'] = pin
    (folder / f'{st}.json').write_text(json.dumps(meta), encoding='utf-8')


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.my = self.root / 'my'
        (self.my / 'maps' / 'town').mkdir(parents=True)
        (self.my / 'maps' / 'town' / 'gen.py').write_text('print(1)\n', encoding='utf-8')
        (self.my / 'notes.md').write_text('notes\n', encoding='utf-8')
        patches = [mock.patch.object(bk, 'MY_ROOT', self.my), mock.patch.object(bk, 'RESTORED', self.root / 'restored'),
                   mock.patch.object(bk, 'LEGACY_ROOT', self.root / 'legacy')]   # never the real project/backups
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.s = settings(self.root)
        self.maps = self.s.content_addon / 'maps'

    def tearDown(self):
        bk.flush(10)
        self._tmp.cleanup()


class Saving(Base):
    def test_save_keeps_compressed_copy_with_sources(self):
        target = self.maps / 'town.vmap'
        target.write_bytes(b'old map ' * 1000)
        job = bk.stage(target, self.s.backups / 'maps')
        replace(target, b'new map')
        bk.submit(job, self.s)
        self.assertTrue(bk.flush(20))
        items = bk.entries(self.s.backups / 'maps' / 'town')
        self.assertEqual(len(items), 1)
        self.assertEqual(bk.read_copy(items[0]), b'old map ' * 1000)
        self.assertLess(items[0]['bytes'], 8000)
        self.assertTrue(items[0]['meta']['sources'])
        self.assertEqual(list((self.maps / bk.PENDING).iterdir()), [], 'held file and sidecar removed')
        doc = bk.load_snapshot(self.s, items[0]['meta']['sources'])
        self.assertIn('my/maps/town/gen.py', doc['files'])
        self.assertNotIn('addon/maps/town.vmap', doc['files'], 'maps are kept as map copies, not sources')

    def test_dead_process_held_file_is_finished_later(self):
        target = self.maps / 'town.vmap'
        target.write_bytes(b'state A')
        job = bk.stage(target, self.s.backups / 'maps')       # staged, never submitted: the process died
        replace(target, b'state B')
        side = Path(job['held']).with_suffix('.json')
        self.assertEqual(bk.recover(self.s), 0, 'too fresh: its process may still finish it')
        old = time.time() - bk.RECOVER_AFTER_S - 5
        os.utime(side, (old, old))
        self.assertEqual(bk.recover(self.s), 1)
        self.assertEqual(bk.read_copy(bk.entries(self.s.backups / 'maps' / 'town')[0]), b'state A')

    def test_changed_maps_get_a_live_copy_once(self):
        target = self.maps / 'town.vmap'
        target.write_bytes(b'hammer saved this')
        old = time.time() - bk.SETTLE_S - 5
        os.utime(target, (old, old))
        self.assertEqual(bk.changed_maps(self.s), [target])
        bk.copy_live(self.s, target, self.s.backups / 'maps')
        self.assertEqual(bk.changed_maps(self.s), [], 'its content is in a backup now')
        self.assertEqual(bk.entries(self.s.backups / 'maps' / 'town')[0]['meta']['kind'], 'live')

    def test_pin_keeps_map_and_sources(self):
        target = self.maps / 'town.vmap'
        target.write_bytes(b'milestone')
        copy = bk.copy_live(self.s, target, self.s.backups / 'maps', kind='pin', pin='square done')
        meta = json.loads(copy.with_name(copy.name.split('.')[0] + '.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['pin'], 'square done')
        self.assertEqual(bk.load_snapshot(self.s, meta['sources']).get('pin'), 'square done')


class Tiers(Base):
    def test_stamps_are_utc(self):
        """25.10: a time-zone change made new copies sort before older ones; UTC names keep the order."""
        utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        self.assertLess(abs((bk._when(bk.stamp_now()) - utc).total_seconds()), 5)

    def test_tiers_keep_sessions_and_pins(self):
        now = dt.datetime(2026, 10, 25, 15, 30)
        folder = self.s.backups / 'maps' / 'town'
        burst = [stamp(now, minutes=m) for m in (1, 2, 3, 4, 5, 6)]             # a rebuild: six saves
        for st in burst:
            fake_copy(folder, st)
        end_of_session = stamp(now, hours=1, minutes=-5)                          # 14:35, a live copy
        before_work = stamp(now, hours=1, minutes=20)                             # 14:10, before a save
        mid = stamp(now, hours=1, minutes=10)                                     # 14:20, half a rebuild
        fake_copy(folder, end_of_session, kind='live')
        fake_copy(folder, before_work)
        fake_copy(folder, mid)
        days = [stamp(now, hours=24 * d + 3) for d in (2, 3)]                     # one per day
        days_extra = [stamp(now, hours=24 * 2 + 2)]                               # same day as days[0], newer
        weekly_out = stamp(now, hours=24 * 7 * 9)                                 # older than 8 weeks
        pinned = stamp(now, hours=24 * 7 * 20)
        for st in days + days_extra + [weekly_out]:
            fake_copy(folder, st)
        fake_copy(folder, pinned, pin='first version')
        bk.trim_map(folder, self.s, now=now)
        left = {e['id'] for e in bk.entries(folder)}
        self.assertTrue(set(burst[:3]) <= left, 'the newest three')
        self.assertIn(burst[-1], left, 'the oldest copy of the current hour: the map before the rebuild')
        self.assertNotIn(burst[3], left)
        self.assertIn(end_of_session, left, 'a finished session wins its hour')
        self.assertNotIn(mid, left)
        self.assertNotIn(before_work, left, 'the hour already has a finished session')
        self.assertTrue(set(days) <= left)
        self.assertNotIn(days_extra[0], left, 'one per day: the oldest')
        self.assertNotIn(weekly_out, left)
        self.assertIn(pinned, left, 'pins stay forever')

    def test_size_cap_keeps_newest_three(self):
        now = dt.datetime(2026, 10, 25, 15, 30)
        folder = self.s.backups / 'maps' / 'town'
        for m in range(6):
            fake_copy(folder, stamp(now, minutes=m), size=2**20)
        s = settings(self.root, limits=(('backups_map_mb', 1), ('backups_keep_last', 6)))
        bk.trim_map(folder, s, now=now)
        self.assertEqual(len(bk.entries(folder)), bk.KEEP_MIN)

    def test_trim_all_caps_every_map_together(self):
        now = dt.datetime.now()
        for name in ('a', 'b'):
            for m in range(5):
                fake_copy(self.s.backups / 'maps' / name, stamp(now, minutes=m), size=2**20)
        s = settings(self.root, limits=(('backups_keep_last', 5),))
        out = bk.trim_all(s, 6 * 2**20)
        self.assertEqual(out['removed'], 4)
        self.assertEqual(len(bk.entries(self.s.backups / 'maps' / 'a')), 3)

    def test_legacy_keep_last_name(self):
        s = settings(self.root, limits=(('backups_per_map', 7),))
        self.assertEqual(bk.limits(s)['backups_keep_last'], 7)
        self.assertEqual(bk.limits(settings(self.root, limits=(('backups_per_map', 1),)))['backups_keep_last'], 3)


class Sources(Base):
    def test_unchanged_sources_reuse_snapshot_and_objects(self):
        first = bk.snapshot_sources(self.s)
        self.assertEqual(bk.snapshot_sources(self.s), first, 'nothing changed: no new snapshot')
        objects = sorted((self.s.backups / 'sources' / 'objects').rglob('*.zst'))
        (self.my / 'maps' / 'town' / 'gen.py').write_text('print(2)\n', encoding='utf-8')
        second = bk.snapshot_sources(self.s)
        self.assertNotEqual(second, first)
        self.assertEqual(len(sorted((self.s.backups / 'sources' / 'objects').rglob('*.zst'))), len(objects) + 1)
        out = bk.restore_sources(self.s, first)
        self.assertEqual(Path(out['folder'], 'my/maps/town/gen.py').read_text(encoding='utf-8'), 'print(1)\n')
        self.assertEqual((self.my / 'maps' / 'town' / 'gen.py').read_text(encoding='utf-8'), 'print(2)\n',
                         'the live folder is not touched')

    def test_trim_sources_removes_unused_objects(self):
        now = dt.datetime.now()
        old = bk.snapshot_sources(self.s)
        (self.my / 'notes.md').write_text('changed\n', encoding='utf-8')
        for i in range(bk.SOURCE_KEEP_LAST + 1):
            (self.my / f'n{i}.md').write_text(str(i), encoding='utf-8')
            bk.snapshot_sources(self.s)
        out = bk.trim_sources(self.s, now=now + dt.timedelta(days=60))
        self.assertNotIn(old, bk.snapshots(self.s))
        self.assertGreaterEqual(out['objects_removed'], 1, 'the first notes.md content is gone')

    def test_excluded_pattern_stays_in_one_folder(self):
        self.assertTrue(bk._excluded('maps/a.vmap', ('maps/*.vmap',)))
        self.assertFalse(bk._excluded('maps/prefabs/b.vmap', ('maps/*.vmap',)))
        self.assertFalse(bk._excluded('materials/a.vmap', ('maps/*.vmap',)))

    def test_backups_dir_inside_my_is_refused(self):
        from cs2map.config import PROJECT_ROOT
        s = Settings(cs2_path=self.root / 'cs2', addon='t', backups_dir=str(PROJECT_ROOT / 'my' / 'bk'))
        with self.assertRaises(ConfigError):
            check_backups_dir(s)
        check_backups_dir(self.s)


class Legacy(Base):
    def test_uncompressed_copies_are_compressed(self):
        folder = self.s.backups / 'maps' / 'town'
        folder.mkdir(parents=True)
        st = stamp(dt.datetime.now(), minutes=5)
        (folder / f'{st}.vmap').write_bytes(b'1.0 copy' * 100)
        out = bk.migrate_legacy(self.s)
        self.assertEqual(out['compressed'], 1)
        e = bk.entries(folder)[0]
        self.assertTrue(str(e['path']).endswith('.vmap.zst'))
        self.assertEqual(bk.read_copy(e), b'1.0 copy' * 100)
        self.assertEqual(e['meta']['kind'], 'legacy')

    def test_periodic_runs_once_per_period(self):
        self.assertIsNotNone(bk.periodic(self.s))
        self.assertIsNone(bk.periodic(self.s), 'another process just ran it')
        self.assertIsNotNone(bk.periodic(self.s, force=True))

    def test_zstd_roundtrip(self):
        self.assertEqual(zstd.decompress(zstd.compress(b'abc' * 10, bk.LEVEL_MAP)), b'abc' * 10)


if __name__ == '__main__':
    unittest.main()
