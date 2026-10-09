import tempfile
import unittest
from pathlib import Path

from cs2map.scripting import _api_entries, _api_type, _safe_name


class ScriptingValidation(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(_safe_name('round_start_1'), 'round_start_1')
        for name in ('../x', 'a-b', '', 'a' * 65):
            with self.assertRaises(ValueError):
                _safe_name(name)

    def test_api_entries_keep_declaration_and_comment(self):
        entries = _api_entries('/** Say hello. */\nMsg(text: any): void;\n/** Start a round. */\nOnRoundStart(callback: () => void): void;')
        self.assertEqual([e['name'] for e in entries], ['Msg', 'OnRoundStart'])
        self.assertIn('Say hello', entries[0]['comment'])
        self.assertIn('Start a round', entries[1]['comment'])

    def test_full_type_preserves_nested_comments_and_multiline_declarations(self):
        text = '''/** Full class {doc}. */
export class CustomHudLayout extends Entity {
 /** Some comment with a } brace. */
 SetDialogVariableString(
  panelId: string, variableName: string, value: string
 ): void;
 Complex(cb: (data: { nested: { a: number } }) => void): void;
}
interface CameraFollowConfig {
 /** Follow the entity. */
 followEntity: Entity;
}
'''
        entry = _api_type(text, 'CustomHudLayout')
        self.assertIn('Full class {doc}', entry['declaration'])
        self.assertIn('value: string', entry['declaration'])
        self.assertTrue(entry['declaration'].endswith('}'))
        self.assertNotIn('CameraFollowConfig', entry['declaration'])
        self.assertIn('followEntity', _api_type(text, 'CameraFollowConfig')['declaration'])
        self.assertIsNone(_api_type(text, 'Missing'))
        methods = _api_entries(text)
        method = next(e for e in methods if e['name'] == 'SetDialogVariableString')
        self.assertIn('value: string', method['declaration'])
        self.assertNotIn('panelId', [e['name'] for e in methods])



class UnchangedScript(unittest.TestCase):
    def test_an_unchanged_compiled_script_is_not_recompiled(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        from cs2map import scripting
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = SimpleNamespace(content_addon=root / 'content', game_addon=root / 'game', backups=root / 'backups')
            project = SimpleNamespace(s=s, jobs=None)
            code = 'import { Instance } from "cs_script/point_script";\nInstance.Msg("x");\n'
            src = s.content_addon / 'maps/scripts/mcp/same.js'
            out = s.game_addon / 'maps/scripts/mcp/same.vjs_c'
            src.parent.mkdir(parents=True); out.parent.mkdir(parents=True)
            src.write_text(code, encoding='utf-8', newline='\n'); out.write_bytes(b'compiled')
            os.utime(out, (src.stat().st_mtime + 5, src.stat().st_mtime + 5))
            with patch('cs2map.compiler.compile_assets') as compile_assets:
                res = scripting.script_write(project, 'same', code)
                compile_assets.assert_not_called()
            self.assertEqual((res['ok'], res['compile_state']), (True, 'unchanged'))
            with patch('cs2map.compiler.compile_assets', side_effect=RuntimeError('compiles')):
                with self.assertRaisesRegex(RuntimeError, 'compiles'):
                    scripting.script_write(project, 'same', code + '// changed\n')
