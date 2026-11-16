"""Master prompts: whole workflows offered by the server as MCP prompts (slash commands in clients that show them).

Each template is data/prompts/<name>.md: a front matter with `description` and `arguments`
(`name | what it is`, separated by ` ; `; a description starting with "Optional" makes the argument optional),
then the text with {argument} placeholders. Prompts cost no tool-description budget; tests check that every tool
and guide section they name exists.
"""
from __future__ import annotations

import re
from pathlib import Path

FOLDER = Path(__file__).with_name('data') / 'prompts'


def templates() -> dict[str, dict]:
    """{name: {description, arguments: [(name, text, required)], text}}"""
    out = {}
    for path in sorted(FOLDER.glob('*.md')):
        raw = path.read_text(encoding='utf-8')
        match = re.match(r'---\n(.*?)\n---\n(.*)', raw.replace('\r\n', '\n'), re.S)
        if not match:
            raise ValueError(f'{path.name}: no front matter')
        head = dict(line.split(':', 1) for line in match.group(1).splitlines() if ':' in line)
        args = []
        for part in head.get('arguments', '').split(' ; '):
            if '|' in part:
                name, text = (s.strip() for s in part.split('|', 1))
                args.append((name, text, not text.lower().startswith('optional')))
        out[path.stem] = {'description': head['description'].strip(), 'arguments': args, 'text': match.group(2)}
    return out


def render(name: str, values: dict) -> str:
    t = templates()[name]
    text = t['text']
    for arg, _, required in t['arguments']:
        value = str(values.get(arg) or '').strip()
        if not value and required:
            raise ValueError(f'prompt {name} needs {arg}')
        text = text.replace('{' + arg + '}', value or 'none given')
    return text


def register(mcp) -> None:
    from mcp.server.fastmcp.prompts.base import Prompt, PromptArgument
    for name, t in templates().items():
        def fn(_name=name, **values):
            return render(_name, values)
        mcp.add_prompt(Prompt(name=name, description=t['description'], fn=fn,
                              arguments=[PromptArgument(name=a, description=d, required=r)
                                         for a, d, r in t['arguments']]))
