import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cs2map import story, scripting
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


def sample():
    return {'op': 'add_story', 'name': 's_unit', 'settings': {'respawn': True},
            'stages': [{'id': 'wake', 'objective': 'Find the button', 'until': {'button': 's_button'},
                        'then': [{'fire': 's_door', 'input': 'Open'}, {'sound': 'tr.Countdown'}]},
                       {'id': 'yard', 'objective': 'Go out to the yard',
                        'until': {'zone': {'mins': [100, -64, 0], 'maxs': [200, 64, 128]}},
                        'then': [{'fade': 'out', 'seconds': 1}, {'teleport': [300, 0, 8]}, {'fade': 'in'}]},
                       {'id': 'end', 'objective': 'Wait', 'until': {'time': 5},
                        'then': [{'save': 'chapter1_done'}, {'end_text': 'Chapter 1 complete'}]}]}


class StoryValidation(unittest.TestCase):
    def test_defaults_and_no_input_mutation(self):
        op = sample()
        before = copy.deepcopy(op)
        spec = story.validate(op)
        self.assertEqual(op, before)
        self.assertEqual(spec['settings'], {'respawn': True, 'resume': False, 'round_minutes': 30, 'bots': 0})
        self.assertTrue(spec['hud']['timer'])

    def test_fail_closed_on_ambiguous_or_bad_json(self):
        variants = []
        def change(fn):
            value = sample(); fn(value); variants.append(value)
        change(lambda x: x.update(name='../outside'))
        change(lambda x: x.update(name='__proto__'))
        change(lambda x: x.update(settings={'bots': True}))
        change(lambda x: x.update(stages=[]))
        change(lambda x: x['stages'][1].update(id='wake'))
        change(lambda x: x['stages'][0].update(until={'button': 's_button', 'time': 1}))
        change(lambda x: x['stages'][0].update(until={'time': float('nan')}))
        change(lambda x: x['stages'][0].update(until={'chat': '!x; quit'}))
        change(lambda x: x['stages'][0].update(until={'pickup': 'prop_item'}))
        change(lambda x: x['stages'][0].update(until={'kills': 1.5}))
        change(lambda x: x['stages'][0].update(until={'input': 'TestReset'}))
        change(lambda x: x['stages'][0].update(then=[{'fire': 'x', 'input': 'Open', 'fade': 'out'}]))
        change(lambda x: x['stages'][0].update(then=[{'teleport': [0, 0]}]))
        change(lambda x: x['stages'][0].update(then=[{'sound': 'x', 'seconds': 3}]))
        change(lambda x: x['stages'][0].update(then=[{'text': 'x', 'seconds': -1}]))
        change(lambda x: x['stages'][1]['until']['zone'].update(maxs=[50, 0, 0]))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                story.validate(value)

    def test_generation_has_unique_effects_and_valid_js(self):
        spec = story.validate(sample())
        code, xml, css, ops = story.generate(spec)
        self.assertEqual(json.loads(code.splitlines()[0][len(story.MARKER):]), spec)
        targets = [x['keyvalues']['targetname'] for x in ops]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertIn('s_unit.css', xml)
        self.assertIn('{s:objective}', xml)
        self.assertIn('Instance.ConnectOutput', code)
        self.assertIn('Instance.OnRoundStart', code)
        self.assertIn('cs2map_stories', code)
        self.assertEqual(scripting.syntax_check(code).get('ok'), True)
        zone = next(x for x in ops if x['classname'] == 'trigger_multiple')
        self.assertEqual(zone['shape']['mins'], [100, -64, 0])
        self.assertEqual(zone['keyvalues']['spawnflags'], 4097)

    def test_all_condition_and_action_variants_generate(self):
        op = sample()
        op['stages'] = [{'id': 'chat', 'objective': 'Say it', 'until': {'chat': '!ready'}, 'then': [
            {'say': 'Hello', 'seconds': 2}, {'text': 'World'}, {'shake': True, 'amplitude': 2},
            {'spawn': 's_template'}, {'camera': [0, 0, 128], 'ang': [10, 90, 0], 'seconds': 2}]},
            {'id': 'input', 'objective': 'Input', 'until': {'input': 'Ready'}},
            {'id': 'kills', 'objective': 'Kill', 'until': {'kills': 2}},
            {'id': 'pickup', 'objective': 'Pick up', 'until': {'pickup': 'weapon_mp5sd'}}]
        code = story.generate(story.validate(op))[0]
        self.assertTrue(scripting.syntax_check(code)['ok'])
        self.assertIn('TakeDamage({damage:1000', code)
        self.assertIn('weapon.GetOwner()', code)


