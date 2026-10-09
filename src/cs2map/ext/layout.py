"""K layout extension; discovered by the server without core changes."""
from .. import checks, layout as api


def register(mcp, project):
    project.register_op('add_layout', api.add_layout)
    project.register_op('add_lift', api.add_lift)
    project.register_preprocessor(api.preprocess)
    checks.register_check('layout', api.check_layout)

    @mcp.tool()
    def layout_info(name: str, layout: str | None = None) -> dict:
        """Read saved layout plans: room interior mins/maxs, floor_z, levels, openings and mesh IDs.

        name is the map; layout optionally selects a plan, e.g. name='k_warehouse', layout='wh'.
        Units are CS2 units. No map or game changes.
        """
        return api.layout_info(project, name, layout)

    @mcp.tool()
    def layout_views(name: str, layout: str | None = None, offset: int = 0, limit: int = 6) -> dict:
        """Capture rooms from the saved layout under the shared CS2 lock.

        One eye-level view per room/platform, k_ names; offset paginates, limit 1..6.
        Loads the compiled map. Open the returned PNGs to assess the actual geometry.
        """
        return api.layout_views(project, name, layout, offset, limit)
