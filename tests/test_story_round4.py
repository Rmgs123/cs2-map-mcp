import copy
import unittest

from cs2map import story, scripting
from tests.test_story import sample


class StoryRound4(unittest.TestCase):
    def test_schema_compatibility_and_round4_fields(self):
        old=story.validate(sample())
        self.assertNotIn('checkpoint',old['stages'][0])
        op=sample()
        op['stages'][0].update(until={'use':'r_note'},checkpoint=[1,2,8],hint=True,
                               route=[{'go':[10,20,0]},{'use':'r_note'}],then=[{'light':'r_lamp','on':True}])
        code,_,_,ops=story.generate(story.validate(op))
        self.assertTrue(scripting.syntax_check(code)['ok'])
        self.assertIn('TraceLine',code)
        self.assertIn('IsInputPressed(CSInputs.USE)',code)
        self.assertTrue(any(o['classname']=='point_worldtext' for o in ops))
        self.assertIn("if(s.checkpoint)checkpoint=s.checkpoint",code)
        self.assertIn("epoch!==run",code)

    def test_bad_new_fields_are_refused(self):
        for fields in ({'hint':1},{'checkpoint':[1,2]},{'route':'auto'},
                       {'then':[{'light':'r_lamp'}]},{'then':[{'light':'r_lamp','on':1}]},
                       {'hint':True,'until':{'time':3}}):
            op=sample();op['stages'][0].update(fields)
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                story.generate(story.validate(op))

    def test_manual_physical_go_is_supported_and_never_changes_walk(self):
        steps=[{'go':[100,0,0]},{'walk':[100,0,8]}]
        self.assertEqual(scripting._test_steps(steps),steps)
