"""MCP extension for conservative visual map checks."""
from __future__ import annotations

from .. import checks, visual


def register(mcp, project):
    @mcp.tool()
    def visual_check(name: str, limit: int = 200, box: list | None = None) -> dict:
        """Audit a VMAP for floating props, deep intersections, unsupported lamps,
        open solid meshes, likely z-fighting, unbacked world text and exposed
        outdoor foundations. Returns warnings with node ids and fixes (the first
        `limit`, errors first) and summary.by_code with every count.
        Unknown stock model bounds are skipped safely. box=[[x0,y0],[x1,y1]] audits one zone.
        """
        return visual.visual_check(project, name, limit, box)

    checks.register_check("visual", visual.visual_check_loaded)
    project.register_op('add_button_visual',visual.add_button_visual)
    project.register_op('add_control_button',visual.add_control_button)
    project.register_op('add_cloth_awning',visual.add_cloth_awning)
