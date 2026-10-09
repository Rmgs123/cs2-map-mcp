"""One weapon operation, add_weapon (stock base + own model + own shot); weapon_prepare builds ready assets."""
from .. import weapons


def register(mcp, project):
    @mcp.tool()
    def weapon_prepare(kind: str = 'railgun') -> dict:
        """Build ready weapon assets: railgun (AWP base, rigged model, six sounds) or zapper (taser base gadget).
        Returns the model and sound events for add_weapon."""
        return weapons.prepare(project, kind)

    project.register_op('add_weapon', weapons.add_weapon)
    # The former two operations, merged into add_weapon: a clear refusal instead of "unknown op".
    project.register_op('add_custom_weapon', weapons.old_op('add_custom_weapon'))
    project.register_op('add_scripted_weapon', weapons.old_op('add_scripted_weapon'))
