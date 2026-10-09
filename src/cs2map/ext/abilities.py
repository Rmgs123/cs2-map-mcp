"""Player abilities: map_edit add_ability (abilities.py); check_map warns when two mechanics share a key."""
from .. import abilities, checks


def register(mcp, project):
    project.register_op('add_ability', abilities.add_ability)
    checks.register_check('input_keys', abilities.check_keys)
