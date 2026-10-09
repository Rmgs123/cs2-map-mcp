"""Prop atlas MCP tools: find, look at and describe stock models (docs/specs/atlas.md)."""
from __future__ import annotations

from .. import atlas


def register(mcp, project):
    @mcp.tool()
    def prop_search(query: str, limit: int = 12) -> dict:
        """Find stock models: one line each with size and the note, if any model has seen it.

        Matches words in the file name, path, materials and notes. Look before you
        place: prop_preview(models=[...]). Size is XxYxZ inches in model axes.
        """
        return atlas.search(project, query, limit)

    @mcp.tool()
    def prop_preview(models: list[str], detail: bool = False, game: bool = False) -> dict:
        """Render stock models offline (about 1 s each, cached) and return one labelled sheet.

        Up to 16 models; detail=true gives two opposite views for up to 6 models.
        Red arrow = model +X (yaw 0), blue box = player. game=true is allowed only
        for models whose offline preview is broken; it occupies CS2 and is slow.
        """
        return atlas.preview(project, models, detail, game)

    @mcp.tool()
    def prop_note(model: str, category: str, tags: list[str], style: str, front: str, mount: str,
                  by: str, note: str = '') -> dict:
        """Record what you saw on a preview (your personal notes, used by prop_search).

        category: architecture, door_window, stairs_railing, furniture, lighting, sign,
        street, vegetation, rock_terrain, vehicle, container, industrial, debris, decor,
        appliance, tool, other. tags: 3..6 lowercase words. style: one word (inferno,
        italy, mirage, generic...). front: +x, -x, +y, -y or none. mount: floor, wall,
        ceiling or any. by: your model name. note: up to 80 chars.
        """
        return atlas.note(project, model, category, tags, style, front, mount, note, by)
