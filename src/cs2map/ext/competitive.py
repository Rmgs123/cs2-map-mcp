"""Competitive map tools and checks (agent C)."""
from .. import checks, competitive


def register(mcp, project):
    project.register_preprocessor(competitive.preprocess)
    project.register_op("add_comp_plan", competitive.add_comp_plan)
    checks.register_check("competitive", competitive.check_competitive)

    @mcp.tool()
    def comp_timings(map: str, timeout: float = 60, speed: float = 250, live: bool = True, offset: int = 0) -> dict:
        """Measure T/CT routes to each competitive objective and mid.

        Static distances follow add_layout openings and report knife-speed
        estimates (250 Source 2 units/s, 1 unit = 0.0254 m).  With live=true
        one route per call is attempted through route_test (repeat next_offset).
        Physical seconds exclude setup; prior rows for this VMAP are retained.
        """
        return competitive.comp_timings(project, map, timeout, speed, live, offset)

    @mcp.tool()
    def comp_playtest(map: str, bots: int = 10, seconds: int = 60, round_seconds: int | None = None,
                      spectate: bool = True) -> dict:
        """Run bots for 1–90s; report movement, completed rounds and death locations.

        Optional round_seconds=30 shortens the test round clock (20–120s),
        then restores its convars. spectate=true (default): you watch, bots play alone.
        Example: map='c_defuse', seconds=60.
        """
        return competitive.comp_playtest(project, map, bots, seconds, round_seconds, spectate)
