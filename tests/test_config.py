import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import helpers  # noqa: F401  (adds src to sys.path)
from cs2map import config


def _fake_cs2(lib: Path) -> Path:
    cs2 = lib / config.CS2_DIR
    exe = cs2 / 'game' / 'bin' / 'win64' / 'resourcecompiler.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')
    return cs2


class ConfigTest(unittest.TestCase):
    def test_find_cs2_in_second_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            steam, lib = Path(tmp) / 'Steam', Path(tmp) / 'Games'
            (steam / 'steamapps').mkdir(parents=True)
            (steam / 'steamapps' / 'libraryfolders.vdf').write_text(
                '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n'
                % (str(steam).replace('\\', '\\\\'), str(lib).replace('\\', '\\\\')), encoding='utf-8')
            cs2 = _fake_cs2(lib)
            self.assertEqual(config.find_cs2(steam), cs2)

    def test_find_cs2_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(config.find_cs2(Path(tmp)))

    def test_settings_file_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            cs2 = _fake_cs2(Path(tmp))
            with patch.dict('os.environ', {}, clear=False) as env, patch.object(config, 'find_cs2', return_value=cs2):
                env.pop('CS2MAP_CS2_PATH', None); env.pop('CS2MAP_ADDON', None)
                s = config.load_settings(Path(tmp) / 'missing.json')
            self.assertEqual((s.cs2_path, s.addon), (cs2, 'mcp_lab'))
            with patch.object(config, 'find_cs2', return_value=None), patch.dict('os.environ', {}, clear=False) as env:
                env.pop('CS2MAP_CS2_PATH', None)
                with self.assertRaises(config.ConfigError):
                    config.load_settings(Path(tmp) / 'missing.json')

    def test_settings_file_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'settings.json'
            f.write_text(json.dumps({'cs2_path': tmp, 'addon': 'my_lab'}), encoding='utf-8')
            with patch.dict('os.environ', {}, clear=False) as env:
                env.pop('CS2MAP_CS2_PATH', None); env.pop('CS2MAP_ADDON', None)
                s = config.load_settings(f)
            self.assertEqual((s.cs2_path, s.addon), (Path(tmp), 'my_lab'))

    def test_example_settings_valid(self):
        data = json.loads((config.PROJECT_ROOT / 'config' / 'settings.example.json').read_text(encoding='utf-8'))
        self.assertEqual(data['addon'], 'mcp_lab')
        config.check_name(data['addon'])


if __name__ == '__main__':
    unittest.main()
