"""Register physical route testing without modifying core tools."""
from .. import route as routes
from .. import checks


def register(mcp, project):
    checks.register_check('route',routes.check_route)
    @mcp.tool()
    def route_test(map: str, route: list[dict] | str = 'auto', timeout: float = 90,
                   start: str | list[float] = 'spawn') -> dict:
        """Walk a route with real player input (feet/map units, no teleport during steps).
        Steps are go [x,y,z] (jump/duck), use targetname, wait seconds, ride
        (to_z), climb_down ([x,y,z] or {to,top}), break targetname and push
        targetname with to [x,y,z].  ``allow_damage`` is opt-in per step.
        route='auto' follows the sole add_story and plans layout lifts/ladders,
        doorways and same-floor grid paths. timeout is 5..90 seconds.
        start='spawn' restarts the round, 'current' uses the current player,
        and start=[x,y,z] deliberately positions the player once before input.
        Returns reached points, per-stage seconds, physical stuck reason, shots and log.
        """
        return routes.route_test(project, map, route, timeout, start)
