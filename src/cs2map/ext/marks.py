"""Player marks (marks.py): the tester's key in CS2 -> frames with map, position, view, time and note.
Published through game_screenshot(views="marks"); the key is bound by game_launch."""
from __future__ import annotations

from .. import marks


def register(mcp, project):
    marks.enable()

    @mcp.tool()
    def game_marks(map: str | None = None, offset: int = 0, limit: int | None = None) -> dict:
        """Player marks newest first: frame path, map, pos (feet), ang, time, note."""
        return marks.listing(project.s, map, offset, limit)
