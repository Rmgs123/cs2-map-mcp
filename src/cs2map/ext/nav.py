"""Bot navigation: the play area, nav markup and the nav cost check (integrator, 1.2)."""
from .. import checks, navzone


def register(mcp, project):
    project.register_op('add_nav_markup', navzone.add_nav_markup)
    project.register_op('set_play_area', navzone.set_play_area)
    checks.register_check('nav', navzone.check_nav)
