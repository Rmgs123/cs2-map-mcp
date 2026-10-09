"""HUD themes, the map default and own layouts (ui.py, ext/ui.py)."""
import unittest

from cs2map import control, story, ui
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


class Themes(unittest.TestCase):
    def test_override_has_theme_position_and_animation(self):
        css = ui.override({'panel': '#control', 'title': '#title'}, {'theme': 'neon', 'position': 'top-right',
                                                                     'size': 'l', 'animation': 'slide'})
        self.assertIn('horizontal-align: right; vertical-align: top;', css)
        self.assertIn('#36f2ff', css)                       # neon accent
        self.assertIn('#control.Hidden { visibility: visible; opacity: 0; transform: translateY(-18px); }', css)
        self.assertEqual(ui.override({'panel': '#x'}, {'title': 'only a title'}), '')

    def test_validation(self):
        for hud, msg in (({'theme': 'pink'}, 'theme'), ({'position': 'middle'}, 'position'),
                         ({'accent': [1, 2]}, 'accent'), ({'width': 50}, 'width'), ({'font': 'Comic Sans'}, 'font'),
                         ({'colour': 1}, 'unknown')):
            with self.subTest(msg=msg), self.assertRaisesRegex(ui.UIError, msg):
                ui.validate(hud)
        self.assertEqual(ui.validate({'title': 'X', 'theme': 'glass'}, extra={'title'})['title'], 'X')

    def test_layout_checks(self):
        ids = ui.check_layout('<root><Panel id="a"><Label id="b" text="{s:x}" /><Button id="c" /></Panel></root>', '')
        self.assertEqual(ids, ['a', 'b', 'c'])
        for xml, msg in (('<root><Video id="v" /></root>', 'unsupported tags'),
                         ('<root><Panel onactivate="x" /></root>', 'scripts'),
                         ('<root><Image src="http://x/y.png" /></root>', 'image src'),
                         ('<root><Panel id="a" /><Panel id="a" /></root>', 'unique')):
            with self.subTest(msg=msg), self.assertRaisesRegex(ui.UIError, msg):
                ui.check_layout(xml, '')

    def test_mechanic_huds_take_the_style(self):
        spec = {'kind': 'drone', 'name': 'd1', 'hud': {'theme': 'paper', 'position': 'left'}}
        roles_css = ui.override(control.HUD_ROLES, spec['hud'], 'bottom')
        self.assertIn('#control {', roles_css)
        code, xml, css, ops = story.generate(story.validate({'op': 'add_story', 'name': 's1', 'hud': {'theme': 'glass'},
                                                             'stages': [{'id': 'a', 'objective': 'Go',
                                                                         'until': {'time': 5}}]}))
        self.assertIn('/* cs2map ui theme */', css)
        self.assertIn('#subtitle {', css.split('/* cs2map ui theme */')[-1])


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class MapDefault(unittest.TestCase):
    def test_set_ui_theme_fills_operations(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'basic')
        ui.set_theme_op(None, m, {'op': 'set_ui_theme', 'theme': 'military', 'size': 's'}, {})
        op = ui.preprocess(None, m, {'op': 'add_minigame', 'hud': {'title': 'RANGE', 'size': 'l'}}, {})
        self.assertEqual(op['hud'], {'title': 'RANGE', 'size': 'l', 'theme': 'military'})
        self.assertEqual(ui.preprocess(None, m, {'op': 'add_mesh'}, {}), {'op': 'add_mesh'})
        with self.assertRaisesRegex(ui.UIError, 'position'):
            ui.set_theme_op(None, m, {'op': 'set_ui_theme', 'position': 'top'}, {})


if __name__ == '__main__':
    unittest.main()
