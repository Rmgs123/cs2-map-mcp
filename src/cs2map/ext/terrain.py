"""MCP registration for terrain and street authoring."""
from .. import terrain, streets

def register(mcp, project):
    project.register_op('add_terrain', terrain.add_terrain)
    project.register_op('scatter', terrain.scatter)
    project.register_op('add_street', streets.add_street)
    project.register_preprocessor(terrain.preprocess)
    project.preprocessors.remove(terrain.preprocess)
    project.preprocessors.insert(0,terrain.preprocess)

    @mcp.tool()
    def terrain_info(name: str, terrain_name: str | None = None) -> dict:
        """Read persisted terrain plans and street lots from a map."""
        return terrain.terrain_info(project, name, terrain_name)

    @mcp.tool()
    def terrain_sample(name: str, terrain_name: str, points: list[list[float]]) -> dict:
        """Sample ground Z, normal and slope at up to 256 XY points."""
        return terrain.terrain_sample(project, name, terrain_name, points)

    @mcp.tool()
    def street_info(name: str, street: str | None = None) -> dict:
        """Read saved street centerline, slope and building lots."""
        return streets.street_info(project, name, street)

    @mcp.tool()
    def terrain_route_test(name: str, terrain_name: str, points: list[list[float]], timeout: float = 90) -> dict:
        """Walk XY terrain waypoints physically with noclip off. Sets mp_freezetime 0 for this loaded map.

        First point is setup/start, later points are walked. 2..32 XY points, timeout 5..90 seconds.
        Holds the shared CS2 lock across setup and route_test; no agent can replace the map mid-test.
        """
        from .. import game, route
        import time
        if not isinstance(points,list) or not 2<=len(points)<=32:
            raise terrain.OpError('points need 2..32 XY points')
        terrain.number(timeout,'timeout',5,90)
        data=terrain.select(project.load(name),terrain_name)
        waypoints=[]
        for point in points:
            xy=terrain.vec(point,'point');waypoints.append(xy+[terrain.sample(data,*xy)['z']+3])
        with game.exclusive(wait=60):
            setup=project.game_screenshot(map=name,views=[{'name':f't_route_setup_{name}_{int(time.time()*1000)}',
                'cmd':'mp_freezetime 0\nbot_kick\nmp_freezetime','grep':'mp_freezetime','wait':.2}])
            out=route.route_test(project,name,[{'go':p,'within':15} for p in waypoints[1:]],timeout,waypoints[0])
            out['freeze_setup']=setup['shots']
            return out
