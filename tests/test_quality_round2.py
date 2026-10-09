import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cs2map import checks, game, lighting
from cs2map.config import PROJECT_ROOT
from cs2map.playtest import _compact_summary


class Round2Quality(unittest.TestCase):
    def test_summary_preserves_counts_and_bounds_response_size(self):
        samples = []
        for i in range(7):
            samples.append({'elapsed': i * 5, 'players': [
                {'slot': 0, 'team': 2, 'bot': False, 'alive': i < 3},
                {'slot': 1, 'team': 3, 'bot': True, 'alive': True}],
                'weapon_pickups': i + 1, 'errors': []})
        result = _compact_summary('c_test', 1, time.monotonic() - 30, samples,
                                  ['error ' + str(i) + 'x' * 1000 for i in range(8)],
                                  [], 24, 'logs/samples.json', True)
        self.assertEqual(result['teams'], {'2': 1, '3': 1})
        self.assertEqual(result['bots_by_team'], {'2': 0, '3': 1})
        self.assertEqual(result['deaths'], {'2': 1, '3': 0})
        self.assertEqual(result['weapon_pickups'], 7)
        self.assertEqual(len(result['alive_by_team']), 3)
        self.assertEqual(result['error_count'], 8)
        self.assertNotIn('samples', result)
        self.assertLess(len(json.dumps(result).encode('utf-8')), 3072)

    def test_cubemap_details_are_saved_and_failure_is_compact(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / '.local' / 'scratch' / 'C') as td:
            project = SimpleNamespace(s=SimpleNamespace(logs=Path(td)))
            raw = {'map': 'c_test', 'ok': False, 'errors': ['failed to bake'],
                   'output_tail': ['many lines'] * 100}
            with mock.patch('cs2map.compiler.build_cubemaps', return_value=raw):
                result = lighting.bake_cubemaps(project, 'c_test', 30)
            self.assertFalse(result['ok'])
            self.assertIn('failed to bake', result['reason'])
            self.assertNotIn('output_tail', result)
            self.assertEqual(json.loads(Path(result['log']).read_text(encoding='utf-8')), raw)

    def test_owned_model_bounds_apply_rotation_and_scale(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / '.local' / 'scratch' / 'C') as td:
            root = Path(td)
            path = root / 'models' / 'mcp' / 'box' / 'model.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'bounds': [[-10, -20, 0], [10, 20, 30]]}), encoding='utf-8')
            project = SimpleNamespace(s=SimpleNamespace(content_addon=root))
            lo, hi = checks._model_bounds(project, 'models/mcp/box/box.vmdl',
                                         [100, 200, 8], [0, 90, 0], [2, 1, 1])
            for actual, expected in zip(lo, [80, 180, 8]):
                self.assertAlmostEqual(actual, expected)
            for actual, expected in zip(hi, [120, 220, 38]):
                self.assertAlmostEqual(actual, expected)
            self.assertIsNone(checks._model_bounds(project, 'models/props/test.vmdl',
                                                   [0, 0, 0], [0, 0, 0], [1, 1, 1]))
