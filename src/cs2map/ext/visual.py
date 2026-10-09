"""MCP extension for conservative visual map checks."""
from __future__ import annotations

from .. import checks, visual


def register(mcp, project):
    @mcp.tool()
    def visual_check(name: str) -> dict:
        """Audit a VMAP for floating props, deep intersections, unsupported lamps,
        open solid meshes, likely z-fighting, unbacked world text and exposed
        outdoor foundations. Returns warnings with node ids and fixes.
        Unknown stock model bounds are skipped safely.
        """
        return visual.visual_check(project, name)

    checks.register_check("visual", visual.visual_check_loaded)
    project.register_op('add_button_visual',visual.add_button_visual)
    project.register_op('add_control_button',visual.add_control_button)
    project.register_op('add_cloth_awning',visual.add_cloth_awning)
