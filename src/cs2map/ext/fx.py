"""Atmosphere extension loaded by the MCP server."""
from .. import fx


def register(mcp,project):
    @mcp.tool()
    def fx_search(query: str = '', limit: int = 30) -> dict:
        """Find installed .vpcf particles and aliases (fire_small, smoke, rain...); limit 1..100."""
        return fx.search(project,query,limit)

    @mcp.tool()
    def fx_prepare_weather() -> dict:
        """Compile reusable point rain/snow assets from installed Valve operators. Run before add_fx weather boxes.
        Rain layers are 640 units tall, snow 540. No downloads; sources particles/mcp/m_rain and m_snow."""
        return fx.prepare_weather(project)

    project.register_op('add_fx',fx.add_fx)
