"""Actor MCP tools and map operations, loaded by the standard extension loader."""
from .. import actors, checks


def register(mcp, project):
    @mcp.tool()
    def actor_build(name: str = 'walker', script: str | None = None, walk_speed: float = 62.0) -> dict:
        """Build own skeletal prop (idle/walk/turn), FBX + ModelDoc + compiler.

        Default is a small stylized walker, inches, feet z=0, face +X. Optional
        script is a project-local Blender .py creating one rig and named Actions.
        walk_speed is the authored walk cycle's speed in units/second.
        """
        return actors.actor_build(project, name, script, walk_speed)

    project.register_op('add_actor', actors.add_actor)
    project.map_edit = actors.map_edit_wrapper(project, project.map_edit)
    checks.register_check('actors', actors.check_actors)
