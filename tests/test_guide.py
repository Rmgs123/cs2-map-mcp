"""The guide is served as a core part plus sections read on demand (saves the model's context)."""
import unittest
from pathlib import Path

from cs2map import server


class Guide(unittest.TestCase):
    def test_core_has_index_and_is_much_smaller_than_everything(self):
        core, everything = server.guide_text(), server.guide_text('all')
        self.assertIn('map_edit operations', core)
        self.assertIn('read_guide(section="<name>")', core)
        for name in ('atlas', 'buildings', 'terrain', 'story', 'visual'):
            self.assertIn(f'- `{name}`:', core)
        self.assertLess(len(core), len(everything) / 3)

    def test_every_section_has_task_table_by_hand_and_size_limit(self):
        """Each section tells what to take for which task, how to do it by hand, and stays small (10.16)."""
        root = Path(server.__file__).resolve().parents[2] / 'docs'
        self.assertLessEqual(len((root / 'TOOLS.md').read_bytes()), 20480)
        for path in sorted((root / 'tools').glob('*.md')):
            text = path.read_text(encoding='utf-8')
            with self.subTest(section=path.stem):
                self.assertLessEqual(len(text.encode()), 16000)
                header = [line for line in text.splitlines() if line.startswith('| ') and
                          ('Take' in line or 'By hand' in line)]
                self.assertTrue(header, 'no "what to take" table')
                self.assertTrue(any(line.startswith('#') and 'by hand' in line.lower() for line in text.splitlines()),
                                'no "By hand" heading')

    def test_section_and_unknown_section(self):
        self.assertIn('prop_preview', server.guide_text('atlas'))
        with self.assertRaisesRegex(ValueError, 'sections: .*atlas'):
            server.guide_text('nope')


if __name__ == '__main__':
    unittest.main()
