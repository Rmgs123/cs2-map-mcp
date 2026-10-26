"""system.file_in_use: a file another handle keeps open without delete sharing (CS2 and a loaded map's .vpk) is
in use although opening it for writing succeeds."""
import tempfile
import unittest
from pathlib import Path

from cs2map import system


class FileInUse(unittest.TestCase):
    def test_open_handle_blocks_replace(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'map.vpk'
            path.write_bytes(b'vpk')
            self.assertFalse(system.file_in_use(path))
            with open(path, 'r+b'):                  # shares read and write, not delete (like CS2)
                self.assertTrue(system.file_in_use(path))
                with open(path, 'r+b'):
                    pass                             # the old probe: writing works, so it said "free"
            self.assertFalse(system.file_in_use(path))
            self.assertFalse(system.file_in_use(Path(d) / 'missing.vpk'))


if __name__ == '__main__':
    unittest.main()
