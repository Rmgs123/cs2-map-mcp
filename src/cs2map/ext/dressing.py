"""Palette tool and file-only dressing operations; discovered by server.py."""
from .. import dressing


def register(mcp, project):
    @mcp.tool()
    def theme_info(theme: str = 'all') -> dict:
        """Get a measured stock CS2 palette: materials, model bounds (inches),
        physics flags, wall decals and sound-event suggestions. Themes:
        warehouse, industrial, office, dust, harbor, sewer, night_street, all.
        verified means opened game frames; sound suggestions are indexed only.
        Use map_edit dress_room with room={mins,maxs}, density=0..1, seed=int,
        keep_clear=[[mins,maxs]] to populate a room without blocking its routes.
        """
        return dressing.theme_info(theme)

    for name in ('dress_room', 'add_trim', 'add_pipes', 'add_cables'):
        project.register_op(name, getattr(dressing, name))
