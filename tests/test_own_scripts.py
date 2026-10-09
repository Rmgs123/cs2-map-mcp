"""Own scripts on the shared pieces: `// @requires` bundles the fragments, `// @fire` adds the shot engine and its
entities (mechanics.bundle, script_write, add_script)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cs2map import mechanics

TOWER = '''// @requires core, fire
// @fire tower {"damage": 15, "cooldown": 0.7, "magazine": 250, "reload": 0.5, "recoil": 0, "beam": {"color": [255, 70, 40], "width": 2.5}}
import { Instance, CSPlayerPawn } from "cs_script/point_script";
const core = Core({tag: "TOWER", name: "tower"});
const fire = Fire({core, fire: FIRE.tower.fire, entities: FIRE.tower.entities});
Instance.Msg("tower " + FIRE.tower.entities.beam);
'''


class Bundle(unittest.TestCase):
    def test_plain_script_is_unchanged(self):
        code = 'import { Instance } from "cs_script/point_script";\nInstance.Msg("x");\n'
        self.assertEqual(mechanics.bundle(code), (code, {}))

    def test_pieces_one_import_and_the_fire_table(self):
        code, info = mechanics.bundle(TOWER)
        self.assertEqual(info, {'fragments': ['core', 'fire'], 'fire': ['tower']})
        imports = [line for line in code.splitlines() if line.startswith('import ')]
        self.assertEqual(len(imports), 1, 'one merged import line')
        for name in ('Instance', 'CSPlayerPawn', 'CSDamageTypes'):
            self.assertIn(name, imports[0])
        self.assertIn('function Core(', code)
        self.assertIn('function Fire(', code)
        self.assertIn('const FIRE = ', code)
        self.assertIn('"beam": "tower_beam_"', code)
        self.assertLess(code.index('function Fire('), code.index('// ---- own code ----'), 'the pieces come first')
        self.assertIn('// @fire tower', code, 'add_script reads the directive from the source')
        self.assertNotIn('@requires', code.split('// ---- own code ----')[1], 'the own code loses its @requires line')

    def test_assembled_mechanic_scripts_and_late_directives_are_left_alone(self):
        from cs2map import weapons
        assembled = weapons.render(weapons.validate({'name': 'g', 'base': 'awp', 'origin': [0, 0, 0], 'fire': {}}))
        self.assertIn('// @requires core', assembled, 'fragment headers inside')
        self.assertEqual(mechanics.bundle(assembled), (assembled, {}), 'never bundled twice (07.10)')
        late = 'import { Instance } from "cs_script/point_script";\n// @requires core\nInstance.Msg("x");\n'
        self.assertEqual(mechanics.bundle(late), (late, {}), 'only the leading comments ask for pieces')

    def test_seat_brings_core_and_bad_input_is_refused(self):
        _, info = mechanics.bundle('// @requires seat\nconst x = 1;\n')
        self.assertEqual(info['fragments'][0], 'core')
        for bad in ('// @requires car_motion\n', '// @requires nothing\n', '// @fire t {not json}\n',
                    '// @fire t {"damage": -1}\n', '// @requires core\nimport { Nope } from "cs_script/point_script";\n'):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                mechanics.bundle(bad)

    def test_the_bundle_runs_on_the_engine_double(self):
        if not mechanics.node_path():
            self.skipTest('Node unavailable')
        code, _ = mechanics.bundle(TOWER)
        out = mechanics.run_harness(code, 'D.assert.ok(D.events.some(m => m.includes("tower tower_beam_")));\n'
                                          'console.log("OWN_OK");')
        self.assertIn('OWN_OK', out)


class AddScript(unittest.TestCase):
    def test_fire_directive_creates_the_engine_entities(self):
        import tempfile
        from pathlib import Path
        from cs2map import scripting
        from cs2map.config import load_settings
        from cs2map.vmap import VMap
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = SimpleNamespace(content_addon=root / 'content', game_addon=root / 'game')
            src = s.content_addon / 'maps/scripts/mcp/tower.js'
            vjs = s.game_addon / 'maps/scripts/mcp/tower.vjs_c'
            src.parent.mkdir(parents=True); vjs.parent.mkdir(parents=True)
            src.write_text(mechanics.bundle(TOWER)[0], encoding='utf-8'); vjs.write_bytes(b'x')
            project = SimpleNamespace(s=s, catalog=SimpleNamespace(defaults=lambda cls: {}), resolve=None)
            m = VMap.from_template(load_settings().cs2_path, 'empty')
            with patch('cs2map.weapons.ensure_hud'), patch('cs2map.fire.ensure_assets'):
                out = scripting.add_script(project, m, {'op': 'add_script', 'name': 'tower', 'origin': [10, 20, 0]}, {})
            self.assertEqual(len(m.entities(classname='point_script')), 1)
            self.assertEqual(len(m.entities(targetname='tower_hud')), 1)
            self.assertGreater(len(m.entities(targetname='tower_beam_0')), 0)
            self.assertGreater(out['fire']['tower'], 1)
            with patch('cs2map.weapons.ensure_hud'), patch('cs2map.fire.ensure_assets'), \
                    self.assertRaisesRegex(ValueError, 'already exists'):
                scripting.add_script(project, m, {'op': 'add_script', 'name': 'tower', 'origin': [0, 0, 0]}, {})


if __name__ == '__main__':
    unittest.main()
