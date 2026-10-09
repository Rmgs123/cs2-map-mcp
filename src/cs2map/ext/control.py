"""D controllable object extension; no changes to core."""
from .. import checks, control


def register(mcp, project):
    project.register_op('add_controllable', control.add_controllable)
    project.map_edit = control.map_edit_wrapper(project, project.map_edit)
    checks.register_check('control', control.check_control)

    @mcp.tool()
    def control_build_assets() -> dict:
        """Build the own quadcopter, separate rotor and remote using Blender/model_build's driver.

        Writes only models/mcp_d and materials/mcp_d. Units: inches, +Z up, front +X.
        Returns compiled model paths, bounds and build logs. No game launch or map edit.
        """
        return control.build_assets(project)
