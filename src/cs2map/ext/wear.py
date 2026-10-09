"""MCP registration for the W (wear) round-7 extension."""
from __future__ import annotations

from .. import wear


def register(mcp, project):
    @mcp.tool()
    def wear_materials(query: str = 'de_inferno decals', limit: int = 50) -> dict:
        """Search stock/addon materials suitable for facade wear and projected overlays."""
        return wear.material_candidates(project, query, limit)

    def add_wear(p, vmap, op, refs):
        return wear.add_wear(p, vmap, op, refs)

    project.register_op('add_wear', add_wear)

