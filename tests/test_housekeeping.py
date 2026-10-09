"""Disk limits: oldest artefacts go first, referenced ones stay, map backups keep the newest per map."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from cs2map import housekeeping as hk


def make(path: Path, size: int, age: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    t = time.time() - age
    os.utime(path, (t, t))


class Limits(unittest.TestCase):
    def test_trim_oldest_first_and_keeps_referenced(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                make(d / f'shot{i}.png', 1000, 100 - i)          # shot0 is the oldest
            out = hk._trim(hk._files(d), 3000, {'shot0.png'}, dry_run=False)
            left = sorted(p.name for p in d.iterdir())
            self.assertIn('shot0.png', left, 'referenced by docs: kept')
            self.assertEqual(len(left), 3)
            self.assertEqual(out['removed'], 3)
            self.assertNotIn('shot1.png', left)

    def test_backups_newest_per_map(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for m in ('a', 'b'):
                for i in range(5):
                    make(d / m / f'2026100{i}.vmap', 10, 50 - i)
            out = hk._backups(d, 2, 10**9, dry_run=False)
            self.assertEqual(sorted(p.name for p in (d / 'a').iterdir()), ['20261003.vmap', '20261004.vmap'])
            self.assertEqual(out['removed'], 6)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(4):
                make(d / f'l{i}.log', 1000, i)
            out = hk._trim(hk._files(d), 1000, set(), dry_run=True)
            self.assertEqual(out['removed'], 3)
            self.assertEqual(len(list(d.iterdir())), 4)


if __name__ == '__main__':
    unittest.main()
