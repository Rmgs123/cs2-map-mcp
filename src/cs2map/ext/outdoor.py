"""Outdoor scene recipes loaded automatically by the MCP server."""
from .. import outdoor


def register(mcp, project):
    @mcp.tool()
    def outdoor_sky(style: str | None = None) -> dict:
        """List verified stock skybox materials, or return one by style."""
        return outdoor.backdrop_sky_info(style)

    project.register_op('add_water_body', outdoor.add_water_body)
    project.register_op('add_backdrop', outdoor.add_backdrop)
    project.register_op('add_supports', outdoor.add_supports)
