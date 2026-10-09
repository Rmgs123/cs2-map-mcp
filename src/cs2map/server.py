"""MCP server (stdio).  Run:  python -m cs2map.server

Core tools are defined here.  Feature areas add their own tools in
src/cs2map/ext/<area>.py, each exposing register(mcp, project); they are
discovered automatically, so parallel work never needs to edit this file.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from . import ext, surface
from .config import PROJECT_ROOT
from .project import Project
from .surface import RAW

GUIDE = PROJECT_ROOT / 'docs' / 'TOOLS.md'
GUIDE_PARTS = PROJECT_ROOT / 'docs' / 'tools'


def guide_sections() -> dict[str, Path]:
    """Feature-area sections of the guide: {name: docs/tools/<name>.md}."""
    return {f.stem: f for f in sorted(GUIDE_PARTS.glob('*.md'))} if GUIDE_PARTS.is_dir() else {}


def _section_title(path: Path) -> str:
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('#'):
            return line.lstrip('#').strip()
    return path.stem


def core_guide() -> str:
    """docs/TOOLS.md, an index of the sections and of the local layer my/ (what this computer learned)."""
    from . import mylayer
    index = [f'- `{name}`: {_section_title(path)}' for name, path in guide_sections().items()]
    return (GUIDE.read_text(encoding='utf-8') + '\n\n## Sections: read_guide(section="<name>")\n\n'
            'Read a section before you use its tools or operations.\n\n' + '\n'.join(index) + '\n'
            + mylayer.guide_index())


def full_guide() -> str:
    """docs/TOOLS.md followed by every docs/tools/*.md section (one per feature area)."""
    parts = [GUIDE.read_text(encoding='utf-8')]
    parts += [path.read_text(encoding='utf-8') for path in guide_sections().values()]
    return '\n\n'.join(parts)


def guide_text(section: str | None = None) -> str:
    if section is None or section == '':
        return core_guide()
    if section == 'all':
        return full_guide()
    if section == 'my' or section.startswith('my:'):
        from . import mylayer
        return mylayer.guide_section(section[3:])
    sections = guide_sections()
    if section not in sections:
        raise ValueError(f'unknown guide section {section!r}; sections: {", ".join(sections)}, all, my, my:<note>')
    return sections[section].read_text(encoding='utf-8')

INSTRUCTIONS = """CS2 map authoring through files: create maps, add geometry (meshes), entities, lights,
triggers and I/O, compile with Valve's resourcecompiler and test in the game.
Workflow: status -> map_create -> map_edit (batches of operations) -> map_build -> job_status
-> game_launch / game_screenshot to look at the result -> fix -> rebuild.
Units: 1 unit = 1 inch; player is 32x32x72, eye height 64, max step 18, jump ~56.
Coordinates: +Z up. Read the guide (read_guide, or resource cs2map://guide) before the first
map_edit: the core lists every operation, shape and the typical entities for a playable map;
read_guide(section=...) gives a feature area (atlas, buildings, terrain, story...) when you need it.
Local layer my/: notes from earlier chats on this computer (read_guide(section="my")); save new
lessons, recipes and own styles with my_notes. When a tool fails without your fault, a guide is wrong
or a feature is missing, write one entry with my_notes(kind="feedback"): the user sends it to the author.
Publishing: map_build(name, "release") puts one map in its own addon for the Steam Workshop."""

project = Project()
mcp = FastMCP('cs2map', instructions=INSTRUCTIONS, log_level='WARNING')


@mcp.resource('cs2map://guide', name='guide', description='Core guide: tools, map_edit operations, shapes, '
              'entity recipes, CS2 dimensions, and the index of feature sections', mime_type='text/markdown')
def guide() -> str:
    return core_guide()


@mcp.prompt(name='cs2map_guide', description='The core cs2map guide as a prompt')
def guide_prompt() -> str:
    return core_guide()


# Core functions behind the public tools (published by surface.py, which owns names and descriptions).
RAW.update({
    'status': project.status,
    'read_guide': guide_text,
    'map_create': project.map_create,
    'map_list': project.map_list,
    'map_info': lambda name, classname=None, kind=None, ids=None, full=False, offset=0, limit=200:
        project.map_info(name, classname, kind, None, ids, full, offset, limit),
    'map_edit': project.map_edit,
    'map_backups': project.map_backups,
    'map_restore': project.map_restore,
    'map_build': project.map_build,
    'job_status': project.job_status,
    'job_cancel': project.job_cancel,
    'asset_search': project.asset_search,
    'material_create': project.material_create,
    'entity_info': project.entity_info,
    'game_launch': project.game_launch,
    'game_command': project.game_command,
    'game_screenshot': project.game_screenshot,
    'hammer_open': project.hammer_open,
    'game_stop': project.game_stop,
})


def load_extensions() -> list[str]:
    """Import every module in cs2map.ext and call its register(mcp, project). Their tool functions go to RAW
    (surface.Collector); operations, kinds and resources register as usual."""
    loaded = []
    collector = surface.Collector(mcp)
    for info in pkgutil.iter_modules(ext.__path__):
        if info.name.startswith('_'):
            continue
        mod = importlib.import_module(f'{ext.__name__}.{info.name}')
        register = getattr(mod, 'register', None)
        if register is not None:
            register(collector, project)
            loaded.append(info.name)
    return loaded


EXTENSIONS = load_extensions()
# Extensions wrap project.map_edit (dry run first, resources written after it); publish the wrapped one.
RAW['map_edit'] = project.map_edit
TOOLS = surface.register(mcp, project)


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    from . import housekeeping
    housekeeping.start_background(project.s)          # disk limits for screenshots/logs/backups (at most hourly)
    mcp.run('stdio')



if __name__ == '__main__':
    main()
