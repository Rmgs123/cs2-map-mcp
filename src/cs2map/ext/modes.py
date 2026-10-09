"""C2 game mode and movement tools."""
from .. import checks, modes


def register(mcp, project):
    project.register_preprocessor(modes.preprocess)
    for name in ("add_mode","add_surf_ramp","add_bhop_platforms","add_movement_course","add_arena"):
        project.register_op(name,getattr(modes,name))
    project.map_edit = modes.map_edit_wrapper(project,project.map_edit)
    checks.register_check("modes",modes.check_modes)

    @mcp.tool()
    def mode_setup(map: str, mode: str) -> dict:
        """Add map-local mode entities/script; return native launch command. Build full afterwards.

        Modes: deathmatch/armsrace/wingman/casual/surf/bhop/kz/aim. Native rules
        need game_type/game_mode BEFORE loading. This tool does not change CS2.
        Example: map='c2_dm', mode='deathmatch'. Read modes guide for movement operations.
        """
        return modes.mode_setup(project,map,mode)

    @mcp.tool()
    def mode_views(map: str, views: list[dict], hide_hud: bool = True) -> dict:
        """Capture 1..6 views with mp_freezetime=0 before/after load, then restore settings.

        views use game_screenshot schema: name c2_*, pos [x,y,z] feet, ang [pitch,yaw,roll].
        Whole map load/capture holds one CS2 lock; unloads to mcp_idle afterwards.
        """
        return modes.mode_views(project,map,views,hide_hud)

    @mcp.tool()
    def mode_course_test(map: str, timeout: int = 60) -> dict:
        """Test course timer/checkpoint/fall triggers using setup teleports, with two HUD frames.

        timeout 15..90s. Real engine trigger events and console expectations are checked;
        this is not a physical surf/bhop completion. Unloads/restores server settings.
        """
        return modes.mode_course_test(project,map,timeout)

    @mcp.tool()
    def mode_playtest(map: str, bots: int = 10, seconds: int = 30) -> dict:
        """Load authored native mode, observe bots/death-respawn events and capture one frame.

        bots 2..20, seconds 5..60. Holds one CS2 lock, unloads to mcp_idle and restores
        mode/physics/respawn convars even on failure. Native DM/armsrace pass requires
        a real kill followed by respawn; a short test can return mode_ok=false.
        """
        return modes.mode_playtest(project,map,bots,seconds)
