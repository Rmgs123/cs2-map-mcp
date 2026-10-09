"""MCP extension: interactive recipes, model body inspection and live probe."""
from .. import physics, checks


def register(mcp, project):
    for name, fn in physics.OPERATIONS.items():
        project.register_op(name, fn)
    checks.register_check('physics', physics.check_physics)

    @mcp.tool()
    def physics_probe(map: str, names: list[str]) -> dict:
        """Observe named runtime entities in the compiled map under the game lock.

        Returns exact-match count, existence, position (CS2 units), angles
        (degrees), velocity, health and alive flag. Compare before/after to
        verify movement or disappearance; disappearance alone cannot prove
        breakage. Example: map='p_interactive', names=['p_door','p_crate'].
        """
        return physics.physics_probe(project, map, names)

    @mcp.tool()
    def physics_model_info(model: str) -> dict:
        """Check whether a compiled own/stock .vmdl contains a PHYS body.

        model accepts a short model_build name or models/.../file.vmdl.
        Physics recipes refuse a model without compiled collision data.
        """
        return physics.model_physics(project, model)
