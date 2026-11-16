"""Master prompts (data/prompts/*.md): they render, and every tool and guide section they name exists."""
import asyncio
import re
import unittest

from cs2map import prompts, server, surface


class Prompts(unittest.TestCase):
    def test_listed_with_arguments(self):
        listed = {p.name: p for p in asyncio.run(server.mcp.list_prompts())}
        for name, t in prompts.templates().items():
            with self.subTest(prompt=name):
                self.assertIn(name, listed)
                self.assertEqual([a.name for a in listed[name].arguments], [a for a, _, _ in t['arguments']])

    def test_render_fills_every_placeholder(self):
        for name, t in prompts.templates().items():
            with self.subTest(prompt=name):
                text = prompts.render(name, {a: 'x' for a, _, _ in t['arguments']})
                self.assertNotRegex(text, r'\{[a-z_]+\}')
                required = [a for a, _, r in t['arguments'] if r]
                if required:
                    with self.assertRaises(ValueError):
                        prompts.render(name, {})

    def test_named_tools_and_sections_exist(self):
        tools = set(surface.PUBLIC)
        sections = set(server.guide_sections()) | {'my', 'all'}
        for name, t in prompts.templates().items():
            with self.subTest(prompt=name):
                for call in re.findall(r'`([a-z_]+)\(', t['text']):
                    self.assertIn(call, tools, f'{name} names a tool that does not exist: {call}')
                for section in re.findall(r'section="([a-z_]+)"', t['text']):
                    self.assertIn(section, sections, f'{name} names a guide section that does not exist: {section}')
                for word in re.findall(r'`([a-z]+_[a-z_]+)`', t['text']):
                    if word in ('map_edit', 'map_build') or word.startswith(('map_', 'game_', 'script_', 'route_',
                                                                             'check_', 'visual_', 'my_', 'read_')):
                        self.assertIn(word, tools, f'{name} names a tool that does not exist: {word}')


if __name__ == '__main__':
    unittest.main()
