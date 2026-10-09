"""MCP tools of the test layer: reset between scenarios and the crash report."""
from __future__ import annotations

from .. import gametest


def register(mcp, project):
    @mcp.tool()
    def game_reset(map: str | None = None) -> dict:
        """Put the running game back to a clean state between scenarios, in one call (~6 s).

        Leaves every controllable object (drone, car, turret: `RunScriptInput Exit`), noclip off, HUD and weapon
        model on, default accuracy and recoil, bots removed, held keys released, then `mp_restartgame 1` and waits for
        a live player. map: the map whose controllables to release (default: the loaded one).
        Returns ok, what was done and the player's health. Use before and after every script_test scenario."""
        return gametest.reset(project, map)

    @mcp.tool()
    def game_crash_report(dump: str | None = None) -> dict:
        """Why did CS2 crash or stop? Reads the newest minidump (cs2_*_crash/_error/_accessviolation.mdmp).

        Returns the exception (e.g. STACK_OVERFLOW), the module it happened in (server.dll = a script or entity
        problem), `crashed_since_launch`, a one-line hint, the last console lines found in the dump and the path of
        the full report in logs/. dump: a file name from game/bin/win64 (default: the newest).
        Call it when CS2 disappeared, the console says it closed, or a tool reports "did the game crash"."""
        return gametest.crash_report(project, dump)
