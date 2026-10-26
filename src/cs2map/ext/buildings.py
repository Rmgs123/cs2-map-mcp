"""MCP entrypoints for stock buildings and local facade references."""
from .. import buildings, reference as reference_mod
from .. import game
from ..project import OpError


def register(mcp, project):
    project.register_op('add_building', buildings.add_building)
    from .. import facades, paving
    project.register_op('add_facade', facades.add_facade)
    project.register_op('add_paving', paving.add_paving)
    from .. import districts
    project.register_op('add_district', districts.add_district)

    @mcp.tool()
    def building_styles(style: str | None = None) -> dict:
        """List building_inferno/building_mirage measured parts, openings and materials.

        Use part keys in one map_edit add_building operation. Units are CS2 inches.
        verified=game means an opened CS2 test-wall frame; bounds means measurement only.
        """
        return buildings.building_styles(style)

    @mcp.tool()
    def reference_facade(image: str, quad: list[list[float]] | None = None,
                         real_width: float | None = None) -> dict:
        """Rectify a local facade photo and suggest floors, window grid and palette.

        quad is TL,TR,BR,BL source pixel corners. real_width is CS2 units.
        Returns rectified PNG, wall crop for material_from_reference, and facades
        for add_building. Open the PNG and correct the heuristic suggestions by eye.
        """
        return reference_mod.reference_facade(image, quad, real_width)

    @mcp.tool()
    def reference_compare(reference: str | None = None, shot: str | None = None, pairs: list | None = None,
                          plan: dict | None = None) -> dict:
        """Place two local images side by side at equal height; return the comparison PNG. pairs: rows of
        [photo, frame]; plan: the map from above with camera marks (reference.reference_plan).

        Open the image, name three visible differences, then refine the map.
        """
        if plan is not None:
            return reference_mod.reference_plan(project, plan)
        if pairs is not None:
            return reference_mod.reference_pairs(pairs)
        if not reference or not shot:
            raise ValueError('give reference and shot, or pairs, or plan')
        return reference_mod.reference_compare(reference, shot)

    @mcp.tool()
    def building_views(map: str, views: list[dict]) -> dict:
        """Capture 1..6 building views under one CS2 lock, with mp_freezetime 0.

        Same camera schema as game_screenshot: name h_*, pos=feet, ang degrees.
        Sets freeze time before loading and repeats it on the first view; open PNGs.
        Does not restart CS2. Other agents' maps cannot load during this call.
        """
        if not isinstance(views,list) or not 1<=len(views)<=6:
            raise OpError('building_views needs 1..6 views')
        if any(not isinstance(v,dict) or not str(v.get('name','')).startswith('h_') for v in views):
            raise OpError('building view names must start h_')
        prepared=[dict(v) for v in views]
        prepared[0]['cmd']='mp_freezetime 0\nmp_freezetime\n'+prepared[0].get('cmd','')
        prepared[0].setdefault('wait',.3)
        with game.exclusive(owner='H-building-views',wait=45):
            game.command(project.s,'mp_freezetime 0\nengine_no_focus_sleep 0',wait=.2)
            return project.game_screenshot(map=map,views=prepared)
