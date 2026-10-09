"""setup_clients.py: client configs are merged, not overwritten; other servers and settings survive."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import setup_clients as sc  # noqa: E402


class Clients(unittest.TestCase):
    def test_toml_replaces_only_our_block(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config.toml'
            p.write_text('model = "x"\n\n[mcp_servers.cs2map]\ncommand = "old"\n\n[mcp_servers.cs2map.env]\nA = "1"\n\n'
                         '[mcp_servers.other]\ncommand = "keep"\n', encoding='utf-8')
            old, new = sc.merged_toml(p)
            self.assertIn('model = "x"', new)
            self.assertIn('[mcp_servers.other]\ncommand = "keep"', new)
            self.assertEqual(new.count('[mcp_servers.cs2map]'), 1)
            self.assertNotIn('command = "old"', new)
            self.assertEqual(sc.merged_toml(Path(d) / 'missing.toml')[1].count('[mcp_servers.cs2map.env]'), 1)

    def test_json_keeps_other_servers(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'c.json'
            p.write_text(json.dumps({'mcpServers': {'other': {'command': 'x'}}, 'theme': 'dark'}), encoding='utf-8')
            new = json.loads(sc.merged_json(p)[1])
            self.assertEqual(new['theme'], 'dark')
            self.assertEqual(set(new['mcpServers']), {'other', 'cs2map'})
            self.assertEqual(new['mcpServers']['cs2map']['args'], ['-m', 'cs2map.server'])


class BlenderChoice(unittest.TestCase):
    def test_steam_first_and_an_untested_version_gives_way(self):
        from unittest.mock import patch
        from cs2map import config
        with tempfile.TemporaryDirectory() as d:
            def install(name, version):
                exe = Path(d) / name / 'blender.exe'
                (exe.parent / version).mkdir(parents=True)
                exe.write_bytes(b'')
                return exe
            steam, portable = install('steam', '5.2'), install('portable', '4.5')
            with patch.object(config, 'blender_candidates', return_value=[steam, portable]):
                self.assertEqual(config.find_blender(), steam)
            future = install('future', '6.1')
            with patch.object(config, 'blender_candidates', return_value=[future, portable]):
                self.assertEqual(config.find_blender(), portable)            # Steam updated past the tested range
            with patch.object(config, 'blender_candidates', return_value=[future]):
                self.assertEqual(config.find_blender(), future)              # still named, so the error says why
            with patch.object(config, 'blender_candidates', return_value=[]):
                self.assertEqual(config.find_blender(), config.BLENDER_DEFAULT)
            self.assertEqual(config.find_blender(str(portable)), portable)  # settings "blender" wins


if __name__ == '__main__':
    unittest.main()