class StoryReferences(unittest.TestCase):
    @unittest.skipUnless(HAVE_CS2, 'CS2 template not installed')
    def test_real_batch_preflight_dry_run_and_late_failure_write_no_assets(self):
        import time
        from cs2map.project import Project
        from unittest.mock import patch
        p = Project()
        p.load = lambda name: VMap.from_template(SETTINGS.cs2_path, 'empty')
        p.register_op('add_story', story.add_story)
        p.map_edit = story.map_edit_wrapper(p, p.map_edit)
        op = sample()
        op['name'] = 's_dry_' + str(time.time_ns())
        op['ref'] = 'story'
        source = p.s.content_addon / f'maps/scripts/mcp/{op["name"]}.js'
        with patch.object(p, '_check_hammer'), patch.object(story.compiler, 'compile_assets') as compile_assets:
            result = p.map_edit('s_dry_contract', [op, {'op': 'update', 'id': '@story', 'origin': [1, 2, 3]}], True)
            self.assertTrue(result['dry_run'])
            self.assertFalse(source.exists())
            with self.assertRaises(ValueError):
                p.map_edit('s_dry_contract', [op, {'op': 'unknown_late_operation'}])
            self.assertFalse(source.exists())
            compile_assets.assert_not_called()

    @unittest.skipUnless(HAVE_CS2, 'CS2 template not installed')
    def test_missing_wrong_class_and_invalid_input(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        script = m.new_entity('point_script', {'cs_script': 'maps/scripts/mcp/s_unit.vjs'})
        m.attach(script)
        project = Mock()
        project.s.game_addon = SETTINGS.game_addon
        project.catalog.io.return_value = (['Close'], [])
        from unittest.mock import patch
        with patch.object(story, 'map_stories', return_value=[(script, story.validate(sample()))]):
            found = story.check_story(project, m)
            self.assertTrue(any('s_button' in p['message'] for p in found))
            m.attach(m.new_entity('logic_relay', {'targetname': 's_button'}))
            m.attach(m.new_entity('func_door', {'targetname': 's_door'}))
            found = story.check_story(project, m)
            self.assertTrue(any('s_button' in p['message'] for p in found))
            self.assertTrue(any(p['code'] == 'story_input' for p in found))


class ScriptTestValidation(unittest.TestCase):
    def test_trace_survives_core_listener_remove_by_equality(self):
        trace = scripting._ConsoleTrace(['a'])
        transient = ['a']
        listeners = [trace, transient]
        listeners.remove(transient)
        self.assertIs(listeners[0], trace)

    def test_validate_before_game_and_runtime_error_filter(self):
        for steps in ('bad', [], [{'cmd': 'x', 'walk': [0, 0, 0]}], [{'expect': '['}],
                      [{'walk': [0, float('inf'), 0]}], [{'fire': 'name input value'}],
                      [{'say': '!x; quit'}], [{'press': '../x'}]):
            with self.subTest(steps=steps), self.assertRaises((ValueError, re.error)):
                scripting._test_steps(steps)
        self.assertEqual(scripting._test_steps([{'walk': [0, 0, 8]}, {'expect': 'ok', 'within': 2}])[0]['walk'][2], 8)
        errors = scripting._script_errors(['Annotation: Error loading file', 'TypeError: x', 'TypeError: x',
                                          'S_STORY_ERROR s_unit broken', 'Error: loading e_prefabs.vmap_user.txt'])
        self.assertEqual(errors, ['TypeError: x', 'S_STORY_ERROR s_unit broken'])
        self.assertEqual(scripting._script_errors(['Uncaught Exception during callback "ScriptInput:Throw"',
                                                  'Error: custom failure', '    at foo (file:///maps/scripts/test.js:1:3)']),
                         ['Error: custom failure'])
        self.assertEqual(scripting._script_errors(['Uncaught Exception during callback "Think"', 'custom string']),
                         ['custom string'])


import re
